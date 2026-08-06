from __future__ import annotations

import logging

from scrappervol.config import Settings
from scrappervol.providers.base import PriceProvider

logger = logging.getLogger(__name__)


def build_providers(settings: Settings) -> list[PriceProvider]:
    """Instancie les sources activées. Une source inconnue ou non importable est ignorée.

    Vit ici, et non dans l'ordonnanceur, parce que deux appelants s'en servent désormais : le
    câblage des passages périodiques, et la recherche à la demande de l'interface.
    """
    sources: list[PriceProvider] = []
    for nom in settings.enabled_providers:
        try:
            if nom == "google_flights":
                from scrappervol.providers.google_flights import GoogleFlightsProvider

                # Cette source ne prend pas de réglages : elle n'a pas d'__init__ et tout ce
                # dont elle a besoin lui arrive par la SearchQuery.
                sources.append(GoogleFlightsProvider())
            elif nom == "transat":
                from scrappervol.providers.transat import TransatProvider

                sources.append(TransatProvider(settings))
            elif nom == "air_canada":
                from scrappervol.providers.air_canada import AirCanadaProvider

                sources.append(AirCanadaProvider(settings))
            else:
                logger.warning("source inconnue ignorée : %s", nom)
        except ImportError as erreur:
            logger.warning("source %s non importable, ignorée : %s", nom, erreur)
    return sources
