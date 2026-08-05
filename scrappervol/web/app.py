from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import Engine
from sqlmodel import Session

from scrappervol.config import Settings
from scrappervol.live.search import SearchRegistry
from scrappervol.providers.factory import build_providers

DOSSIER_GABARITS = Path(__file__).parent / "templates"
DOSSIER_STATIQUE = Path(__file__).parent / "static"

# L'échappement est posé explicitement, et non laissé à Starlette.
#
# Starlette construit son environnement avec `select_autoescape()`, dont les extensions reconnues
# sont `.html`, `.htm` et `.xml`. Nos gabarits s'appelant `*.html.j2`, l'extension résolue est
# `j2` : l'échappement était donc *désactivé* sur toute l'interface, et un libellé de trajet
# contenant du balisage était rendu tel quel. Ici tous les gabarits sont du HTML, sans exception,
# d'où un `autoescape=True` franc plutôt que le `select_autoescape` nuancé de `notify/render.py`,
# qui doit lui composer avec des gabarits texte.
_env = Environment(
    loader=FileSystemLoader(DOSSIER_GABARITS),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
)
templates = Jinja2Templates(env=_env)


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
    application.state.search_registry = SearchRegistry()
    # Posée sur l'état plutôt qu'importée par les routes : un test peut ainsi substituer des
    # sources factices sans toucher aux vraies, qui ouvriraient un navigateur.
    application.state.build_providers = build_providers

    def _session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    application.dependency_overrides[get_session] = _session
    application.mount("/static", StaticFiles(directory=DOSSIER_STATIQUE), name="static")

    from scrappervol.web.routes import router

    application.include_router(router)
    return application
