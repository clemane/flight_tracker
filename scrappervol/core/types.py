from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum


class TripType(StrEnum):
    ROUND_TRIP = "round_trip"
    ONE_WAY = "one_way"


class DatePolicyKind(StrEnum):
    FIXED = "fixed"
    WINDOW = "window"
    FLEXIBLE = "flexible"


def compute_offer_hash(
    *,
    provider: str,
    origin: str,
    destination: str,
    depart_date: date,
    return_date: date | None,
    airline: str,
    stops: int,
) -> str:
    graine = "|".join(
        [
            provider,
            origin,
            destination,
            depart_date.isoformat(),
            return_date.isoformat() if return_date else "",
            airline,
            str(stops),
        ]
    )
    return hashlib.sha256(graine.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class SearchQuery:
    origin: str
    destination: str
    depart_date: date
    return_date: date | None = None
    passengers: int = 1
    max_stops: int | None = None
    trip_type: TripType = TripType.ROUND_TRIP
    calendar_window: tuple[date, date] | None = None


@dataclass(frozen=True, slots=True)
class FlightOffer:
    provider: str
    origin: str
    destination: str
    depart_date: date
    return_date: date | None
    price_cad: int
    price_original: float
    currency_original: str
    airline: str
    stops: int
    duration_minutes: int | None
    deep_link: str
    raw: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def offer_hash(self) -> str:
        return compute_offer_hash(
            provider=self.provider,
            origin=self.origin,
            destination=self.destination,
            depart_date=self.depart_date,
            return_date=self.return_date,
            airline=self.airline,
            stops=self.stops,
        )


@dataclass(frozen=True, slots=True)
class RoutePolicy:
    origins: list[str]
    destinations: list[str]
    date_policy: DatePolicyKind
    policy_params: dict
    trip_type: TripType = TripType.ROUND_TRIP
    passengers: int = 1
    max_stops: int | None = None
