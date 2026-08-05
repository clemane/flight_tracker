from __future__ import annotations

import re
from datetime import date

from scrappervol.core.query_planner import plan_queries
from scrappervol.core.types import DatePolicyKind, RoutePolicy, SearchQuery, TripType

_SEPARATEURS = re.compile(r"[,\s;]+")
_CODE_IATA = re.compile(r"^[A-Z]{3}$")


class RouteFormError(ValueError):
    """Saisie invalide dans le formulaire de trajet."""


def parse_airports(text: str) -> list[str]:
    """Codes IATA normalisés.

    La forme est vérifiée ici parce qu'un code fantaisiste ne casse rien de visible : la source
    répond « aucun vol », le trajet reste vert sur le tableau de bord et l'on croit surveiller un
    voyage qui n'est jamais interrogé.
    """
    codes = [code.strip().upper() for code in _SEPARATEURS.split(text or "") if code.strip()]
    invalides = [code for code in codes if not _CODE_IATA.match(code)]
    if invalides:
        raise RouteFormError(
            f"code d'aéroport attendu sur trois lettres, reçu : {', '.join(invalides)}"
        )
    return codes


def _entier(valeur: str | None, defaut: int | None = None) -> int | None:
    if valeur is None or str(valeur).strip() == "":
        return defaut
    try:
        return int(valeur)
    except ValueError as erreur:
        raise RouteFormError(f"nombre attendu, reçu « {valeur} »") from erreur


def build_policy_params(date_policy: DatePolicyKind, form: dict) -> dict:
    aller_retour = TripType(form.get("trip_type") or TripType.ROUND_TRIP) is TripType.ROUND_TRIP

    if date_policy is DatePolicyKind.FIXED:
        depart = (form.get("depart") or "").strip()
        if not depart:
            raise RouteFormError("une politique à dates fixes exige une date de départ")
        retour = (form.get("retour") or "").strip()
        if aller_retour and not retour:
            # `_dates_fixed` pose `retour = None` dès que la clé manque, sans regarder le
            # `trip_type` : le planificateur produirait une requête d'aller simple et son prix
            # deviendrait le plus bas du trajet. C'est la panne constatée sur Air Transat,
            # atteignable ici d'un simple champ laissé vide.
            raise RouteFormError(
                "un aller-retour exige une date de retour ; sans elle le prix relevé serait "
                "celui d'un aller simple"
            )
        params: dict = {"depart": depart}
        if retour:
            params["retour"] = retour
        flex = _entier(form.get("flex_days"), 0)
        if flex:
            params["flex_days"] = flex
        return params

    sejour_min = _entier(form.get("sejour_min"), 7) or 7
    sejour_max = _entier(form.get("sejour_max"), 14) or 14
    if sejour_min < 1 or sejour_max < 1:
        raise RouteFormError("un séjour se compte en nuits : au moins une")
    if sejour_min > sejour_max:
        raise RouteFormError("le séjour minimal dépasse le séjour maximal")

    if date_policy is DatePolicyKind.WINDOW:
        mois = [m.strip() for m in _SEPARATEURS.split(form.get("mois") or "") if m.strip()]
        if not mois:
            raise RouteFormError("une politique par fenêtre exige au moins un mois")
        return {"mois": mois, "sejour_min": sejour_min, "sejour_max": sejour_max}

    horizon = _entier(form.get("horizon_mois"), 12) or 12
    return {"horizon_mois": horizon, "sejour_min": sejour_min, "sejour_max": sejour_max}


def validate_route_form(form: dict) -> dict:
    label = (form.get("label") or "").strip()
    if not label:
        raise RouteFormError("le libellé est obligatoire")

    origines = parse_airports(form.get("origins", ""))
    destinations = parse_airports(form.get("destinations", ""))
    if not origines:
        raise RouteFormError("au moins une origine est requise")
    if not destinations:
        raise RouteFormError("au moins une destination est requise")

    politique = DatePolicyKind(form.get("date_policy") or DatePolicyKind.FLEXIBLE)

    seuil = form.get("exception_threshold")
    seuil = 0.40 if seuil in (None, "") else float(seuil)
    if not 0 < seuil < 1:
        raise RouteFormError("le seuil d'exception doit être strictement compris entre 0 et 1")

    return {
        "label": label,
        "origins": origines,
        "destinations": destinations,
        "date_policy": politique,
        "policy_params": build_policy_params(politique, form),
        "trip_type": TripType(form.get("trip_type") or TripType.ROUND_TRIP),
        "passengers": _entier(form.get("passengers"), 1) or 1,
        "max_stops": _entier(form.get("max_stops"), None),
        "target_price_cad": _entier(form.get("target_price_cad"), None),
        "exception_threshold": seuil,
    }


def _un_seul_aeroport(texte: str, champ: str) -> str:
    codes = parse_airports(texte)
    if not codes:
        raise RouteFormError(f"{champ} est obligatoire")
    if len(codes) > 1:
        raise RouteFormError(f"un seul aéroport pour {champ}, reçu : {', '.join(codes)}")
    return codes[0]


def validate_search_form(form: dict, today: date) -> SearchQuery:
    """Traduit le formulaire de recherche en une requête concrète.

    La construction passe par `plan_queries` plutôt que par un `SearchQuery(...)` direct : c'est
    le même chemin que celui d'un trajet surveillé, donc les mêmes règles de dates s'appliquent,
    et une recherche ne peut pas produire une requête qu'un relevé automatique refuserait.
    """
    origine = _un_seul_aeroport(form.get("origin", ""), "l'origine")
    destination = _un_seul_aeroport(form.get("destination", ""), "la destination")
    if origine == destination:
        raise RouteFormError("l'origine et la destination sont identiques")

    type_voyage = TripType(form.get("trip_type") or TripType.ROUND_TRIP)
    params = build_policy_params(DatePolicyKind.FIXED, {**form, "trip_type": type_voyage})

    requetes = plan_queries(
        RoutePolicy(
            origins=[origine],
            destinations=[destination],
            date_policy=DatePolicyKind.FIXED,
            policy_params=params,
            trip_type=type_voyage,
            passengers=_entier(form.get("passengers"), 1) or 1,
            max_stops=_entier(form.get("max_stops"), None),
        ),
        today=today,
    )
    if not requetes:
        # `plan_queries` écarte les créneaux qui ne sont pas dans le futur : sans ce message,
        # une date passée rendrait une recherche vide, impossible à distinguer d'un vol complet.
        raise RouteFormError("la date de départ doit être postérieure à aujourd'hui")

    requete = requetes[0]
    if requete.return_date is not None and requete.return_date < requete.depart_date:
        raise RouteFormError("le retour précède le départ")
    return requete
