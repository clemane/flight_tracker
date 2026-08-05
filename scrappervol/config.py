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

    # Deux sources actives : Google Flights et Air Transat.
    #
    # `transat` : la page pilotée à la tâche 11 s'arrêtait à l'étape « departure », qui n'affiche
    # que le prix du vol aller (« à partir de »), pas un total aller-retour — d'où son retrait
    # d'`ENABLED_PROVIDERS` à l'époque. Depuis la tâche 21, le pilotage se poursuit jusqu'à la page
    # récapitulative `/summary`, où `.flight-container-total .price` donne le total aller-retour
    # déjà choisi (au tarif le moins cher) : `parse_summary` en tire une seule offre par relevé, au
    # même titre qu'un total Google Flights. Le piège d'origine — un prix aller-seul qui
    # deviendrait le plus bas permanent du trajet, `daily_low` étant indexé par (route_id, day)
    # sans le provider — ne s'applique plus.
    #
    # `air_canada` : source définitivement écartée. L'essai de la tâche 12 a piloté le formulaire
    # sans peine, mais chaque soumission retombait sur une page d'erreur (BKRW-DBS-999) produite
    # côté client, et l'URL de résultats directe renvoie un 403 Akamai : aucun prix n'a pu être
    # relevé. Le parcours est protégé contre l'automatisation, le contourner est hors sujet.
    # Compte rendu : docs/superpowers/notes/2026-08-05-air-canada-abandon.md
    #
    # Google Flights renvoie déjà les vols Air Canada, à un prix aller-retour agrégé donc
    # comparable — c'est cette comparabilité, pas le nombre de sources, qui fait la valeur de la
    # détection pour cette route restée hors de portée.
    enabled_providers: Annotated[list[str], NoDecode] = ["google_flights", "transat"]

    @field_validator("enabled_providers", mode="before")
    @classmethod
    def _split_providers(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
