"""Accès aux données.

Aucune fonction de ce module ne valide la transaction : elles écrivent (`flush`) pour que les
identifiants soient attribués et que les lectures suivantes voient les données, mais la décision
de valider appartient à l'appelant, qui seul sait ce qui forme une unité de travail cohérente.

Un passage de scan enregistre des observations, met à jour le plus bas du jour et pose une trace
d'alerte : validé étape par étape, un incident au milieu laissait un état bancal — des
observations sans le plus bas qu'elles justifiaient — qu'aucun passage ultérieur ne rattrapait.
La règle a une exception assumée, du côté de l'appelant : ce qui suit l'envoi d'un courriel est
validé immédiatement, un message parti ne pouvant pas être défait par un `rollback`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlmodel import Session, col, delete, select

from scrappervol.core.types import FlightOffer
from scrappervol.storage.models import (
    Alert,
    AlertKind,
    DailyLow,
    NotifyHealth,
    Observation,
    ProviderHealth,
    Route,
)


def active_routes(session: Session) -> list[Route]:
    return list(session.exec(select(Route).where(col(Route.active).is_(True))).all())


def record_observations(
    session: Session,
    route_id: int,
    offers: Sequence[FlightOffer],
    observed_at: datetime,
) -> list[Observation]:
    """Persiste un lot d'offres, en ne gardant que la moins chère par `offer_hash`."""
    meilleures: dict[str, FlightOffer] = {}
    for offre in offers:
        connue = meilleures.get(offre.offer_hash)
        if connue is None or offre.price_cad < connue.price_cad:
            meilleures[offre.offer_hash] = offre

    observations = [
        Observation.from_offer(route_id, offre, observed_at) for offre in meilleures.values()
    ]
    for observation in observations:
        session.add(observation)
    session.flush()
    for observation in observations:
        session.refresh(observation)
    return observations


def upsert_daily_low(
    session: Session,
    route_id: int,
    day: date,
    observation: Observation,
) -> DailyLow | None:
    """Écrase le plus bas du jour si l'observation est strictement meilleure.

    Retourne `None` si le prix existant est déjà inférieur ou égal : le plus bas du
    jour ne doit jamais remonter, sous peine de faire dériver vers le haut la base de
    comparaison sur laquelle repose la détection d'aubaines.
    """
    existante = session.get(DailyLow, (route_id, day))
    if existante is not None and existante.price_cad <= observation.price_cad:
        return None

    ligne = existante or DailyLow(route_id=route_id, day=day, price_cad=observation.price_cad)
    ligne.price_cad = observation.price_cad
    ligne.observation_id = observation.id
    ligne.provider = observation.provider
    session.add(ligne)
    session.flush()
    session.refresh(ligne)
    return ligne


def daily_low_history(
    session: Session,
    route_id: int,
    before_day: date,
    window_days: int = 90,
) -> list[int]:
    """Prix des plus bas quotidiens de la fenêtre, du plus récent au plus ancien.

    Le jour courant (`before_day`) est exclu : l'inclure ferait entrer le prix du jour
    dans sa propre médiane de comparaison et réduirait mécaniquement l'anomalie qu'il
    représente.
    """
    debut = before_day - timedelta(days=window_days)
    lignes = session.exec(
        select(DailyLow)
        .where(DailyLow.route_id == route_id)
        .where(col(DailyLow.day) < before_day)
        .where(col(DailyLow.day) >= debut)
        .order_by(col(DailyLow.day).desc())
    ).all()
    return [ligne.price_cad for ligne in lignes]


def daily_low_for(session: Session, route_id: int, day: date) -> DailyLow | None:
    return session.get(DailyLow, (route_id, day))


def best_by_departure_date(
    session: Session,
    route_id: int,
    *,
    since: datetime,
    limit: int = 12,
) -> list[Observation]:
    """Meilleure offre relevée pour chaque date de départ, la moins chère en tête.

    Un balayage sur douze mois produit des dizaines de dates de départ dont les prix varient
    du simple au double. Le plus bas quotidien les écrase en un seul chiffre : il dit combien,
    jamais pour quand. Cette vue rouvre l'éventail.

    `since` écarte les relevés périmés. Un prix vieux de trois semaines n'est plus une offre,
    c'est un souvenir, et l'afficher au même rang qu'un relevé du matin induirait en erreur.
    """
    planchers = (
        select(
            col(Observation.departure_date).label("jour"),
            func.min(col(Observation.price_cad)).label("plancher"),
        )
        .where(Observation.route_id == route_id)
        .where(col(Observation.observed_at) >= since)
        .group_by(col(Observation.departure_date))
        .subquery()
    )

    lignes = session.exec(
        select(Observation)
        .join(
            planchers,
            (col(Observation.departure_date) == planchers.c.jour)
            & (col(Observation.price_cad) == planchers.c.plancher),
        )
        .where(Observation.route_id == route_id)
        .where(col(Observation.observed_at) >= since)
        .order_by(col(Observation.price_cad), col(Observation.observed_at).desc())
    ).all()

    # Deux relevés peuvent toucher le même plancher pour une même date, sur deux sources ou
    # à deux heures : garder le plus récent, que le tri ci-dessus a déjà placé en premier.
    retenues: dict[date, Observation] = {}
    for ligne in lignes:
        retenues.setdefault(ligne.departure_date, ligne)
    return sorted(retenues.values(), key=lambda o: o.price_cad)[:limit]


def purge_observations(session: Session, now: datetime, retention_days: int = 90) -> int:
    """Supprime les observations antérieures à la fenêtre de rétention.

    Ne touche jamais aux `DailyLow` : ce sont eux la mémoire longue du système, et la
    détection d'aubaines a besoin d'un historique dépassant largement la rétention des
    observations brutes.
    """
    limite = now - timedelta(days=retention_days)
    resultat = session.exec(delete(Observation).where(col(Observation.observed_at) < limite))
    session.flush()
    return int(resultat.rowcount or 0)


def get_or_create_health(session: Session, provider: str) -> ProviderHealth:
    sante = session.get(ProviderHealth, provider)
    if sante is None:
        sante = ProviderHealth(provider=provider)
        session.add(sante)
        session.flush()
        session.refresh(sante)
    return sante


def record_provider_success(
    session: Session, provider: str, offers_count: int, at: datetime
) -> ProviderHealth:
    sante = get_or_create_health(session, provider)
    sante.last_success_at = at
    sante.consecutive_failures = 0
    sante.disabled_until = None
    sante.last_error = None
    sante.offers_last_run = offers_count
    session.add(sante)
    session.flush()
    session.refresh(sante)
    return sante


def record_provider_failure(
    session: Session,
    provider: str,
    error: str,
    at: datetime,
    disabled_until: datetime | None,
) -> ProviderHealth:
    sante = get_or_create_health(session, provider)
    sante.consecutive_failures += 1
    sante.last_error = error
    sante.disabled_until = disabled_until
    sante.offers_last_run = 0
    session.add(sante)
    session.flush()
    session.refresh(sante)
    return sante


CANAL_COURRIEL = "email"


def get_or_create_notify_health(session: Session, channel: str = CANAL_COURRIEL) -> NotifyHealth:
    sante = session.get(NotifyHealth, channel)
    if sante is None:
        sante = NotifyHealth(channel=channel)
        session.add(sante)
        session.flush()
        session.refresh(sante)
    return sante


def record_notify_success(
    session: Session, at: datetime, channel: str = CANAL_COURRIEL
) -> NotifyHealth:
    sante = get_or_create_notify_health(session, channel)
    sante.last_success_at = at
    sante.consecutive_failures = 0
    sante.last_error = None
    session.add(sante)
    session.flush()
    session.refresh(sante)
    return sante


def record_notify_failure(
    session: Session, error: str, at: datetime, channel: str = CANAL_COURRIEL
) -> NotifyHealth:
    """Le dernier succès est conservé : il dit depuis quand le canal est muet."""
    sante = get_or_create_notify_health(session, channel)
    sante.consecutive_failures += 1
    sante.last_failure_at = at
    sante.last_error = error
    session.add(sante)
    session.flush()
    session.refresh(sante)
    return sante


def exception_already_sent(session: Session, route_id: int, offer_hash: str) -> bool:
    """Une exception a-t-elle déjà été envoyée pour cette offre sur ce trajet ?

    Bornée à `route_id` + `offer_hash` + `AlertKind.EXCEPTION` uniquement : trop large
    (par exemple sans `route_id`) et plus aucune alerte ne part pour un trajet qui
    partage un hash avec un autre ; trop étroit (par exemple en incluant les digests) et
    la même offre finit par boucler indéfiniment.
    """
    alertes = session.exec(
        select(Alert).where(Alert.route_id == route_id).where(Alert.kind == AlertKind.EXCEPTION)
    ).all()
    return any(alerte.payload.get("offer_hash") == offer_hash for alerte in alertes)


def record_alert(
    session: Session,
    route_id: int,
    observation_id: int | None,
    kind: AlertKind,
    payload: dict[str, Any],
    at: datetime,
) -> Alert:
    alerte = Alert(
        route_id=route_id,
        observation_id=observation_id,
        kind=kind,
        sent_at=at,
        payload=payload,
    )
    session.add(alerte)
    session.flush()
    session.refresh(alerte)
    return alerte
