from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any
from urllib.parse import quote_plus

from scrappervol.core.types import FlightOffer, SearchQuery, TripType
from scrappervol.providers.base import EmptyResultError, ProviderError

NOM = "google_flights"

# On demande cette devise à Google et on croit la réponse sur parole : rien dans la charge utile ne
# l'atteste. Un basculement silencieux vers l'USD passerait donc inaperçu ici — c'est le plancher de
# crédibilité de la détection qui rattrape l'ordre de grandeur aberrant.
DEVISE = "CAD"

# Google range ses résultats en deux listes distinctes dans la charge utile : les « meilleurs vols »
# puis les « autres vols ». fast-flights ne lit que la seconde. Or les moins chers sont dans la
# première : sur YUL→CDG, elle seule portait le vol à 1717 $, la seconde s'ouvrant à 2292 $. Ne lire
# qu'une section revenait donc à rater exactement les offres que cette veille existe pour trouver.
_SECTIONS = (2, 3)


def _fusionner_sections(payload: list[Any]) -> list[Any]:
    """Rassemble les vols de toutes les sections connues dans celle que le parseur sait lire.

    Rendre le payload modifié plutôt qu'une liste de vols permet de réutiliser le parseur de la
    bibliothèque au lieu de redire ici les indices magiques de chaque segment — c'est la partie qui
    change le plus souvent chez Google, et celle qu'on veut le moins entretenir.
    """
    vols: list[Any] = []
    for i in _SECTIONS:
        if i >= len(payload):
            continue
        section = payload[i]
        if isinstance(section, list) and section and isinstance(section[0], list):
            vols.extend(section[0])

    # Rien ne garantit que Google renvoie toujours les deux sections : on creuse la place plutôt que
    # d'abandonner en chemin les vols déjà recueillis.
    cible = _SECTIONS[-1]
    while len(payload) <= cible:
        payload.append(None)
    if not isinstance(payload[cible], list) or not payload[cible]:
        payload[cible] = [None]
    payload[cible][0] = vols
    return payload


def _date_du_segment(segment: Mapping[str, Any]) -> date | None:
    """Date de départ réelle d'un segment, ou None si la réponse ne la porte pas.

    `SimpleDatetime.date` est un triplet (année, mois, jour), toujours observé complet. On reste
    défensif : cette fonction lit une source hostile, pas une structure maison.
    """
    brut = (segment.get("departure") or {}).get("date")
    if not isinstance(brut, (list, tuple)) or len(brut) != 3:
        return None
    try:
        return date(int(brut[0]), int(brut[1]), int(brut[2]))
    except (TypeError, ValueError):
        return None


def _lien(query: SearchQuery, depart: date) -> str:
    """Lien de recherche Google Flights.

    fast-flights ne rend pas d'URL par offre : le lien pointe la recherche, pas le billet.
    """
    termes = f"Flights from {query.origin} to {query.destination} on {depart.isoformat()}"
    if query.return_date:
        termes += f" through {query.return_date.isoformat()}"
    return f"https://www.google.com/travel/flights?q={quote_plus(termes)}"


def to_offers(resultats: Sequence[Mapping[str, Any]], query: SearchQuery) -> list[FlightOffer]:
    """Traduit la réponse de fast-flights en offres du domaine.

    Fonction pure : ni réseau ni horloge, ce qui permet de la tester sur une capture réelle. Toute
    entrée dont le prix ou les segments sont inexploitables est **écartée**, jamais complétée par
    défaut : une offre devinée devient une fausse alerte, et une fausse alerte coûte plus cher que
    l'offre manquée.
    """
    offres: list[FlightOffer] = []
    for brut in resultats:
        prix = brut.get("price")
        if not isinstance(prix, int) or isinstance(prix, bool) or prix <= 0:
            continue

        segments = list(brut.get("flights") or ())
        if not segments:
            continue

        escales = len(segments) - 1
        if query.max_stops is not None and escales > query.max_stops:
            continue

        durees = [s.get("duration") for s in segments]
        duree = sum(durees) if all(isinstance(d, int) for d in durees) else None

        compagnies = [c for c in (brut.get("airlines") or ()) if c]
        compagnie = ", ".join(compagnies) if compagnies else str(brut.get("type") or "?")

        depart = _date_du_segment(segments[0]) or query.depart_date

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
                deep_link=_lien(query, depart),
                raw=dict(brut),
            )
        )
    return offres


class GoogleFlightsProvider:
    """Source Google Flights, via fast-flights 3."""

    name = NOM

    def _fetch(self, query: SearchQuery) -> list[Mapping[str, Any]]:
        """Appelle la bibliothèque et rend des dictionnaires bruts. Isolé pour les tests."""
        import json

        from fast_flights import FlightQuery, Passengers, create_query, fetch_flights_html
        from fast_flights.parser import parse_js
        from selectolax.lexbor import LexborHTMLParser

        vols = [
            FlightQuery(
                date=query.depart_date.isoformat(),
                from_airport=query.origin,
                to_airport=query.destination,
            )
        ]
        if query.trip_type is TripType.ROUND_TRIP and query.return_date:
            vols.append(
                FlightQuery(
                    date=query.return_date.isoformat(),
                    from_airport=query.destination,
                    to_airport=query.origin,
                )
            )

        requete = create_query(
            flights=vols,
            trip="round-trip" if len(vols) == 2 else "one-way",
            seat="economy",
            passengers=Passengers(adults=query.passengers),
            currency=DEVISE,
            max_stops=query.max_stops,
        )

        # On refait le chemin de get_flights() pour pouvoir intercaler la fusion des sections.
        html = fetch_flights_html(requete)
        script = LexborHTMLParser(html).css_first(r"script.ds\:1")
        if script is None:
            raise ProviderError(f"{NOM} : charge utile introuvable dans la page")
        brut = script.text().split("data:", 1)[1].rsplit(",", 1)[0]
        payload = _fusionner_sections(json.loads(brut))
        vols = parse_js("data:" + json.dumps(payload) + ",")
        return [dataclasses.asdict(vol) for vol in vols]

    def search(self, query: SearchQuery) -> list[FlightOffer]:
        try:
            bruts = self._fetch(query)
        except Exception as exc:  # noqa: BLE001 — traduire toute panne est le contrat de l'interface
            raise ProviderError(f"{NOM} : échec de la requête ({exc})") from exc

        offres = to_offers(bruts, query)
        if not offres:
            raise EmptyResultError(
                f"{NOM} : aucune offre exploitable pour "
                f"{query.origin}->{query.destination} le {query.depart_date}"
            )
        return offres
