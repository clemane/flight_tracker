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
    from playwright.sync_api import Page

logger = logging.getLogger(__name__)

NOM = "transat"

URL_RECHERCHE = "https://www.airtransat.com/fr-CA?search=flight"

# Air Transat n'expose aucune API ni page de résultats adressable par URL (vérifié à la tâche 11 :
# l'URL supposée par le plan initial renvoie 404) — il faut piloter le formulaire à autocomplétion.
# La page de résultats est elle-même en JS : `"currency":"CAD"` apparaît dans sa configuration pour
# le marché fr-CA/CA (relevé dans tests/fixtures/transat_yul_cun.html), d'où cette devise fixe,
# comme google_flights.py fixe la sienne sur la devise demandée plutôt que sur une valeur du flux.
DEVISE = "CAD"

_MOIS_FR = {
    "janv": 1,
    "jan": 1,
    "fevr": 2,
    "févr": 2,
    "fev": 2,
    "mars": 3,
    "avr": 4,
    "mai": 5,
    "juin": 6,
    "juil": 7,
    "aout": 8,
    "août": 8,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
    "déc": 12,
}

_MOTIF_DATE_ETIQUETTE = re.compile(r"(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\.?\s+(\d{4})")
_MOTIF_DUREE = re.compile(r"(?:(\d+)\s*j)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*min)?", re.IGNORECASE)


def _prix_en_cad(texte: str) -> int | None:
    """Lit un prix affiché ("297$", "1 229$", "1\xa0229$") en entier de dollars canadiens.

    Rend None si le texte ne contient rien d'exploitable : un prix mal lu doit disparaître de la
    liste, jamais être deviné ou remplacé par une valeur par défaut.
    """
    if not texte:
        return None
    chiffres = re.sub(r"[^\d]", "", texte)
    if not chiffres:
        return None
    valeur = int(chiffres)
    return valeur if valeur > 0 else None


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


def _date_depuis_etiquette(etiquette: str) -> date | None:
    """Lit une date française comme "Lun. 9 nov. 2026" telle qu'affichée sur la page.

    Rend None si le format n'est pas reconnu : mieux vaut retomber sur la date demandée que
    d'enregistrer une date fabriquée à partir d'un texte mal compris.
    """
    if not etiquette:
        return None
    correspondance = _MOTIF_DATE_ETIQUETTE.search(etiquette)
    if not correspondance:
        return None
    jour, mois_texte, annee = correspondance.groups()
    mois = _MOIS_FR.get(mois_texte.strip(".").lower())
    if mois is None:
        return None
    try:
        return date(int(annee), mois, int(jour))
    except ValueError:
        return None


def _date_de_depart_affichee(soup: BeautifulSoup) -> date | None:
    """La date de départ réellement sélectionnée, lue dans le curseur de dates de la page.

    C'est le seul endroit de cette page qui confirme la date obtenue plutôt que la date demandée
    (voir le piège relevé à la tâche 10 : un champ recopié de la requête ne prouve rien).
    """
    bouton = soup.select_one('.date-slider-dateBtn[aria-current="date"]')
    if bouton is None:
        return None
    return _date_depuis_etiquette(bouton.get("aria-label", ""))


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


def parse_results(html: str, query: SearchQuery) -> list[FlightOffer]:
    """Traduit une page de résultats Air Transat en offres du domaine.

    Fonction pure : ni réseau ni horloge, ce qui permet de la tester hors ligne sur une capture
    réelle (tests/fixtures/transat_yul_cun.html). Chaque carte de vol porte plusieurs classes
    tarifaires (Économie, Club, ...), chacune avec son propre prix « à partir de » : on en fait une
    offre par classe, pas une seule offre par carte.

    Important : le prix lu ici est celui du vol **aller seul** (« à partir de »), pas un total
    aller-retour. Cette page est l'étape « departure » du parcours de réservation Air Transat,
    avant le choix du vol retour — contrairement à google_flights.py, qui rend un prix aller-retour
    déjà agrégé. Comparer les deux sources telles quelles reviendrait à comparer un prix par
    personne pour un aller à un prix pour un aller-retour complet.

    Toute carte ou tout prix illisible est écarté, jamais deviné : une offre inventée devient une
    fausse alerte, et une fausse alerte coûte plus cher qu'une offre manquée.
    """
    soup = BeautifulSoup(html, "html.parser")
    depart = _date_de_depart_affichee(soup) or query.depart_date

    offres: list[FlightOffer] = []
    for carte in soup.select(".co-shopResult-flightResult-card"):
        if not _aeroports_correspondent(carte, query.origin, query.destination):
            continue

        escales = _escales(carte)
        if escales is None:
            continue

        compagnie = _compagnie(carte)
        if not compagnie:
            continue

        noeud_duree = carte.select_one(".common-time .common-duration .flightTime")
        duree = _duree_en_minutes(noeud_duree.get_text(strip=True)) if noeud_duree else None

        numero_carte = carte.get("id", "")
        vols = [v.get_text(strip=True) for v in carte.select(".panel-type .panel-number")]

        for bouton_tarif in carte.select(".co-shopResult-flightResult-fareClassesBtn .fare-btn"):
            classes = bouton_tarif.get("class") or []
            palier = next((c for c in classes if c != "fare-btn"), None)
            noeud_prix = bouton_tarif.select_one(".expandFare-price")
            if noeud_prix is None:
                continue
            prix = _prix_en_cad(noeud_prix.get_text())
            if prix is None:
                continue

            offres.append(
                FlightOffer(
                    provider=NOM,
                    origin=query.origin,
                    destination=query.destination,
                    depart_date=depart,
                    return_date=query.return_date,
                    price_cad=prix,
                    price_original=float(prix),
                    currency_original=DEVISE,
                    airline=compagnie,
                    stops=escales,
                    duration_minutes=duree,
                    deep_link=_lien(query),
                    raw={
                        "card_id": numero_carte,
                        "fare_tier": palier,
                        "price_text": noeud_prix.get_text(strip=True),
                        "flight_numbers": vols,
                    },
                )
            )
    return offres


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
    """Callback `interact` pour `fetch_html` : pilote le formulaire jusqu'à la page de résultats."""
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

        offres = parse_results(html, query)
        if not offres:
            raise EmptyResultError(
                f"{NOM} : aucune offre exploitable pour "
                f"{query.origin}->{query.destination} le {query.depart_date}"
            )
        return offres
