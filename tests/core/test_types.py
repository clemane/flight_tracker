from datetime import date

import pytest

from scrappervol.core.types import (
    DatePolicyKind,
    FlightOffer,
    RoutePolicy,
    SearchQuery,
    TripType,
    compute_offer_hash,
)


def _offre(**surcharges) -> FlightOffer:
    base = {
        "provider": "google_flights",
        "origin": "YUL",
        "destination": "CDG",
        "depart_date": date(2027, 3, 12),
        "return_date": date(2027, 3, 22),
        "price_cad": 612,
        "price_original": 612.0,
        "currency_original": "CAD",
        "airline": "Air Transat",
        "stops": 0,
        "duration_minutes": 425,
        "deep_link": "https://example.com/offre",
        "raw": {},
    }
    return FlightOffer(**{**base, **surcharges})


def test_offer_hash_est_stable_entre_deux_instances_identiques():
    assert _offre().offer_hash == _offre().offer_hash


def test_offer_hash_distingue_deux_offres_differentes():
    assert _offre().offer_hash != _offre(airline="Air Canada").offer_hash
    assert _offre().offer_hash != _offre(stops=1).offer_hash
    assert _offre().offer_hash != _offre(depart_date=date(2027, 3, 13)).offer_hash


def test_offer_hash_ignore_le_prix():
    """Le condensat suit une offre dans le temps ; c'est justement son prix qui bouge."""
    assert _offre().offer_hash == _offre(price_cad=399).offer_hash


def test_offer_hash_gere_un_aller_simple():
    aller_simple = _offre(return_date=None)
    assert aller_simple.offer_hash != _offre().offer_hash


def test_compute_offer_hash_est_la_meme_fonction_que_la_propriete():
    offre = _offre()
    attendu = compute_offer_hash(
        provider=offre.provider,
        origin=offre.origin,
        destination=offre.destination,
        depart_date=offre.depart_date,
        return_date=offre.return_date,
        airline=offre.airline,
        stops=offre.stops,
    )
    assert offre.offer_hash == attendu


def test_les_offres_sont_immuables():
    # dataclasses.FrozenInstanceError est une sous-classe d'AttributeError ; on l'attrape via son
    # parent pour satisfaire ruff B017 (pas d'assertion sur une exception trop large) sans changer
    # ce que le test vérifie.
    with pytest.raises(AttributeError):
        _offre().price_cad = 1


def test_search_query_est_utilisable_comme_cle():
    q = SearchQuery(
        origin="YUL",
        destination="CDG",
        depart_date=date(2027, 3, 12),
        return_date=date(2027, 3, 22),
    )
    assert {q, q} == {q}


def test_route_policy_porte_les_listes_et_les_parametres():
    politique = RoutePolicy(
        origins=["YUL", "YQB"],
        destinations=["CDG", "ORY"],
        date_policy=DatePolicyKind.FIXED,
        policy_params={"depart": "2027-03-12", "retour": "2027-03-22", "flex_days": 3},
        trip_type=TripType.ROUND_TRIP,
        passengers=1,
        max_stops=None,
    )
    assert politique.origins == ["YUL", "YQB"]
    assert politique.date_policy is DatePolicyKind.FIXED
