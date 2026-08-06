from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from scrappervol.core.airports import chercher
from scrappervol.core.types import DatePolicyKind, TripType
from scrappervol.detection.rules import (
    SEUIL_SOURCE_MUETTE_H,
    PriceContext,
    is_find,
    relative_gap,
)
from scrappervol.live.search import SearchRun
from scrappervol.notify.mailer import build_mailer, defaut_de_configuration
from scrappervol.notify.render import render_test
from scrappervol.storage import repo
from scrappervol.storage.models import ProviderHealth, Route
from scrappervol.web.app import get_now, get_session, templates
from scrappervol.web.charts import sparkline_points
from scrappervol.web.forms import RouteFormError, validate_route_form, validate_search_form

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


@dataclass
class EtatCourriel:
    """Vue du canal de sortie pour la page de santé.

    `defaut_configuration` et `dernière erreur` disent deux choses différentes : l'un se règle dans
    le `.env`, l'autre se diagnostique. Les confondre enverrait chercher une panne réseau là où il
    n'y a qu'un fichier d'exemple jamais rempli.
    """

    defaut_configuration: str | None
    destinataire: str
    last_success_at: datetime | None
    last_failure_at: datetime | None
    consecutive_failures: int
    last_error: str | None

    @property
    def utilisable(self) -> bool:
        return self.defaut_configuration is None

    @property
    def en_panne(self) -> bool:
        return self.utilisable and self.consecutive_failures > 0


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


@dataclass
class LigneOffre:
    """Une offre telle qu'affichée : l'offre, sa place dans la recherche, son écart à la médiane."""

    offre: object
    meilleur: bool
    gap_vs_median: float | None


def _mediane_surveillee(
    session: Session, requete, settings, jour
) -> float | None:
    """Médiane d'un trajet surveillé couvrant la même liaison, s'il en existe un.

    Une recherche libre n'a aucun historique propre : sans trajet surveillé sur la même liaison,
    il n'y a pas de médiane et donc pas d'écart à afficher. Mieux vaut ne rien montrer qu'un
    pourcentage calculé sur trois relevés, qui donnerait une fausse assurance.
    """
    for trajet in repo.active_routes(session):
        if requete.origin not in trajet.origins or requete.destination not in trajet.destinations:
            continue
        historique = repo.daily_low_history(
            session, trajet.id, before_day=jour, window_days=settings.history_window_days
        )
        contexte = PriceContext(daily_lows=historique)
        if contexte.has_significant_history(settings.min_history_days):
            return contexte.median_price
    return None


def _contexte_resultats(
    request: Request, session: Session, run: SearchRun, maintenant: datetime
) -> dict:
    settings = request.app.state.settings
    mediane = _mediane_surveillee(session, run.query, settings, maintenant.date())
    offres = run.offres
    lignes = [
        LigneOffre(
            offre=offre,
            meilleur=index == 0 and len(offres) > 1,
            gap_vs_median=relative_gap(offre.price_cad, mediane) if mediane else None,
        )
        for index, offre in enumerate(offres)
    ]
    return {"run": run, "lignes": lignes, "mediane": mediane}


@router.get("/", response_class=HTMLResponse)
def accueil(
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008 — idiome FastAPI standard
    maintenant: datetime = Depends(get_now),  # noqa: B008
) -> HTMLResponse:
    settings = request.app.state.settings
    trajets = session.exec(select(Route).order_by(Route.id)).all()
    lignes = [_ligne(session, trajet, settings, maintenant) for trajet in trajets]
    return templates.TemplateResponse(
        request,
        "search.html.j2",
        {"lignes": lignes, "aujourdhui": maintenant.date(), "page": "recherche"},
    )


@router.get("/airports", response_class=HTMLResponse)
def suggestions_aeroports(
    request: Request,
    q: str = Query(""),
    champ: str = Query("origin"),
    multiple: bool = Query(False),
) -> HTMLResponse:
    """Suggestions d'aéroports pour un champ de saisie.

    htmx envoie la valeur du champ sous le nom de celui-ci (`origin=montr`), pas sous un nom
    convenu ; `champ` dit donc lequel des paramètres reçus porte la saisie. `q` reste accepté
    pour interroger la route directement.

    En deçà de deux caractères, on ne propose rien : la liste serait dominée par les aéroports
    les plus desservis du monde, sans rapport avec ce que l'utilisateur est en train de taper.
    """
    saisie = request.query_params.get(champ) or q
    if multiple:
        # Un champ à codes multiples vaut « YUL, YQB, mont » : seul le dernier est en cours de
        # frappe, les précédents sont déjà choisis et ne doivent pas fausser la recherche.
        saisie = saisie.rsplit(",", 1)[-1]
    resultats = chercher(saisie) if len(saisie.strip()) >= 2 else []
    return templates.TemplateResponse(
        request,
        "_suggestions.html.j2",
        {"aeroports": resultats, "champ": champ, "multiple": multiple},
    )


@router.post("/search", response_class=HTMLResponse)
async def lancer_recherche(
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008 — idiome FastAPI standard
    maintenant: datetime = Depends(get_now),  # noqa: B008
) -> HTMLResponse:
    formulaire = await _champs_du_formulaire(request)
    try:
        requete = validate_search_form(formulaire, today=maintenant.date())
    except RouteFormError as erreur:
        return templates.TemplateResponse(
            request, "_resultats.html.j2", {"erreur": str(erreur)}, status_code=422
        )

    registre = request.app.state.search_registry
    sources = request.app.state.build_providers(request.app.state.settings)
    if not sources:
        return templates.TemplateResponse(
            request,
            "_resultats.html.j2",
            {"erreur": "aucune source activée : voir ENABLED_PROVIDERS"},
            status_code=422,
        )

    run = registre.demarrer(requete, sources)
    return templates.TemplateResponse(
        request, "_resultats.html.j2", _contexte_resultats(request, session, run, maintenant)
    )


@router.get("/search/{run_id}", response_class=HTMLResponse)
def suivre_recherche(
    request: Request,
    run_id: str,
    session: Session = Depends(get_session),  # noqa: B008 — idiome FastAPI standard
    maintenant: datetime = Depends(get_now),  # noqa: B008
) -> HTMLResponse:
    run = request.app.state.search_registry.get(run_id)
    if run is None:
        # Recherche expirée ou processus redémarré : le fragment le dit et cesse de se rafraîchir.
        return templates.TemplateResponse(
            request, "_resultats.html.j2", {"erreur": "cette recherche a expiré"}
        )
    return templates.TemplateResponse(
        request, "_resultats.html.j2", _contexte_resultats(request, session, run, maintenant)
    )


@router.post("/search/{run_id}/watch")
def surveiller_recherche(
    request: Request,
    run_id: str,
    session: Session = Depends(get_session),  # noqa: B008 — idiome FastAPI standard
):
    run = request.app.state.search_registry.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="recherche introuvable")

    requete = run.query
    champs = validate_route_form(
        {
            "label": f"{requete.origin} → {requete.destination}",
            "origins": requete.origin,
            "destinations": requete.destination,
            "date_policy": DatePolicyKind.FIXED.value,
            "depart": requete.depart_date.isoformat(),
            "retour": requete.return_date.isoformat() if requete.return_date else "",
            "trip_type": TripType(requete.trip_type).value,
            "passengers": str(requete.passengers),
            "max_stops": "" if requete.max_stops is None else str(requete.max_stops),
        }
    )
    session.add(Route(**champs, created_at=datetime.now(UTC)))
    session.commit()
    return RedirectResponse("/routes", status_code=303)


def _contexte_sante(request: Request, session: Session, maintenant: datetime) -> dict[str, object]:
    """Contexte de la page de santé, partagé par l'affichage et par l'envoi de test."""
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

    sante_mail = repo.get_or_create_notify_health(session)
    courriel = EtatCourriel(
        defaut_configuration=defaut_de_configuration(settings),
        destinataire=settings.alert_to,
        last_success_at=sante_mail.last_success_at,
        last_failure_at=sante_mail.last_failure_at,
        consecutive_failures=sante_mail.consecutive_failures,
        last_error=sante_mail.last_error,
    )

    return {"sources": sources, "courriel": courriel, "page": "sante"}


@router.get("/health", response_class=HTMLResponse)
def health(
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008 — idiome FastAPI standard
    maintenant: datetime = Depends(get_now),  # noqa: B008
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "health.html.j2", _contexte_sante(request, session, maintenant)
    )


@router.post("/health/test-courriel", response_class=HTMLResponse)
def tester_courriel(
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008 — idiome FastAPI standard
    maintenant: datetime = Depends(get_now),  # noqa: B008
) -> HTMLResponse:
    """Envoie un courriel de vérification et rend compte du résultat sur la page.

    Le compte rendu vaut autant que l'envoi : sans lui, la seule façon de savoir si la chaîne
    fonctionne était d'attendre une aubaine, c'est-à-dire de découvrir la panne au pire moment.
    """
    settings = request.app.state.settings
    defaut = defaut_de_configuration(settings)

    if defaut is not None:
        erreur = f"Envoi impossible : {defaut}."
    else:
        try:
            build_mailer(settings).send(render_test(maintenant), settings.alert_to)
        except Exception as exc:  # noqa: BLE001 — le message est destiné à l'écran, pas au code
            repo.record_notify_failure(session, str(exc), maintenant)
            erreur = f"Envoi refusé : {exc}"
        else:
            repo.record_notify_success(session, maintenant)
            erreur = None
    session.commit()

    contexte = _contexte_sante(request, session, maintenant)
    contexte["test_erreur"] = erreur
    contexte["test_reussi"] = erreur is None
    return templates.TemplateResponse(request, "health.html.j2", contexte)


@router.get("/routes", response_class=HTMLResponse)
def liste_trajets(
    request: Request,
    session: Session = Depends(get_session),  # noqa: B008 — idiome FastAPI standard
) -> HTMLResponse:
    trajets = session.exec(select(Route).order_by(Route.id)).all()
    return templates.TemplateResponse(
        request, "routes.html.j2", {"routes": trajets, "page": "trajets"}
    )


@router.get("/routes/new", response_class=HTMLResponse)
def nouveau_trajet(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "route_form.html.j2",
        {"route": None, "date_policy": "fixed", "params": {}, "erreur": None, "page": "trajets"},
    )


_CHAMPS_POLITIQUE = (
    "depart",
    "retour",
    "flex_days",
    "sejour_min",
    "sejour_max",
    "horizon_mois",
)


def _params_soumis(source) -> dict:
    """Relit les paramètres de politique déjà saisis, pour les réafficher tels quels.

    Sans cela, changer de politique dans le formulaire vide les champs déjà remplis, et une
    erreur de validation à l'édition renvoie une page dont les champs contredisent la politique
    sélectionnée.
    """
    params = {nom: valeur for nom in _CHAMPS_POLITIQUE if (valeur := source.get(nom))}
    mois = source.get("mois")
    if mois:
        params["mois"] = [m.strip() for m in mois.split(",") if m.strip()]
    return params


@router.get("/routes/policy-fields", response_class=HTMLResponse)
def champs_politique(request: Request, date_policy: str = Query(...)) -> HTMLResponse:
    if date_policy not in {p.value for p in DatePolicyKind}:
        raise HTTPException(status_code=422, detail="politique de dates inconnue")
    return templates.TemplateResponse(
        request,
        "policy_fields.html.j2",
        {"date_policy": date_policy, "params": _params_soumis(request.query_params)},
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
            "page": "trajets",
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
                "params": _params_soumis(formulaire),
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
                # Les paramètres viennent du formulaire soumis, pas de la base : sinon la
                # politique affichée et les champs affichés proviennent de deux états différents.
                "params": _params_soumis(formulaire),
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
