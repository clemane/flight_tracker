from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    database_url: str = "sqlite:////app/data/scrappervol.db"
    data_dir: Path = Path("/app/data")
    timezone: str = "America/Toronto"

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "scrappervol@localhost"
    alert_to: str = ""

    interval_google_hours: int = 4
    interval_transat_hours: int = 6
    interval_air_canada_hours: int = 8
    digest_hour: int = 18

    exception_threshold: float = 0.40
    find_threshold: float = 0.15
    credibility_floor_cad: int = 50
    min_history_days: int = 14
    history_window_days: int = 90
    retention_days: int = 90
    max_queries_per_route: int = 6
    request_pause_min_s: int = 5
    request_pause_max_s: int = 20

    # Une seule source active, et c'est délibéré.
    #
    # `transat` : la page pilotée à la tâche 11 n'affiche que le prix du vol aller (« à partir
    # de »), pas un total aller-retour. Comme `daily_low` est indexé par (route_id, day) sans le
    # provider et ne conserve que le prix le plus bas, un prix aller-seul deviendrait le plus bas
    # permanent du trajet : médiane de référence effondrée, aubaines aller-retour indétectables,
    # le tout sans qu'aucun voyant ne passe au rouge. Le code de la source reste en place.
    #
    # `air_canada` : la source n'est pas encore écrite. Elle ne sera ajoutée ici que si l'essai de
    # la tâche 12 obtient un prix aller-retour total — l'usage visé est le voyage de vacances, où
    # un prix aller-seul n'est pas une donnée dégradée mais une donnée hors sujet.
    #
    # Google Flights renvoie déjà les vols Air Transat et Air Canada, à un prix aller-retour
    # agrégé donc comparable — c'est cette comparabilité, pas le nombre de sources, qui fait la
    # valeur de la détection.
    enabled_providers: Annotated[list[str], NoDecode] = ["google_flights"]

    @field_validator("enabled_providers", mode="before")
    @classmethod
    def _split_providers(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
