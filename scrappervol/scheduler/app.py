from __future__ import annotations

import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import Engine

from scrappervol.config import Settings
from scrappervol.notify.mailer import Mailer
from scrappervol.providers.base import PriceProvider
from scrappervol.scheduler.jobs import purge_old_data, run_scan, send_digest
from scrappervol.storage.db import session_scope

logger = logging.getLogger(__name__)

# Toute source constructible par build_providers doit figurer ici, sinon le câblage lève une
# KeyError au démarrage. Air Canada n'y est pas : la source a été abandonnée (voir
# docs/superpowers/notes/2026-08-05-air-canada-abandon.md), le réglage interval_air_canada_hours
# subsiste dans Settings mais ne pilote plus rien.
INTERVALLES = {
    "google_flights": "interval_google_hours",
    "transat": "interval_transat_hours",
}

# Décalage aléatoire appliqué à chaque passage, en secondes.
JITTER_S = 1800


def build_providers(settings: Settings) -> list[PriceProvider]:
    """Instancie les sources activées. Une source inconnue ou non importable est ignorée."""
    sources: list[PriceProvider] = []
    for nom in settings.enabled_providers:
        try:
            if nom == "google_flights":
                from scrappervol.providers.google_flights import GoogleFlightsProvider

                # Cette source ne prend pas de réglages : elle n'a pas d'__init__ et tout ce
                # dont elle a besoin lui arrive par la SearchQuery.
                sources.append(GoogleFlightsProvider())
            elif nom == "transat":
                from scrappervol.providers.transat import TransatProvider

                sources.append(TransatProvider(settings))
            else:
                logger.warning("source inconnue ignorée : %s", nom)
        except ImportError as erreur:
            logger.warning("source %s non importable, ignorée : %s", nom, erreur)
    return sources


def build_scheduler(engine: Engine, settings: Settings, mailer: Mailer) -> BackgroundScheduler:
    """Câble les jobs sans démarrer l'ordonnanceur ; le démarrage appartient au point d'entrée."""
    fuseau = ZoneInfo(settings.timezone)
    ordonnanceur = BackgroundScheduler(timezone=fuseau)

    for source in build_providers(settings):
        heures = getattr(settings, INTERVALLES[source.name])
        ordonnanceur.add_job(
            _job_scan,
            trigger=IntervalTrigger(hours=heures, jitter=JITTER_S),
            args=[engine, settings, mailer, source],
            id=f"scan:{source.name}",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )

    ordonnanceur.add_job(
        _job_digest,
        trigger=CronTrigger(hour=settings.digest_hour, minute=0, timezone=fuseau),
        args=[engine, settings, mailer],
        id="digest",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    ordonnanceur.add_job(
        _job_purge,
        trigger=CronTrigger(hour=3, minute=30, timezone=fuseau),
        args=[engine, settings],
        id="purge",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    return ordonnanceur


def _job_scan(engine: Engine, settings: Settings, mailer: Mailer, provider: PriceProvider) -> None:
    with session_scope(engine) as session:
        resultat = run_scan(session, provider, settings, mailer, datetime.now(UTC))
    logger.info(
        "scan %s : %s offres, %s nouveaux plus bas, %s alertes",
        resultat.provider,
        resultat.offers_recorded,
        resultat.new_lows,
        resultat.exceptions_sent,
    )


def _job_digest(engine: Engine, settings: Settings, mailer: Mailer) -> None:
    with session_scope(engine) as session:
        send_digest(session, settings, mailer, datetime.now(UTC))


def _job_purge(engine: Engine, settings: Settings) -> None:
    with session_scope(engine) as session:
        purge_old_data(session, settings, datetime.now(UTC))
