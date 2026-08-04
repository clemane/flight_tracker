from __future__ import annotations

from datetime import datetime, timedelta

from scrappervol.storage.models import ProviderHealth

ECHECS_AVANT_REPOS = 3
REPOS_INITIAL_H = 1
REPOS_MAX_H = 24


def backoff_until(consecutive_failures: int, now: datetime) -> datetime | None:
    """Fin du repos imposé à une source, ou None si elle n'a pas encore assez échoué.

    Le délai double à chaque échec au-delà du seuil, plafonné à 24 h.
    """
    if consecutive_failures < ECHECS_AVANT_REPOS:
        return None
    exposant = consecutive_failures - ECHECS_AVANT_REPOS
    heures = min(REPOS_INITIAL_H * 2**exposant, REPOS_MAX_H)
    return now + timedelta(hours=heures)


def is_disabled(health: ProviderHealth, now: datetime) -> bool:
    return health.disabled_until is not None and health.disabled_until > now
