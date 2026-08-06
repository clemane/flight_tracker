from datetime import UTC, datetime, timedelta

import pytest

from scrappervol.config import Settings
from scrappervol.core.types import DatePolicyKind
from scrappervol.providers.base import ProviderError
from scrappervol.providers.runner import plafond_de, run_provider
from scrappervol.storage import repo
from scrappervol.storage.models import ProviderHealth, Route

MAINTENANT = datetime(2026, 8, 4, 14, 0, tzinfo=UTC)


@pytest.fixture
def reglages():
    return Settings(max_queries_per_route=2, request_pause_min_s=0, request_pause_max_s=0)


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


def test_les_offres_sont_regroupees_par_trajet(session, reglages, fausse_source, sans_pause):
    trajet = _trajet(session)
    source = fausse_source(name="google_flights", offres=[(612, "Air Transat")])
    dormir, _ = sans_pause

    rapport = run_provider(session, source, reglages, MAINTENANT, sleeper=dormir)

    assert rapport.failed is False
    assert list(rapport.offers_by_route) == [trajet.id]
    assert rapport.offers_by_route[trajet.id][0].price_cad == 612


def test_les_trajets_inactifs_sont_ignores(session, reglages, fausse_source, sans_pause):
    _trajet(session, active=False)
    source = fausse_source(offres=[(612, "Air Transat")])
    dormir, _ = sans_pause

    rapport = run_provider(session, source, reglages, MAINTENANT, sleeper=dormir)

    assert rapport.offers_by_route == {}
    assert source.appels == []


def test_une_exception_est_capturee_et_nabout_pas_hors_du_runner(
    session, reglages, fausse_source, sans_pause
):
    _trajet(session)
    source = fausse_source(exception=ProviderError("sélecteur introuvable"))
    dormir, _ = sans_pause

    rapport = run_provider(session, source, reglages, MAINTENANT, sleeper=dormir)

    assert rapport.failed is True
    assert "sélecteur introuvable" in rapport.error


def test_une_exception_inattendue_est_aussi_capturee(session, reglages, fausse_source, sans_pause):
    _trajet(session)
    source = fausse_source(exception=RuntimeError("panne inattendue"))
    dormir, _ = sans_pause

    rapport = run_provider(session, source, reglages, MAINTENANT, sleeper=dormir)

    assert rapport.failed is True


def test_un_echec_incremente_la_sante_et_pose_un_repos_au_troisieme(
    session, reglages, fausse_source, sans_pause
):
    _trajet(session)
    source = fausse_source(name="transat", exception=ProviderError("boum"))
    dormir, _ = sans_pause

    for _ in range(3):
        run_provider(session, source, reglages, MAINTENANT, sleeper=dormir)

    sante = repo.get_or_create_health(session, "transat")
    assert sante.consecutive_failures == 3
    assert sante.disabled_until == MAINTENANT + timedelta(hours=1)


def test_une_source_au_repos_nest_pas_interrogee(session, reglages, fausse_source, sans_pause):
    _trajet(session)
    session.add(
        ProviderHealth(
            provider="transat",
            disabled_until=MAINTENANT + timedelta(hours=2),
            consecutive_failures=3,
        )
    )
    session.commit()
    source = fausse_source(name="transat", offres=[(612, "Air Transat")])
    dormir, _ = sans_pause

    rapport = run_provider(session, source, reglages, MAINTENANT, sleeper=dormir)

    assert rapport.skipped is True
    assert source.appels == []


def test_le_repos_echu_laisse_repasser_la_source(session, reglages, fausse_source, sans_pause):
    _trajet(session)
    session.add(
        ProviderHealth(
            provider="transat",
            disabled_until=MAINTENANT - timedelta(minutes=1),
            consecutive_failures=3,
        )
    )
    session.commit()
    source = fausse_source(name="transat", offres=[(612, "Air Transat")])
    dormir, _ = sans_pause

    rapport = run_provider(session, source, reglages, MAINTENANT, sleeper=dormir)

    assert rapport.skipped is False
    assert source.appels != []


def test_un_succes_remet_la_sante_a_zero(session, reglages, fausse_source, sans_pause):
    _trajet(session)
    dormir, _ = sans_pause
    run_provider(
        session,
        fausse_source(name="transat", exception=ProviderError("boum")),
        reglages,
        MAINTENANT,
        sleeper=dormir,
    )

    run_provider(
        session,
        fausse_source(name="transat", offres=[(612, "Air Transat")]),
        reglages,
        MAINTENANT,
        sleeper=dormir,
    )

    sante = repo.get_or_create_health(session, "transat")
    assert sante.consecutive_failures == 0
    assert sante.offers_last_run > 0


def test_zero_offre_est_un_succes_la_premiere_fois(session, reglages, fausse_source, sans_pause):
    """Un trajet qui n'a jamais rien donné peut légitimement ne rien donner."""
    _trajet(session)
    source = fausse_source(name="transat", muette=True)
    dormir, _ = sans_pause

    rapport = run_provider(session, source, reglages, MAINTENANT, sleeper=dormir)

    assert rapport.failed is False


def test_zero_offre_apres_un_passage_fructueux_est_un_echec(
    session, reglages, fausse_source, sans_pause
):
    """Une dérive de sélecteur renvoie un HTTP 200 sans exception : sans cette règle, elle passe
    inaperçue et le digest annonce fidèlement qu'il n'y a rien à signaler."""
    _trajet(session)
    dormir, _ = sans_pause
    run_provider(
        session,
        fausse_source(name="transat", offres=[(612, "Air Transat")]),
        reglages,
        MAINTENANT,
        sleeper=dormir,
    )

    rapport = run_provider(
        session,
        fausse_source(name="transat", muette=True),
        reglages,
        MAINTENANT + timedelta(hours=6),
        sleeper=dormir,
    )

    assert rapport.failed is True
    assert "aucune offre" in rapport.error.lower()

    # Le rapport n'est qu'un compte rendu en mémoire : ce qui protège vraiment la veille, c'est
    # l'échec inscrit en base. Remplacer ici record_provider_failure par record_provider_success
    # laisse le rapport intact et n'arme jamais le disjoncteur — la panne silencieuse exacte que
    # cette règle existe pour attraper.
    sante = repo.get_or_create_health(session, "transat")
    assert sante.consecutive_failures == 1
    assert sante.last_error is not None
    assert "aucune offre" in sante.last_error.lower()
    assert sante.offers_last_run == 0


def test_le_plafond_de_requetes_est_respecte(session, reglages, fausse_source, sans_pause):
    _trajet(
        session,
        origins=["YUL", "YQB"],
        destinations=["CDG", "ORY", "BRU"],
        date_policy=DatePolicyKind.WINDOW,
        policy_params={"mois": ["2027-03", "2027-04"], "sejour_min": 8, "sejour_max": 12},
    )
    source = fausse_source(offres=[(612, "Air Transat")])
    dormir, _ = sans_pause

    run_provider(session, source, reglages, MAINTENANT, sleeper=dormir)

    assert len(source.appels) == reglages.max_queries_per_route


def test_une_pause_est_observee_entre_les_requetes(session, reglages, fausse_source, sans_pause):
    _trajet(session, origins=["YUL", "YQB"])
    source = fausse_source(offres=[(612, "Air Transat")])
    dormir, appels = sans_pause

    run_provider(session, source, reglages, MAINTENANT, sleeper=dormir)

    assert len(appels) == len(source.appels)


def test_la_pause_entre_requetes_respecte_lintervalle_configure(session, fausse_source, sans_pause):
    """La fixture `reglages` met les pauses à zéro : aucun test ne distingue alors une vraie pause
    de son absence. Celui-ci configure un intervalle non nul exprès.

    Sans pause, le scraper enchaîne les requêtes à pleine vitesse et se fait bannir de la
    source. C'est une panne qui ne se voit pas en test — elle ne se voit qu'en production, une
    fois l'adresse bloquée, et elle prive la veille de sa source la plus riche.
    """
    reglages_lents = Settings(max_queries_per_route=2, request_pause_min_s=3, request_pause_max_s=7)
    _trajet(session, origins=["YUL", "YQB"])
    source = fausse_source(offres=[(612, "Air Transat")])
    dormir, appels = sans_pause

    run_provider(session, source, reglages_lents, MAINTENANT, sleeper=dormir)

    assert len(appels) >= 2, "il faut au moins deux requêtes pour observer une pause"
    assert appels[0] == 0, "la première requête ne doit pas attendre"
    assert all(3 <= pause <= 7 for pause in appels[1:]), appels


def test_desactiver_ses_trajets_ne_condamne_pas_les_sources(
    session, reglages, fausse_source, sans_pause
):
    """Une source qu'on n'interroge pas ne peut pas échouer.

    Sans la garde sur `queries_run`, le passage qui suit la désactivation conclut « aucune offre
    alors que le passage précédent en produisait », incrémente le compteur d'échecs et pose un
    repos qui double jusqu'à 24 h. Les sources dorment alors au moment précis où l'utilisateur
    réactive un trajet, et la page de santé annonce trois pannes qui n'existent pas.
    """
    trajet = _trajet(session)
    dormir, _ = sans_pause
    run_provider(
        session,
        fausse_source(name="transat", offres=[(612, "Air Transat")]),
        reglages,
        MAINTENANT,
        sleeper=dormir,
    )

    trajet.active = False
    session.add(trajet)
    session.commit()

    rapport = run_provider(
        session,
        fausse_source(name="transat", muette=True),
        reglages,
        MAINTENANT + timedelta(hours=6),
        sleeper=dormir,
    )

    assert rapport.queries_run == 0
    assert rapport.failed is False
    assert repo.get_or_create_health(session, "transat").disabled_until is None


def test_le_plafond_de_requetes_est_applique_par_trajet(
    session, reglages, fausse_source, sans_pause
):
    """`max_queries_per_route` borne chaque trajet, pas l'ensemble du passage.

    `plan_queries` est appelé à l'intérieur de la boucle sur les trajets actifs, avec le même
    plafond à chaque itération : deux trajets actifs avec un plafond de 2 doivent produire
    4 requêtes, pas 2. Ce plafond borne la charge infligée à une source pour *un* trajet donné ;
    le nombre de trajets suivis est un choix de l'utilisateur, pas une raison d'en surveiller
    chacun avec moins d'attention. Un seul trajet actif ne peut pas distinguer un plafond « par
    trajet » d'un plafond « global » : il en faut deux.
    """
    premier = _trajet(session, label="Paris", origins=["YUL", "YQB"])
    second = _trajet(session, label="Rome", origins=["YUL", "YQB"], destinations=["FCO"])
    source = fausse_source(offres=[(612, "Air Transat")])
    dormir, _ = sans_pause

    rapport = run_provider(session, source, reglages, MAINTENANT, sleeper=dormir)

    assert rapport.queries_run == 4
    assert set(rapport.offers_by_route) == {premier.id, second.id}


class TestPlafondParSource:
    """Le plafond de requêtes suit le coût de la source, non le plus lent du lot."""

    def test_une_source_sans_navigateur_recoit_un_plafond_plus_haut(self):
        reglages = Settings(max_queries_per_route=6)

        assert plafond_de("google_flights", reglages) == 12

    def test_les_sources_a_navigateur_gardent_le_plafond_commun(self):
        reglages = Settings(max_queries_per_route=6)

        assert plafond_de("transat", reglages) == 6
        assert plafond_de("air_canada", reglages) == 6

    def test_kayak_garde_le_plafond_commun_malgre_son_faible_cout(self):
        """Il balaye déjà les jours voisins : une requête y couvre sept dates, pas une."""
        assert plafond_de("kayak", Settings(max_queries_per_route=6)) == 6

    def test_une_source_inconnue_retombe_sur_le_reglage(self):
        assert plafond_de("source_future", Settings(max_queries_per_route=9)) == 9

    def test_le_plafond_specifique_ignore_le_reglage_commun(self):
        """Sinon relever `MAX_QUERIES_PER_ROUTE` n'aurait aucun effet sur la source concernée."""
        assert plafond_de("google_flights", Settings(max_queries_per_route=2)) == 12


def test_run_provider_applique_le_plafond_propre_a_la_source(
    session, reglages, fausse_source, sans_pause
):
    """Le plafond doit être branché, pas seulement juste.

    Une route à deux origines et deux destinations offre quatre combinaisons ; avec le plafond
    commun à 2, une source à navigateur en tire 2 et Google Flights les 4. Sans cette épreuve,
    `plafond_de` pourrait rester correcte et n'être appelée nulle part.
    """
    _trajet(session, label="Paris", origins=["YUL", "YQB"], destinations=["CDG", "ORY"])
    dormir, _ = sans_pause
    bride = Settings(**{**reglages.model_dump(), "max_queries_per_route": 2})

    lente = run_provider(
        session, fausse_source(name="transat", offres=[(612, "Air Transat")]), bride,
        MAINTENANT, sleeper=dormir,
    )
    rapide = run_provider(
        session, fausse_source(name="google_flights", offres=[(612, "Air Canada")]), bride,
        MAINTENANT, sleeper=dormir,
    )

    assert lente.queries_run == 2
    assert rapide.queries_run == 4
