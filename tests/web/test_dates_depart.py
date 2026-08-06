"""L'éventail des dates de départ.

Le balayage couvre douze mois et produit des dizaines de dates de départ, mais la carte d'un
trajet n'affichait qu'un montant sans échéance : combien, jamais pour quand. Sur un tel horizon
l'écart entre deux dates dépasse souvent celui qui déclencherait une alerte, si bien que la
collecte la plus coûteuse restait invisible.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from scrappervol.config import Settings
from scrappervol.core.types import DatePolicyKind, FlightOffer
from scrappervol.storage import repo
from scrappervol.storage.models import DailyLow, Route
from scrappervol.web.app import create_app, get_now, get_session

MAINTENANT = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
AUJOURDHUI = date(2026, 8, 6)


@pytest.fixture
def client(engine, session):
    application = create_app(engine, Settings(enabled_providers=["google_flights"]))
    application.dependency_overrides[get_session] = lambda: session
    application.dependency_overrides[get_now] = lambda: MAINTENANT
    return TestClient(application)


def _trajet(session, **surcharges) -> Route:
    base = {
        "label": "Paris à l'aubaine",
        "origins": ["YUL"],
        "destinations": ["CDG"],
        "date_policy": DatePolicyKind.FLEXIBLE,
        "policy_params": {"horizon_mois": 12},
    }
    trajet = Route(**{**base, **surcharges})
    session.add(trajet)
    session.commit()
    session.refresh(trajet)
    return trajet


def _offre(price_cad: int, depart: date, **surcharges) -> FlightOffer:
    base = {
        "provider": "kayak",
        "origin": "YUL",
        "destination": "CDG",
        "depart_date": depart,
        "return_date": depart + timedelta(days=10),
        "price_cad": price_cad,
        "price_original": float(price_cad),
        "currency_original": "CAD",
        "airline": "Air Transat",
        "stops": 0,
        "duration_minutes": 425,
        "deep_link": "https://exemple.test/offre",
        "raw": {},
    }
    return FlightOffer(**{**base, **surcharges})


def _releve(session, trajet, price_cad: int, depart: date, quand=MAINTENANT, **surcharges):
    """Enregistre une observation et retourne-la."""
    observations = repo.record_observations(
        session, trajet.id, [_offre(price_cad, depart, **surcharges)], quand
    )
    session.commit()
    return observations[0]


class TestEventail:
    def test_les_dates_sortent_de_la_moins_chere_a_la_plus_chere(self, client, session):
        trajet = _trajet(session)
        for prix, depart in (
            (880, date(2027, 3, 4)),
            (520, date(2027, 3, 11)),
            (660, date(2027, 4, 8)),
        ):
            _releve(session, trajet, prix, depart)

        corps = client.get(f"/routes/{trajet.id}/dates").text

        rang = [corps.index(f'data-prix="{p}"') for p in (520, 660, 880)]
        assert rang == sorted(rang)

    def test_la_date_la_moins_chere_est_signalee(self, client, session):
        trajet = _trajet(session)
        _releve(session, trajet, 880, date(2027, 3, 4))
        _releve(session, trajet, 520, date(2027, 3, 11))

        corps = client.get(f"/routes/{trajet.id}/dates").text

        assert "data-meilleure-date" in corps
        assert corps.count("data-meilleure-date") == 1

    def test_une_seule_date_nest_pas_couronnee_meilleure(self, client, session):
        """Désigner « la meilleure » parmi une seule offre ne renseigne sur rien."""
        trajet = _trajet(session)
        _releve(session, trajet, 520, date(2027, 3, 11))

        assert "data-meilleure-date" not in client.get(f"/routes/{trajet.id}/dates").text

    def test_chaque_date_porte_son_depart_sa_compagnie_et_sa_source(self, client, session):
        trajet = _trajet(session)
        _releve(session, trajet, 520, date(2027, 3, 11), airline="Air France")

        corps = client.get(f"/routes/{trajet.id}/dates").text

        assert 'data-depart="2027-03-11"' in corps
        assert "Air France" in corps
        assert 'data-source-date="kayak"' in corps
        assert "https://exemple.test/offre" in corps

    def test_un_releve_hors_fenetre_de_fraicheur_disparait(self, client, session):
        """Un prix vieux de trois semaines n'est plus une offre, c'est un souvenir."""
        trajet = _trajet(session)
        _releve(session, trajet, 300, date(2027, 3, 4), quand=MAINTENANT - timedelta(days=21))
        _releve(session, trajet, 700, date(2027, 4, 8))

        corps = client.get(f"/routes/{trajet.id}/dates").text

        assert 'data-prix="700"' in corps
        assert 'data-prix="300"' not in corps

    def test_sans_releve_recent_la_page_le_dit(self, client, session):
        trajet = _trajet(session)

        corps = client.get(f"/routes/{trajet.id}/dates").text

        assert "data-aucune-date" in corps
        assert 'data-dates="0"' in corps

    def test_le_nombre_de_dates_affichees_est_borne(self, client, session):
        trajet = _trajet(session)
        for jour in range(1, 21):
            _releve(session, trajet, 500 + jour, date(2027, 3, 1) + timedelta(days=jour))

        corps = client.get(f"/routes/{trajet.id}/dates").text

        assert 'data-dates="12"' in corps

    def test_un_trajet_inconnu_donne_404(self, client):
        assert client.get("/routes/9999/dates").status_code == 404


class TestEcartALaMediane:
    def _historique(self, session, trajet, jours: int, prix: int) -> None:
        for i in range(1, jours + 1):
            session.add(
                DailyLow(
                    route_id=trajet.id,
                    day=AUJOURDHUI - timedelta(days=i),
                    price_cad=prix,
                    provider="kayak",
                )
            )
        session.commit()

    def test_lecart_apparait_quand_lhistorique_est_significatif(self, client, session):
        trajet = _trajet(session)
        self._historique(session, trajet, jours=30, prix=1000)
        _releve(session, trajet, 500, date(2027, 3, 11))

        corps = client.get(f"/routes/{trajet.id}/dates").text

        # Médiane 1000, prix 500 : la moitié.
        assert "data-ecart-date" in corps
        assert "50 % sous la médiane" in corps

    def test_sans_historique_significatif_aucun_pourcentage_nest_avance(self, client, session):
        """Trois relevés ne justifient pas d'annoncer un écart : ce serait une fausse assurance."""
        trajet = _trajet(session)
        self._historique(session, trajet, jours=3, prix=1000)
        _releve(session, trajet, 500, date(2027, 3, 11))

        corps = client.get(f"/routes/{trajet.id}/dates").text

        assert "sous la médiane" not in corps
        assert 'data-prix="500"' in corps


class TestCarteDuTrajet:
    def test_la_carte_dit_pour_quelle_date_vaut_le_prix_affiche(self, client, session):
        trajet = _trajet(session)
        observation = _releve(session, trajet, 744, date(2027, 3, 11))
        repo.upsert_daily_low(session, trajet.id, AUJOURDHUI, observation)
        session.commit()

        corps = client.get("/").text

        assert 'data-depart-du-prix="2027-03-11"' in corps
        assert "départ le 2027-03-11" in corps

    def test_sans_observation_rattachee_la_carte_naffiche_aucune_date(self, client, session):
        """Un plus bas hérité d'une purge n'a plus d'observation : la carte ne doit pas casser."""
        trajet = _trajet(session)
        session.add(
            DailyLow(route_id=trajet.id, day=AUJOURDHUI, price_cad=744, provider="kayak")
        )
        session.commit()

        corps = client.get("/").text

        assert "data-depart-du-prix" not in corps
        assert 'data-prix="744"' in corps

    def test_chaque_carte_offre_le_deplie_des_dates(self, client, session):
        trajet = _trajet(session)

        corps = client.get("/").text

        assert f'data-voir-dates="{trajet.id}"' in corps
        assert f'hx-get="/routes/{trajet.id}/dates"' in corps
