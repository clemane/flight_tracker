from datetime import UTC, date, datetime

from sqlmodel import select

from scrappervol.core.types import DatePolicyKind, FlightOffer, TripType
from scrappervol.storage.models import (
    Alert,
    AlertKind,
    DailyLow,
    Observation,
    ProviderHealth,
    Route,
)

MAINTENANT = datetime(2026, 8, 4, 14, 0, tzinfo=UTC)


def test_un_trajet_se_persiste_avec_ses_listes_json(session):
    trajet = Route(
        label="Paris au printemps",
        origins=["YUL", "YQB"],
        destinations=["CDG", "ORY"],
        date_policy=DatePolicyKind.FIXED,
        policy_params={"depart": "2027-03-12", "retour": "2027-03-22", "flex_days": 3},
    )
    session.add(trajet)
    session.commit()

    relu = session.exec(select(Route)).one()
    assert relu.origins == ["YUL", "YQB"]
    assert relu.policy_params["flex_days"] == 3
    assert relu.active is True
    assert relu.passengers == 1
    assert relu.exception_threshold == 0.40


def test_to_policy_projette_le_trajet_vers_le_type_du_domaine(session):
    trajet = Route(
        label="Paris",
        origins=["YUL"],
        destinations=["CDG"],
        date_policy=DatePolicyKind.WINDOW,
        policy_params={"mois": ["2027-03"], "sejour_min": 8, "sejour_max": 12},
        passengers=2,
        max_stops=1,
    )

    politique = trajet.to_policy()

    assert politique.origins == ["YUL"]
    assert politique.date_policy is DatePolicyKind.WINDOW
    assert politique.passengers == 2
    assert politique.max_stops == 1
    assert politique.trip_type is TripType.ROUND_TRIP


def test_une_observation_se_construit_depuis_une_offre(session):
    offre = FlightOffer(
        provider="google_flights",
        origin="YUL",
        destination="CDG",
        depart_date=date(2027, 3, 12),
        return_date=date(2027, 3, 22),
        price_cad=612,
        price_original=612.0,
        currency_original="CAD",
        airline="Air Transat",
        stops=0,
        duration_minutes=425,
        deep_link="https://example.com/offre",
        raw={"brut": True},
    )

    observation = Observation.from_offer(route_id=1, offer=offre, observed_at=MAINTENANT)
    session.add(observation)
    session.commit()

    relu = session.exec(select(Observation)).one()
    assert relu.price_cad == 612
    assert relu.offer_hash == offre.offer_hash
    assert relu.raw == {"brut": True}
    assert relu.provider == "google_flights"
    assert relu.observed_at.tzinfo is not None
    assert relu.observed_at == MAINTENANT


def test_daily_low_a_une_cle_composee(session):
    session.add(
        DailyLow(
            route_id=1,
            day=date(2026, 8, 4),
            price_cad=612,
            observation_id=1,
            provider="google_flights",
        )
    )
    session.commit()

    relu = session.exec(select(DailyLow)).one()
    assert relu.route_id == 1
    assert relu.day == date(2026, 8, 4)


def test_provider_health_a_des_defauts_neutres(session):
    session.add(ProviderHealth(provider="google_flights"))
    session.commit()

    relu = session.exec(select(ProviderHealth)).one()
    assert relu.consecutive_failures == 0
    assert relu.last_success_at is None
    assert relu.disabled_until is None
    assert relu.offers_last_run == 0


def test_les_datetimes_conservent_leur_fuseau_apres_persistance(session):
    """SQLite ne stocke aucun fuseau nativement : sans un type de colonne dédié, un
    datetime timezone-aware ressort naïf après un aller-retour en base, ce qui viole
    silencieusement l'invariant « aucun horodatage naïf » du projet.
    """
    trajet = Route(
        label="Test",
        origins=["YUL"],
        destinations=["CDG"],
        date_policy=DatePolicyKind.FIXED,
        policy_params={},
        created_at=MAINTENANT,
    )
    sante = ProviderHealth(
        provider="google_flights",
        last_success_at=MAINTENANT,
        disabled_until=MAINTENANT,
    )
    session.add(trajet)
    session.add(sante)
    session.commit()

    trajet_relu = session.exec(select(Route)).one()
    sante_relue = session.exec(select(ProviderHealth)).one()

    assert trajet_relu.created_at is not None
    assert trajet_relu.created_at.tzinfo is not None
    assert trajet_relu.created_at == MAINTENANT
    assert sante_relue.last_success_at == MAINTENANT
    assert sante_relue.disabled_until == MAINTENANT


def test_une_alerte_journalise_son_type_et_sa_charge(session):
    session.add(
        Alert(
            route_id=1,
            observation_id=7,
            kind=AlertKind.EXCEPTION,
            sent_at=MAINTENANT,
            payload={"offer_hash": "abc123"},
        )
    )
    session.commit()

    relu = session.exec(select(Alert)).one()
    assert relu.kind is AlertKind.EXCEPTION
    assert relu.payload["offer_hash"] == "abc123"
    assert relu.sent_at.tzinfo is not None
    assert relu.sent_at == MAINTENANT
