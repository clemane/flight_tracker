from fastapi import FastAPI
from fastapi.testclient import TestClient

from scrappervol.main import build_application


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
