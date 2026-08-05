from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session

from scrappervol.config import Settings
from scrappervol.detection.rules import PriceContext, is_exception, relative_gap
from scrappervol.notify.mailer import Mailer
from scrappervol.notify.render import ExceptionData, render_exception
from scrappervol.providers.base import PriceProvider
from scrappervol.providers.runner import run_provider
from scrappervol.storage import repo
from scrappervol.storage.models import AlertKind, Observation, Route

logger = logging.getLogger(__name__)


@dataclass
class ScanOutcome:
    provider: str
    offers_recorded: int = 0
    new_lows: int = 0
    exceptions_sent: int = 0
    failed: bool = False
    skipped: bool = False


def run_scan(
    session: Session,
    provider: PriceProvider,
    settings: Settings,
    mailer: Mailer,
    now: datetime,
    sleeper: Callable[[float], None] = time.sleep,
) -> ScanOutcome:
    """Un passage complet d'une source : relève, enregistre, détecte, alerte."""
    rapport = run_provider(session, provider, settings, now, sleeper=sleeper)
    resultat = ScanOutcome(provider=provider.name, failed=rapport.failed, skipped=rapport.skipped)
    if rapport.failed or rapport.skipped:
        return resultat

    jour = now.date()

    for route_id, offres in rapport.offers_by_route.items():
        observations = repo.record_observations(session, route_id, offres, now)
        resultat.offers_recorded += len(observations)
        if not observations:
            continue

        meilleure = min(observations, key=lambda obs: obs.price_cad)
        if repo.upsert_daily_low(session, route_id, jour, meilleure) is not None:
            resultat.new_lows += 1

        trajet = session.get(Route, route_id)
        if trajet is None:
            continue

        if _traiter_exception(session, trajet, meilleure, settings, mailer, now):
            resultat.exceptions_sent += 1

    return resultat


def _traiter_exception(
    session: Session,
    route: Route,
    observation: Observation,
    settings: Settings,
    mailer: Mailer,
    now: datetime,
) -> bool:
    historique = repo.daily_low_history(
        session, route.id, before_day=now.date(), window_days=settings.history_window_days
    )
    contexte = PriceContext(daily_lows=historique)

    deja = repo.exception_already_sent(session, route.id, observation.offer_hash)
    if not is_exception(
        price_cad=observation.price_cad,
        context=contexte,
        threshold=route.exception_threshold,
        min_history_days=settings.min_history_days,
        credibility_floor=settings.credibility_floor_cad,
        already_alerted=deja,
    ):
        return False

    mediane = contexte.median_price or 0.0
    courriel = render_exception(
        ExceptionData(
            label=route.label,
            origin=observation.origin,
            destination=observation.destination,
            depart_date=observation.departure_date,
            return_date=observation.return_date,
            price_cad=observation.price_cad,
            airline=observation.airline,
            provider=observation.provider,
            deep_link=observation.deep_link,
            median_price=mediane,
            gap_vs_median=relative_gap(observation.price_cad, mediane),
            history_days=contexte.days_of_history,
        )
    )

    try:
        mailer.send(courriel, settings.alert_to)
    except Exception as erreur:  # noqa: BLE001 — un SMTP en panne ne doit pas coûter les données
        logger.error("alerte non envoyée, sera retentée au prochain passage : %s", erreur)
        return False

    repo.record_alert(
        session,
        route.id,
        observation.id,
        AlertKind.EXCEPTION,
        {"offer_hash": observation.offer_hash, "price_cad": observation.price_cad},
        now,
    )

    return True
