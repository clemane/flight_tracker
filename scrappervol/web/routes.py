from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from scrappervol.core.types import DatePolicyKind
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
from scrappervol.web.forms import RouteFormError, validate_route_form

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


@router.get("/routes", response_class=HTMLResponse)
def liste_trajets(
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008 — idiome FastAPI standard
) -> HTMLResponse:
    trajets = session.exec(select(Route).order_by(Route.id)).all()
    return templates.TemplateResponse(request, "routes.html.j2", {"routes": trajets})


@router.get("/routes/new", response_class=HTMLResponse)
def nouveau_trajet(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "route_form.html.j2",
        {"route": None, "date_policy": "fixed", "params": {}, "erreur": None},
    )


@router.get("/routes/policy-fields", response_class=HTMLResponse)
def champs_politique(request: Request, date_policy: str = Query(...)) -> HTMLResponse:
    if date_policy not in {p.value for p in DatePolicyKind}:
        raise HTTPException(status_code=422, detail="politique de dates inconnue")
    return templates.TemplateResponse(
        request, "policy_fields.html.j2", {"date_policy": date_policy, "params": {}}
    )


@router.get("/routes/{route_id}/edit", response_class=HTMLResponse)
def editer_trajet(
    request: Request,
    route_id: int,
    session: Session = Depends(get_session),  # noqa: B008 — idiome FastAPI standard
) -> HTMLResponse:
    trajet = session.get(Route, route_id)
    if trajet is None:
        raise HTTPException(status_code=404, detail="trajet introuvable")
    return templates.TemplateResponse(
        request,
        "route_form.html.j2",
        {
            "route": trajet,
            "date_policy": str(trajet.date_policy),
            "params": trajet.policy_params,
            "erreur": None,
        },
    )


async def _champs_du_formulaire(request: Request) -> dict:
    return dict(await request.form())


@router.post("/routes")
async def creer_trajet(
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008 — idiome FastAPI standard
):
    formulaire = await _champs_du_formulaire(request)
    try:
        champs = validate_route_form(formulaire)
    except RouteFormError as erreur:
        return templates.TemplateResponse(
            request,
            "route_form.html.j2",
            {
                "route": None,
                "date_policy": formulaire.get("date_policy", "fixed"),
                "params": {},
                "erreur": str(erreur),
            },
            status_code=422,
        )

    trajet = Route(**champs, created_at=datetime.now(UTC))
    session.add(trajet)
    session.commit()
    return RedirectResponse("/routes", status_code=303)


@router.post("/routes/{route_id}")
async def modifier_trajet(
    request: Request,
    route_id: int,
    session: Session = Depends(get_session),  # noqa: B008 — idiome FastAPI standard
):
    trajet = session.get(Route, route_id)
    if trajet is None:
        raise HTTPException(status_code=404, detail="trajet introuvable")

    formulaire = await _champs_du_formulaire(request)
    try:
        champs = validate_route_form(formulaire)
    except RouteFormError as erreur:
        return templates.TemplateResponse(
            request,
            "route_form.html.j2",
            {
                "route": trajet,
                "date_policy": formulaire.get("date_policy", "fixed"),
                "params": trajet.policy_params,
                "erreur": str(erreur),
            },
            status_code=422,
        )

    for nom, valeur in champs.items():
        setattr(trajet, nom, valeur)
    session.add(trajet)
    session.commit()
    return RedirectResponse("/routes", status_code=303)


@router.post("/routes/{route_id}/toggle")
def basculer_trajet(
    route_id: int,
    session: Session = Depends(get_session),  # noqa: B008 — idiome FastAPI standard
):
    trajet = session.get(Route, route_id)
    if trajet is None:
        raise HTTPException(status_code=404, detail="trajet introuvable")
    trajet.active = not trajet.active
    session.add(trajet)
    session.commit()
    return RedirectResponse("/routes", status_code=303)


@router.post("/routes/{route_id}/delete")
def supprimer_trajet(
    route_id: int,
    session: Session = Depends(get_session),  # noqa: B008 — idiome FastAPI standard
):
    trajet = session.get(Route, route_id)
    if trajet is None:
        raise HTTPException(status_code=404, detail="trajet introuvable")
    session.delete(trajet)
    session.commit()
    return RedirectResponse("/routes", status_code=303)
