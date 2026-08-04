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
        from fast_flights import FlightQuery, Passengers, create_query, get_flights

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
        return [dataclasses.asdict(vol) for vol in get_flights(requete)]

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
