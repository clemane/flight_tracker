from datetime import UTC, datetime

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from scrappervol.config import Settings
from scrappervol.core.types import DatePolicyKind
from scrappervol.scheduler.app import (
    INTERVALLES,
    JITTER_S,
    _job_digest,
    _job_purge,
    _job_scan,
    build_providers,
    build_scheduler,
)
from scrappervol.storage import repo
from scrappervol.storage.models import Route


@pytest.fixture
def reglages():
    # Les pauses sont mises à zéro : les tests qui exécutent réellement un job passeraient
    # sinon par les 5 à 20 secondes d'attente par défaut, à chacune des requêtes.
    return Settings(
        enabled_providers=["google_flights", "transat"],
        interval_google_hours=4,
        interval_transat_hours=6,
        digest_hour=18,
        timezone="America/Toronto",
        request_pause_min_s=0,
        request_pause_max_s=0,
        max_queries_per_route=1,
    )


def _trajet(session) -> Route:
    trajet = Route(
        label="Cancún",
        origins=["YUL"],
        destinations=["CUN"],
        date_policy=DatePolicyKind.FIXED,
        policy_params={"depart": "2027-03-12", "retour": "2027-03-22"},
    )
    session.add(trajet)
    session.commit()
    session.refresh(trajet)
    return trajet


def test_seules_les_sources_activees_sont_construites(reglages):
    sources = build_providers(reglages)

    assert [s.name for s in sources] == ["google_flights", "transat"]


def test_une_source_inconnue_est_ignoree_sans_lever():
    reglages = Settings(enabled_providers=["google_flights", "compagnie_imaginaire"])

    assert [s.name for s in build_providers(reglages)] == ["google_flights"]


def test_un_job_de_scan_par_source_activee(engine, reglages, faux_mailer):
    ordonnanceur = build_scheduler(engine, reglages, faux_mailer)

    scans = [j for j in ordonnanceur.get_jobs() if j.id.startswith("scan:")]
    assert {j.id for j in scans} == {"scan:google_flights", "scan:transat"}


def test_chaque_scan_utilise_lintervalle_de_sa_source(engine, reglages, faux_mailer):
    ordonnanceur = build_scheduler(engine, reglages, faux_mailer)

    google = ordonnanceur.get_job("scan:google_flights")
    transat = ordonnanceur.get_job("scan:transat")

    assert isinstance(google.trigger, IntervalTrigger)
    assert google.trigger.interval.total_seconds() == 4 * 3600
    assert transat.trigger.interval.total_seconds() == 6 * 3600


def test_les_passages_sont_decales_aleatoirement(engine, reglages, faux_mailer):
    """Frapper à l'heure ronde est la signature la plus facile à repérer côté serveur."""
    ordonnanceur = build_scheduler(engine, reglages, faux_mailer)

    assert ordonnanceur.get_job("scan:google_flights").trigger.jitter == JITTER_S


def test_un_scan_qui_deborde_nest_jamais_lance_en_double(engine, reglages, faux_mailer):
    """Un passage Playwright plus long que son intervalle est le cas nominal, pas l'exception :
    sans ces deux réglages, l'ordonnanceur empilerait les exécutions sur la même source et les
    ferait rattraper leur retard une par une."""
    ordonnanceur = build_scheduler(engine, reglages, faux_mailer)

    for identifiant in ("scan:google_flights", "digest", "purge"):
        job = ordonnanceur.get_job(identifiant)
        assert job.max_instances == 1
        assert job.coalesce is True


def test_toute_source_constructible_a_un_intervalle(reglages):
    """Ajouter une source à build_providers sans l'inscrire dans INTERVALLES ne se verrait qu'au
    démarrage, sous la forme d'une KeyError."""
    reglages_tout = Settings(enabled_providers=["google_flights", "transat"])

    assert {s.name for s in build_providers(reglages_tout)} <= set(INTERVALLES)


def test_le_digest_est_planifie_a_lheure_configuree(engine, reglages, faux_mailer):
    ordonnanceur = build_scheduler(engine, reglages, faux_mailer)

    digest = ordonnanceur.get_job("digest")
    assert isinstance(digest.trigger, CronTrigger)
    assert str(digest.trigger.timezone) == "America/Toronto"
    assert "hour='18'" in str(digest.trigger)
    # Sans cette ligne, décaler la minute du digest — par exemple à 18h05 — laissait la suite
    # entièrement verte : seule l'heure était vérifiée.
    assert "minute='0'" in str(digest.trigger)


def test_un_job_de_purge_quotidien_existe(engine, reglages, faux_mailer):
    ordonnanceur = build_scheduler(engine, reglages, faux_mailer)

    assert ordonnanceur.get_job("purge") is not None


def test_la_purge_est_planifiee_a_3h30_locales(engine, reglages, faux_mailer):
    """Le créneau de la purge n'était vérifié nulle part ailleurs que dans cette fonction : sans
    ces deux assertions, décaler son heure ou sa minute — par exemple en pleine fenêtre de scan —
    laissait la suite entièrement verte, alors que l'intérêt de 3h30 est justement d'éviter ce
    chevauchement."""
    ordonnanceur = build_scheduler(engine, reglages, faux_mailer)

    purge = ordonnanceur.get_job("purge")
    assert isinstance(purge.trigger, CronTrigger)
    assert str(purge.trigger.timezone) == "America/Toronto"
    assert "hour='3'" in str(purge.trigger)
    assert "minute='30'" in str(purge.trigger)


def test_lordonnanceur_nest_pas_demarre_a_la_construction(engine, reglages, faux_mailer):
    ordonnanceur = build_scheduler(engine, reglages, faux_mailer)

    assert ordonnanceur.running is False


def test_le_job_de_scan_enregistre_reellement_les_offres(
    engine, session, reglages, faux_mailer, fausse_source
):
    """Tout ce qui précède ne vérifie que la présence de jobs et la forme de leurs déclencheurs.
    Un ordonnanceur dont les fonctions ne feraient rien aurait exactement la même allure — et
    c'est précisément la panne que le design nomme comme risque principal, celle qu'on ne voit
    pas. Ce test-ci est le seul à faire tourner la chaîne complète : session, relevé, écriture."""
    trajet = _trajet(session)

    _job_scan(
        engine, reglages, faux_mailer, fausse_source(name="google_flights", offres=[(499, "TS")])
    )

    assert repo.daily_low_for(session, trajet.id, datetime.now(UTC).date()) is not None


def test_le_job_de_digest_envoie_reellement_un_courriel(engine, session, reglages, faux_mailer):
    _trajet(session)

    _job_digest(engine, reglages, faux_mailer)

    assert len(faux_mailer.envois) == 1


def test_le_job_de_purge_sexecute_sur_une_base_reelle(engine, session, reglages):
    """Sans trajet ni donnée ancienne il n'y a rien à supprimer ; ce qui est vérifié ici est que
    le job ouvre bien une session et traverse la purge sans lever."""
    _trajet(session)

    _job_purge(engine, reglages)


def test_les_jobs_sont_cables_avec_les_bons_arguments(engine, reglages, faux_mailer):
    """Les arguments sont passés positionnellement : deux d'entre eux intervertis donneraient un
    ordonnanceur qui se construit sans broncher et qui échoue à sa première exécution réelle,
    des heures plus tard."""
    ordonnanceur = build_scheduler(engine, reglages, faux_mailer)

    scan = ordonnanceur.get_job("scan:google_flights")
    assert scan.func is _job_scan
    assert scan.args[0] is engine
    assert scan.args[1] is reglages
    assert scan.args[2] is faux_mailer
    assert scan.args[3].name == "google_flights"

    # APScheduler normalise args en tuple : on compare donc sur une liste reconstruite.
    digest = ordonnanceur.get_job("digest")
    assert digest.func is _job_digest
    assert list(digest.args) == [engine, reglages, faux_mailer]

    purge = ordonnanceur.get_job("purge")
    assert purge.func is _job_purge
    assert list(purge.args) == [engine, reglages]
