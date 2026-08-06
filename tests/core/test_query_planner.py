from datetime import UTC, date, datetime

from scrappervol.core.query_planner import plan_queries, rotation_for
from scrappervol.core.types import DatePolicyKind, RoutePolicy, TripType

AUJOURDHUI = date(2026, 8, 4)


def _politique(**surcharges) -> RoutePolicy:
    base = {
        "origins": ["YUL"],
        "destinations": ["CDG"],
        "date_policy": DatePolicyKind.FIXED,
        "policy_params": {"depart": "2027-03-12", "retour": "2027-03-22", "flex_days": 3},
        "trip_type": TripType.ROUND_TRIP,
        "passengers": 1,
        "max_stops": None,
    }
    return RoutePolicy(**{**base, **surcharges})


def test_fixed_produit_une_requete_avec_les_dates_exactes():
    requetes = plan_queries(_politique(), today=AUJOURDHUI)

    assert len(requetes) == 1
    q = requetes[0]
    assert q.origin == "YUL"
    assert q.destination == "CDG"
    assert q.depart_date == date(2027, 3, 12)
    assert q.return_date == date(2027, 3, 22)


def test_fixed_traduit_flex_days_en_fenetre_calendaire():
    q = plan_queries(_politique(), today=AUJOURDHUI)[0]

    assert q.calendar_window == (date(2027, 3, 9), date(2027, 3, 15))


def test_fixed_sans_flex_days_na_pas_de_fenetre():
    politique = _politique(policy_params={"depart": "2027-03-12", "retour": "2027-03-22"})

    assert plan_queries(politique, today=AUJOURDHUI)[0].calendar_window is None


def test_aller_simple_na_pas_de_date_de_retour():
    politique = _politique(
        trip_type=TripType.ONE_WAY,
        policy_params={"depart": "2027-03-12"},
    )

    assert plan_queries(politique, today=AUJOURDHUI)[0].return_date is None


def test_produit_cartesien_des_origines_et_destinations():
    politique = _politique(origins=["YUL", "YQB"], destinations=["CDG", "ORY"])

    requetes = plan_queries(politique, today=AUJOURDHUI, max_queries=10)

    couples = {(q.origin, q.destination) for q in requetes}
    assert couples == {("YUL", "CDG"), ("YUL", "ORY"), ("YQB", "CDG"), ("YQB", "ORY")}


def test_window_sonde_chaque_mois_declare_semaine_par_semaine():
    """Un jalon par semaine, et non un seul par mois.

    Un tarif d'erreur n'existe que certains jours : ne sonder que le 15 revenait à ne
    pratiquement jamais en croiser. Les jalons sont espacés d'une semaine, ce qui correspond à
    ce qu'un battement de trois jours couvre autour de chacun.
    """
    politique = _politique(
        date_policy=DatePolicyKind.WINDOW,
        policy_params={"mois": ["2027-03", "2027-04"], "sejour_min": 8, "sejour_max": 12},
    )

    requetes = plan_queries(politique, today=AUJOURDHUI, max_queries=50)

    assert {q.depart_date.month for q in requetes} == {3, 4}

    jours_de_mars = sorted(q.depart_date.day for q in requetes if q.depart_date.month == 3)
    assert jours_de_mars == [4, 11, 18, 25, 28]

    # Aucun trou : chaque jour du mois est à moins de trois jours d'un jalon.
    for jour in range(1, 32):
        assert any(abs(jour - jalon) <= 3 for jalon in jours_de_mars)


def test_window_centre_le_depart_et_deduit_le_retour_du_sejour_moyen():
    politique = _politique(
        date_policy=DatePolicyKind.WINDOW,
        policy_params={"mois": ["2027-03"], "sejour_min": 8, "sejour_max": 12},
    )

    q = plan_queries(politique, today=AUJOURDHUI)[0]

    assert q.depart_date == date(2027, 3, 4)
    assert q.return_date == date(2027, 3, 14)
    assert q.calendar_window == (date(2027, 3, 1), date(2027, 3, 31))


def test_flexible_couvre_lhorizon_par_tranches_de_deux_mois():
    politique = _politique(
        date_policy=DatePolicyKind.FLEXIBLE,
        policy_params={"horizon_mois": 6, "sejour_min": 7, "sejour_max": 14},
    )

    mois_couverts = set()
    for rotation in range(3):
        for q in plan_queries(politique, today=AUJOURDHUI, rotation=rotation, max_queries=10):
            mois_couverts.add((q.depart_date.year, q.depart_date.month))

    assert len(mois_couverts) == 6

    # Et chaque rotation n'explore qu'une tranche. Sans cette seconde assertion, la taille
    # de tranche pourrait passer de 2 à 3 sans rien faire rougir : l'horizon finirait de
    # toute façon couvert, seulement en moins de passages. Or ce nombre règle la charge d'un
    # seul passage, et c'est en envoyant trop de requêtes d'un coup qu'on se fait bloquer.
    for rotation in range(3):
        mois_de_la_rotation = {
            (q.depart_date.year, q.depart_date.month)
            for q in plan_queries(politique, today=AUJOURDHUI, rotation=rotation, max_queries=10)
        }
        assert len(mois_de_la_rotation) == 2


def test_flexible_ne_propose_jamais_une_date_passee():
    politique = _politique(
        date_policy=DatePolicyKind.FLEXIBLE,
        policy_params={"horizon_mois": 12, "sejour_min": 7, "sejour_max": 14},
    )

    for rotation in range(6):
        for q in plan_queries(politique, today=AUJOURDHUI, rotation=rotation, max_queries=10):
            assert q.depart_date > AUJOURDHUI


def test_le_plafond_tronque_le_plan():
    politique = _politique(
        origins=["YUL", "YQB"],
        destinations=["CDG", "ORY", "BRU"],
        date_policy=DatePolicyKind.WINDOW,
        policy_params={"mois": ["2027-03", "2027-04"], "sejour_min": 8, "sejour_max": 12},
    )

    requetes = plan_queries(politique, today=AUJOURDHUI, max_queries=4)

    assert len(requetes) == 4


def test_la_rotation_fait_defiler_le_plan_tronque():
    politique = _politique(
        origins=["YUL", "YQB"],
        destinations=["CDG", "ORY", "BRU"],
        date_policy=DatePolicyKind.WINDOW,
        policy_params={"mois": ["2027-03", "2027-04"], "sejour_min": 8, "sejour_max": 12},
    )

    premier = plan_queries(politique, today=AUJOURDHUI, rotation=0, max_queries=4)
    second = plan_queries(politique, today=AUJOURDHUI, rotation=1, max_queries=4)

    assert premier != second


def test_la_rotation_finit_par_couvrir_tout_le_plan():
    politique = _politique(
        origins=["YUL", "YQB"],
        destinations=["CDG", "ORY", "BRU"],
        date_policy=DatePolicyKind.WINDOW,
        policy_params={"mois": ["2027-03", "2027-04"], "sejour_min": 8, "sejour_max": 12},
    )

    vues = set()
    for rotation in range(3):
        vues.update(plan_queries(politique, today=AUJOURDHUI, rotation=rotation, max_queries=4))

    assert len(vues) == 12


def test_flexible_avec_troncature_finit_par_couvrir_tout_lhorizon():
    """Non-régression : la troncature ne doit pas se figer sur la même moitié du plan à
    l'intérieur d'une tranche flexible, même quand `rotation` change mais reste dans la
    même tranche de deux mois.

    Soixante passages, contre vingt-quatre lorsqu'un seul jalon était sondé par mois : sonder le
    mois semaine par semaine allonge d'autant le cycle, à plafond de requêtes inchangé. Le
    compromis est assumé — une aubaine tombant le 8 novembre n'était auparavant jamais vue,
    puisque seul le 15 était interrogé. `max_queries_per_route` reste le réglage qui arbitre
    entre la longueur du cycle et la charge d'un seul passage.
    """
    politique = _politique(
        origins=["YUL", "YQB"],
        destinations=["CDG", "ORY", "BRU"],
        date_policy=DatePolicyKind.FLEXIBLE,
        policy_params={"horizon_mois": 12, "sejour_min": 7, "sejour_max": 14},
    )

    couverts = set()
    for rotation in range(60):
        for q in plan_queries(politique, today=AUJOURDHUI, rotation=rotation, max_queries=6):
            couverts.add((q.origin, q.destination, (q.depart_date.year, q.depart_date.month)))

    assert len(couverts) == 72


def test_les_contraintes_du_trajet_sont_reportees_sur_chaque_requete():
    politique = _politique(passengers=2, max_stops=1)

    q = plan_queries(politique, today=AUJOURDHUI)[0]

    assert q.passengers == 2
    assert q.max_stops == 1


def test_une_politique_fixed_deja_passee_ne_produit_rien():
    politique = _politique(policy_params={"depart": "2020-01-01", "retour": "2020-01-10"})

    assert plan_queries(politique, today=AUJOURDHUI) == []


def test_rotation_for_avance_avec_le_temps():
    debut = datetime(2026, 8, 4, 0, 0, tzinfo=UTC)
    plus_tard = datetime(2026, 8, 4, 7, 0, tzinfo=UTC)

    assert rotation_for(plus_tard) == rotation_for(debut) + 7


def test_rotation_for_est_stable_dans_la_meme_heure():
    a = datetime(2026, 8, 4, 3, 5, tzinfo=UTC)
    b = datetime(2026, 8, 4, 3, 55, tzinfo=UTC)

    assert rotation_for(a) == rotation_for(b)
