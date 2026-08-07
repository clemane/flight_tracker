from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlmodel import Session

from scrappervol.config import Settings
from scrappervol.detection.rules import (
    FRAICHEUR_RELEVE_J,
    SEUIL_SOURCE_MUETTE_H,
    PriceContext,
    is_calendar_exception,
    is_exception,
    is_find,
    relative_gap,
)
from scrappervol.detection.stats import median
from scrappervol.notify.mailer import Mailer
from scrappervol.notify.render import (
    DateAlternative,
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

# Assez d'alternatives pour montrer que la date compte, assez peu pour que le courriel reste
# lisible sur un téléphone.
ALTERNATIVES_DIGEST = 3


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
    """Un passage complet d'une source : relève, enregistre, détecte, alerte.

    Le passage forme une unité de travail : ou bien les observations, le plus bas du jour et les
    traces d'alerte sont tous enregistrés, ou bien rien ne l'est. Une validation à chaque étape
    laissait, en cas d'incident, des observations privées du plus bas qu'elles justifiaient —
    lacune définitive, puisque le plus bas d'un jour ne se recalcule pas après coup.

    Les traces d'alerte échappent à cette règle et sont validées dès l'envoi : un courriel parti
    ne se rattrape pas par un `rollback`, et sans sa trace il repartirait au passage suivant.
    """
    rapport = run_provider(session, provider, settings, now, sleeper=sleeper)
    resultat = ScanOutcome(provider=provider.name, failed=rapport.failed, skipped=rapport.skipped)
    if rapport.failed or rapport.skipped:
        return resultat

    jour = now.date()

    try:
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

        session.commit()
    except Exception:
        session.rollback()
        raise

    return resultat


@dataclass(frozen=True, slots=True)
class _Verdict:
    """Ce sur quoi repose une alerte, pour que le courriel puisse le dire sans mentir."""

    median_price: float
    history_days: int
    calendar_dates: int | None


def _juger_exception(
    session: Session,
    route: Route,
    observation: Observation,
    settings: Settings,
    now: datetime,
) -> _Verdict | None:
    """Deux lectures d'une même aubaine, essayées dans l'ordre où elles font autorité.

    La comparaison dans le temps est celle du design, et reste la plus sûre : elle mesure une
    baisse réelle. Mais elle exige deux semaines d'historique, et le balayage lui fait comparer
    des dates de départ différentes d'un passage à l'autre — sur un horizon ouvert, ses points
    ne mesurent plus la même chose. L'éventail des dates prend alors le relais : il se lit à
    l'instant, sans historique, et la rotation n'a pas de prise sur lui.
    """
    deja = repo.exception_already_sent(session, route.id, observation.offer_hash)

    historique = repo.daily_low_history(
        session, route.id, before_day=now.date(), window_days=settings.history_window_days
    )
    contexte = PriceContext(daily_lows=historique)
    if is_exception(
        price_cad=observation.price_cad,
        context=contexte,
        threshold=route.exception_threshold,
        min_history_days=settings.min_history_days,
        credibility_floor=settings.credibility_floor_cad,
        already_alerted=deja,
    ):
        return _Verdict(
            median_price=contexte.median_price or 0.0,
            history_days=contexte.days_of_history,
            calendar_dates=None,
        )

    eventail = repo.calendar_floor_prices(
        session, route.id, since=now - timedelta(days=FRAICHEUR_RELEVE_J)
    )
    if is_calendar_exception(
        price_cad=observation.price_cad,
        calendar_prices=eventail,
        threshold=route.exception_threshold,
        credibility_floor=settings.credibility_floor_cad,
        already_alerted=deja,
    ):
        return _Verdict(
            median_price=median(eventail),
            history_days=contexte.days_of_history,
            calendar_dates=len(eventail),
        )

    return None


def _traiter_exception(
    session: Session,
    route: Route,
    observation: Observation,
    settings: Settings,
    mailer: Mailer,
    now: datetime,
) -> bool:
    verdict = _juger_exception(session, route, observation, settings, now)
    if verdict is None:
        return False

    mediane = verdict.median_price
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
            history_days=verdict.history_days,
            stops=observation.stops,
            duration_minutes=observation.duration_minutes,
            calendar_dates=verdict.calendar_dates,
        )
    )

    try:
        mailer.send(courriel, settings.alert_to)
    except Exception as erreur:  # noqa: BLE001 — un SMTP en panne ne doit pas coûter les données
        logger.error("alerte non envoyée, sera retentée au prochain passage : %s", erreur)
        # Pas de validation ici : rien d'irréversible n'a eu lieu, et le passage reste une unité de
        # travail. La trace part avec le commit de fin de scan.
        repo.record_notify_failure(session, str(erreur), now)
        return False

    repo.record_notify_success(session, now)
    repo.record_alert(
        session,
        route.id,
        observation.id,
        AlertKind.EXCEPTION,
        {"offer_hash": observation.offer_hash, "price_cad": observation.price_cad},
        now,
    )
    # Validée sur-le-champ, sans attendre la fin du passage : le courriel est parti, et une trace
    # annulée par un incident ultérieur ferait repartir la même alerte au prochain scan.
    session.commit()

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
        autres_dates=_dates_alternatives(session, route.id, ligne.price_cad, observation, now),
    )


def _dates_alternatives(
    session: Session,
    route_id: int,
    prix_mis_en_avant: int,
    observation: Observation | None,
    now: datetime,
) -> tuple[DateAlternative, ...]:
    """Les meilleures autres dates de départ relevées pour ce trajet.

    La date déjà mise en avant est retirée : la répéter occuperait une ligne pour ne rien
    apprendre. Les dates restent classées par prix, si bien qu'une alternative plus chère que
    le prix du jour ne figure ici que faute de moins chère — ce que l'écart affiché montre.
    """
    candidates = repo.best_by_departure_date(
        session,
        route_id,
        since=now - timedelta(days=FRAICHEUR_RELEVE_J),
        # Une de plus que le nombre affiché, pour que le retrait de la date déjà montrée
        # ne réduise pas la liste.
        limit=ALTERNATIVES_DIGEST + 1,
    )
    deja_montree = observation.departure_date if observation else None

    return tuple(
        DateAlternative(
            depart_date=candidate.departure_date,
            price_cad=candidate.price_cad,
            provider=candidate.provider,
            ecart_cad=candidate.price_cad - prix_mis_en_avant,
        )
        for candidate in candidates
        if candidate.departure_date != deja_montree
    )[:ALTERNATIVES_DIGEST]


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
        repo.record_notify_failure(session, str(erreur), now)
        session.commit()
        return False

    repo.record_notify_success(session, now)
    repo.record_alert(
        session,
        route_id=0,
        observation_id=None,
        kind=AlertKind.DIGEST,
        payload={"find_count": donnees.find_count, "routes": len(donnees.blocks)},
        at=now,
    )
    session.commit()
    return True


def purge_old_data(session: Session, settings: Settings, now: datetime) -> int:
    supprimees = repo.purge_observations(session, now, settings.retention_days)
    session.commit()
    logger.info("purge : %s observations supprimées", supprimees)
    return supprimees
