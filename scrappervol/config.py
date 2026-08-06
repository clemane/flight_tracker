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
    # `air_canada` : source rouverte le 6 août après un premier abandon. L'essai de la tâche 12
    # concluait à une protection anti-automatisation infranchissable ; la cause était plus simple
    # — le navigateur tournait sans fenêtre. Le même parcours, mené par un navigateur à fenêtre
    # (serveur X démarré par le conteneur), aboutit au récapitulatif de réservation et à son
    # total. Compte rendu : docs/superpowers/notes/2026-08-05-air-canada-abandon.md
    #
    # C'est la source la plus lente des trois (environ 80 s, contre un appel d'API pour Google
    # Flights) : elle mène un parcours de réservation complet, aller puis retour, parce que les
    # pages de résultats n'affichent que des tarifs « par personne, dans chaque sens ».
    enabled_providers: Annotated[list[str], NoDecode] = [
        "google_flights",
        "transat",
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
