from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, timedelta
from itertools import product

from scrappervol.core.types import DatePolicyKind, RoutePolicy, SearchQuery, TripType

_EPOQUE = datetime(1970, 1, 1, tzinfo=UTC)
_MOIS_PAR_TRANCHE = 2


def rotation_for(now: datetime) -> int:
    """Compteur horaire déterministe, servant à faire défiler un plan tronqué."""
    return int((now - _EPOQUE).total_seconds() // 3600)


def _fenetre_du_mois(annee: int, mois: int) -> tuple[date, date]:
    dernier = calendar.monthrange(annee, mois)[1]
    return date(annee, mois, 1), date(annee, mois, dernier)


# Un jalon par semaine dans le mois, plutôt qu'un seul.
#
# Les sources qui savent élargir autour d'une date le font de trois jours de part et d'autre,
# soit sept journées par jalon (voir `amplitude_flexible` dans providers/kayak.py) : des jalons
# espacés d'une semaine couvrent alors le mois sans trou. Pour les autres sources, qui
# interrogent la date telle quelle, c'est simplement quatre sondages au lieu d'un.
#
# Cela allonge le plan sans alourdir un passage : `max_queries` le tronque comme avant, et la
# rotation fait défiler la fenêtre d'un passage à l'autre. Ce qui change est la couverture dans
# le temps, pas la charge d'un seul relevé — un tarif d'erreur n'existant que certains jours,
# n'interroger que le 15 revenait à ne jamais en croiser.
_PAS_JALONS_J = 7
_PREMIER_JALON = 4


def _jalons_du_mois(annee: int, mois: int) -> list[date]:
    """Jours de départ à sonder dans un mois, du premier au dernier, espacés d'une semaine."""
    dernier = calendar.monthrange(annee, mois)[1]
    jalons = [
        date(annee, mois, jour)
        for jour in range(_PREMIER_JALON, dernier + 1, _PAS_JALONS_J)
    ]
    # La queue du mois resterait sinon hors de portée : dans un mois de 31 jours, le dernier
    # jalon tombe le 25 et son battement s'arrête au 28.
    marge = _PAS_JALONS_J // 2
    if jalons and dernier - jalons[-1].day > marge:
        jalons.append(date(annee, mois, dernier - marge))
    return jalons


def _decale_mois(reference: date, decalage: int) -> tuple[int, int]:
    total = reference.month - 1 + decalage
    return reference.year + total // 12, total % 12 + 1


def _sejour_moyen(params: dict) -> int:
    return (int(params.get("sejour_min", 7)) + int(params.get("sejour_max", 14))) // 2


def _nb_tranches(params: dict) -> int:
    horizon = int(params.get("horizon_mois", 12))
    return max(1, -(-horizon // _MOIS_PAR_TRANCHE))


def _dates_fixed(
    params: dict, trip_type: TripType
) -> list[tuple[date, date | None, tuple[date, date] | None]]:
    depart = date.fromisoformat(params["depart"])
    retour = (
        date.fromisoformat(params["retour"])
        if trip_type is TripType.ROUND_TRIP and params.get("retour")
        else None
    )
    flex = int(params.get("flex_days", 0))
    fenetre = (depart - timedelta(days=flex), depart + timedelta(days=flex)) if flex else None
    return [(depart, retour, fenetre)]


def _dates_window(
    params: dict, trip_type: TripType
) -> list[tuple[date, date | None, tuple[date, date] | None]]:
    sejour = _sejour_moyen(params)
    resultat = []
    for mois_iso in params.get("mois", []):
        annee, mois = (int(part) for part in mois_iso.split("-"))
        for depart in _jalons_du_mois(annee, mois):
            retour = depart + timedelta(days=sejour) if trip_type is TripType.ROUND_TRIP else None
            resultat.append((depart, retour, _fenetre_du_mois(annee, mois)))
    return resultat


def _dates_flexible(
    params: dict, today: date, trip_type: TripType, rotation: int
) -> list[tuple[date, date | None, tuple[date, date] | None]]:
    horizon = int(params.get("horizon_mois", 12))
    sejour = _sejour_moyen(params)
    nb_tranches = _nb_tranches(params)
    tranche = rotation % nb_tranches

    resultat = []
    for offset in range(_MOIS_PAR_TRANCHE):
        index_mois = tranche * _MOIS_PAR_TRANCHE + offset + 1
        if index_mois > horizon:
            break
        annee, mois = _decale_mois(today, index_mois)
        for depart in _jalons_du_mois(annee, mois):
            retour = depart + timedelta(days=sejour) if trip_type is TripType.ROUND_TRIP else None
            resultat.append((depart, retour, _fenetre_du_mois(annee, mois)))
    return resultat


def plan_queries(
    policy: RoutePolicy,
    today: date,
    rotation: int = 0,
    max_queries: int = 6,
) -> list[SearchQuery]:
    """Développe une intention de voyage en requêtes concrètes.

    Le plan complet est le produit cartésien des origines, des destinations et des créneaux
    de dates. Quand il dépasse `max_queries`, seule une fenêtre est retournée ; elle avance
    d'un passage à l'autre, si bien que la couverture est étalée dans le temps plutôt
    qu'amputée.

    Cette fenêtre avance au rythme des visites du créneau courant (`rotation // nb_tranches`),
    et non des rotations. Pour `FLEXIBLE`, `rotation` sélectionne d'abord une tranche de mois :
    une tranche donnée n'est revue qu'une rotation sur `nb_tranches`. Indexer la fenêtre sur
    `rotation` la ferait bondir de `nb_tranches * max_queries` entre deux visites — un pas qui
    retombe sur lui-même dès que `len(plan)` divise ce produit, et fige alors la fenêtre pour
    toujours. Avec les valeurs par défaut (6 tranches, `max_queries=6`) et 2 origines x 3
    destinations, la fenêtre resterait collée sur la même moitié du plan de chaque tranche :
    comme le produit cartésien fait varier l'origine le plus lentement, une origine sur deux
    ne serait jamais interrogée, pour aucun mois de l'horizon, sans qu'aucun compteur ni
    aucune erreur ne le signale. Pour `FIXED` et `WINDOW`, `nb_tranches` vaut 1 et ce compteur
    de passages coïncide avec `rotation`.
    """
    params = policy.policy_params or {}

    if policy.date_policy is DatePolicyKind.FIXED:
        creneaux = _dates_fixed(params, policy.trip_type)
        nb_tranches = 1
    elif policy.date_policy is DatePolicyKind.WINDOW:
        creneaux = _dates_window(params, policy.trip_type)
        nb_tranches = 1
    else:
        creneaux = _dates_flexible(params, today, policy.trip_type, rotation)
        nb_tranches = _nb_tranches(params)

    creneaux = [c for c in creneaux if c[0] > today]
    if not creneaux:
        return []

    plan = [
        SearchQuery(
            origin=origine,
            destination=destination,
            depart_date=depart,
            return_date=retour,
            passengers=policy.passengers,
            max_stops=policy.max_stops,
            trip_type=policy.trip_type,
            calendar_window=fenetre,
        )
        for origine, destination, (depart, retour, fenetre) in product(
            policy.origins, policy.destinations, creneaux
        )
    ]

    if len(plan) <= max_queries:
        return plan

    passages = rotation // nb_tranches
    debut = (passages * max_queries) % len(plan)
    doublé = plan + plan
    return doublé[debut : debut + max_queries]
