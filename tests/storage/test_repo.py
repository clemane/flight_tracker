from datetime import UTC, date, datetime, timedelta

from scrappervol.core.types import FlightOffer
from scrappervol.storage import repo
from scrappervol.storage.models import AlertKind, DailyLow, Observation, Route

MAINTENANT = datetime(2026, 8, 4, 14, 0, tzinfo=UTC)
AUJOURDHUI = date(2026, 8, 4)


def _offre(price_cad: int, **surcharges) -> FlightOffer:
    base = {
        "provider": "google_flights",
        "origin": "YUL",
        "destination": "CDG",
        "depart_date": date(2027, 3, 12),
        "return_date": date(2027, 3, 22),
        "price_cad": price_cad,
        "price_original": float(price_cad),
        "currency_original": "CAD",
        "airline": "Air Transat",
        "stops": 0,
        "duration_minutes": 425,
        "deep_link": "https://example.com",
        "raw": {},
    }
    return FlightOffer(**{**base, **surcharges})


def _trajet(session, **surcharges) -> Route:
    base = {"label": "Paris", "origins": ["YUL"], "destinations": ["CDG"]}
    trajet = Route(**{**base, **surcharges})
    session.add(trajet)
    session.commit()
    session.refresh(trajet)
    return trajet


def test_active_routes_ignore_les_trajets_desactives(session):
    _trajet(session)
    _trajet(session, active=False)

    assert len(repo.active_routes(session)) == 1


def test_record_observations_persiste_chaque_offre(session):
    trajet = _trajet(session)

    resultat = repo.record_observations(
        session, trajet.id, [_offre(612), _offre(700, airline="Air Canada")], MAINTENANT
    )

    assert len(resultat) == 2
    assert all(o.id is not None for o in resultat)


def test_record_observations_deduplique_dans_le_lot_en_gardant_la_moins_chere(session):
    trajet = _trajet(session)

    resultat = repo.record_observations(session, trajet.id, [_offre(612), _offre(540)], MAINTENANT)

    assert len(resultat) == 1
    assert resultat[0].price_cad == 540


def test_upsert_daily_low_cree_la_ligne_absente(session):
    trajet = _trajet(session)
    observation = repo.record_observations(session, trajet.id, [_offre(612)], MAINTENANT)[0]

    ligne = repo.upsert_daily_low(session, trajet.id, AUJOURDHUI, observation)

    assert ligne is not None
    assert ligne.price_cad == 612
    assert ligne.provider == "google_flights"


def test_upsert_daily_low_ecrase_un_prix_superieur(session):
    trajet = _trajet(session)
    haute = repo.record_observations(session, trajet.id, [_offre(612)], MAINTENANT)[0]
    repo.upsert_daily_low(session, trajet.id, AUJOURDHUI, haute)
    basse = repo.record_observations(
        session, trajet.id, [_offre(480, airline="Air France")], MAINTENANT
    )[0]

    ligne = repo.upsert_daily_low(session, trajet.id, AUJOURDHUI, basse)

    assert ligne is not None
    assert ligne.price_cad == 480


def test_upsert_daily_low_ne_remonte_jamais_un_prix(session):
    trajet = _trajet(session)
    basse = repo.record_observations(session, trajet.id, [_offre(480)], MAINTENANT)[0]
    repo.upsert_daily_low(session, trajet.id, AUJOURDHUI, basse)
    haute = repo.record_observations(
        session, trajet.id, [_offre(900, airline="Air France")], MAINTENANT
    )[0]

    assert repo.upsert_daily_low(session, trajet.id, AUJOURDHUI, haute) is None
    assert repo.daily_low_for(session, trajet.id, AUJOURDHUI).price_cad == 480


def test_upsert_daily_low_prix_egal_ne_bouge_rien(session):
    """Un prix égal n'est ni une création, ni un abaissement : `None` doit revenir, et la
    ligne existante (observation_id, provider) doit rester intacte. Un garde-fou avec `<`
    au lieu de `<=` laisserait passer ce cas silencieusement : même prix, mais la ligne
    serait réécrite et une valeur non-None reviendrait, ce qui romprait le contrat
    « retourne la ligne seulement si elle a été créée ou abaissée » sans jamais faire
    dériver le prix vers le haut — donc sans qu'aucun autre test ne le remarque.
    """
    trajet = _trajet(session)
    premiere = repo.record_observations(session, trajet.id, [_offre(480)], MAINTENANT)[0]
    repo.upsert_daily_low(session, trajet.id, AUJOURDHUI, premiere)
    egale = repo.record_observations(
        session, trajet.id, [_offre(480, airline="Air France")], MAINTENANT
    )[0]

    resultat = repo.upsert_daily_low(session, trajet.id, AUJOURDHUI, egale)

    assert resultat is None
    ligne = repo.daily_low_for(session, trajet.id, AUJOURDHUI)
    assert ligne.price_cad == 480
    assert ligne.observation_id == premiere.id
    assert ligne.provider == "google_flights"


def test_daily_low_history_retourne_la_fenetre_hors_jour_courant(session):
    trajet = _trajet(session)
    for decalage in range(5):
        session.add(
            DailyLow(
                route_id=trajet.id,
                day=AUJOURDHUI - timedelta(days=decalage),
                price_cad=500 + decalage,
                provider="google_flights",
            )
        )
    session.commit()

    historique = repo.daily_low_history(session, trajet.id, before_day=AUJOURDHUI, window_days=90)

    assert historique == [501, 502, 503, 504]


def test_daily_low_history_exclut_ce_qui_precede_la_fenetre(session):
    trajet = _trajet(session)
    session.add(
        DailyLow(
            route_id=trajet.id,
            day=AUJOURDHUI - timedelta(days=200),
            price_cad=300,
            provider="google_flights",
        )
    )
    session.add(
        DailyLow(
            route_id=trajet.id,
            day=AUJOURDHUI - timedelta(days=2),
            price_cad=500,
            provider="google_flights",
        )
    )
    session.commit()

    assert repo.daily_low_history(session, trajet.id, AUJOURDHUI, window_days=90) == [500]


def test_daily_low_history_est_vide_si_seul_le_jour_courant_existe(session):
    """Preuve ciblée et directe du point 2 : si le seul `DailyLow` connu est celui du jour
    courant, l'historique doit être vide. Un `<=` au lieu d'un `<` sur `day` ferait
    entrer le prix du jour dans sa propre médiane — l'aubaine la plus grosse serait celle
    qui paraîtrait le moins anormale.
    """
    trajet = _trajet(session)
    session.add(
        DailyLow(route_id=trajet.id, day=AUJOURDHUI, price_cad=100, provider="google_flights")
    )
    session.commit()

    assert repo.daily_low_history(session, trajet.id, AUJOURDHUI, window_days=90) == []


def test_purge_supprime_les_observations_anciennes_et_garde_daily_low(session):
    trajet = _trajet(session)
    ancienne = Observation.from_offer(trajet.id, _offre(612), MAINTENANT - timedelta(days=120))
    recente = Observation.from_offer(trajet.id, _offre(500, airline="X"), MAINTENANT)
    session.add(ancienne)
    session.add(recente)
    session.add(
        DailyLow(route_id=trajet.id, day=date(2025, 1, 1), price_cad=400, provider="google_flights")
    )
    session.commit()

    supprimees = repo.purge_observations(session, now=MAINTENANT, retention_days=90)

    assert supprimees == 1
    assert repo.daily_low_for(session, trajet.id, date(2025, 1, 1)) is not None


def test_succes_remet_le_compteur_dechecs_a_zero(session):
    repo.record_provider_failure(session, "transat", "timeout", MAINTENANT, None)
    repo.record_provider_failure(session, "transat", "timeout", MAINTENANT, None)

    sante = repo.record_provider_success(session, "transat", offers_count=12, at=MAINTENANT)

    assert sante.consecutive_failures == 0
    assert sante.last_success_at == MAINTENANT
    assert sante.offers_last_run == 12
    assert sante.disabled_until is None


def test_echec_incremente_le_compteur_et_retient_lerreur(session):
    repo.record_provider_failure(session, "transat", "premier", MAINTENANT, None)
    sante = repo.record_provider_failure(session, "transat", "second", MAINTENANT, None)

    assert sante.consecutive_failures == 2
    assert sante.last_error == "second"


def test_echec_peut_poser_une_mise_au_repos(session):
    jusqua = MAINTENANT + timedelta(hours=1)

    sante = repo.record_provider_failure(session, "transat", "bloqué", MAINTENANT, jusqua)

    assert sante.disabled_until == jusqua


def test_exception_already_sent_ne_voit_que_les_alertes_dexception(session):
    trajet = _trajet(session)
    repo.record_alert(session, trajet.id, None, AlertKind.DIGEST, {"offer_hash": "abc"}, MAINTENANT)

    assert repo.exception_already_sent(session, trajet.id, "abc") is False

    repo.record_alert(session, trajet.id, 1, AlertKind.EXCEPTION, {"offer_hash": "abc"}, MAINTENANT)

    assert repo.exception_already_sent(session, trajet.id, "abc") is True
    assert repo.exception_already_sent(session, trajet.id, "autre") is False


def test_exception_already_sent_est_borne_au_trajet(session):
    """Preuve ciblée du point 4 : deux trajets peuvent partager un `offer_hash` (même
    itinéraire suivi par deux règles différentes, par exemple). Une alerte d'exception
    envoyée pour le trajet A ne doit jamais éteindre l'alerte du trajet B — sinon une
    aubaine réelle sur B ne serait jamais signalée.
    """
    trajet_a = _trajet(session)
    trajet_b = _trajet(session)
    repo.record_alert(
        session, trajet_a.id, 1, AlertKind.EXCEPTION, {"offer_hash": "partage"}, MAINTENANT
    )

    assert repo.exception_already_sent(session, trajet_a.id, "partage") is True
    assert repo.exception_already_sent(session, trajet_b.id, "partage") is False


def test_un_echec_denvoi_conserve_la_date_du_dernier_succes(session):
    """C'est cette date qui dit depuis quand le canal est muet.

    L'effacer réduirait le diagnostic à « en panne », sans permettre de distinguer une coupure
    d'une heure d'un canal mort depuis trois semaines.
    """
    repo.record_notify_success(session, MAINTENANT)

    plus_tard = MAINTENANT + timedelta(hours=6)
    sante = repo.record_notify_failure(session, "refusé", plus_tard)

    assert sante.last_success_at == MAINTENANT
    assert sante.last_failure_at == plus_tard
    assert sante.consecutive_failures == 1


def test_les_echecs_denvoi_saccumulent_jusquau_prochain_succes(session):
    for i in range(3):
        repo.record_notify_failure(session, "refusé", MAINTENANT + timedelta(hours=i))

    assert repo.get_or_create_notify_health(session).consecutive_failures == 3

    sante = repo.record_notify_success(session, MAINTENANT + timedelta(hours=4))

    assert sante.consecutive_failures == 0
    assert sante.last_error is None


def test_la_sante_du_canal_est_creee_a_la_demande(session):
    sante = repo.get_or_create_notify_health(session)

    assert sante.channel == "email"
    assert sante.consecutive_failures == 0
    assert sante.last_success_at is None
    assert sante.last_failure_at is None


class TestMeilleuresDatesDeDepart:
    """`best_by_departure_date` rouvre l'éventail que le plus bas quotidien écrase."""

    def test_retient_le_prix_plancher_de_chaque_date(self, session):
        trajet = _trajet(session)
        repo.record_observations(
            session,
            trajet.id,
            [
                _offre(900, depart_date=date(2027, 3, 12)),
                _offre(612, depart_date=date(2027, 3, 12), airline="Air Canada"),
                _offre(750, depart_date=date(2027, 4, 9)),
            ],
            MAINTENANT,
        )

        resultat = repo.best_by_departure_date(session, trajet.id, since=MAINTENANT)

        assert [(o.departure_date, o.price_cad) for o in resultat] == [
            (date(2027, 3, 12), 612),
            (date(2027, 4, 9), 750),
        ]

    def test_classe_les_dates_de_la_moins_chere_a_la_plus_chere(self, session):
        trajet = _trajet(session)
        repo.record_observations(
            session,
            trajet.id,
            [
                _offre(prix, depart_date=date(2027, 3, jour))
                for jour, prix in ((1, 800), (2, 520), (3, 660))
            ],
            MAINTENANT,
        )

        resultat = repo.best_by_departure_date(session, trajet.id, since=MAINTENANT)

        assert [o.price_cad for o in resultat] == [520, 660, 800]

    def test_un_releve_anterieur_a_la_fenetre_est_ignore(self, session):
        """Un prix périmé affiché au même rang qu'un relevé du matin induirait en erreur."""
        trajet = _trajet(session)
        vieux = MAINTENANT - timedelta(days=30)
        repo.record_observations(
            session, trajet.id, [_offre(300, depart_date=date(2027, 3, 12))], vieux
        )
        repo.record_observations(
            session, trajet.id, [_offre(700, depart_date=date(2027, 4, 9))], MAINTENANT
        )

        resultat = repo.best_by_departure_date(
            session, trajet.id, since=MAINTENANT - timedelta(days=7)
        )

        assert [o.price_cad for o in resultat] == [700]

    def test_le_plancher_ecarte_ne_remonte_pas_par_la_jointure(self, session):
        """Le relevé hors fenêtre ne doit pas non plus servir de plancher à sa propre date."""
        trajet = _trajet(session)
        repo.record_observations(
            session,
            trajet.id,
            [_offre(300, depart_date=date(2027, 3, 12))],
            MAINTENANT - timedelta(days=30),
        )
        repo.record_observations(
            session,
            trajet.id,
            [_offre(880, depart_date=date(2027, 3, 12), airline="Air Canada")],
            MAINTENANT,
        )

        resultat = repo.best_by_departure_date(
            session, trajet.id, since=MAINTENANT - timedelta(days=7)
        )

        assert [o.price_cad for o in resultat] == [880]

    def test_la_limite_borne_le_nombre_de_dates(self, session):
        trajet = _trajet(session)
        repo.record_observations(
            session,
            trajet.id,
            [_offre(500 + jour, depart_date=date(2027, 3, jour)) for jour in range(1, 21)],
            MAINTENANT,
        )

        resultat = repo.best_by_departure_date(session, trajet.id, since=MAINTENANT, limit=5)

        assert len(resultat) == 5
        assert [o.price_cad for o in resultat] == [501, 502, 503, 504, 505]

    def test_les_observations_dun_autre_trajet_restent_dehors(self, session):
        trajet = _trajet(session)
        autre = _trajet(session, destinations=["LIS"])
        repo.record_observations(
            session, trajet.id, [_offre(700, depart_date=date(2027, 3, 12))], MAINTENANT
        )
        repo.record_observations(
            session, autre.id, [_offre(200, depart_date=date(2027, 3, 12))], MAINTENANT
        )

        resultat = repo.best_by_departure_date(session, trajet.id, since=MAINTENANT)

        assert [o.price_cad for o in resultat] == [700]

    def test_a_prix_egal_cest_le_releve_le_plus_recent_qui_sort(self, session):
        """Deux sources peuvent toucher le même plancher : une seule ligne doit sortir."""
        trajet = _trajet(session)
        repo.record_observations(
            session,
            trajet.id,
            [_offre(640, depart_date=date(2027, 3, 12), airline="Air Transat")],
            MAINTENANT - timedelta(hours=5),
        )
        repo.record_observations(
            session,
            trajet.id,
            [_offre(640, depart_date=date(2027, 3, 12), airline="Air Canada")],
            MAINTENANT,
        )

        resultat = repo.best_by_departure_date(
            session, trajet.id, since=MAINTENANT - timedelta(days=1)
        )

        assert len(resultat) == 1
        assert resultat[0].airline == "Air Canada"

    def test_sans_observation_la_vue_est_vide(self, session):
        trajet = _trajet(session)

        assert repo.best_by_departure_date(session, trajet.id, since=MAINTENANT) == []
