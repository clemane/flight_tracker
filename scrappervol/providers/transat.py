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

NOM = "transat"

URL_RECHERCHE = "https://www.airtransat.com/fr-CA?search=flight"

# Air Transat n'expose aucune API ni page de résultats adressable par URL (vérifié à la tâche 11 :
# l'URL supposée par le plan initial renvoie 404) — il faut piloter le formulaire à autocomplétion.
# La page de résultats est elle-même en JS : `"currency":"CAD"` apparaît dans sa configuration pour
# le marché fr-CA/CA (relevé dans tests/fixtures/transat_summary_yul_cun.html), d'où cette devise
# fixe, comme google_flights.py fixe la sienne sur la devise demandée plutôt que sur une valeur du
# flux.
DEVISE = "CAD"

_MOTIF_DUREE = re.compile(r"(?:(\d+)\s*j)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*min)?", re.IGNORECASE)
_MOTIF_PRIX = re.compile(r"(?P<entier>[\d][\d\s\xa0]*)(?:,(?P<decimales>\d{1,2}))?")

# Sélecteurs du parcours de sélection de tarif, vérifiés à la reconnaissance de la tâche 21.
_SELECTEUR_FARE_BTN = ".co-shopResult-flightResult-fareClassesBtn .fare-btn"
_SELECTEUR_SOUS_TARIF = ".fare-panel .container"
_SELECTEUR_MODALE_UPSELL = "#fareUpsellModal"
_SELECTEUR_POURSUIVRE = "#fareUpsellModal button.co-btn-level4"
_SELECTEUR_TOTAL = ".flight-container-total .price"


def _prix_en_cad(texte: str) -> int | None:
    """Lit un prix affiché ("297$", "1 229$", "1\xa0229$", "533,41$") en entier de dollars
    canadiens, arrondi au dollar le plus proche s'il porte des décimales.

    Convention canadienne-française observée sur le site : l'espace (normal ou insécable) sépare
    les milliers, la virgule sépare les décimales — jamais l'inverse. Sans cette distinction,
    "533,41$" (lu sur `/summary`) serait pris pour 53 341 $ plutôt que 533 $, une erreur d'un
    facteur 100 sur le seul total aller-retour de la page.

    Rend None si le texte ne contient rien d'exploitable : un prix mal lu doit disparaître de la
    liste, jamais être deviné ou remplacé par une valeur par défaut.
    """
    if not texte:
        return None
    correspondance = _MOTIF_PRIX.search(texte)
    if not correspondance:
        return None
    partie_entiere = re.sub(r"[^\d]", "", correspondance.group("entier"))
    if not partie_entiere:
        return None
    decimales = correspondance.group("decimales")
    valeur = float(f"{partie_entiere}.{decimales}") if decimales else float(partie_entiere)
    arrondi = round(valeur)
    return arrondi if arrondi > 0 else None


def _duree_en_minutes(texte: str) -> int | None:
    """ "4h 50min" -> 290. Rend None si rien n'a pu être lu, jamais 0 par défaut."""
    if not texte:
        return None
    correspondance = _MOTIF_DUREE.search(texte)
    if not correspondance or not any(correspondance.groups()):
        return None
    jours, heures, minutes = (int(g) if g else 0 for g in correspondance.groups())
    total = jours * 24 * 60 + heures * 60 + minutes
    return total if total > 0 else None


def _compagnie(carte: Tag) -> str | None:
    bouton = carte.select_one(".common-airline .panel-airlineType-btn")
    if bouton is None:
        return None
    etiquette = (bouton.get("aria-label") or "").strip()
    prefixe = "Exploité par "
    if etiquette.startswith(prefixe):
        etiquette = etiquette[len(prefixe) :].strip()
    return etiquette or None


def _escales(carte: Tag) -> int | None:
    """Nombre d'escales, déduit du nombre de numéros de vol affichés dans la carte.

    Seul le cas d'un vol direct (un unique `.panel-number`) a été observé dans la fixture réelle :
    le balisage d'un vol avec escale n'est pas connu. Une carte qui n'affiche pas exactement un
    numéro de vol est donc écartée plutôt que de deviner un nombre d'escales.
    """
    numeros = carte.select(".panel-type .panel-number")
    if len(numeros) != 1:
        return None
    return 0


def _aeroports_correspondent(carte: Tag, origine: str, destination: str) -> bool:
    villes = carte.select(".common-location .cityAirport")
    if len(villes) != 2:
        return False
    texte_origine = villes[0].get_text(strip=True).upper()
    texte_destination = villes[1].get_text(strip=True).upper()
    return origine.upper() in texte_origine and destination.upper() in texte_destination


def _lien(query: SearchQuery) -> str:
    """Lien de recherche Air Transat, reconstruit à partir de la requête.

    La page ne fournit pas d'URL par offre individuelle : ce lien reproduit la forme observée dans
    la barre d'adresse une fois le formulaire soumis (relevé lors de la reconnaissance de la
    tâche 11), pas une URL extraite de la réponse.
    """
    retour = query.return_date.isoformat() if query.return_date else ""
    return (
        "https://www.airtransat.com/fr-CA/flight-search-result/departure"
        f"?flightType=RT&search=flight&gateway=AIRPORT_{query.origin}-AIRPORT_{query.destination}"
        f"&date={query.depart_date.isoformat()}_{retour}&pax=1-0-0-0-0"
    )


def _prix_total_affiche(soup: BeautifulSoup) -> tuple[int, str] | None:
    """Le total lu dans `.flight-container-total .price` ("533,41$"), avec son texte source.

    Rend None si l'élément est absent ou si son contenu n'est pas un prix exploitable : c'est
    l'unique total de la page `/summary`, et une page sans total lisible ne doit produire aucune
    offre plutôt qu'un prix deviné.
    """
    noeud = soup.select_one(_SELECTEUR_TOTAL)
    if noeud is None:
        return None
    texte = noeud.get_text(strip=True)
    prix = _prix_en_cad(texte)
    if prix is None:
        return None
    return prix, texte


def _vols_aller_retour(soup: BeautifulSoup) -> tuple[Tag, Tag] | None:
    """Les blocs « Vol aller » puis « Vol retour » de `/summary`, dans cet ordre d'affichage.

    Rend None si la page n'en présente pas exactement deux : un itinéraire à plus de deux segments
    (multi-destinations) n'a jamais été observé sur ce balisage — mieux vaut écarter l'offre que
    deviner lequel des blocs est l'aller.
    """
    cartes = soup.select(".flight-card")
    if len(cartes) != 2:
        return None
    return cartes[0], cartes[1]


def _duree_carte(carte: Tag) -> int | None:
    noeud = carte.select_one(".common-time .common-duration .flightTime")
    return _duree_en_minutes(noeud.get_text(strip=True)) if noeud else None


def parse_summary(html: str, query: SearchQuery) -> list[FlightOffer]:
    """Traduit la page récapitulative `/summary` d'Air Transat en une offre aller-retour.

    Fonction pure : ni réseau ni horloge, ce qui permet de la tester hors ligne sur une capture
    réelle (tests/fixtures/transat_summary_yul_cun.html). Remplace parse_results, qui lisait l'étape
    « departure » et n'y trouvait qu'un prix d'aller seul par classe tarifaire : cette page-ci
    affiche le total du couple aller+retour déjà choisi par `_piloter_recherche`, donc **une seule**
    offre en sort, jamais une par classe tarifaire.

    Les dates viennent de la requête, pas de la page : le pilotage qui a produit ce HTML a déjà
    vérifié la sélection de chaque date au calendrier (`_choisir_date_calendrier` lève ProviderError
    sinon), et l'URL atteinte les porte elle-même dans ses paramètres `outbound=`/`inbound=` — les
    relire dans un texte affiché n'apporterait pas de garantie supplémentaire.

    Toute donnée manquante ou illisible fait écarter l'offre entière, jamais une valeur par défaut :
    une offre inventée devient une fausse alerte, et une fausse alerte coûte plus cher qu'une offre
    manquée.
    """
    soup = BeautifulSoup(html, "html.parser")

    total = _prix_total_affiche(soup)
    if total is None:
        return []
    prix, texte_prix = total

    vols = _vols_aller_retour(soup)
    if vols is None:
        return []
    carte_aller, carte_retour = vols

    if not _aeroports_correspondent(carte_aller, query.origin, query.destination):
        return []
    if not _aeroports_correspondent(carte_retour, query.destination, query.origin):
        return []

    escale_aller = _escales(carte_aller)
    escale_retour = _escales(carte_retour)
    if escale_aller is None or escale_retour is None:
        return []

    compagnie_aller = _compagnie(carte_aller)
    compagnie_retour = _compagnie(carte_retour)
    if not compagnie_aller or not compagnie_retour:
        return []
    compagnie = (
        compagnie_aller
        if compagnie_aller == compagnie_retour
        else f"{compagnie_aller} / {compagnie_retour}"
    )

    duree_aller = _duree_carte(carte_aller)
    duree_retour = _duree_carte(carte_retour)
    duree_totale = (
        duree_aller + duree_retour if duree_aller is not None and duree_retour is not None else None
    )

    numeros_vol = [v.get_text(strip=True) for v in soup.select(".panel-number")]

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
            airline=compagnie,
            stops=escale_aller + escale_retour,
            duration_minutes=duree_totale,
            deep_link=_lien(query),
            raw={
                "price_text": texte_prix,
                "flight_numbers": numeros_vol,
            },
        )
    ]


def _remplir_aeroport(page: Page, id_champ: str, code: str) -> None:
    """Sélectionne `code` dans le champ d'autocomplétion `id_champ`.

    Logique éprouvée lors de la reconnaissance manuelle de la tâche 11 : taper immédiatement après
    avoir vidé le champ perd parfois la première frappe dans le re-rendu de la liste par défaut,
    d'où l'attente avant de taper et la vérification-avec-reprise après.
    """
    id_liste = f"{id_champ.split('-input')[0]}-list"
    champ = page.locator(f"#{id_champ}")
    champ.click()
    champ.press("Control+a")
    champ.press("Delete")
    page.wait_for_timeout(400)
    champ.press_sequentially(code, delay=150)
    for _ in range(3):
        if code in champ.input_value().upper():
            break
        champ.press("Control+a")
        champ.press("Delete")
        page.wait_for_timeout(400)
        champ.press_sequentially(code, delay=150)

    page.wait_for_selector(f"#{id_liste}", timeout=8000)
    options = page.locator(f"#{id_liste} li")
    cible = None
    for _ in range(10):
        page.wait_for_timeout(400)
        for i in range(options.count()):
            if code in options.nth(i).inner_text().upper():
                cible = options.nth(i)
                break
        if cible is not None:
            break
    if cible is None:
        raise ProviderError(f"{NOM} : aucune suggestion d'aéroport pour {code!r}")

    cible.click()
    page.wait_for_timeout(300)
    if code not in champ.input_value().upper():
        raise ProviderError(f"{NOM} : le champ {id_champ!r} ne porte pas {code!r} après sélection")


def _choisir_date_calendrier(page: Page, id_calendrier: str, cible: date) -> bool:
    """Clique sur la cellule `data-id` correspondant à `cible`, en avançant le calendrier si besoin.

    Rend True si la cellule a été trouvée et cliquée, False sinon.
    """
    data_id = f"{cible.year}-{cible.month}-{cible.day}"
    for _ in range(8):
        cellule = page.locator(f"#{id_calendrier} td.vdpCell.selectable[data-id='{data_id}']")
        if cellule.count() > 0:
            cellule.first.click()
            return True
        suivant = page.locator(f"#{id_calendrier} button.vdpArrowNext")
        if suivant.count() == 0 or not suivant.first.is_visible():
            return False
        suivant.first.click()
        page.wait_for_timeout(300)
    return False


def _fare_btn_moins_cher(page: Page) -> Locator | None:
    """Le bouton de catégorie tarifaire (Économie, Club, ...) affichant le prix le plus bas parmi
    ceux visibles, ou None si aucun n'est lisible.

    `.fare-btn` ne fait que déplier le panneau d'une catégorie, il ne sélectionne rien (piège relevé
    à la tâche 11 : cliquer dessus ne fait pas avancer l'étape) — mais son prix affiché est déjà le
    minimum de sa catégorie, donc comparer les `.fare-btn` entre eux revient à comparer les minimums
    de chaque catégorie : le prix global le plus bas de la page se trouve forcément dans celle ainsi
    désignée.
    """
    boutons = page.locator(_SELECTEUR_FARE_BTN)
    candidats: list[tuple[int, Locator]] = []
    for i in range(boutons.count()):
        bouton = boutons.nth(i)
        if not bouton.is_visible():
            continue
        noeud_prix = bouton.locator(".expandFare-price")
        if noeud_prix.count() == 0:
            continue
        prix = _prix_en_cad(noeud_prix.first.inner_text())
        if prix is None:
            continue
        candidats.append((prix, bouton))
    if not candidats:
        return None
    candidats.sort(key=lambda c: c[0])
    return candidats[0][1]


def _sous_tarif_moins_cher(page: Page) -> Locator | None:
    """Le bouton « Sélectionner » de la sous-classe la moins chère parmi celles visibles après
    dépliage d'une catégorie, ou None si aucune n'est lisible.

    Piège relevé à la reconnaissance : déplier une catégorie fait apparaître au DOM les
    sous-classes des DEUX catégories (0 `button.co-btn-level1[aria-label='Sélectionner']` avant, 6
    après pour 2 catégories × 3 sous-classes), mais seule celle qu'on vient de déplier est
    réellement visible — le panneau de l'autre catégorie porte l'attribut `hidden`. D'où le filtre
    `is_visible()`.
    """
    conteneurs = page.locator(_SELECTEUR_SOUS_TARIF)
    candidats: list[tuple[int, Locator]] = []
    for i in range(conteneurs.count()):
        conteneur = conteneurs.nth(i)
        if not conteneur.is_visible():
            continue
        noeud_prix = conteneur.locator(".price")
        bouton = conteneur.locator("button.co-btn-level1[aria-label='Sélectionner']")
        if noeud_prix.count() == 0 or bouton.count() == 0:
            continue
        prix = _prix_en_cad(noeud_prix.first.inner_text())
        if prix is None:
            continue
        candidats.append((prix, bouton.first))
    if not candidats:
        return None
    candidats.sort(key=lambda c: c[0])
    return candidats[0][1]


def _franchir_modale_upsell(page: Page) -> None:
    """Ferme la modale d'upsell en conservant le tarif déjà choisi, si elle s'ouvre.

    `#fareUpsellModal` intercepte tous les clics suivants tant qu'elle reste ouverte (sinon, timeout
    de 30 s sur le clic suivant). `button.co-btn-level4` (« Poursuivre avec <tarif> ») conserve le
    tarif le moins cher déjà sélectionné ; `button.co-btn-whiterabbit` (« Sélectionner <tarif
    supérieur> ») est la montée en gamme — ne jamais la cliquer, elle relèverait le prix relevé.
    """
    modale = page.locator(_SELECTEUR_MODALE_UPSELL)
    if modale.count() == 0 or not modale.first.is_visible():
        return
    bouton = page.locator(_SELECTEUR_POURSUIVRE)
    if bouton.count() == 0 or not bouton.first.is_visible():
        raise ProviderError(
            f"{NOM} : modale d'upsell ouverte mais bouton de confirmation introuvable"
        )
    bouton.first.click()
    page.wait_for_timeout(2500)
    try:
        page.wait_for_load_state("networkidle", timeout=20_000)
    except Exception:  # noqa: BLE001 — la page suivante peut rester "occupée" (widgets tiers)
        logger.debug("%s : pas de networkidle sous 20s après franchissement de la modale", NOM)


def _selectionner_tarif(page: Page, etape: str) -> None:
    """Sélectionne le tarif le moins cher visible à l'étape `etape` (« aller » ou « retour ») :
    déplie la catégorie la moins chère, choisit la sous-classe la moins chère qu'elle révèle, puis
    franchit la modale d'upsell si elle s'ouvre.
    """
    categorie = _fare_btn_moins_cher(page)
    if categorie is None:
        raise ProviderError(f"{NOM} : aucun tarif exploitable à l'étape {etape!r}")
    categorie.click()
    page.wait_for_timeout(2000)

    bouton = _sous_tarif_moins_cher(page)
    if bouton is None:
        raise ProviderError(
            f"{NOM} : aucun sous-tarif exploitable après dépliage à l'étape {etape!r}"
        )
    bouton.click()
    page.wait_for_timeout(3000)
    try:
        page.wait_for_load_state("networkidle", timeout=20_000)
    except Exception:  # noqa: BLE001 — la page suivante peut rester "occupée" (widgets tiers)
        logger.debug("%s : pas de networkidle sous 20s après sélection à l'étape %r", NOM, etape)

    _franchir_modale_upsell(page)


def _verifier_etape_sommaire(url: str) -> None:
    """Vérifie que le parcours a bien atteint `/summary` avant d'y lire un total.

    Fonction pure (une simple chaîne en entrée) pour rester testable sans navigateur : c'est la
    garde qui empêche de chercher un total sur une page qui n'est pas la bonne — par exemple si le
    parcours est resté bloqué sur `/return` faute de tarif sélectionnable.
    """
    if "/summary" not in url:
        raise ProviderError(f"{NOM} : étape /summary non atteinte (URL = {url})")


def _valider_portee(query: SearchQuery) -> None:
    """Rejette avant tout accès réseau les requêtes hors de la portée pilotée et vérifiée.

    Portée assumée à la tâche 11 : seul l'aller-retour à un passager a été piloté et vérifié — deux
    passages réels, à cinq minutes d'intervalle, ont chacun produit une page avec trois prix CAD
    distincts. Toute autre combinaison lève une ProviderError plutôt que de tenter, sans navigateur
    ouvert, un parcours jamais observé.
    """
    if query.trip_type is not TripType.ROUND_TRIP or query.return_date is None:
        raise ProviderError(
            f"{NOM} : seul l'aller-retour est piloté pour l'instant (trip_type={query.trip_type})"
        )
    if query.passengers != 1:
        raise ProviderError(
            f"{NOM} : seul un passager est piloté pour l'instant (passengers={query.passengers})"
        )


def _piloter_recherche(page: Page, query: SearchQuery) -> None:
    """Callback `interact` pour `fetch_html` : pilote le formulaire, puis le choix des tarifs aller
    et retour, jusqu'à la page récapitulative `/summary`."""
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:  # noqa: BLE001 — une lenteur réseau au chargement initial n'est pas fatale
        logger.debug("%s : pas de networkidle sous 15s après le chargement initial", NOM)

    try:
        bouton_cookies = page.locator("#onetrust-accept-btn-handler")
        if bouton_cookies.is_visible(timeout=3000):
            bouton_cookies.click()
    except Exception:  # noqa: BLE001 — bandeau absent ou déjà fermé
        pass
    page.wait_for_timeout(500)

    _remplir_aeroport(page, "departureOriginDropdown-input", query.origin.upper())
    _remplir_aeroport(page, "departureDestinationDropdown-input", query.destination.upper())

    depart_input = page.locator("#datePickerDeparture")
    depart_input.click()
    page.wait_for_timeout(600)
    ok_depart = _choisir_date_calendrier(page, "datePickerDeparture-calendar", query.depart_date)
    page.wait_for_timeout(500)
    if not ok_depart:
        raise ProviderError(f"{NOM} : date de départ {query.depart_date} introuvable au calendrier")

    # Le retour se choisit parfois sur le même calendrier bi-mensuel encore ouvert, parfois sur un
    # second calendrier propre à #datePickerReturn — observé selon la distance entre les deux dates.
    if page.locator("#datePickerDeparture-calendar").count() > 0:
        ok_retour = _choisir_date_calendrier(
            page, "datePickerDeparture-calendar", query.return_date
        )
    else:
        ok_retour = False
    if not ok_retour:
        retour_input = page.locator("#datePickerReturn")
        retour_input.click(timeout=3000)
        page.wait_for_timeout(600)
        ok_retour = _choisir_date_calendrier(page, "datePickerReturn-calendar", query.return_date)
    page.wait_for_timeout(500)
    if not ok_retour:
        raise ProviderError(f"{NOM} : date de retour {query.return_date} introuvable au calendrier")

    # #form button.stepSetContinue est le vrai bouton du moteur de vol. Piège relevé à la
    # reconnaissance : button.co-searchBtn est la recherche générique du site (dans le header),
    # présente en triple dans le DOM, qui mène à une page Google Custom Search vide.
    candidats = ["#form button.stepSetContinue", "#form button[type='submit']"]
    bouton = None
    for selecteur in candidats:
        groupe = page.locator(selecteur)
        for i in range(groupe.count()):
            if groupe.nth(i).is_visible():
                bouton = groupe.nth(i)
                break
        if bouton is not None:
            break
    if bouton is None:
        raise ProviderError(f"{NOM} : bouton de soumission du formulaire introuvable")

    bouton.click()
    try:
        page.wait_for_load_state("networkidle", timeout=30_000)
    except Exception:  # noqa: BLE001 — la page de résultats peut rester "occupée" (widgets tiers)
        logger.debug("%s : pas de networkidle sous 30s après soumission", NOM)
    page.wait_for_timeout(2000)

    # Le choix d'un tarif aller mène automatiquement à l'étape retour (aucun clic « continuer »
    # explicite n'est nécessaire, vérifié à la reconnaissance), puis le choix du tarif retour mène
    # de même à /summary.
    _selectionner_tarif(page, "aller")
    _selectionner_tarif(page, "retour")

    _verifier_etape_sommaire(page.url)
    page.wait_for_selector(_SELECTEUR_TOTAL, timeout=20_000)


class TransatProvider:
    """Source Air Transat, pilotée par formulaire à autocomplétion (pas d'API, pas de page de
    résultats adressable par URL)."""

    name = NOM

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or Settings()

    def _fetch(self, query: SearchQuery) -> str:
        """Isolé pour les tests : simuler cette méthode évite de lancer un navigateur.

        La validation de portée se fait ici, avant tout accès réseau : une requête hors périmètre
        (aller simple, plusieurs passagers) ne doit pas ouvrir de navigateur pour rien.
        """
        _valider_portee(query)

        def interagir(page: Any) -> None:
            _piloter_recherche(page, query)

        return fetch_html(
            URL_RECHERCHE,
            self._settings,
            provider_name=NOM,
            interact=interagir,
            timeout_ms=60_000,
        )

    def search(self, query: SearchQuery) -> list[FlightOffer]:
        try:
            html = self._fetch(query)
        except Exception as exc:  # noqa: BLE001 — traduire toute panne est le contrat de l'interface
            raise ProviderError(f"{NOM} : échec de la requête ({exc})") from exc

        offres = parse_summary(html, query)
        if not offres:
            raise EmptyResultError(
                f"{NOM} : aucune offre exploitable pour "
                f"{query.origin}->{query.destination} le {query.depart_date}"
            )
        return offres
