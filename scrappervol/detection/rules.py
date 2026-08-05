from __future__ import annotations

from dataclasses import dataclass

from scrappervol.detection.stats import median, modified_z

SEUIL_Z_MODIFIE = -3.5

# Au-delà de ce silence, une source est considérée hors service et signalée en tête du digest.
SEUIL_SOURCE_MUETTE_H = 48


@dataclass(frozen=True, slots=True)
class PriceContext:
    """Historique des plus bas quotidiens d'un trajet, jour courant exclu."""

    daily_lows: list[int]

    @property
    def days_of_history(self) -> int:
        return len(self.daily_lows)

    @property
    def median_price(self) -> float | None:
        return median(self.daily_lows) if self.daily_lows else None

    def has_significant_history(self, min_days: int) -> bool:
        return self.days_of_history >= min_days


def relative_gap(price_cad: int, median_price: float) -> float:
    """Fraction sous la médiane. Positive quand le prix est plus bas qu'à l'ordinaire."""
    if median_price <= 0:
        return 0.0
    return (median_price - price_cad) / median_price


def is_exception(
    price_cad: int,
    context: PriceContext,
    threshold: float,
    min_history_days: int,
    credibility_floor: int,
    already_alerted: bool,
) -> bool:
    """Les quatre conditions du §6 du design, plus la condition MAD.

    L'ordre des tests suit le coût croissant : les rejets francs d'abord.
    """
    if already_alerted:
        return False
    if price_cad <= credibility_floor:
        return False
    if not context.has_significant_history(min_history_days):
        return False

    mediane = context.median_price
    if mediane is None:
        return False
    if price_cad > mediane * (1 - threshold):
        return False

    score = modified_z(price_cad, context.daily_lows)
    if score is None:
        return True
    return score <= SEUIL_Z_MODIFIE


def is_find(
    price_cad: int,
    context: PriceContext,
    target_price_cad: int | None,
    find_threshold: float,
    min_history_days: int,
    credibility_floor: int,
) -> bool:
    """Trouvaille au sens du §8 : sous la cible absolue, ou nettement sous la médiane."""
    if price_cad <= credibility_floor:
        return False
    if target_price_cad is not None and price_cad <= target_price_cad:
        return True
    if not context.has_significant_history(min_history_days):
        return False

    mediane = context.median_price
    if mediane is None:
        return False
    return relative_gap(price_cad, mediane) >= find_threshold
