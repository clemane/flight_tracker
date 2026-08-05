import logging
import threading
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from scrappervol.main import build_application, main


def test_lapplication_se_construit(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SMTP_HOST", "")

    application = build_application()

    assert isinstance(application, FastAPI)


def test_le_schema_est_cree_au_demarrage_et_les_pages_repondent(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("ENABLED_PROVIDERS", "")

    with TestClient(build_application()) as client:
        assert client.get("/").status_code == 200
        assert client.get("/routes").status_code == 200
        assert client.get("/health").status_code == 200

    assert (tmp_path / "test.db").exists()


def test_un_trajet_cree_survit_a_la_requete_qui_la_cree(tmp_path, monkeypatch):
    """Le seul test qui exerce la vraie session de service.

    Partout ailleurs, `get_session` est remplacé par une session de test que rien ne referme
    entre la requête et l'assertion. Un trajet ajouté sans être validé y resterait visible.
    Ici la session est celle du service : elle se referme à la fin de la requête, et seul ce
    qui a été validé subsiste.
    """
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("ENABLED_PROVIDERS", "")

    with TestClient(build_application()) as client:
        client.post(
            "/routes",
            data={
                "label": "Lisbonne en mai",
                "origins": "YUL",
                "destinations": "LIS",
                "date_policy": "fixed",
                "depart": "2027-05-04",
                "retour": "2027-05-18",
            },
        )

        assert "Lisbonne en mai" in client.get("/routes").text


def test_lordonnanceur_demarre_et_sarrete_avec_lapplication(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("ENABLED_PROVIDERS", "")

    application = build_application()
    with TestClient(application):
        assert application.state.scheduler.running is True

    assert application.state.scheduler.running is False


def test_larret_de_lordonnanceur_nattend_pas_un_job_en_cours(tmp_path, monkeypatch):
    """`ordonnanceur.shutdown(wait=False)` doit rendre la main tout de suite.

    C'est tout l'intérêt de `wait=False` plutôt que `wait=True` : ce dernier bloquerait la
    fermeture de l'application jusqu'à la fin de tout job en cours d'exécution.
    """
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("ENABLED_PROVIDERS", "")

    demarre = threading.Event()

    def job_lent():
        demarre.set()
        time.sleep(2)

    application = build_application()
    with TestClient(application):
        application.state.scheduler.add_job(job_lent)
        assert demarre.wait(timeout=1), "le job de test n'a pas démarré à temps"
        debut = time.monotonic()

    duree = time.monotonic() - debut
    assert duree < 1, f"la fermeture a attendu la fin du job en cours ({duree:.2f} s)"


def test_le_message_de_demarrage_liste_les_sources_actives(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("ENABLED_PROVIDERS", "google_flights,transat")

    application = build_application()
    with caplog.at_level(logging.INFO, logger="scrappervol.main"), TestClient(application):
        pass

    assert "ScrapperVol démarré — sources : google_flights, transat" in caplog.text
    assert "ScrapperVol arrêté" in caplog.text


def test_le_message_de_demarrage_indique_aucune_source_si_la_liste_est_vide(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("ENABLED_PROVIDERS", "")

    application = build_application()
    with caplog.at_level(logging.INFO, logger="scrappervol.main"), TestClient(application):
        pass

    assert "ScrapperVol démarré — sources : aucune" in caplog.text


def test_le_mailer_construit_est_bien_transmis_a_lordonnanceur(tmp_path, monkeypatch):
    """Vérifie le câblage plutôt que le comportement du mailer lui-même (déjà testé ailleurs).

    Une mutation qui remplacerait le mailer transmis par `None` ne casserait aucune requête HTTP
    du test (aucun job ne s'exécute pendant la durée d'un test) : seule une vérification directe
    de l'objet transmis à `build_scheduler` la détecte.
    """
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("ENABLED_PROVIDERS", "")

    import scrappervol.main as module_principal
    from scrappervol.scheduler.app import build_scheduler as vrai_build_scheduler

    sentinelle = object()
    recu = {}

    def faux_build_mailer(settings):
        return sentinelle

    def faux_build_scheduler(engine, settings, mailer):
        recu["mailer"] = mailer
        return vrai_build_scheduler(engine, settings, mailer)

    monkeypatch.setattr(module_principal, "build_mailer", faux_build_mailer)
    monkeypatch.setattr(module_principal, "build_scheduler", faux_build_scheduler)

    module_principal.build_application()

    assert recu["mailer"] is sentinelle


def test_main_configure_le_journal_et_lance_uvicorn(tmp_path, monkeypatch):
    """`main()` n'est appelée par aucun autre test : sans celui-ci, son câblage (hôte, port,
    niveau de journalisation) échappe entièrement à la suite. `uvicorn.run` et
    `logging.basicConfig` sont remplacés pour ne pas réellement démarrer de serveur ni
    reconfigurer le journal du reste de la suite.
    """
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("ENABLED_PROVIDERS", "")

    appels_journal = {}
    appels_uvicorn = {}

    def faux_basic_config(**kwargs):
        appels_journal.update(kwargs)

    def faux_run(app, **kwargs):
        appels_uvicorn["app"] = app
        appels_uvicorn.update(kwargs)

    monkeypatch.setattr("logging.basicConfig", faux_basic_config)
    monkeypatch.setattr("uvicorn.run", faux_run)

    main()

    assert appels_journal.get("level") == logging.INFO
    assert appels_uvicorn["host"] == "0.0.0.0"
    assert appels_uvicorn["port"] == 8080
    assert appels_uvicorn["log_level"] == "info"
    assert isinstance(appels_uvicorn["app"], FastAPI)
