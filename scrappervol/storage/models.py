from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Column, DateTime
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, SQLModel

from scrappervol.core.types import DatePolicyKind, FlightOffer, RoutePolicy, TripType


class UTCDateTime(TypeDecorator):
    """Colonne DATETIME qui préserve le fuseau à travers SQLite.

    SQLite n'a pas de type date/heure natif : SQLAlchemy sérialise un ``datetime`` en
    chaîne en ignorant son décalage, même avec ``DateTime(timezone=True)``. Un
    ``datetime`` timezone-aware ressort donc naïf après un aller-retour en base — une
    violation silencieuse de l'invariant « aucun horodatage naïf », qu'aucune des
    valeurs stockées ni relues ne signale d'elle-même. Ce type impose l'écriture en
    UTC et ré-attache explicitement le fuseau à la lecture.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("un datetime naïf ne peut pas être persisté : fournis un tzinfo")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class AlertKind(StrEnum):
    DIGEST = "digest"
    EXCEPTION = "exception"


class Route(SQLModel, table=True):
    __tablename__ = "route"

    id: int | None = Field(default=None, primary_key=True)
    label: str
    origins: list[str] = Field(sa_column=Column(JSON), default_factory=list)
    destinations: list[str] = Field(sa_column=Column(JSON), default_factory=list)
    date_policy: DatePolicyKind = DatePolicyKind.FLEXIBLE
    policy_params: dict[str, Any] = Field(sa_column=Column(JSON), default_factory=dict)
    trip_type: TripType = TripType.ROUND_TRIP
    passengers: int = 1
    max_stops: int | None = None
    target_price_cad: int | None = None
    exception_threshold: float = 0.40
    active: bool = True
    created_at: datetime | None = Field(default=None, sa_column=Column(UTCDateTime))

    def to_policy(self) -> RoutePolicy:
        return RoutePolicy(
            origins=list(self.origins),
            destinations=list(self.destinations),
            date_policy=DatePolicyKind(self.date_policy),
            policy_params=dict(self.policy_params),
            trip_type=TripType(self.trip_type),
            passengers=self.passengers,
            max_stops=self.max_stops,
        )


class Observation(SQLModel, table=True):
    __tablename__ = "observation"

    id: int | None = Field(default=None, primary_key=True)
    route_id: int = Field(index=True)
    provider: str = Field(index=True)
    observed_at: datetime = Field(sa_column=Column(UTCDateTime, index=True, nullable=False))
    price_cad: int
    currency_original: str
    price_original: float
    departure_date: date
    return_date: date | None = None
    origin: str
    destination: str
    airline: str
    stops: int
    duration_minutes: int | None = None
    deep_link: str
    offer_hash: str = Field(index=True)
    raw: dict[str, Any] = Field(sa_column=Column(JSON), default_factory=dict)

    @classmethod
    def from_offer(cls, route_id: int, offer: FlightOffer, observed_at: datetime) -> Observation:
        return cls(
            route_id=route_id,
            provider=offer.provider,
            observed_at=observed_at,
            price_cad=offer.price_cad,
            currency_original=offer.currency_original,
            price_original=offer.price_original,
            departure_date=offer.depart_date,
            return_date=offer.return_date,
            origin=offer.origin,
            destination=offer.destination,
            airline=offer.airline,
            stops=offer.stops,
            duration_minutes=offer.duration_minutes,
            deep_link=offer.deep_link,
            offer_hash=offer.offer_hash,
            raw=dict(offer.raw),
        )


class DailyLow(SQLModel, table=True):
    __tablename__ = "daily_low"

    route_id: int = Field(primary_key=True)
    day: date = Field(primary_key=True)
    price_cad: int
    observation_id: int | None = None
    provider: str = ""


class ProviderHealth(SQLModel, table=True):
    __tablename__ = "provider_health"

    provider: str = Field(primary_key=True)
    last_success_at: datetime | None = Field(default=None, sa_column=Column(UTCDateTime))
    consecutive_failures: int = 0
    disabled_until: datetime | None = Field(default=None, sa_column=Column(UTCDateTime))
    last_error: str | None = None
    offers_last_run: int = 0


class Alert(SQLModel, table=True):
    __tablename__ = "alert"

    id: int | None = Field(default=None, primary_key=True)
    route_id: int = Field(index=True)
    observation_id: int | None = None
    kind: AlertKind
    sent_at: datetime = Field(sa_column=Column(UTCDateTime, index=True, nullable=False))
    payload: dict[str, Any] = Field(sa_column=Column(JSON), default_factory=dict)
