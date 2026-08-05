"""Le kit de composants.

Ces tests protègent ce qui a motivé le kit : qu'une correction faite une fois s'applique partout.
Ils s'assurent donc qu'aucune page ne rédige plus ses champs à la main, et que les macros rendent
un balisage valide.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scrappervol.config import Settings
from scrappervol.web.app import create_app, get_session

GABARITS = Path(__file__).resolve().parents[2] / "scrappervol" / "web" / "templates"
PAGES = ["/", "/routes", "/routes/new", "/health"]


@pytest.fixture
def client(engine, session) -> TestClient:
    application = create_app(engine, Settings(database_url="sqlite://"))
    application.dependency_overrides[get_session] = lambda: session
    yield TestClient(application)
    application.state.search_registry.arreter()


def gabarits_de_page() -> list[Path]:
    """Les gabarits de page, hors kit lui-même et hors base."""
    exclus = {"ui.html.j2", "base.html.j2", "_suggestions.html.j2"}
    return [p for p in GABARITS.glob("*.j2") if p.name not in exclus]


@pytest.mark.parametrize("gabarit", gabarits_de_page(), ids=lambda p: p.name)
def test_aucune_page_ne_redige_ses_champs_a_la_main(gabarit: Path):
    """Un `<input>` écrit en dur échappe aux corrections faites dans le kit — c'est exactement
    ainsi que l'autocomplétion avait été posée sur une page et oubliée sur l'autre."""
    contenu = gabarit.read_text(encoding="utf-8")
    assert "<input" not in contenu, f"{gabarit.name} contient un <input> hors du kit"


@pytest.mark.parametrize("gabarit", gabarits_de_page(), ids=lambda p: p.name)
def test_aucune_page_ne_redige_ses_badges_a_la_main(gabarit: Path):
    contenu = gabarit.read_text(encoding="utf-8")
    assert 'class="badge' not in contenu, f"{gabarit.name} écrit un badge hors du kit"


@pytest.mark.parametrize("page", PAGES)
def test_chaque_page_rend_un_balisage_equilibre(client: TestClient, page: str):
    """Une macro qui oublierait de refermer une balise passerait inaperçue à l'œil."""
    corps = client.get(page).text
    for balise in ("div", "form", "span"):
        ouvertes = len(re.findall(rf"<{balise}[\s>]", corps))
        fermees = len(re.findall(rf"</{balise}>", corps))
        assert ouvertes == fermees, f"{page} : <{balise}> ouvert {ouvertes}× fermé {fermees}×"


def rendre(source: str) -> str:
    """Rend un fragment avec l'environnement réel de l'application, `trim_blocks` compris."""
    from scrappervol.web.app import _env

    return _env.from_string('{% import "ui.html.j2" as ui %}' + source).render()


def test_les_attributs_dun_champ_restent_separes():
    """`trim_blocks` avale les sauts de ligne : sans espace explicite, la macro produirait
    `placeholder="x"required`, que les navigateurs rattrapent mais qui reste invalide."""
    rendu = rendre(
        '{{ ui.champ("d", "D", type="number", placeholder="p", requis=true, min=1, max=9) }}'
    )
    for attendu in (' placeholder="p"', " required", ' min="1"', ' max="9"'):
        assert attendu in rendu, f"{attendu!r} manque ou se colle à son voisin : {rendu!r}"
    assert not re.search(r'"(placeholder|required|min|max|step)=', rendu)


def test_un_champ_daeroport_declare_tout_ce_quil_faut_a_lautocompletion():
    """La macro est le seul endroit où ce câblage existe : s'il s'en échappe un attribut, aucune
    page n'a de suggestions."""
    rendu = rendre('{{ ui.champ_aeroport("origin", "Départ") }}')
    for attendu in (
        "data-aeroport",
        'hx-get="/airports?champ=origin"',
        'hx-target="#suggestions-origin"',
        'hx-swap="innerHTML"',
        'id="suggestions-origin"',
        'id="choix-origin"',
        'autocomplete="off"',
    ):
        assert attendu in rendu, f"{attendu!r} manque au champ d'aéroport"


def test_un_champ_daeroport_multiple_le_signale_des_deux_cotes():
    """Le navigateur en a besoin pour compléter au lieu de remplacer, le serveur pour ne chercher
    que le dernier code."""
    rendu = rendre('{{ ui.champ_aeroport("origins", "Origines", multiple=true) }}')
    assert " data-multiple" in rendu
    assert "multiple=1" in rendu


@pytest.mark.parametrize("page", PAGES)
def test_chaque_champ_porte_une_etiquette_liee(client: TestClient, page: str):
    """Vérifié dans les deux sens : un `for` qui ne désigne aucun champ laisse le libellé muet,
    et un champ sans `for` qui le vise n'a plus de libellé du tout pour un lecteur d'écran."""
    corps = client.get(page).text
    identifiants = set(re.findall(r'<(?:input|select)[^>]*\bid="([^"]+)"', corps))
    vises = set(re.findall(r'<label[^>]*\bfor="([^"]+)"', corps))

    for cible in vises:
        assert cible in identifiants, f'{page} : <label for="{cible}"> ne vise aucun champ'
    for ident in identifiants:
        assert ident in vises, f'{page} : le champ "{ident}" n\'a pas d\'étiquette'


def test_le_kit_est_bien_la_source_unique_des_champs():
    """Si ce compte tombe à zéro, les macros ne servent plus et la duplication est revenue."""
    kit = (GABARITS / "ui.html.j2").read_text(encoding="utf-8")
    assert kit.count("{% macro ") >= 7
    utilisateurs = [
        p for p in gabarits_de_page() if 'import "ui.html.j2"' in p.read_text(encoding="utf-8")
    ]
    assert len(utilisateurs) >= 5
