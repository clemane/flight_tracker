from datetime import UTC, date, datetime, timedelta

import pytest
from sqlmodel import select

from scrappervol.config import Settings
from scrappervol.core.types import DatePolicyKind, FlightOffer
from scrappervol.scheduler.jobs import build_digest, purge_old_data, send_digest
from scrappervol.storage import repo
from scrappervol.storage.models import (
    Alert,
    AlertKind,
    DailyLow,
    Observation,
    ProviderHealth,
    Route,
)

MAINTENANT = datetime(2026, 8, 4, 18, 0, tzinfo=UTC)
AUJOURDHUI = date(2026, 8, 4)


@pytest.fixture
def reglages():
    # `enabled_providers` fixé explicitement : `.env`, chargé dans tout le conteneur via
    # `env_file`, porte encore `transat,air_canada` — reliquat des tâches 11/12, désactivées
    # depuis en code mais pas dans le fichier d'environnement. Sans cette fixation, les tests
    # d'état des sources verraient trois fournisseurs au lieu d'un seul.
    return Settings(alert_to="moi@example.com", enabled_providers=["google_flights"])


def _trajet(session, **surcharges) -> Route:
    base = {
        "label": "Paris",
        "origins": ["YUL"],
        "destinations": ["CDG"],
        "date_policy": DatePolicyKind.FIXED,
        "policy_params": {"depart": "2027-03-12", "retour": "2027-03-22"},
    }
    trajet = Route(**{**base, **surcharges})
    session.add(trajet)
    session.commit()
    session.refresh(trajet)
    return trajet


def _offre(prix: int) -> FlightOffer:
    return FlightOffer(
        provider="google_flights",
        origin="YUL",
        destination="CDG",
        depart_date=date(2027, 3, 12),
        return_date=date(2027, 3, 22),
        price_cad=prix,
        price_original=float(prix),
        currency_original="CAD",
        airline="Air Transat",
        stops=0,
        duration_minutes=425,
        deep_link="https://example.com",
        raw={},
    )


def _jour(session, route_id: int, jour: date, prix: int) -> None:
    session.add(DailyLow(route_id=route_id, day=jour, price_cad=prix, provider="google_flights"))
    session.commit()


def test_le_digest_contient_un_bloc_par_trajet_actif(session, reglages):
    _trajet(session, label="Paris")
    _trajet(session, label="Lisbonne")
    _trajet(session, label="Inactif", active=False)

    donnees = build_digest(session, reglages, MAINTENANT)

    assert {b.label for b in donnees.blocks} == {"Paris", "Lisbonne"}


def test_le_bloc_porte_le_plus_bas_du_jour_et_son_contexte(session, reglages):
    trajet = _trajet(session)
    observation = repo.record_observations(session, trajet.id, [_offre(480)], MAINTENANT)[0]
    repo.upsert_daily_low(session, trajet.id, AUJOURDHUI, observation)
    for decalage in range(1, 21):
        _jour(session, trajet.id, AUJOURDHUI - timedelta(days=decalage), 600)

    bloc = build_digest(session, reglages, MAINTENANT).blocks[0]

    assert bloc.price_cad == 480
    assert bloc.airline == "Air Transat"
    assert bloc.median_price == 600
    assert round(bloc.gap_vs_median, 2) == 0.20
    assert bloc.gap_vs_yesterday == -120
    assert bloc.history_building is False


def test_un_trajet_sans_historique_significatif_est_marque_en_construction(session, reglages):
    trajet = _trajet(session)
    observation = repo.record_observations(session, trajet.id, [_offre(480)], MAINTENANT)[0]
    repo.upsert_daily_low(session, trajet.id, AUJOURDHUI, observation)
    for decalage in range(1, 4):
        _jour(session, trajet.id, AUJOURDHUI - timedelta(days=decalage), 600)

    bloc = build_digest(session, reglages, MAINTENANT).blocks[0]

    assert bloc.history_building is True
    assert bloc.is_find is False


def test_un_trajet_sans_prix_du_jour_apparait_quand_meme(session, reglages):
    """Un trajet muet doit rester visible — c'est ainsi qu'une panne se voit — mais il ne doit
    évidemment pas être annoncé comme une trouvaille faute de prix à comparer."""
    _trajet(session, target_price_cad=500)

    bloc = build_digest(session, reglages, MAINTENANT).blocks[0]

    assert bloc.price_cad is None
    assert bloc.is_find is False


def test_un_prix_du_jour_sans_historique_laisse_les_ecarts_absents(session, reglages):
    """Premier jour d'un trajet : un prix est déjà tombé, mais ni médiane ni veille n'existent
    encore. Sans les gardes qui protègent `gap_vs_median` et `gap_vs_yesterday`, ces calculs
    planteraient (comparaison puis soustraction avec `None`) plutôt que de rester absents."""
    trajet = _trajet(session)
    observation = repo.record_observations(session, trajet.id, [_offre(480)], MAINTENANT)[0]
    repo.upsert_daily_low(session, trajet.id, AUJOURDHUI, observation)

    bloc = build_digest(session, reglages, MAINTENANT).blocks[0]

    assert bloc.price_cad == 480
    assert bloc.median_price is None
    assert bloc.gap_vs_median is None
    assert bloc.gap_vs_yesterday is None
    assert bloc.history_building is True


def _trajet_avec_prix(session, prix: int, mediane: int, **surcharges) -> None:
    """Un trajet actif, un plus bas du jour à `prix`, et 20 jours d'historique à `mediane`.

    Vingt jours dépassent `min_history_days` (14), sans quoi `find_count` ne compterait rien :
    il exclut les trajets encore en construction.
    """
    trajet = _trajet(session, **surcharges)
    observation = repo.record_observations(session, trajet.id, [_offre(prix)], MAINTENANT)[0]
    repo.upsert_daily_low(session, trajet.id, AUJOURDHUI, observation)
    for decalage in range(1, 21):
        _jour(session, trajet.id, AUJOURDHUI - timedelta(days=decalage), mediane)


def test_un_prix_sous_la_cible_compte_comme_trouvaille(session, reglages):
    """La cible absolue suffit seule : ici l'écart à la médiane vaut 10 %, sous le seuil de 15 %,
    donc seule la branche `price_cad <= target_price_cad` peut produire la trouvaille."""
    _trajet_avec_prix(session, prix=450, mediane=500, target_price_cad=500)

    assert build_digest(session, reglages, MAINTENANT).find_count == 1


def test_un_prix_nettement_sous_la_mediane_compte_comme_trouvaille(session, reglages):
    """L'écart à la médiane suffit seul : aucune cible n'est fixée, et 450 contre 600 fait 25 %,
    au-delà du seuil de 15 %."""
    _trajet_avec_prix(session, prix=450, mediane=600, target_price_cad=None)

    assert build_digest(session, reglages, MAINTENANT).find_count == 1


def test_un_prix_ordinaire_ne_compte_pas_comme_trouvaille(session, reglages):
    """Le pendant négatif des deux tests ci-dessus, sans lequel un `is_find` toujours vrai les
    satisferait tous les deux : 450 reste au-dessus de la cible de 400, et son écart à la médiane
    de 500 n'est que de 10 %."""
    _trajet_avec_prix(session, prix=450, mediane=500, target_price_cad=400)

    assert build_digest(session, reglages, MAINTENANT).find_count == 0


def test_le_plancher_de_credibilite_est_applique_dans_le_digest(session, reglages):
    """Sans ce test, un `credibility_floor` non câblé jusqu'à `is_find` — par exemple codé en dur
    à 0 dans `_bloc_trajet` — resterait invisible : un prix aberrant sous la cible se déclarerait
    trouvaille du jour au lieu d'être écarté comme une erreur de lecture."""
    _trajet_avec_prix(session, prix=12, mediane=600, target_price_cad=500)

    donnees = build_digest(session, reglages, MAINTENANT)

    assert donnees.blocks[0].is_find is False
    assert donnees.find_count == 0


def test_letat_des_sources_est_toujours_present(session, reglages):
    _trajet(session)
    session.add(ProviderHealth(provider="google_flights", last_success_at=MAINTENANT))
    session.commit()

    donnees = build_digest(session, reglages, MAINTENANT)

    assert {p.name for p in donnees.providers} == set(reglages.enabled_providers)
    # Sans ces deux lignes, un `is_stale` constamment vrai satisferait toute la suite : les deux
    # tests suivants ne vérifient que le cas muet. Le digest crierait à la panne chaque jour.
    assert all(p.is_stale is False for p in donnees.providers)
    assert donnees.has_stale_provider is False


def test_une_source_muette_depuis_plus_de_48h_est_marquee(session, reglages):
    """La source porte ici le nom d'une source activée. `build_digest` rend l'état des sources
    que l'on utilise — `enabled_providers`, verrouillé par le test ci-dessus — et une santé
    enregistrée pour une source désactivée n'y figurerait pas."""
    _trajet(session)
    session.add(
        ProviderHealth(provider="google_flights", last_success_at=MAINTENANT - timedelta(hours=72))
    )
    session.commit()

    donnees = build_digest(session, reglages, MAINTENANT)

    source = next(p for p in donnees.providers if p.name == "google_flights")
    assert source.is_stale is True
    assert donnees.has_stale_provider is True


def test_une_source_muette_depuis_exactement_48h_nest_pas_encore_marquee(session, reglages):
    """Frontière du seuil : `>` est strict, donc pile 48h ne bascule pas encore en panne.

    Sans ce test, remplacer `>` par `>=` dans `_statut_source` laissait toute la suite verte —
    le test ci-dessus vérifie 72h, largement au-delà de la frontière."""
    _trajet(session)
    session.add(
        ProviderHealth(provider="google_flights", last_success_at=MAINTENANT - timedelta(hours=48))
    )
    session.commit()

    donnees = build_digest(session, reglages, MAINTENANT)

    source = next(p for p in donnees.providers if p.name == "google_flights")
    assert source.is_stale is False


def test_une_source_qui_na_jamais_reussi_est_marquee(session, reglages):
    _trajet(session)

    donnees = build_digest(session, reglages, MAINTENANT)

    assert all(p.is_stale for p in donnees.providers)


def test_le_digest_est_envoye_et_journalise(session, reglages, faux_mailer):
    _trajet(session)

    assert send_digest(session, reglages, faux_mailer, MAINTENANT) is True
    assert len(faux_mailer.envois) == 1
    assert faux_mailer.envois[0][1] == "moi@example.com"

    # Un test qui ne regarde que la valeur de retour n'exerce que la moitié de la fonction :
    # sans cette assertion, permuter `AlertKind.DIGEST` pour `AlertKind.EXCEPTION` — ou tout
    # autre `kind` — laissait la suite verte.
    alertes = session.exec(select(Alert)).all()
    assert len(alertes) == 1
    assert alertes[0].kind is AlertKind.DIGEST
    assert alertes[0].route_id == 0
    assert alertes[0].payload["routes"] == 1


def test_un_echec_denvoi_du_digest_ne_journalise_rien(session, reglages):
    """Symétrique de `test_une_alerte_non_envoyee_est_retentee_au_passage_suivant` côté digest :
    un SMTP injoignable ne doit ni planter le job ni laisser une alerte fantôme, faute de quoi
    le prochain passage croirait le digest du jour déjà envoyé."""

    class MailerCasse:
        def send(self, mail, to):
            raise RuntimeError("SMTP injoignable")

    _trajet(session)

    assert send_digest(session, reglages, MailerCasse(), MAINTENANT) is False
    assert session.exec(select(Alert)).all() == []


def test_aucun_digest_sans_trajet_actif(session, reglages, faux_mailer):
    _trajet(session, active=False)

    assert send_digest(session, reglages, faux_mailer, MAINTENANT) is False
    assert faux_mailer.envois == []


def test_la_purge_supprime_les_observations_anciennes(session, reglages):
    trajet = _trajet(session)
    session.add(Observation.from_offer(trajet.id, _offre(612), MAINTENANT - timedelta(days=120)))
    session.add(Observation.from_offer(trajet.id, _offre(500), MAINTENANT))
    session.commit()

    assert purge_old_data(session, reglages, MAINTENANT) == 1


def test_la_purge_respecte_la_fenetre_de_retention_configuree(session, reglages):
    """Sans ce test, ignorer `settings.retention_days` — par exemple en purgeant toujours à
    partir de `now` — laissait la suite verte : le seul test existant place ses deux
    observations à 120 jours et à l'instant présent, loin de part et d'autre de n'importe
    quelle fenêtre plausible. Ici, une observation à 10 jours doit survivre à une fenêtre de
    90 jours alors qu'elle ne survivrait pas à une fenêtre de 0."""
    trajet = _trajet(session)
    recente = Observation.from_offer(trajet.id, _offre(500), MAINTENANT - timedelta(days=10))
    ancienne = Observation.from_offer(trajet.id, _offre(612), MAINTENANT - timedelta(days=120))
    session.add(recente)
    session.add(ancienne)
    session.commit()

    assert purge_old_data(session, reglages, MAINTENANT) == 1

    restantes = session.exec(select(Observation)).all()
    assert [o.price_cad for o in restantes] == [500]


def test_la_purge_epargne_lhistorique_des_plus_bas(session, reglages):
    trajet = _trajet(session)
    _jour(session, trajet.id, date(2024, 1, 1), 400)

    purge_old_data(session, reglages, MAINTENANT)

    assert repo.daily_low_for(session, trajet.id, date(2024, 1, 1)) is not None
