from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlmodel import Session

from scrappervol.config import Settings
from scrappervol.detection.rules import (
    SEUIL_SOURCE_MUETTE_H,
    PriceContext,
    is_exception,
    is_find,
    relative_gap,
)
from scrappervol.notify.mailer import Mailer
from scrappervol.notify.render import (
    DigestData,
    ExceptionData,
    ProviderStatus,
    RouteBlock,
    render_digest,
    render_exception,
)
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
            stops=observation.stops,
            duration_minutes=observation.duration_minutes,
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


def _statut_source(session: Session, provider: str, now: datetime) -> ProviderStatus:
    sante = repo.get_or_create_health(session, provider)
    heures = (
        (now - sante.last_success_at).total_seconds() / 3600
        if sante.last_success_at is not None
        else None
    )
    return ProviderStatus(
        name=provider,
        last_success_at=sante.last_success_at,
        consecutive_failures=sante.consecutive_failures,
        hours_silent=heures,
        is_stale=heures is None or heures > SEUIL_SOURCE_MUETTE_H,
    )


def _bloc_trajet(session: Session, route: Route, settings: Settings, now: datetime) -> RouteBlock:
    jour = now.date()
    ligne = repo.daily_low_for(session, route.id, jour)
    historique = repo.daily_low_history(
        session, route.id, before_day=jour, window_days=settings.history_window_days
    )
    contexte = PriceContext(daily_lows=historique)
    en_construction = not contexte.has_significant_history(settings.min_history_days)
    mediane = contexte.median_price

    if ligne is None:
        return RouteBlock(
            label=route.label,
            price_cad=None,
            airline="",
            origin="",
            destination="",
            depart_date=None,
            return_date=None,
            provider="",
            deep_link="",
            median_price=mediane,
            gap_vs_median=None,
            gap_vs_yesterday=None,
            is_find=False,
            history_building=en_construction,
        )

    observation = session.get(Observation, ligne.observation_id) if ligne.observation_id else None
    veille = repo.daily_low_for(session, route.id, jour - timedelta(days=1))

    return RouteBlock(
        label=route.label,
        price_cad=ligne.price_cad,
        airline=observation.airline if observation else "",
        origin=observation.origin if observation else "",
        destination=observation.destination if observation else "",
        depart_date=observation.departure_date if observation else None,
        return_date=observation.return_date if observation else None,
        provider=ligne.provider,
        deep_link=observation.deep_link if observation else "",
        median_price=mediane,
        gap_vs_median=relative_gap(ligne.price_cad, mediane) if mediane else None,
        gap_vs_yesterday=ligne.price_cad - veille.price_cad if veille else None,
        is_find=is_find(
            price_cad=ligne.price_cad,
            context=contexte,
            target_price_cad=route.target_price_cad,
            find_threshold=settings.find_threshold,
            min_history_days=settings.min_history_days,
            credibility_floor=settings.credibility_floor_cad,
        ),
        history_building=en_construction,
    )


def build_digest(session: Session, settings: Settings, now: datetime) -> DigestData:
    return DigestData(
        day=now.date(),
        blocks=[
            _bloc_trajet(session, route, settings, now) for route in repo.active_routes(session)
        ],
        providers=[_statut_source(session, nom, now) for nom in settings.enabled_providers],
    )


def send_digest(session: Session, settings: Settings, mailer: Mailer, now: datetime) -> bool:
    """Envoie le digest quotidien. Retourne False si aucun trajet n'est actif (§8 du design)."""
    donnees = build_digest(session, settings, now)
    if not donnees.blocks:
        logger.info("aucun trajet actif, digest non envoyé")
        return False

    courriel = render_digest(donnees)
    try:
        mailer.send(courriel, settings.alert_to)
    except Exception as erreur:  # noqa: BLE001 — un SMTP en panne ne doit pas coûter les données
        logger.error("digest non envoyé : %s", erreur)
        return False

    repo.record_alert(
        session,
        route_id=0,
        observation_id=None,
        kind=AlertKind.DIGEST,
        payload={"find_count": donnees.find_count, "routes": len(donnees.blocks)},
        at=now,
    )
    return True


def purge_old_data(session: Session, settings: Settings, now: datetime) -> int:
    supprimees = repo.purge_observations(session, now, settings.retention_days)
    logger.info("purge : %s observations supprimées", supprimees)
    return supprimees
