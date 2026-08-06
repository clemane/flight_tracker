from __future__ import annotations

import logging
import re
from datetime import date
from typing import TYPE_CHECKING, Any

from bs4 import BeautifulSoup
from bs4.element import Tag

from scrappervol.config import Settings
from scrappervol.core.types import FlightOffer, SearchQuery, TripType
from scrappervol.providers.base import EmptyResultError, ProviderError
from scrappervol.providers.playwright_base import fetch_html

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page

logger = logging.getLogger(__name__)

NOM = "air_canada"

URL_ACCUEIL = "https://www.aircanada.com/"

# La devise est fixée plutôt que lue dans la page : le parcours est ouvert sur l'édition
# canadienne (aircanada.com/ redirige vers /ca/fr), dont les prix sont libellés en dollars
# canadiens — le balisage l'écrit d'ailleurs en toutes lettres dans le texte masqué du total
# (« Total général 540,71CAD »), format sur lequel _prix_en_cad s'ancre.
DEVISE = "CAD"

# Sélecteurs relevés à la reconnaissance du 6 août (voir docs/superpowers/notes/).
# Une cellule de cabine porte la classe `cabin-fare-{rang}-{code cabine}` : `Y` désigne
# l'Économique, la seule cabine suivie ici. Le rang est l'index du vol dans la liste, ce qui permet
# de rattacher une cellule au bloc de vol qui la décrit (escales, durée) sans dépendre de la
# structure d'imbrication, laquelle change entre la page aller et la page retour.
_SEL_CELLULE_ECONOMIQUE = "button[class*='cabin-fare-'][class*='-Y']"
_SEL_BLOC_VOL = "ac-ui-flight-block-summary-pres"
_SEL_TOTAL = ".total-price"
_SEL_VILLES = "ac-ui-city-pairing"

# Le panneau de tarifs qui s'ouvre au clic sur une cellule de cabine. Le libellé du bouton est
# « Sélectionner » : on n'en cible qu'un fragment sans accent, la normalisation Unicode du « é »
# servi par le site ne correspondant pas à celle d'un littéral Python (piège vérifié à la
# reconnaissance : un sélecteur `:text-is('Sélectionner')` ne remonte aucun élément alors que cinq
# boutons sont affichés).
_SEL_BOUTON_TARIF = "button:has-text('lectionner')"

# Modale de vente incitative, ouverte après le choix du tarif le plus bas. Elle intercepte les
# clics suivants tant qu'elle reste ouverte. Son pied de page porte l'unique bouton qui conserve
# le tarif choisi (« Continuez avec le De base ») ; les autres montent en gamme et fausseraient
# le prix relevé.
_SEL_MODALE = "ac-ui-avail-fare-upsell-modal-pres"
_SEL_MODALE_CONSERVER = f"{_SEL_MODALE} .basic-footer button"
_SEL_MODALE_CASE = f"{_SEL_MODALE} input[type=checkbox]"

_ETAPE_RECAPITULATIF = "review-trip"

# « Total général 540,71CAD », « 1 234,56CAD » : l'espace (normal ou insécable) sépare les
# milliers, la virgule les décimales.
_MOTIF_PRIX = re.compile(r"(?P<entier>\d[\d\s\xa0 ]*)(?:,(?P<decimales>\d{1,2}))?\s*CAD")
_MOTIF_ESCALES = re.compile(r"(\d+)\s+escale", re.IGNORECASE)
_MOTIF_SANS_ESCALE = re.compile(r"sans\s+escale", re.IGNORECASE)
_MOTIF_DUREE = re.compile(r"(\d+)\s*h(?:\s*(\d+)\s*m)?")
_MOTIF_RANG = re.compile(r"cabin-fare-(\d+)-")


def _prix_en_cad(texte: str) -> int | None:
    """Lit un montant suivi de « CAD » et le rend en dollars entiers, arrondi au plus proche.

    S'ancre sur le texte masqué du site (« Total général 540,71CAD ») plutôt que sur le montant
    affiché (« 540,71 $ CA ») : celui-ci sépare ses milliers par une espace fine insécable et
    accole une mention de devise, deux détails de présentation susceptibles de bouger, là où la
    forme masquée est produite pour les lecteurs d'écran et reste stable.

    Rend None si rien d'exploitable n'est trouvé : un prix mal lu doit faire disparaître l'offre,
    jamais être deviné.
    """
    if not texte:
        return None
    correspondance = _MOTIF_PRIX.search(texte)
    if not correspondance:
        return None
    partie_entiere = re.sub(r"\D", "", correspondance.group("entier"))
    if not partie_entiere:
        return None
    decimales = correspondance.group("decimales")
    valeur = float(f"{partie_entiere}.{decimales}") if decimales else float(partie_entiere)
    arrondi = round(valeur)
    return arrondi if arrondi > 0 else None


def _escales(texte: str) -> int | None:
    """« Sans escale » -> 0, « 1 escale » -> 1, « 2 escales » -> 2. None si illisible.

    L'ordre compte : « Sans escale » ne contient aucun chiffre, mais le texte complet d'un bloc de
    vol en contient beaucoup (heures, durées), d'où la recherche du motif « N escale » plutôt
    qu'un chiffre isolé.
    """
    if not texte:
        return None
    if _MOTIF_SANS_ESCALE.search(texte):
        return 0
    correspondance = _MOTIF_ESCALES.search(texte)
    if not correspondance:
        return None
    return int(correspondance.group(1))


def _duree_en_minutes(texte: str) -> int | None:
    """« 5h » -> 300, « 13h 30m » -> 810. None si rien n'a pu être lu, jamais 0 par défaut.

    La partie « heures » est exigée, les minutes sont facultatives : un motif dont tous les groupes
    seraient facultatifs se satisferait de la chaîne vide et rendrait une durée pour n'importe quel
    texte.
    """
    if not texte:
        return None
    correspondance = _MOTIF_DUREE.search(texte)
    if not correspondance:
        return None
    heures = int(correspondance.group(1))
    minutes = int(correspondance.group(2)) if correspondance.group(2) else 0
    total = heures * 60 + minutes
    return total if total > 0 else None


def _compagnie(blocs: list[Tag]) -> str:
    """Nom de la compagnie, en tenant compte des trajets confiés à Air Canada Rouge.

    Le récapitulatif signale ces trajets par une phrase (« Comprend les trajets exploités par Air
    Canada Rouge. »). Ce nom entre dans l'empreinte d'une offre : le distinguer évite qu'un vol
    Rouge et un vol Air Canada principal, au même prix et au même nombre d'escales, se confondent
    en une seule offre et fassent taire l'alerte de l'un des deux.
    """
    texte = " ".join(bloc.get_text(" ", strip=True) for bloc in blocs)
    return "Air Canada Rouge" if "Air Canada Rouge" in texte else "Air Canada"


def _villes_correspondent(soup: BeautifulSoup, origine: str, destination: str) -> bool:
    """Vérifie que le récapitulatif décrit bien l'aller puis le retour demandés.

    Garde contre un parcours qui aurait dérivé (mauvais aéroport retenu dans l'autocomplétion) :
    sans elle, un prix parfaitement lisible mais portant sur un autre trajet entrerait dans
    l'historique et fausserait durablement la médiane.
    """
    paires = soup.select(_SEL_VILLES)
    if len(paires) != 2:
        return False
    aller = paires[0].get_text(" ", strip=True).upper()
    retour = paires[1].get_text(" ", strip=True).upper()
    return (
        origine.upper() in aller
        and destination.upper() in aller
        and destination.upper() in retour
        and origine.upper() in retour
    )


def _lien(query: SearchQuery) -> str:
    """Lien de recherche, reconstruit depuis la requête.

    Air Canada n'expose pas d'URL adressable par offre : la page de résultats vit dans une session
    (une navigation directe vers `/availability/rt/outbound` renvoie sur l'accueil, vérifié à la
    reconnaissance). Ce lien rouvre donc le site sur la page d'accueil, à charge pour la personne
    qui le suit de relancer la recherche — mieux vaut un lien honnête qu'une URL fabriquée qui
    mènerait à une page vide.
    """
    return URL_ACCUEIL


def parse_review_trip(html: str, query: SearchQuery) -> list[FlightOffer]:
    """Traduit le récapitulatif `review-trip` en une offre aller-retour.

    Fonction pure (ni réseau ni horloge), donc testable hors ligne sur une capture réelle.

    Cette page est la seule du parcours à porter un vrai total : les pages de résultats affichent
    des tarifs « par personne, **dans chaque sens** », soit environ la moitié du prix à payer. Lire
    un de ces demi-prix reviendrait à annoncer une aubaine qui n'existe pas.

    Vérifié à la reconnaissance : 319 $ (aller) + 222 $ (retour) relevés sur les pages de
    résultats, pour un total facturé de 540,71 $ — la somme des tarifs affichés retombe bien sur
    le total, à l'arrondi près, mais chacun pris isolément vaut la moitié du prix réel.
    """
    soup = BeautifulSoup(html, "html.parser")

    noeud_total = soup.select_one(_SEL_TOTAL)
    if noeud_total is None:
        return []
    texte_total = noeud_total.get_text(" ", strip=True)
    prix = _prix_en_cad(texte_total)
    if prix is None:
        return []

    if not _villes_correspondent(soup, query.origin, query.destination):
        return []

    blocs = soup.select(_SEL_BLOC_VOL)
    if len(blocs) != 2:
        return []

    escales = [_escales(bloc.get_text(" ", strip=True)) for bloc in blocs]
    if any(e is None for e in escales):
        return []

    durees = [_duree_en_minutes(bloc.get_text(" ", strip=True)) for bloc in blocs]
    duree_totale = sum(durees) if all(d is not None for d in durees) else None

    return [
        FlightOffer(
            provider=NOM,
            origin=query.origin,
            destination=query.destination,
            depart_date=query.depart_date,
            return_date=query.return_date,
            price_cad=prix,
            price_original=float(prix),
            currency_original=DEVISE,
            airline=_compagnie(blocs),
            stops=sum(escales),
            duration_minutes=duree_totale,
            deep_link=_lien(query),
            raw={"price_text": texte_total, "stops_per_leg": escales},
        )
    ]


def _valider_portee(query: SearchQuery) -> None:
    """Rejette avant tout accès réseau les requêtes hors du parcours vérifié.

    Seul l'aller-retour à un passager a été piloté de bout en bout. Toute autre combinaison lève
    plutôt que d'ouvrir un navigateur pour un parcours jamais observé.
    """
    if query.trip_type is not TripType.ROUND_TRIP or query.return_date is None:
        raise ProviderError(
            f"{NOM} : seul l'aller-retour est piloté pour l'instant (trip_type={query.trip_type})"
        )
    if query.passengers != 1:
        raise ProviderError(
            f"{NOM} : seul un passager est piloté pour l'instant (passengers={query.passengers})"
        )


def _accepter_temoins(page: Page) -> None:
    try:
        bouton = page.locator("#onetrust-accept-btn-handler").first
        if bouton.is_visible(timeout=3000):
            bouton.click(force=True)
            page.wait_for_timeout(1200)
    except Exception:  # noqa: BLE001 — bandeau absent ou déjà fermé
        pass


def _cliquer(page: Page, cible: Locator) -> None:
    """Clique en ramenant d'abord l'élément dans la fenêtre.

    Les pages de résultats sont longues et leurs cellules restent parfois annoncées « hors de la
    fenêtre » par Playwright après défilement, ce qui fait expirer un clic ordinaire ; le clic
    déclenché depuis la page elle-même aboutit alors.
    """
    cible.scroll_into_view_if_needed()
    page.wait_for_timeout(700)
    try:
        cible.click(timeout=8000)
    except Exception:  # noqa: BLE001 — repli sur un clic déclenché côté page
        page.evaluate("(el) => el.click()", cible.element_handle())


def _remplir_aeroport(page: Page, conteneur: str, champ: str, suggestion: str, code: str) -> None:
    """Saisit `code` dans un champ d'aéroport et retient la première suggestion.

    Le champ arrive prérempli d'une valeur par défaut : sans effacement explicite, la frappe s'y
    concatène et aucune suggestion ne correspond (relevé dès le premier essai, en août).
    """
    page.click(conteneur)
    page.wait_for_timeout(700)
    page.click(champ)
    page.keyboard.press("Control+a")
    page.keyboard.press("Delete")
    page.type(champ, code, delay=140)
    try:
        page.wait_for_selector(suggestion, timeout=15_000)
    except Exception as erreur:  # noqa: BLE001 — traduit vers l'exception du domaine
        raise ProviderError(f"{NOM} : aucune suggestion d'aéroport pour {code!r}") from erreur
    page.wait_for_timeout(500)
    page.click(suggestion)
    page.wait_for_timeout(700)


def _choisir_dates(page: Page, depart: date, retour: date) -> None:
    """Ouvre le calendrier et y clique les deux dates, puis confirme la plage.

    Sans le clic de confirmation, le panneau du calendrier reste ouvert et intercepte le clic sur
    le bouton de recherche.
    """
    page.click("#bkmg-desktop_travelDates-formfield-1")
    page.wait_for_timeout(1200)
    for cible in (depart, retour):
        selecteur = f"#bkmg-desktop_travelDates-date-{cible.isoformat()}"
        trouve = False
        for _ in range(14):
            cellule = page.locator(selecteur)
            if cellule.count() and cellule.first.is_visible():
                trouve = True
                break
            page.click("#bkmg-desktop_travelDates_nextMonth")
            page.wait_for_timeout(500)
        if not trouve:
            raise ProviderError(f"{NOM} : date {cible} introuvable au calendrier")
        page.click(selecteur)
        page.wait_for_timeout(600)
    page.click("#bkmg-desktop_travelDates_1_confirmDates")
    page.wait_for_timeout(800)


def _rang_cellule(classe: str) -> int | None:
    """Index du vol porté par la classe d'une cellule de cabine (`cabin-fare-3-Y` -> 3)."""
    correspondance = _MOTIF_RANG.search(classe or "")
    return int(correspondance.group(1)) if correspondance else None


def _cellule_la_moins_chere(page: Page, max_stops: int | None) -> Locator:
    """La cellule Économique au tarif le plus bas, parmi les vols respectant `max_stops`.

    Le filtre sur les escales n'est pas cosmétique : le vol le moins cher d'une page de retour peut
    comporter une escale de treize heures (observé sur YUL->CUN). Retenir un tel vol quand la
    requête demande un direct rendrait le prix incomparable à celui des autres sources, et
    l'historique d'une route mêlerait alors deux réalités différentes.
    """
    page.wait_for_selector("button.button-cell-container", timeout=45_000)
    page.wait_for_timeout(2500)

    cellules = page.locator(_SEL_CELLULE_ECONOMIQUE)
    blocs = page.locator(_SEL_BLOC_VOL)
    nombre_blocs = blocs.count()

    candidats: list[tuple[int, int]] = []
    for i in range(cellules.count()):
        cellule = cellules.nth(i)
        try:
            prix = _prix_en_cad(cellule.inner_text())
        except Exception:  # noqa: BLE001 — cellule disparue du DOM entre-temps
            continue
        if prix is None:
            continue
        if max_stops is not None:
            rang = _rang_cellule(cellule.get_attribute("class") or "")
            if rang is None or rang >= nombre_blocs:
                continue
            escales = _escales(blocs.nth(rang).inner_text())
            if escales is None or escales > max_stops:
                continue
        candidats.append((prix, i))

    if not candidats:
        raise ProviderError(f"{NOM} : aucun tarif Économique exploitable sur la page de résultats")
    candidats.sort()
    return cellules.nth(candidats[0][1])


def _franchir_modale(page: Page) -> None:
    """Conserve le tarif choisi quand la modale de vente incitative s'ouvre.

    Elle exige d'abord d'accepter les restrictions du tarif le plus bas (une case à cocher), sans
    quoi son bouton de confirmation reste inopérant.
    """
    modale = page.locator(_SEL_MODALE)
    if modale.count() == 0:
        return

    case = page.locator(_SEL_MODALE_CASE).first
    if case.count() and case.is_visible():
        try:
            case.check(timeout=5000)
        except Exception:  # noqa: BLE001 — repli sur un clic déclenché côté page
            page.evaluate("(el) => el.click()", case.element_handle())
        page.wait_for_timeout(1000)

    conserver = page.locator(_SEL_MODALE_CONSERVER).first
    if conserver.count() == 0 or not conserver.is_visible():
        raise ProviderError(f"{NOM} : modale de tarif ouverte sans bouton pour conserver le choix")
    _cliquer(page, conserver)
    page.wait_for_timeout(9000)


def _selectionner_vol(page: Page, etape: str, max_stops: int | None) -> None:
    """Choisit le vol le moins cher de l'étape, son tarif le plus bas, puis franchit la modale."""
    _cliquer(page, _cellule_la_moins_chere(page, max_stops))
    page.wait_for_timeout(3500)

    tarifs = page.locator(_SEL_BOUTON_TARIF)
    visibles = [i for i in range(tarifs.count()) if tarifs.nth(i).is_visible()]
    if not visibles:
        raise ProviderError(f"{NOM} : aucun tarif proposé à l'étape {etape!r}")
    # Le panneau range ses tarifs du moins cher au plus cher : le premier bouton visible est donc
    # le tarif le plus bas de la cabine, celui dont le prix vient d'être relevé sur la cellule.
    _cliquer(page, tarifs.nth(visibles[0]))
    page.wait_for_timeout(3000)

    _franchir_modale(page)


def _verifier_etape_recapitulatif(url: str) -> None:
    """Garde avant lecture du total : vérifie que le parcours a bien atteint le récapitulatif.

    Fonction pure pour rester testable sans navigateur. Sans elle, une page de résultats restée
    affichée faute de tarif sélectionnable serait fouillée à la recherche d'un total qui ne s'y
    trouve pas — et l'échec serait attribué à une dérive de sélecteur plutôt qu'à un parcours
    interrompu.
    """
    if _ETAPE_RECAPITULATIF not in url:
        raise ProviderError(f"{NOM} : récapitulatif non atteint (URL = {url})")


def _piloter_recherche(page: Page, query: SearchQuery) -> None:
    """Callback `interact` : remplit le formulaire, choisit l'aller puis le retour, et s'arrête
    sur le récapitulatif — la seule page du parcours qui porte le total aller-retour."""
    page.wait_for_timeout(8000)
    _accepter_temoins(page)

    _remplir_aeroport(
        page,
        "#flightsOriginLocationbkmgLocationContainer",
        "#flightsOriginLocation",
        "#flightsOriginLocationSearchResult0",
        query.origin.upper(),
    )
    _remplir_aeroport(
        page,
        "#flightsOriginDestinationbkmgLocationContainer",
        "#flightsOriginDestination",
        "#flightsOriginDestinationSearchResult0",
        query.destination.upper(),
    )
    if query.return_date is None:  # pragma: no cover — _valider_portee l'a déjà exclu
        raise ProviderError(f"{NOM} : date de retour absente")
    _choisir_dates(page, query.depart_date, query.return_date)

    page.click("#bkmg-desktop_findButton")
    page.wait_for_timeout(22_000)
    _accepter_temoins(page)

    _selectionner_vol(page, "aller", query.max_stops)
    _selectionner_vol(page, "retour", query.max_stops)

    _verifier_etape_recapitulatif(page.url)
    page.wait_for_selector(_SEL_TOTAL, timeout=20_000)


class AirCanadaProvider:
    """Source Air Canada, pilotée au formulaire jusqu'au récapitulatif de réservation.

    Le site n'expose ni API ni page de résultats adressable par URL, et refuse le parcours à un
    navigateur sans fenêtre — d'où `headless=False`, qui suppose le serveur X démarré par
    l'entrée du conteneur.
    """

    name = NOM

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or Settings()

    def _fetch(self, query: SearchQuery) -> str:
        """Isolé pour les tests : le simuler évite de lancer un navigateur.

        La portée est validée ici, avant tout accès réseau : une requête hors périmètre ne doit
        pas ouvrir de navigateur pour rien.
        """
        _valider_portee(query)

        def interagir(page: Any) -> None:
            _piloter_recherche(page, query)

        return fetch_html(
            URL_ACCUEIL,
            self._settings,
            provider_name=NOM,
            interact=interagir,
            timeout_ms=90_000,
            headless=False,
        )

    def search(self, query: SearchQuery) -> list[FlightOffer]:
        try:
            html = self._fetch(query)
        except Exception as exc:  # noqa: BLE001 — traduire toute panne est le contrat de l'interface
            raise ProviderError(f"{NOM} : échec de la requête ({exc})") from exc

        offres = parse_review_trip(html, query)
        if not offres:
            raise EmptyResultError(
                f"{NOM} : aucune offre exploitable pour "
                f"{query.origin}->{query.destination} le {query.depart_date}"
            )
        return offres
