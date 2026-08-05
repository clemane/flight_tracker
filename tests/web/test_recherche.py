from __future__ import annotations

import re
import threading
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from scrappervol.config import Settings
from scrappervol.core.types import FlightOffer, SearchQuery
from scrappervol.storage.models import DailyLow, Observation, ProviderHealth, Route
from scrappervol.web.app import create_app, get_now, get_session

MAINTENANT = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
FORMULAIRE = {
    "origin": "YUL",
    "destination": "CUN",
    "depart": "2026-11-03",
    "retour": "2026-11-10",
    "passengers": "1",
}


def _offre(prix: int, provider: str = "fausse") -> FlightOffer:
    return FlightOffer(
        provider=provider,
        origin="YUL",
        destination="CUN",
        depart_date=date(2026, 11, 3),
        return_date=date(2026, 11, 10),
        price_cad=prix,
        price_original=float(prix),
        currency_original="CAD",
        airline="Air Transat",
        stops=0,
        duration_minutes=425,
        deep_link="https://example.invalid/vol",
    )


class SourceImmediate:
    def __init__(self, nom: str = "rapide", prix: int = 533) -> None:
        self.name = nom
        self._prix = prix

    def search(self, query: SearchQuery) -> list[FlightOffer]:
        return [_offre(self._prix, self.name)]


class SourceRetenue:
    def __init__(self, nom: str = "lente") -> None:
        self.name = nom
        self.relacher = threading.Event()
        self.demarree = threading.Event()

    def search(self, query: SearchQuery) -> list[FlightOffer]:
        self.demarree.set()
        self.relacher.wait(timeout=5)
        return [_offre(900, self.name)]


@pytest.fixture
def app(engine, session):
    application = create_app(engine, Settings(database_url="sqlite://"))
    application.dependency_overrides[get_session] = lambda: session
    application.dependency_overrides[get_now] = lambda: MAINTENANT
    yield application
    application.state.search_registry.arreter()


@pytest.fixture
def client(app):
    return TestClient(app)


def _sources(app, *sources):
    app.state.build_providers = lambda reglages: list(sources)


def _attendre(predicat, timeout: float = 5.0) -> bool:
    horloge = threading.Event()
    for _ in range(int(timeout / 0.02)):
        if predicat():
            return True
        horloge.wait(0.02)
    return predicat()


def test_la_page_daccueil_porte_le_formulaire_de_recherche(client):
    corps = client.get("/").text

    assert 'action="/search"' in corps
    assert 'name="origin"' in corps
    assert 'name="destination"' in corps


def test_une_recherche_rend_un_fragment_et_non_une_page(client, app):
    """Le fragment est injecté dans la page existante : s'il héritait du gabarit de base, htmx
    imbriquerait un document entier dans le corps du document courant."""
    _sources(app, SourceImmediate())

    reponse = client.post("/search", data=FORMULAIRE)

    assert reponse.status_code == 200
    assert "<html" not in reponse.text
    assert "<body" not in reponse.text


def test_une_recherche_en_cours_arme_son_propre_rafraichissement(client, app):
    lente = SourceRetenue()
    _sources(app, SourceImmediate(), lente)
    try:
        reponse = client.post("/search", data=FORMULAIRE)

        assert 'hx-trigger="every 2s"' in reponse.text
        assert re.search(r'hx-get="/search/\w+"', reponse.text)
    finally:
        lente.relacher.set()


def test_le_rafraichissement_sarrete_une_fois_toutes_les_sources_rentrees(client, app):
    """Sans cet arrêt, la page continuerait d'interroger le serveur indéfiniment après la fin de
    la recherche."""
    _sources(app, SourceImmediate())
    reponse = client.post("/search", data=FORMULAIRE)
    run_id = re.search(r'/search/(\w+)', reponse.text).group(1)

    assert _attendre(lambda: "every 2s" not in client.get(f"/search/{run_id}").text)

    final = client.get(f"/search/{run_id}").text
    assert "hx-trigger" not in final
    assert "533" in final


def test_les_offres_saffichent_de_la_moins_chere_a_la_plus_chere(client, app):
    _sources(app, SourceImmediate("chere", 900), SourceImmediate("bon", 400))
    reponse = client.post("/search", data=FORMULAIRE)
    run_id = re.search(r'/search/(\w+)', reponse.text).group(1)

    assert _attendre(lambda: "every 2s" not in client.get(f"/search/{run_id}").text)
    corps = client.get(f"/search/{run_id}").text

    assert corps.index("400") < corps.index("900")
    # Un seul « meilleur prix » : le marquer partout reviendrait à ne le marquer nulle part.
    assert corps.count("data-meilleur") == 1


def test_une_source_en_panne_est_signalee_sans_masquer_les_autres(client, app):
    class Cassee:
        name = "cassee"

        def search(self, query):
            raise RuntimeError("sélecteur introuvable")

    _sources(app, SourceImmediate(), Cassee())
    reponse = client.post("/search", data=FORMULAIRE)
    run_id = re.search(r'/search/(\w+)', reponse.text).group(1)

    assert _attendre(lambda: "every 2s" not in client.get(f"/search/{run_id}").text)
    corps = client.get(f"/search/{run_id}").text

    assert 'data-statut="erreur"' in corps
    assert "533" in corps


def test_une_recherche_nécrit_rien_en_base(client, app, session):
    """L'isolation promise : une recherche manuelle ne doit ni nourrir l'historique — des dates
    arbitraires fausseraient la médiane — ni toucher l'état de santé, sous peine de masquer une
    panne du relevé automatique ou d'en déclencher une."""
    _sources(app, SourceImmediate())
    reponse = client.post("/search", data=FORMULAIRE)
    run_id = re.search(r'/search/(\w+)', reponse.text).group(1)
    assert _attendre(lambda: "every 2s" not in client.get(f"/search/{run_id}").text)

    assert session.exec(select(Observation)).all() == []
    assert session.exec(select(DailyLow)).all() == []
    assert session.exec(select(ProviderHealth)).all() == []


def test_une_recherche_expiree_le_dit_et_cesse_de_se_rafraichir(client):
    reponse = client.get("/search/inexistante")

    assert reponse.status_code == 200
    assert "hx-trigger" not in reponse.text
    assert "expiré" in reponse.text


@pytest.mark.parametrize(
    ("champ", "valeur", "attendu"),
    [
        ("origin", "XXXX", "trois lettres"),
        ("destination", "", "obligatoire"),
        ("retour", "", "aller simple"),
        ("depart", "2020-01-01", "postérieure"),
    ],
)
def test_une_saisie_invalide_est_refusee_avec_son_motif(client, app, champ, valeur, attendu):
    _sources(app, SourceImmediate())

    reponse = client.post("/search", data={**FORMULAIRE, champ: valeur})

    assert reponse.status_code == 422
    assert attendu in reponse.text
    assert "data-erreur" in reponse.text


def test_une_origine_identique_a_la_destination_est_refusee(client, app):
    _sources(app, SourceImmediate())

    reponse = client.post("/search", data={**FORMULAIRE, "destination": "YUL"})

    assert reponse.status_code == 422
    assert "identiques" in reponse.text


def test_une_recherche_sans_source_activee_le_dit(client, app):
    """Sans ce garde-fou, `ENABLED_PROVIDERS` vidé produirait une recherche éternellement
    « terminée » et sans résultat, impossible à distinguer d'une liaison sans vol."""
    _sources(app)

    reponse = client.post("/search", data=FORMULAIRE)

    assert reponse.status_code == 422
    assert "aucune source" in reponse.text


def test_surveiller_une_recherche_cree_le_trajet_correspondant(client, app, session):
    _sources(app, SourceImmediate())
    reponse = client.post("/search", data=FORMULAIRE)
    run_id = re.search(r'/search/(\w+)', reponse.text).group(1)

    suite = client.post(f"/search/{run_id}/watch", follow_redirects=False)

    assert suite.status_code == 303
    assert suite.headers["location"] == "/routes"
    trajets = session.exec(select(Route)).all()
    assert len(trajets) == 1
    assert trajets[0].origins == ["YUL"]
    assert trajets[0].destinations == ["CUN"]
    assert trajets[0].policy_params == {"depart": "2026-11-03", "retour": "2026-11-10"}
    assert trajets[0].active


def test_surveiller_une_recherche_inconnue_est_un_404(client):
    assert client.post("/search/inexistante/watch").status_code == 404


def test_lecart_a_la_mediane_nest_montre_que_si_un_trajet_surveille_a_de_lhistorique(
    client, app, session
):
    """Une recherche libre n'a pas d'historique propre. Afficher un écart calculé sur trois
    relevés donnerait une fausse assurance sur un chiffre qui ne veut rien dire."""
    _sources(app, SourceImmediate())

    reponse = client.post("/search", data=FORMULAIRE)
    run_id = re.search(r'/search/(\w+)', reponse.text).group(1)
    assert _attendre(lambda: "every 2s" not in client.get(f"/search/{run_id}").text)
    assert "data-ecart" not in client.get(f"/search/{run_id}").text

    trajet = Route(
        label="Cancún",
        origins=["YUL"],
        destinations=["CUN"],
        policy_params={},
        created_at=MAINTENANT,
    )
    session.add(trajet)
    session.commit()
    for jour in range(1, 21):
        session.add(
            DailyLow(
                route_id=trajet.id,
                day=date(2026, 7, jour),
                price_cad=800,
                provider="google_flights",
            )
        )
    session.commit()

    corps = client.get(f"/search/{run_id}").text
    assert "data-ecart" in corps
    assert "33 % sous la médiane" in corps


def _recherche_terminee(client, app, sources=None):
    _sources(app, *(sources or [SourceImmediate()]))
    reponse = client.post("/search", data=FORMULAIRE)
    run_id = re.search(r"/search/(\w+)", reponse.text).group(1)
    assert _attendre(lambda: "every 2s" not in client.get(f"/search/{run_id}").text)
    return run_id


def _trajet_avec_historique(session, destinations: list[str], jours: int) -> Route:
    trajet = Route(
        label="référence",
        origins=["YUL"],
        destinations=destinations,
        policy_params={},
        created_at=MAINTENANT,
    )
    session.add(trajet)
    session.commit()
    for jour in range(1, jours + 1):
        session.add(
            DailyLow(
                route_id=trajet.id,
                day=date(2026, 7, jour),
                price_cad=800,
                provider="google_flights",
            )
        )
    session.commit()
    return trajet


def test_un_historique_trop_court_ne_donne_pas_décart(client, app, session):
    """Le seuil des 14 jours vaut ici comme ailleurs : trois relevés ne font pas une médiane, et
    un écart affiché sur si peu inviterait à acheter sur un chiffre sans fondement."""
    _trajet_avec_historique(session, ["CUN"], jours=3)

    run_id = _recherche_terminee(client, app)

    assert "data-ecart" not in client.get(f"/search/{run_id}").text


def test_la_mediane_dun_trajet_vers_une_autre_destination_nest_pas_utilisee(
    client, app, session
):
    """Un trajet YUL → CDG bien fourni ne dit rien du prix d'un YUL → CUN. S'en servir comme
    référence produirait un « sous la médiane » calculé sur une liaison sans rapport."""
    _trajet_avec_historique(session, ["CDG"], jours=30)

    run_id = _recherche_terminee(client, app)

    assert "data-ecart" not in client.get(f"/search/{run_id}").text
