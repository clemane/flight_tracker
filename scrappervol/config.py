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

    # `transat` est volontairement absent : la page de résultats pilotée à la tâche 11 n'affiche
    # que le prix du vol aller (« à partir de »), pas un total aller-retour. Comme `daily_low` est
    # indexé par (route_id, day) sans le provider et ne garde que le prix le plus bas, activer
    # cette source ferait d'un prix aller-seul le plus bas permanent du trajet : la médiane de
    # référence s'effondrerait et aucune aubaine aller-retour ne serait plus jamais détectée.
    # Le code de la source reste en place, prêt à servir si le parcours est un jour poussé jusqu'au
    # prix total. Google Flights renvoie déjà les vols Air Transat, à un prix comparable.
    enabled_providers: Annotated[list[str], NoDecode] = [
        "google_flights",
        "air_canada",
    ]

    @field_validator("enabled_providers", mode="before")
    @classmethod
    def _split_providers(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
