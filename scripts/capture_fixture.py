"""Capture une réponse réelle d'une source et l'enregistre en fixture.

Usage : ./dev shell puis  python scripts/capture_fixture.py google_flights

Ce script touche le réseau ; il n'est jamais lancé par la suite de tests. Il sert à rafraîchir la
fixture le jour où Google change la forme de ses données — c'est-à-dire le jour où la source se met
à mentir en silence plutôt qu'à échouer franchement.
"""

import dataclasses
import json
import sys
from datetime import date, timedelta
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
FIXTURES = RACINE / "tests" / "fixtures"


def capture_google_flights() -> None:
    from fast_flights import FlightQuery, Passengers, create_query, get_flights

    depart = date.today() + timedelta(days=90)
    retour = depart + timedelta(days=10)

    requete = create_query(
        flights=[
            FlightQuery(date=depart.isoformat(), from_airport="YUL", to_airport="CDG"),
            FlightQuery(date=retour.isoformat(), from_airport="CDG", to_airport="YUL"),
        ],
        trip="round-trip",
        seat="economy",
        passengers=Passengers(adults=1),
        currency="CAD",
    )
    resultats = get_flights(requete)

    charge = {
        "query": {
            "origin": "YUL",
            "destination": "CDG",
            "depart": depart.isoformat(),
            "retour": retour.isoformat(),
        },
        "results": [dataclasses.asdict(vol) for vol in resultats],
    }
    FIXTURES.mkdir(parents=True, exist_ok=True)
    cible = FIXTURES / "google_flights_yul_cdg.json"
    cible.write_text(json.dumps(charge, indent=2, ensure_ascii=False, default=str))
    print(f"écrit : {cible}  ({len(charge['results'])} résultats)")


if __name__ == "__main__":
    source = sys.argv[1] if len(sys.argv) > 1 else "google_flights"
    if source == "google_flights":
        capture_google_flights()
    else:
        raise SystemExit(f"source inconnue : {source}")
