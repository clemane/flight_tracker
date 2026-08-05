"""Déclenche tout de suite ce que l'ordonnanceur ne ferait que plus tard.

Les passages de collecte sont espacés de plusieurs heures et le digest ne part qu'à 18 h : au
démarrage, l'application ne montre donc rien avant un long moment. Ce script appelle les mêmes
fonctions que les jobs, immédiatement, pour pouvoir essayer l'installation et diagnostiquer une
panne sans attendre le prochain passage.

    python scripts/run_once.py scan [--source transat]
    python scripts/run_once.py preview          # affiche le digest, n'envoie rien
    python scripts/run_once.py digest           # construit ET envoie le digest
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime

from sqlalchemy.engine import Engine

from scrappervol.config import Settings
from scrappervol.notify.mailer import NullMailer, build_mailer
from scrappervol.notify.render import render_digest
from scrappervol.scheduler.app import build_providers
from scrappervol.scheduler.jobs import build_digest, run_scan, send_digest
from scrappervol.storage.db import create_engine_for, init_db, session_scope


def _scan(engine: Engine, settings: Settings, source: str | None) -> int:
    mailer = build_mailer(settings)
    sources = [s for s in build_providers(settings) if source in (None, s.name)]
    if not sources:
        actives = ", ".join(settings.enabled_providers) or "aucune"
        print(f"aucune source à interroger — actives : {actives}", file=sys.stderr)
        return 1

    for provider in sources:
        print(f"→ {provider.name} …", flush=True)
        with session_scope(engine) as session:
            bilan = run_scan(session, provider, settings, mailer, datetime.now(UTC))
        if bilan.failed:
            print(f"  {provider.name} : ÉCHEC — voir les journaux et data/debug/")
        elif bilan.skipped:
            print(f"  {provider.name} : ignorée (disjoncteur ouvert, ou aucun trajet actif)")
        else:
            print(
                f"  {provider.name} : {bilan.offers_recorded} offres, "
                f"{bilan.new_lows} nouveaux plus bas, {bilan.exceptions_sent} alertes"
            )
    return 0


def _preview(engine: Engine, settings: Settings) -> int:
    with session_scope(engine) as session:
        donnees = build_digest(session, settings, datetime.now(UTC))
    if not donnees.blocks:
        print("aucun trajet actif : le digest ne serait pas envoyé.", file=sys.stderr)
        return 1

    courriel = render_digest(donnees)
    print(f"Objet : {courriel.subject}\n{'─' * 72}\n{courriel.text}")
    return 0


def _digest(engine: Engine, settings: Settings) -> int:
    mailer = build_mailer(settings)
    if isinstance(mailer, NullMailer):
        print("SMTP_HOST est vide : le courriel serait produit puis jeté.", file=sys.stderr)
        print("Utilisez « preview » pour le voir sans l'envoyer.", file=sys.stderr)
        return 1

    with session_scope(engine) as session:
        envoye = send_digest(session, settings, mailer, datetime.now(UTC))
    if envoye:
        print(f"digest envoyé à {settings.alert_to}")
        return 0
    print("digest non envoyé — aucun trajet actif, ou SMTP en échec (voir les journaux).")
    return 1


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")

    analyseur = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sous = analyseur.add_subparsers(dest="action", required=True)
    scan = sous.add_parser("scan", help="un passage de collecte immédiat")
    scan.add_argument("--source", help="n'interroger que cette source (défaut : toutes)")
    sous.add_parser("preview", help="afficher le digest sans l'envoyer")
    sous.add_parser("digest", help="construire et envoyer le digest")
    arguments = analyseur.parse_args()

    settings = Settings()
    engine = create_engine_for(settings.database_url)
    init_db(engine)

    if arguments.action == "scan":
        return _scan(engine, settings, arguments.source)
    if arguments.action == "preview":
        return _preview(engine, settings)
    return _digest(engine, settings)


if __name__ == "__main__":
    raise SystemExit(main())
