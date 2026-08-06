from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlmodel import Session

from scrappervol.config import Settings
from scrappervol.core.query_planner import plan_queries, rotation_for
from scrappervol.core.types import FlightOffer
from scrappervol.providers.base import PriceProvider
from scrappervol.providers.health import backoff_until, is_disabled
from scrappervol.storage import repo

logger = logging.getLogger(__name__)

# Combien de requêtes une source consacre à un trajet par passage, quand son coût le permet.
#
# Le plafond commun de `max_queries_per_route` était calibré sur les sources qui pilotent un
# navigateur : Air Transat et Air Canada ouvrent Chromium, remplissent un formulaire et attendent
# un parcours complet, soit une minute et plus par requête. Google Flights ne fait qu'un appel
# HTTP de quelques secondes. Les brider au même chiffre revenait à régler la source la moins
# coûteuse sur les contraintes de la plus lourde.
#
# L'enjeu est la vitesse de couverture, non le volume : le planificateur fait tourner les
# combinaisons de dates d'un passage à l'autre, si bien que le plafond fixe en combien de temps
# un trajet flexible est entièrement sondé. Mesuré sur un horizon de douze mois, doubler ce
# plafond ramène le cycle complet de 59 à 29 rotations — de dix jours à cinq.
#
# Kayak reste au plafond commun malgré son faible coût : il balaye déjà les jours voisins de
# chaque date demandée, et couvre donc sept fois plus de terrain par requête.
PLAFONDS_PAR_SOURCE = {"google_flights": 12}


def plafond_de(provider: str, settings: Settings) -> int:
    return PLAFONDS_PAR_SOURCE.get(provider, settings.max_queries_per_route)


@dataclass
class RunReport:
    provider: str
    offers_by_route: dict[int, list[FlightOffer]] = field(default_factory=dict)
    queries_run: int = 0
    failed: bool = False
    error: str | None = None
    skipped: bool = False


def run_provider(
    session: Session,
    provider: PriceProvider,
    settings: Settings,
    now: datetime,
    sleeper: Callable[[float], None] = time.sleep,
) -> RunReport:
    """Interroge une source sur tous les trajets actifs, sans jamais laisser échapper d'exception.

    Le contrat est strict : quoi qu'il arrive dans le scraper, cette fonction retourne un rapport.
    C'est ce qui garantit qu'une source cassée n'emporte pas les deux autres.

    L'état de santé est validé ici, et non laissé à l'appelant : c'est lui qui porte le compte des
    échecs consécutifs et la mise au repos. Le confier à une transaction plus large reviendrait à
    le perdre en cas d'incident ultérieur — c'est-à-dire précisément quand il compte, une source
    en panne devant rester marquée en panne au passage suivant.
    """
    rapport = RunReport(provider=provider.name)
    sante = repo.get_or_create_health(session, provider.name)

    if is_disabled(sante, now):
        rapport.skipped = True
        logger.info("source %s au repos jusqu'à %s", provider.name, sante.disabled_until)
        return rapport

    produisait_avant = sante.offers_last_run > 0
    rotation = rotation_for(now)
    premiere_requete = True

    try:
        for trajet in repo.active_routes(session):
            requetes = plan_queries(
                trajet.to_policy(),
                today=now.date(),
                rotation=rotation,
                max_queries=plafond_de(provider.name, settings),
            )
            for requete in requetes:
                if not premiere_requete:
                    sleeper(
                        random.uniform(settings.request_pause_min_s, settings.request_pause_max_s)
                    )
                else:
                    sleeper(0)
                premiere_requete = False

                offres = provider.search(requete)
                rapport.queries_run += 1
                if offres:
                    rapport.offers_by_route.setdefault(trajet.id, []).extend(offres)
    except Exception as erreur:  # noqa: BLE001 — l'isolation est le but de cette fonction
        rapport.failed = True
        rapport.error = f"{type(erreur).__name__}: {erreur}"
        logger.warning("échec de la source %s : %s", provider.name, rapport.error)
        repo.record_provider_failure(
            session,
            provider.name,
            rapport.error,
            now,
            backoff_until(sante.consecutive_failures + 1, now),
        )
        session.commit()
        return rapport

    total = sum(len(offres) for offres in rapport.offers_by_route.values())

    if total == 0 and produisait_avant and rapport.queries_run > 0:
        rapport.failed = True
        rapport.error = "aucune offre alors que le passage précédent en produisait"
        repo.record_provider_failure(
            session,
            provider.name,
            rapport.error,
            now,
            backoff_until(sante.consecutive_failures + 1, now),
        )
        session.commit()
        return rapport

    repo.record_provider_success(session, provider.name, total, now)
    session.commit()
    return rapport
