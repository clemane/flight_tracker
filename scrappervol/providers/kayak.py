from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from scrappervol.config import Settings
from scrappervol.core.types import FlightOffer, SearchQuery, TripType
from scrappervol.providers.base import EmptyResultError, ProviderError
from scrappervol.providers.playwright_base import AGENT_UTILISATEUR, debug_path

if TYPE_CHECKING:
    from playwright.sync_api import Page

logger = logging.getLogger(__name__)

NOM = "kayak"

# Kayak n'est pas un transporteur mais un comparateur : un seul relevé ici rapporte les tarifs de
# vingt-quatre revendeurs — dont Expedia, FlightHub, Gotogate, Trip.com, eDreams et Kiwi.com, que
# leurs sites respectifs refusent aux robots (403 Cloudflare, 429 DataDome, page vide). C'est la
# raison d'être de cette source : elle apporte les intermédiaires que les sources de compagnies
# ne peuvent pas voir, et ce sont eux qui portent les tarifs les plus agressifs.
#
# Contrairement à transat et air_canada, rien n'est lu dans le HTML. La page rendue n'expose ses
# résultats que sous des classes CSS engendrées (`hYzH-price`, `p6Cx-content`), que Kayak fait
# tourner ; s'y accrocher, c'est programmer une panne à échéance. On lit à la place la réponse de
# l'API de sondage interne qui alimente cette page, dont les noms de champs sont stables et
# décrivent le domaine — même arbitrage que google_flights, qui lit la charge utile plutôt que la
# page.
URL_BASE = "https://ca.kayak.com/flights"

# Le domaine canadien et ce paramètre suffisent à obtenir des dollars canadiens partout : la
# capture de reconnaissance ne contenait pas un seul montant dans une autre devise (4630 sur 4630).
# Une option libellée autrement est écartée plutôt que convertie — le taux du jour n'est pas
# notre affaire, et une conversion approximative fausserait la médiane en silence.
DEVISE = "CAD"

_CHEMIN_SONDAGE = "search/dynamic/flights/poll"

# Kayak affine ses résultats par sondages successifs et annonce lui-même la fin de la recherche.
# On attend cet aveu plutôt qu'un délai choisi au hasard : à la reconnaissance il tombait vers
# 40 s, mais c'est une observation, pas une garantie.
_STATUT_TERMINE = "complete"
_ATTENTE_TOTALE_MS = 90_000
_PAS_DE_SONDAGE_MS = 2_500

# Le tri par prix croissant est demandé au site : les premiers résultats sont alors les moins
# chers, et un relevé tronqué reste un relevé qui contient l'aubaine.
_TRI_PRIX_CROISSANT = "price_a"

# Une page de résultats porte une cinquantaine de vols. Les enregistrer tous gonflerait la base
# sans rien apprendre : la détection travaille sur le minimum du relevé (jobs.py retient
# `min(observations)`), et la liste est déjà triée par prix. On garde de quoi juger la dispersion
# du jour, pas le catalogue.
_MAX_OFFRES = 20

# Les publicités voyagent dans la même liste que les vols, avec un prix d'appel qui n'engage
# personne. Seul `core` désigne un vrai résultat réservable.
_TYPE_VOL = "core"


class _SondageIndisponibleError(ProviderError):
    """Aucune réponse de sondage exploitable n'a été captée pendant la navigation."""


def _texte_requete(query: SearchQuery) -> str:
    """`YUL-PAR/2026-11-03/2026-11-10`, ou sans la seconde date pour un aller simple."""
    trajet = f"{query.origin}-{query.destination}"
    dates = query.depart_date.isoformat()
    if query.return_date is not None:
        dates += f"/{query.return_date.isoformat()}"
    return f"{trajet}/{dates}"


def url_recherche(query: SearchQuery) -> str:
    return (
        f"{URL_BASE}/{_texte_requete(query)}"
        f"?sort={_TRI_PRIX_CROISSANT}&currency={DEVISE}"
    )


def _valider_portee(query: SearchQuery) -> None:
    """Rejette avant tout accès réseau ce que ce provider ne sait pas lire honnêtement.

    Kayak chiffre `priceMode: per-person`. À un passager, ce prix est le total du voyage et se
    compare à ceux des autres sources ; au-delà, il ne le serait plus, et une observation par
    personne glissée parmi des totaux fausserait la médiane du trajet sans rien signaler.
    """
    if query.passengers != 1:
        raise ProviderError(
            f"{NOM} : seul un passager est pris en charge, le site chiffrant par personne "
            f"(passengers={query.passengers})"
        )
    if query.trip_type is TripType.ROUND_TRIP and query.return_date is None:
        raise ProviderError(f"{NOM} : aller-retour demandé sans date de retour")


def _prix_le_plus_bas(resultat: dict) -> tuple[int, str] | None:
    """Le moins cher des revendeurs proposant ce vol, avec le nom de celui qui l'offre.

    Un même vol est vendu par plusieurs intermédiaires à des prix différents — c'est là que se
    trouve l'écart qui nous intéresse. Une option libellée dans une autre devise est ignorée.
    Rend None si rien n'est exploitable, jamais un prix deviné.
    """
    meilleur: tuple[int, str] | None = None
    for option in resultat.get("bookingOptions") or []:
        prix = option.get("displayPrice") or {}
        if prix.get("currency") != DEVISE:
            continue
        montant = prix.get("price")
        if not isinstance(montant, (int, float)):
            continue
        arrondi = round(float(montant))
        if arrondi <= 0:
            continue
        if meilleur is None or arrondi < meilleur[0]:
            meilleur = (arrondi, str(option.get("providerCode") or ""))
    return meilleur


def _escales(resultat: dict) -> int:
    """Somme des correspondances de l'aller et du retour.

    Une jambe de N segments compte N-1 escales ; un aller-retour direct en compte donc zéro, et
    non deux.
    """
    total = 0
    for jambe in resultat.get("legs") or []:
        segments = jambe.get("segments") or []
        total += max(len(segments) - 1, 0)
    return total


def _duree_minutes(resultat: dict, jambes: dict) -> int | None:
    """Durée cumulée des jambes, d'après la table de référence du document.

    Rend None dès qu'une jambe manque à l'appel : une durée partielle serait plus trompeuse
    qu'une durée absente.
    """
    total = 0
    for reference in resultat.get("legs") or []:
        jambe = jambes.get(reference.get("id"))
        if not isinstance(jambe, dict):
            return None
        duree = jambe.get("duration")
        if not isinstance(duree, (int, float)):
            return None
        total += int(duree)
    return total or None


def _compagnie(resultat: dict, segments: dict, compagnies: dict) -> str:
    """Nom des transporteurs du voyage, dédoublonnés et ordonnés.

    L'ordre est alphabétique et non chronologique : ce libellé entre dans `offer_hash`, qui sert
    d'anti-répétition pour les alertes. Un même vol dont les segments seraient rapportés dans un
    autre ordre doit produire la même empreinte, sans quoi la même aubaine alerterait deux fois.
    """
    codes: set[str] = set()
    for jambe in resultat.get("legs") or []:
        for reference in jambe.get("segments") or []:
            segment = segments.get(reference.get("id"))
            if isinstance(segment, dict) and segment.get("airline"):
                codes.add(str(segment["airline"]))
    if not codes:
        return "Inconnue"
    noms = sorted(
        (compagnies.get(code) or {}).get("name") or code for code in codes
    )
    return ", ".join(noms)


def _lien(resultat: dict, query: SearchQuery) -> str:
    """Lien vers le vol précis quand le document en porte un, vers la recherche sinon."""
    partiel = resultat.get("shareableUrl")
    if isinstance(partiel, str) and partiel.startswith("/"):
        return f"https://ca.kayak.com{partiel}"
    return url_recherche(query)


def parse_poll(donnees: dict, query: SearchQuery) -> list[FlightOffer]:
    """Traduit une réponse de sondage en offres, de la moins chère à la plus chère.

    Fonction pure : c'est elle qui porte toute la lecture du format, et elle s'éprouve sur une
    capture enregistrée sans ouvrir de navigateur.
    """
    jambes = donnees.get("legs") if isinstance(donnees.get("legs"), dict) else {}
    segments = donnees.get("segments") if isinstance(donnees.get("segments"), dict) else {}
    compagnies = donnees.get("airlines") if isinstance(donnees.get("airlines"), dict) else {}

    offres: list[FlightOffer] = []
    for resultat in donnees.get("results") or []:
        if not isinstance(resultat, dict) or resultat.get("type") != _TYPE_VOL:
            continue

        prix = _prix_le_plus_bas(resultat)
        if prix is None:
            continue
        montant, revendeur = prix

        escales = _escales(resultat)
        if query.max_stops is not None and escales > query.max_stops:
            continue

        offres.append(
            FlightOffer(
                provider=NOM,
                origin=query.origin,
                destination=query.destination,
                depart_date=query.depart_date,
                return_date=query.return_date,
                price_cad=montant,
                price_original=float(montant),
                currency_original=DEVISE,
                airline=_compagnie(resultat, segments, compagnies),
                stops=escales,
                duration_minutes=_duree_minutes(resultat, jambes),
                deep_link=_lien(resultat, query),
                raw={
                    "result_id": resultat.get("resultId", ""),
                    "provider_code": revendeur,
                    "booking_options": len(resultat.get("bookingOptions") or []),
                },
            )
        )

    offres.sort(key=lambda offre: (offre.price_cad, offre.stops))
    return offres[:_MAX_OFFRES]


def _sondage_termine(corps: str) -> dict | None:
    """Rend la charge utile si elle est complète et lisible, None sinon."""
    try:
        donnees = json.loads(corps)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(donnees, dict):
        return None
    return donnees if donnees.get("status") == _STATUT_TERMINE else None


def _capturer_sondage(page: Page, url: str) -> dict:
    """Navigue et retient la dernière réponse de sondage, en s'arrêtant dès qu'elle est complète.

    On garde aussi les réponses incomplètes : si Kayak n'annonce jamais la fin dans le temps
    imparti, un relevé partiel trié par prix vaut mieux qu'un échec — il contient déjà les
    résultats les moins chers.
    """
    recues: list[str] = []

    def sur_reponse(reponse: Any) -> None:
        if _CHEMIN_SONDAGE not in reponse.url:
            return
        try:
            recues.append(reponse.text())
        except Exception:  # noqa: BLE001 — une réponse illisible n'est pas une panne
            logger.debug("%s : réponse de sondage illisible", NOM)

    page.on("response", sur_reponse)
    page.goto(url, timeout=_ATTENTE_TOTALE_MS, wait_until="domcontentloaded")

    for _ in range(_ATTENTE_TOTALE_MS // _PAS_DE_SONDAGE_MS):
        page.wait_for_timeout(_PAS_DE_SONDAGE_MS)
        if recues and (donnees := _sondage_termine(recues[-1])) is not None:
            return donnees

    if not recues:
        raise _SondageIndisponibleError(
            f"{NOM} : aucune réponse de sondage captée sur {url}"
        )
    try:
        derniere = json.loads(recues[-1])
    except (json.JSONDecodeError, ValueError) as erreur:
        raise _SondageIndisponibleError(f"{NOM} : réponse de sondage illisible") from erreur
    logger.info(
        "%s : recherche encore en cours après %d s, relevé partiel retenu (statut=%s)",
        NOM,
        _ATTENTE_TOTALE_MS // 1000,
        derniere.get("status"),
    )
    return derniere


class KayakProvider:
    """Source Kayak, lue par son API de sondage interne plutôt que par sa page.

    Un navigateur reste nécessaire : le sondage n'est servi qu'à une session ayant exécuté le
    script de la page, qui porte l'identifiant de recherche et les témoins. Le mode sans fenêtre
    suffit — contrairement à air_canada, rien ici ne déroute un navigateur automatisé.
    """

    name = NOM

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or Settings()

    def _fetch(self, query: SearchQuery) -> dict:
        """Isolé pour les tests : le simuler évite de lancer un navigateur."""
        _valider_portee(query)

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as erreur:
            raise ProviderError(f"Playwright indisponible : {erreur}") from erreur

        url = url_recherche(query)
        with sync_playwright() as playwright:
            navigateur = playwright.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                contexte = navigateur.new_context(
                    user_agent=AGENT_UTILISATEUR,
                    viewport={"width": 1440, "height": 900},
                    locale="fr-CA",
                    timezone_id=self._settings.timezone,
                )
                page = contexte.new_page()
                page.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                )
                donnees = _capturer_sondage(page, url)
            finally:
                navigateur.close()

        # Même règle que les autres sources : la capture de débogage est écrite dans tous les cas,
        # pour qu'une dérive de format se répare sans avoir à reproduire le problème. Une dérive
        # ne lève pas d'exception, elle rend une liste vide — indiscernable d'un trajet sans vol.
        debug_path(self._settings, NOM).with_suffix(".json").write_text(
            json.dumps(donnees, ensure_ascii=False), encoding="utf-8"
        )
        return donnees

    def search(self, query: SearchQuery) -> list[FlightOffer]:
        try:
            donnees = self._fetch(query)
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 — traduire toute panne est le contrat de l'interface
            raise ProviderError(f"{NOM} : échec de la requête ({exc})") from exc

        offres = parse_poll(donnees, query)
        if not offres:
            raise EmptyResultError(
                f"{NOM} : aucune offre exploitable pour "
                f"{query.origin}->{query.destination} le {query.depart_date}"
            )
        return offres
