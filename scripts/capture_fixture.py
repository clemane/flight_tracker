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


def capture_transat() -> None:
    """Rejoue le pilotage de formulaire éprouvé à la tâche 11 et fige le HTML obtenu.

    Contrairement à google_flights, Air Transat n'a pas d'API : la fixture est la page HTML brute
    de résultats, pas une structure déjà analysée. Le jour où ce script est relancé, il touche le
    vrai site une fois — reste économe : ce n'est pas fait pour tourner en boucle.
    """
    from scrappervol.config import Settings
    from scrappervol.core.types import SearchQuery
    from scrappervol.providers.playwright_base import fetch_html
    from scrappervol.providers.transat import _piloter_recherche

    depart = date.today() + timedelta(days=97)
    retour = depart + timedelta(days=7)
    requete = SearchQuery(origin="YUL", destination="CUN", depart_date=depart, return_date=retour)

    html = fetch_html(
        "https://www.airtransat.com/fr-CA?search=flight",
        Settings(),
        provider_name="transat_fixture",
        interact=lambda page: _piloter_recherche(page, requete),
        timeout_ms=60_000,
    )

    # capture_transat() n'a pas été relancé pour produire tests/fixtures/transat_yul_cun.html : le
    # fichier versionné vient d'une capture réelle et vérifiée obtenue lors de la reconnaissance de
    # la tâche 11 (deux passages identiques, à cinq minutes d'intervalle, chacun avec trois prix CAD
    # distincts — voir task-11-report.md). Cette fonction sert à en obtenir une nouvelle le jour où
    # la forme de la page aura changé.
    FIXTURES.mkdir(parents=True, exist_ok=True)
    cible = FIXTURES / "transat_yul_cun.html"
    cible.write_text(html, encoding="utf-8")
    print(f"écrit : {cible}  ({len(html)} caractères)")


if __name__ == "__main__":
    source = sys.argv[1] if len(sys.argv) > 1 else "google_flights"
    if source == "google_flights":
        capture_google_flights()
    elif source == "transat":
        capture_transat()
    else:
        raise SystemExit(f"source inconnue : {source}")
