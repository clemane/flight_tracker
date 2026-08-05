from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from scrappervol.detection.rules import (
    SEUIL_SOURCE_MUETTE_H,
    PriceContext,
    is_find,
    relative_gap,
)
from scrappervol.storage import repo
from scrappervol.storage.models import ProviderHealth, Route
from scrappervol.web.app import get_now, get_session, templates
from scrappervol.web.charts import sparkline_points

router = APIRouter()


@dataclass
class LigneTableau:
    route: Route
    price_cad: int | None
    provider: str
    median_price: float | None
    gap_vs_median: float | None
    is_find: bool
    history_building: bool
    points: str


@dataclass
class LigneSante:
    """Vue d'une source pour la page de santé.

    `is_stale` se calcule à la lecture et n'est pas une colonne. Le poser directement sur
    l'instance ProviderHealth lève `ValueError: "ProviderHealth" object has no field "is_stale"` :
    SQLModel refuse tout attribut non déclaré, que l'objet soit attaché à une session ou non.
    """

    provider: str
    last_success_at: datetime | None
    consecutive_failures: int
    offers_last_run: int
    last_error: str | None
    is_stale: bool


def _ligne(session: Session, route: Route, settings, now: datetime) -> LigneTableau:
    jour = now.date()
    ligne = repo.daily_low_for(session, route.id, jour)
    historique = repo.daily_low_history(
        session, route.id, before_day=jour, window_days=settings.history_window_days
    )
    contexte = PriceContext(daily_lows=historique)
    mediane = contexte.median_price
    prix = ligne.price_cad if ligne else None

    return LigneTableau(
        route=route,
        price_cad=prix,
        provider=ligne.provider if ligne else "",
        median_price=mediane,
        gap_vs_median=relative_gap(prix, mediane) if prix and mediane else None,
        is_find=(
            is_find(
                price_cad=prix,
                context=contexte,
                target_price_cad=route.target_price_cad,
                find_threshold=settings.find_threshold,
                min_history_days=settings.min_history_days,
                credibility_floor=settings.credibility_floor_cad,
            )
            if prix
            else False
        ),
        history_building=not contexte.has_significant_history(settings.min_history_days),
        points=sparkline_points(list(reversed(historique))),
    )


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008 — idiome FastAPI standard
    maintenant: datetime = Depends(get_now),  # noqa: B008
) -> HTMLResponse:
    settings = request.app.state.settings
    trajets = session.exec(select(Route).order_by(Route.id)).all()
    lignes = [_ligne(session, trajet, settings, maintenant) for trajet in trajets]
    return templates.TemplateResponse(request, "dashboard.html.j2", {"lignes": lignes})


@router.get("/health", response_class=HTMLResponse)
def health(
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008 — idiome FastAPI standard
    maintenant: datetime = Depends(get_now),  # noqa: B008
) -> HTMLResponse:
    settings = request.app.state.settings

    sources = []
    for nom in settings.enabled_providers:
        sante = session.get(ProviderHealth, nom) or ProviderHealth(provider=nom)
        heures = (
            (maintenant - sante.last_success_at).total_seconds() / 3600
            if sante.last_success_at
            else None
        )
        sources.append(
            LigneSante(
                provider=sante.provider,
                last_success_at=sante.last_success_at,
                consecutive_failures=sante.consecutive_failures,
                offers_last_run=sante.offers_last_run,
                last_error=sante.last_error,
                is_stale=heures is None or heures > SEUIL_SOURCE_MUETTE_H,
            )
        )

    return templates.TemplateResponse(request, "health.html.j2", {"sources": sources})
