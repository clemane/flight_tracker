from __future__ import annotations

from typing import Protocol, runtime_checkable

from scrappervol.core.types import FlightOffer, SearchQuery


class ProviderError(Exception):
    """Échec d'une source. Toute exception d'un scraper doit être traduite en celle-ci."""


class EmptyResultError(ProviderError):
    """Zéro offre là où la veille en produisait : traité comme un échec, pas comme un succès."""


@runtime_checkable
class PriceProvider(Protocol):
    name: str

    def search(self, query: SearchQuery) -> list[FlightOffer]: ...
