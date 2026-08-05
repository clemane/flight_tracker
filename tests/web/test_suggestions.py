"""Route de suggestions : ce que htmx envoie, et ce que le fragment doit contenir."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from scrappervol.config import Settings
from scrappervol.web.app import create_app, get_session


@pytest.fixture
def client(engine, session) -> TestClient:
    application = create_app(engine, Settings(database_url="sqlite://"))
    application.dependency_overrides[get_session] = lambda: session
    yield TestClient(application)
    application.state.search_registry.arreter()


def test_la_saisie_arrive_sous_le_nom_du_champ(client: TestClient):
    """htmx nomme le paramètre d'après le champ (`origin=montr`), pas `q`."""
    reponse = client.get("/airports", params={"champ": "origin", "origin": "montr"})
    assert reponse.status_code == 200
    assert 'data-code="YUL"' in reponse.text


def test_le_parametre_q_reste_accepte(client: TestClient):
    """Pour interroger la route directement, hors du formulaire."""
    assert 'data-code="YUL"' in client.get("/airports", params={"q": "montr"}).text


def test_le_champ_vise_est_repercute_dans_le_fragment(client: TestClient):
    """Sans quoi la sélection remplirait le mauvais champ du formulaire."""
    reponse = client.get("/airports", params={"champ": "destination", "destination": "canc"})
    assert 'data-cible="destination"' in reponse.text


def test_une_saisie_trop_courte_ne_propose_rien(client: TestClient):
    """Une seule lettre listerait les aéroports les plus desservis du monde, hors sujet."""
    reponse = client.get("/airports", params={"champ": "origin", "origin": "m"})
    assert reponse.status_code == 200
    assert "data-code" not in reponse.text


def test_une_saisie_sans_correspondance_ne_propose_rien(client: TestClient):
    reponse = client.get("/airports", params={"champ": "origin", "origin": "zzzzz"})
    assert "data-code" not in reponse.text


def test_le_fragment_ne_contient_pas_de_page_entiere(client: TestClient):
    """Il est injecté dans la page : une balise <html> y ferait un document imbriqué."""
    corps = client.get("/airports", params={"champ": "origin", "origin": "montr"}).text
    assert "<html" not in corps.lower()


def test_le_libelle_de_rappel_accompagne_chaque_proposition(client: TestClient):
    """C'est lui qui s'affiche sous le champ une fois le code posé."""
    corps = client.get("/airports", params={"champ": "origin", "origin": "canc"}).text
    assert 'data-libelle="Cancún, Mexique"' in corps


def test_un_champ_a_codes_multiples_ne_cherche_que_le_dernier(client: TestClient):
    """« YUL, YQB, mont » : les deux premiers sont choisis, seul le dernier est en cours de
    frappe. Chercher la chaîne entière ne ramènerait rien."""
    reponse = client.get(
        "/airports", params={"champ": "origins", "multiple": "1", "origins": "YUL, YQB, canc"}
    )
    # Cancún, en cours de frappe — et surtout pas Montréal, déjà choisi en tête de liste.
    assert 'data-code="CUN"' in reponse.text
    assert 'data-code="YUL"' not in reponse.text


def test_un_champ_simple_ne_decoupe_pas_sur_la_virgule(client: TestClient):
    """Sans le drapeau, la valeur entière est la saisie ; « YUL, mont » n'est pas un aéroport."""
    reponse = client.get("/airports", params={"champ": "origin", "origin": "YUL, mont"})
    assert "data-code" not in reponse.text


def test_les_champs_dun_trajet_acceptent_plusieurs_aeroports(client: TestClient):
    """Un trajet surveille plusieurs origines : la sélection doit compléter, non remplacer."""
    corps = client.get("/routes/new").text
    for champ in ("origins", "destinations"):
        assert f"hx-get=\"/airports?champ={champ}&amp;multiple=1\"" in corps
        assert f'id="suggestions-{champ}"' in corps
    assert corps.count("data-multiple") == 2


def test_le_formulaire_de_trajet_a_bien_lautocompletion(client: TestClient):
    """Le premier oubli fut celui-là : l'autocomplétion posée sur une page et pas sur l'autre."""
    corps = client.get("/routes/new").text
    assert corps.count("data-aeroport") == 2


def test_le_champ_de_recherche_du_formulaire_declare_sa_cible(client: TestClient):
    """La page doit câbler les deux champs sur la route, chacun vers sa propre boîte."""
    corps = client.get("/").text
    for champ in ("origin", "destination"):
        assert f'hx-get="/airports?champ={champ}"' in corps
        assert f'hx-target="#suggestions-{champ}"' in corps


def test_le_swap_est_declare_explicitement_sur_les_champs(client: TestClient):
    """Le formulaire déclare `outerHTML` pour ses résultats et htmx le transmet à ses
    descendants : sans cette reprise en main, la première réponse remplacerait la boîte
    elle-même, introuvable à la frappe suivante."""
    corps = client.get("/").text
    assert corps.count('hx-swap="innerHTML"') >= 2


def test_les_suggestions_reagissent_a_input_et_non_aux_touches(client: TestClient):
    """`keyup` laisserait sans suggestion le collage à la souris et la saisie assistée."""
    corps = client.get("/").text
    assert 'hx-trigger="input changed delay:150ms"' in corps
    assert "keyup" not in corps
