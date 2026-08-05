from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from scrappervol.config import Settings
from scrappervol.notify.mailer import build_mailer
from scrappervol.scheduler.app import build_scheduler
from scrappervol.storage.db import create_engine_for, init_db
from scrappervol.web.app import create_app

logger = logging.getLogger(__name__)


def build_application() -> FastAPI:
    """Assemble base, ordonnanceur et interface web en une seule application."""
    settings = Settings()
    engine = create_engine_for(settings.database_url)
    init_db(engine)

    mailer = build_mailer(settings)
    ordonnanceur = build_scheduler(engine, settings, mailer)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        ordonnanceur.start()
        logger.info(
            "ScrapperVol démarré — sources : %s",
            ", ".join(settings.enabled_providers) or "aucune",
        )
        try:
            yield
        finally:
            ordonnanceur.shutdown(wait=False)
            logger.info("ScrapperVol arrêté")

    application = create_app(engine, settings, lifespan=lifespan)
    application.state.scheduler = ordonnanceur
    return application


def main() -> None:
    # La configuration du journal appartient au point d'entrée, pas à l'import du module :
    # placée au niveau module, elle s'appliquerait aussi quand la suite de tests importe
    # `scrappervol.main`, et reconfigurerait le journal de toute la suite au passage.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    import uvicorn

    uvicorn.run(build_application(), host="0.0.0.0", port=8080, log_level="info")  # noqa: S104


if __name__ == "__main__":
    main()
