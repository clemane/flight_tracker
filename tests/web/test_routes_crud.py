import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from scrappervol.config import Settings
from scrappervol.core.types import DatePolicyKind
from scrappervol.storage.models import Route
from scrappervol.web.app import create_app, get_session


@pytest.fixture
def client(engine, session):
    application = create_app(engine, Settings())
    application.dependency_overrides[get_session] = lambda: session
    return TestClient(application)


FORMULAIRE = {
    "label": "Paris au printemps",
    "origins": "YUL, YQB",
    "destinations": "CDG",
    "date_policy": "fixed",
    "trip_type": "round_trip",
    "passengers": "1",
    "depart": "2027-03-12",
    "retour": "2027-03-22",
    "flex_days": "3",
    "exception_threshold": "0.40",
}


def test_la_page_des_trajets_repond(client):
    assert client.get("/routes").status_code == 200


def test_creation_dun_trajet(client, session):
    reponse = client.post("/routes", data=FORMULAIRE, follow_redirects=False)

    assert reponse.status_code in (200, 303)
    trajet = session.exec(select(Route)).one()
    assert trajet.label == "Paris au printemps"
    assert trajet.origins == ["YUL", "YQB"]
    assert trajet.policy_params["flex_days"] == 3
    assert trajet.active is True


def test_un_formulaire_invalide_est_refuse_et_explique(client, session):
    reponse = client.post("/routes", data={**FORMULAIRE, "label": ""})

    assert reponse.status_code == 422
    assert "libellé" in reponse.text
    assert session.exec(select(Route)).all() == []


def test_modification_dun_trajet(client, session):
    client.post("/routes", data=FORMULAIRE)
    trajet = session.exec(select(Route)).one()

    client.post(f"/routes/{trajet.id}", data={**FORMULAIRE, "label": "Paris en avril"})

    session.refresh(trajet)
    assert trajet.label == "Paris en avril"


def test_le_formulaire_de_modification_est_prerempli(client, session):
    client.post("/routes", data=FORMULAIRE)
    trajet = session.exec(select(Route)).one()

    corps = client.get(f"/routes/{trajet.id}/edit").text

    assert "Paris au printemps" in corps
    assert "YUL, YQB" in corps or "YUL,YQB" in corps


def test_activation_et_desactivation(client, session):
    client.post("/routes", data=FORMULAIRE)
    trajet = session.exec(select(Route)).one()

    client.post(f"/routes/{trajet.id}/toggle")
    session.refresh(trajet)
    assert trajet.active is False

    client.post(f"/routes/{trajet.id}/toggle")
    session.refresh(trajet)
    assert trajet.active is True


def test_suppression_dun_trajet(client, session):
    client.post("/routes", data=FORMULAIRE)
    trajet = session.exec(select(Route)).one()

    client.post(f"/routes/{trajet.id}/delete")

    assert session.exec(select(Route)).all() == []


def test_agir_sur_un_trajet_inexistant_retourne_404(client):
    assert client.post("/routes/999/toggle").status_code == 404
    assert client.post("/routes/999/delete").status_code == 404
    assert client.get("/routes/999/edit").status_code == 404


def test_les_champs_de_politique_sont_servis_a_la_demande(client):
    fixe = client.get("/routes/policy-fields", params={"date_policy": "fixed"}).text
    fenetre = client.get("/routes/policy-fields", params={"date_policy": "window"}).text
    flexible = client.get("/routes/policy-fields", params={"date_policy": "flexible"}).text

    assert "flex_days" in fixe
    assert "mois" in fenetre
    assert "horizon_mois" in flexible
    assert "<html" not in fixe


def test_une_politique_inconnue_retourne_422(client):
    reponse = client.get("/routes/policy-fields", params={"date_policy": "n_importe_quoi"})

    assert reponse.status_code == 422


def test_creation_dun_trajet_en_politique_fenetre(client, session):
    client.post(
        "/routes",
        data={
            "label": "Sud cet hiver",
            "origins": "YUL",
            "destinations": "CUN, PUJ",
            "date_policy": "window",
            "mois": "2027-01, 2027-02",
            "sejour_min": "7",
            "sejour_max": "10",
        },
    )

    trajet = session.exec(select(Route)).one()
    assert trajet.date_policy is DatePolicyKind.WINDOW
    assert trajet.policy_params["mois"] == ["2027-01", "2027-02"]
