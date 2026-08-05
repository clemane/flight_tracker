from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine
from sqlmodel import Session

from scrappervol.config import Settings

DOSSIER_GABARITS = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(DOSSIER_GABARITS))


def get_session() -> Iterator[Session]:  # pragma: no cover — surchargé en test et à la création
    raise RuntimeError("dépendance de session non configurée")


def get_now() -> datetime:
    """Horloge injectable.

    Les pages comparent le jour courant aux plus bas quotidiens et l'âge du dernier succès au
    seuil de 48 h. Lire `datetime.now` au fond d'une route rendrait ces tests vrais le jour de
    leur écriture et faux la semaine suivante.
    """
    return datetime.now(UTC)


def create_app(engine: Engine, settings: Settings, lifespan: Callable | None = None) -> FastAPI:
    """Construit l'application web.

    `lifespan` sert au point d'entrée pour y greffer l'ordonnanceur.
    """
    application = FastAPI(title="ScrapperVol", docs_url=None, redoc_url=None, lifespan=lifespan)
    application.state.settings = settings
    application.state.engine = engine

    def _session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    application.dependency_overrides[get_session] = _session

    from scrappervol.web.routes import router

    application.include_router(router)
    return application
