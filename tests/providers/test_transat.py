from datetime import date, timedelta
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from scrappervol.core.types import SearchQuery, TripType
from scrappervol.providers.base import EmptyResultError, ProviderError
from scrappervol.providers.transat import (
    TransatProvider,
    _fare_btn_moins_cher,
    _franchir_modale_upsell,
    _selectionner_tarif,
    _sous_tarif_moins_cher,
    _verifier_etape_sommaire,
    parse_summary,
)

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "transat_summary_yul_cun.html"

# La requête qui correspond exactement à ce qui a produit la fixture réelle (capture de la
# reconnaissance de la tâche 21, page /summary, total 533,41 $, vols TS938 / TS539).
REQUETE_REELLE = SearchQuery(
    origin="YUL",
    destination="CUN",
    depart_date=date(2026, 11, 3),
    return_date=date(2026, 11, 10),
    trip_type=TripType.ROUND_TRIP,
)


@pytest.fixture
def html_reel():
    return FIXTURE.read_text(encoding="utf-8")


def _carte_vol_html(
    *,
    origine: str = "Montréal (YUL)",
    destination: str = "Cancun (CUN)",
    airline_label: str = "Exploité par Air Transat",
    duree: str = "4h 50min",
    numeros_vol: tuple[str, ...] = ("TS938",),
    avec_compagnie: bool = True,
    avec_duree: bool = True,
) -> str:
    """Construit un bloc `.flight-card` minimal mais fidèle aux sélecteurs relevés dans la fixture
    réelle (tests/fixtures/transat_summary_yul_cun.html), pour isoler un seul comportement à la
    fois sans dépendre de la mise en page complète de la page réelle."""
    bloc_compagnie = (
        f'<div class="common-airline">'
        f'<button class="panel-airlineType-btn" aria-label="{airline_label}"></button>'
        f"</div>"
        if avec_compagnie
        else ""
    )
    bloc_duree = (
        f'<div class="common-time"><div class="common-duration">'
        f'<span class="flightTime">{duree}</span></div></div>'
        if avec_duree
        else ""
    )
    numeros_html = "".join(f'<span class="panel-number">{n}</span>' for n in numeros_vol)
    return (
        '<div class="flight-card">'
        f"{bloc_duree}"
        f'<div class="common-location">'
        f'<span class="cityAirport">{origine}</span>'
        f'<span class="cityAirport">{destination}</span>'
        f"</div>"
        f"{bloc_compagnie}"
        f'<div class="panel-type">{numeros_html}</div>'
        f"</div>"
    )


def _page_resume_html(
    *,
    prix: str | None = "533,41$",
    carte_aller: str | None = None,
    carte_retour: str | None = None,
) -> str:
    if carte_aller is None:
        carte_aller = _carte_vol_html()
    if carte_retour is None:
        carte_retour = _carte_vol_html(
            origine="Cancun (CUN)",
            destination="Montréal (YUL)",
            numeros_vol=("TS539",),
            duree="4h 10min",
        )
    bloc_total = (
        f'<div class="flight-container-total"><div class="label">Total</div>'
        f'<div class="value"><div class="price">{prix}</div></div></div>'
        if prix is not None
        else ""
    )
    cartes = "".join(c for c in (carte_aller, carte_retour) if c)
    return f"<html><body>{cartes}{bloc_total}</body></html>"


# --- Sur la fixture réelle -----------------------------------------------------------------------


def test_parse_summary_traduit_la_fixture_reelle(html_reel):
    """Le contrat de base, mesuré sur la page qu'Air Transat a réellement renvoyée : une seule
    offre, le total aller-retour, pas un prix d'aller seul par classe tarifaire."""
    offres = parse_summary(html_reel, REQUETE_REELLE)

    assert len(offres) == 1
    (offre,) = offres
    assert offre.provider == "transat"
    assert offre.origin == "YUL"
    assert offre.destination == "CUN"
    assert offre.price_cad == 533
    assert offre.price_original == 533.0
    assert offre.currency_original == "CAD"
    assert offre.airline == "Air Transat"
    assert offre.stops == 0
    assert offre.duration_minutes == 4 * 60 + 50 + 4 * 60 + 10
    assert offre.depart_date == REQUETE_REELLE.depart_date
    assert offre.return_date == REQUETE_REELLE.return_date
    assert offre.deep_link.startswith("https://www.airtransat.com/")


def test_parse_summary_conserve_le_texte_source_et_les_numeros_de_vol_dans_raw(html_reel):
    (offre,) = parse_summary(html_reel, REQUETE_REELLE)

    assert offre.raw["price_text"] == "533,41$"
    assert offre.raw["flight_numbers"] == ["TS938", "TS539"]


# --- Sur des pages synthétiques, pour isoler chaque comportement ---------------------------------


def test_parse_summary_reporte_la_date_de_retour_de_la_requete():
    """Cette page ne montre pas de curseur de dates confirmant une date affichée (contrairement à
    l'ancienne étape « departure ») : les deux dates viennent nécessairement de la requête."""
    html = _page_resume_html()

    (offre,) = parse_summary(html, REQUETE_REELLE)

    assert offre.depart_date == REQUETE_REELLE.depart_date
    assert offre.return_date == REQUETE_REELLE.return_date


def test_parse_summary_lit_le_prix_dans_le_texte_pas_une_valeur_fixe():
    """Si l'extraction du prix était remplacée par une constante, ce test le détecterait : le
    total choisi ici (612,34 $) ne correspond à aucune valeur codée en dur ailleurs dans le
    module."""
    html = _page_resume_html(prix="612,34$")

    (offre,) = parse_summary(html, REQUETE_REELLE)

    assert offre.price_cad == 612
    assert offre.raw["price_text"] == "612,34$"


def test_parse_summary_lit_un_prix_avec_espace_insecable():
    html = _page_resume_html(prix="1\xa0229$")

    (offre,) = parse_summary(html, REQUETE_REELLE)

    assert offre.price_cad == 1229


def test_parse_summary_interprete_la_virgule_comme_separateur_decimal():
    """Piège propre à cette page : "533,41$" doit se lire comme 533 $ (arrondi), pas comme
    53 341 $. Sans distinguer virgule décimale et espace de milliers, le total serait gonflé d'un
    facteur 100 — la panne la plus coûteuse possible sur cette fonction."""
    html = _page_resume_html(prix="533,41$")

    (offre,) = parse_summary(html, REQUETE_REELLE)

    assert offre.price_cad == 533


def test_parse_summary_ecarte_un_prix_illisible():
    html = _page_resume_html(prix="Bientôt disponible")

    assert parse_summary(html, REQUETE_REELLE) == []


def test_parse_summary_ecarte_un_prix_nul():
    """Un « 0$ » est un défaut d'affichage, pas une aubaine : le laisser passer produirait un
    plus bas absolu du trajet et empoisonnerait durablement la base de comparaison."""
    html = _page_resume_html(prix="0$")

    assert parse_summary(html, REQUETE_REELLE) == []


def test_parse_summary_rend_aucune_offre_si_le_total_est_absent():
    html = _page_resume_html(prix=None)

    assert parse_summary(html, REQUETE_REELLE) == []


def test_parse_summary_rend_une_liste_vide_si_la_page_est_vide():
    """Une page sans structure exploitable ne doit jamais lever d'exception : c'est au provider,
    pas à cette fonction pure, de traduire l'absence de résultat en échec."""
    assert parse_summary("<html><body></body></html>", REQUETE_REELLE) == []


def test_parse_summary_ecarte_si_moins_de_deux_blocs_de_vol():
    html = _page_resume_html(carte_retour="")

    assert parse_summary(html, REQUETE_REELLE) == []


def test_parse_summary_ecarte_si_les_aeroports_du_bloc_aller_ne_correspondent_pas():
    html = _page_resume_html(
        carte_aller=_carte_vol_html(origine="Toronto (YYZ)", destination="Cancun (CUN)")
    )

    assert parse_summary(html, REQUETE_REELLE) == []


def test_parse_summary_ecarte_si_les_aeroports_du_bloc_retour_ne_correspondent_pas():
    html = _page_resume_html(
        carte_retour=_carte_vol_html(
            origine="Cancun (CUN)", destination="Toronto (YYZ)", numeros_vol=("TS539",)
        )
    )

    assert parse_summary(html, REQUETE_REELLE) == []


def test_parse_summary_ecarte_si_un_bloc_a_plusieurs_numeros_de_vol():
    """Le balisage d'un vol avec escale n'a jamais été observé dans une fixture réelle. Plutôt que
    d'inventer une formule sur le nombre d'escales, l'offre entière est écartée."""
    html = _page_resume_html(carte_aller=_carte_vol_html(numeros_vol=("TS938", "TS200")))

    assert parse_summary(html, REQUETE_REELLE) == []


def test_parse_summary_ecarte_si_un_bloc_est_sans_compagnie():
    html = _page_resume_html(carte_aller=_carte_vol_html(avec_compagnie=False))

    assert parse_summary(html, REQUETE_REELLE) == []


def test_parse_summary_tolere_une_duree_absente():
    html = _page_resume_html(carte_aller=_carte_vol_html(avec_duree=False))

    (offre,) = parse_summary(html, REQUETE_REELLE)

    assert offre.duration_minutes is None


def test_parse_summary_additionne_les_deux_compagnies_si_elles_different():
    html = _page_resume_html(
        carte_aller=_carte_vol_html(airline_label="Exploité par Air Transat"),
        carte_retour=_carte_vol_html(
            origine="Cancun (CUN)",
            destination="Montréal (YUL)",
            numeros_vol=("TS539",),
            airline_label="Exploité par Compagnie Fictive",
        ),
    )

    (offre,) = parse_summary(html, REQUETE_REELLE)

    assert offre.airline == "Air Transat / Compagnie Fictive"


# --- Sélection du tarif le moins cher, via une fausse page Playwright ----------------------------
#
# `_fare_btn_moins_cher`, `_sous_tarif_moins_cher` et `_franchir_modale_upsell` prennent une
# `Page` Playwright en argument, mais leur logique de décision (quel prix retenir, quel bouton
# cliquer) ne dépend que de ce que `.locator(...)` rend. `_FakePage`/`_FakeLocator` réimplantent
# juste assez de l'API Playwright par-dessus `BeautifulSoup.select()` (qui interprète les mêmes
# sélecteurs CSS) pour tester cette logique de décision hors ligne, sans navigateur.


class _FakeLocator:
    def __init__(self, tags, clics):
        self._tags = list(tags)
        self._clics = clics

    def count(self) -> int:
        return len(self._tags)

    def nth(self, i: int) -> "_FakeLocator":
        return _FakeLocator([self._tags[i]], self._clics)

    @property
    def first(self) -> "_FakeLocator":
        return self.nth(0)

    def locator(self, selecteur: str) -> "_FakeLocator":
        resultats = []
        for tag in self._tags:
            resultats.extend(tag.select(selecteur))
        return _FakeLocator(resultats, self._clics)

    def is_visible(self) -> bool:
        if not self._tags:
            return False
        noeud = self._tags[0]
        while noeud is not None and hasattr(noeud, "has_attr"):
            if noeud.has_attr("hidden"):
                return False
            noeud = noeud.parent
        return True

    def inner_text(self) -> str:
        return self._tags[0].get_text(strip=True)

    def click(self) -> None:
        self._clics.append(self._tags[0])


class _FakePage:
    """Simule juste ce que `_selectionner_tarif` et ses aides appellent sur une `Page` réelle."""

    def __init__(self, html: str):
        self._soup = BeautifulSoup(html, "html.parser")
        self.clics: list = []
        self.url = "https://www.airtransat.com/fr-CA/flight-search-result/departure"

    def locator(self, selecteur: str) -> _FakeLocator:
        return _FakeLocator(self._soup.select(selecteur), self.clics)

    def wait_for_timeout(self, _ms: int) -> None:
        pass

    def wait_for_load_state(self, _state: str, timeout: int | None = None) -> None:
        pass


def _fare_btn_html(prix: str, *, categorie: str = "eco", cache: bool = False) -> str:
    """`.fare-btn` seul ne suffit pas : le sélecteur de production
    (`.co-shopResult-flightResult-fareClassesBtn .fare-btn`) exige cet ancêtre précis, d'où
    l'enveloppe ici — sans elle, `_fare_btn_moins_cher` ne trouverait jamais rien dans ces pages
    synthétiques, quel que soit le prix inscrit."""
    attr_hidden = " hidden" if cache else ""
    return (
        f'<div class="co-shopResult-flightResult-fareClassesBtn"{attr_hidden}>'
        f'<button class="fare-btn {categorie}"><span class="expandFare">'
        f'<span class="expandFare-startFrom">À partir de '
        f'<span class="expandFare-price">{prix}</span></span></span></button></div>'
    )


def _sous_tarif_html(titre: str, prix: str, *, cache: bool = False) -> str:
    attr_hidden = " hidden" if cache else ""
    return (
        f'<div class="container">'
        f'<span class="title">{titre}</span><span class="price">{prix}</span>'
        f'<button class="co-btn co-btn-level1" aria-label="Sélectionner"{attr_hidden}>'
        f"Sélectionner</button></div>"
    )


def _fare_panel_html(sous_tarifs: str, *, cache: bool) -> str:
    return f'<div class="fare-panel"{" hidden" if cache else ""}>{sous_tarifs}</div>'


def test_fare_btn_moins_cher_retient_le_minimum_pas_le_premier_visible():
    """L'ordre au DOM place le prix le plus haut en premier : si l'implémentation retenait « le
    premier visible » plutôt que le minimum, ce test le détecterait."""
    html = (
        "<html><body>"
        + _fare_btn_html("687$", categorie="club")
        + _fare_btn_html("297$", categorie="eco")
        + "</body></html>"
    )
    page = _FakePage(html)

    bouton = _fare_btn_moins_cher(page)

    assert bouton is not None
    assert bouton.locator(".expandFare-price").first.inner_text() == "297$"


def test_fare_btn_moins_cher_ignore_un_bouton_cache_meme_moins_cher():
    html = (
        "<html><body>"
        + _fare_btn_html("50$", categorie="eco", cache=True)
        + _fare_btn_html("687$", categorie="club")
        + "</body></html>"
    )
    page = _FakePage(html)

    bouton = _fare_btn_moins_cher(page)

    assert bouton is not None
    assert bouton.locator(".expandFare-price").first.inner_text() == "687$"


def test_fare_btn_moins_cher_ignore_un_prix_illisible():
    html = (
        "<html><body>"
        + '<div class="fare"><button class="fare-btn eco"><span class="expandFare">'
        + '<span class="expandFare-startFrom">Non disponible</span></span></button></div>'
        + _fare_btn_html("687$", categorie="club")
        + "</body></html>"
    )
    page = _FakePage(html)

    bouton = _fare_btn_moins_cher(page)

    assert bouton is not None
    assert bouton.locator(".expandFare-price").first.inner_text() == "687$"


def test_fare_btn_moins_cher_rend_none_si_rien_nest_exploitable():
    html = "<html><body></body></html>"
    page = _FakePage(html)

    assert _fare_btn_moins_cher(page) is None


def test_sous_tarif_moins_cher_retient_le_minimum_du_panneau_visible():
    """L'ordre au DOM place le sous-tarif le plus cher en premier dans le panneau visible : si
    l'implémentation retenait « le premier visible » plutôt que le minimum, ce test le
    détecterait."""
    panneau_visible = _sous_tarif_html("Eco Flex", "502$") + _sous_tarif_html("Eco Budget", "297$")
    html = "<html><body>" + _fare_panel_html(panneau_visible, cache=False) + "</body></html>"
    page = _FakePage(html)

    bouton = _sous_tarif_moins_cher(page)

    assert bouton is not None
    conteneur_retenu = bouton._tags[0].find_parent("div", class_="container")
    assert conteneur_retenu.select_one(".title").get_text(strip=True) == "Eco Budget"
    assert conteneur_retenu.select_one(".price").get_text(strip=True) == "297$"


def test_sous_tarif_moins_cher_ignore_le_panneau_cache_meme_moins_cher():
    panneau_cache = _sous_tarif_html("Eco Budget", "50$")
    panneau_visible = _sous_tarif_html("Club Essentiel", "600$")
    html = (
        "<html><body>"
        + _fare_panel_html(panneau_cache, cache=True)
        + _fare_panel_html(panneau_visible, cache=False)
        + "</body></html>"
    )
    page = _FakePage(html)

    bouton = _sous_tarif_moins_cher(page)
    conteneurs = page.locator(".fare-panel .container")

    assert bouton is not None
    # Le sous-tarif retenu doit être celui du panneau visible (600$), jamais celui du panneau
    # caché (50$) bien que numériquement moindre.
    prix_retenus = [
        c.locator(".price").first.inner_text()
        for i in range(conteneurs.count())
        for c in [conteneurs.nth(i)]
        if c.is_visible()
    ]
    assert prix_retenus == ["600$"]


def test_sous_tarif_moins_cher_rend_none_si_rien_nest_exploitable():
    html = "<html><body></body></html>"
    page = _FakePage(html)

    assert _sous_tarif_moins_cher(page) is None


def test_franchir_modale_upsell_clique_poursuivre_jamais_whiterabbit():
    """Le test qui protège directement contre le piège documenté à la reconnaissance : cliquer
    sur `co-btn-whiterabbit` relèverait le tarif déjà choisi vers l'upsell."""
    html = (
        '<html><body><div id="fareUpsellModal">'
        '<div class="dialog-colFlex selectedFare">'
        '<button class="co-btn co-btn-level4">Poursuivre avec Eco Budget</button></div>'
        '<div class="dialog-colFlex recoFare">'
        '<button class="co-btn co-btn-whiterabbit">Sélectionner Eco Standard</button></div>'
        "</div></body></html>"
    )
    page = _FakePage(html)

    _franchir_modale_upsell(page)

    assert len(page.clics) == 1
    (clic,) = page.clics
    assert "co-btn-level4" in (clic.get("class") or [])
    assert "co-btn-whiterabbit" not in (clic.get("class") or [])


def test_franchir_modale_upsell_ne_fait_rien_si_absente():
    page = _FakePage("<html><body></body></html>")

    _franchir_modale_upsell(page)

    assert page.clics == []


def test_franchir_modale_upsell_leve_provider_error_si_bouton_de_confirmation_introuvable():
    html = '<html><body><div id="fareUpsellModal"><p>chargement…</p></div></body></html>'
    page = _FakePage(html)

    with pytest.raises(ProviderError, match="transat"):
        _franchir_modale_upsell(page)


def test_selectionner_tarif_choisit_la_categorie_et_le_sous_tarif_les_moins_chers():
    """Bout en bout sur `_selectionner_tarif` : la catégorie la moins chère (297$, pas 687$) est
    cliquée, puis le sous-tarif le moins cher de son panneau (297$, pas 352$) est cliqué."""
    panneau_eco = _sous_tarif_html("Eco Standard", "352$") + _sous_tarif_html("Eco Budget", "297$")
    panneau_club = _sous_tarif_html("Club", "900$")
    html = (
        "<html><body>"
        + _fare_btn_html("687$", categorie="club")
        + _fare_btn_html("297$", categorie="eco")
        + _fare_panel_html(panneau_eco, cache=False)
        + _fare_panel_html(panneau_club, cache=True)
        + "</body></html>"
    )
    page = _FakePage(html)

    _selectionner_tarif(page, "aller")

    assert len(page.clics) == 2
    clic_categorie, clic_sous_tarif = page.clics
    assert "eco" in (clic_categorie.get("class") or [])
    assert clic_sous_tarif.get("aria-label") == "Sélectionner"
    conteneur_clique = clic_sous_tarif.find_parent("div", class_="container")
    assert conteneur_clique.select_one(".title").get_text(strip=True) == "Eco Budget"


def test_selectionner_tarif_leve_provider_error_si_aucune_categorie_exploitable():
    page = _FakePage("<html><body></body></html>")

    with pytest.raises(ProviderError, match="aller"):
        _selectionner_tarif(page, "aller")


def test_selectionner_tarif_leve_provider_error_si_aucun_sous_tarif_apres_depliage():
    html = "<html><body>" + _fare_btn_html("297$", categorie="eco") + "</body></html>"
    page = _FakePage(html)

    with pytest.raises(ProviderError, match="retour"):
        _selectionner_tarif(page, "retour")


# --- La garde sur /summary, fonction pure -------------------------------------------------------


def test_verifier_etape_sommaire_ne_leve_rien_sur_summary():
    _verifier_etape_sommaire(
        "https://www.airtransat.com/fr-CA/flight-search-result/summary?outbound=x&inbound=y"
    )


def test_verifier_etape_sommaire_leve_provider_error_avec_lurl_atteinte():
    """L'erreur doit porter l'URL réellement atteinte, pas un message générique : c'est ce qui
    permet de savoir, sans rouvrir un navigateur, où le parcours est resté bloqué."""
    url = "https://www.airtransat.com/fr-CA/flight-search-result/return?outbound=x"

    with pytest.raises(ProviderError) as excinfo:
        _verifier_etape_sommaire(url)

    assert url in str(excinfo.value)


# --- TransatProvider -------------------------------------------------------------------------


def test_le_provider_leve_empty_result_quand_rien_nest_exploitable(monkeypatch):
    source = TransatProvider()
    monkeypatch.setattr(source, "_fetch", lambda query: "<html><body></body></html>")

    with pytest.raises(EmptyResultError):
        source.search(REQUETE_REELLE)


def test_le_provider_traduit_toute_panne_en_provider_error(monkeypatch):
    def tombe(query):
        raise RuntimeError("le formulaire a changé de forme")

    source = TransatProvider()
    monkeypatch.setattr(source, "_fetch", tombe)

    with pytest.raises(ProviderError, match="transat"):
        source.search(REQUETE_REELLE)


def test_le_provider_rend_les_offres_de_la_fixture_via_fetch(monkeypatch, html_reel):
    source = TransatProvider()
    monkeypatch.setattr(source, "_fetch", lambda query: html_reel)

    offres = source.search(REQUETE_REELLE)

    assert len(offres) == 1
    assert offres[0].price_cad == 533


def test_le_provider_expose_son_nom():
    assert TransatProvider().name == "transat"


def _interdire_le_reseau(monkeypatch):
    """Remplace `fetch_html` par une sentinelle qui échoue bruyamment si on l'appelle.

    Sans cette sentinelle, un test de portée se trompe de preuve : en l'absence de garde,
    `fetch_html` échoue de toute façon (pas de navigateur) en produisant « échec du chargement de
    https://www.airtransat.com/... », message où `match="transat"` trouve son motif dans le nom de
    domaine. Le test passerait alors sans que la garde existe.
    """

    def sentinelle(*args, **kwargs):
        raise AssertionError(
            "fetch_html appelée : la portée aurait dû être rejetée avant le réseau"
        )

    monkeypatch.setattr("scrappervol.providers.transat.fetch_html", sentinelle)


def test_le_provider_refuse_laller_simple_avant_douvrir_un_navigateur(monkeypatch):
    """Portée assumée : seul l'aller-retour a été piloté et vérifié à la tâche 11."""
    _interdire_le_reseau(monkeypatch)
    requete_aller_simple = SearchQuery(
        origin="YUL",
        destination="CUN",
        depart_date=date(2026, 11, 9),
        return_date=None,
        trip_type=TripType.ONE_WAY,
    )

    with pytest.raises(ProviderError, match="aller-retour"):
        TransatProvider()._fetch(requete_aller_simple)


def test_le_provider_refuse_plusieurs_passagers_avant_douvrir_un_navigateur(monkeypatch):
    """Le formulaire n'a été piloté et vérifié que pour un passager : deux adultes suivraient un
    parcours jamais observé, dont on ne sait pas s'il rend le même balisage."""
    _interdire_le_reseau(monkeypatch)
    requete_deux_adultes = SearchQuery(
        origin="YUL",
        destination="CUN",
        depart_date=date(2026, 11, 9),
        return_date=date(2026, 11, 16),
        trip_type=TripType.ROUND_TRIP,
        passengers=2,
    )

    with pytest.raises(ProviderError, match="passager"):
        TransatProvider()._fetch(requete_deux_adultes)


@pytest.mark.live
def test_fumee_reseau_transat():
    """Touche le vrai site Air Transat en pilotant le formulaire jusqu'à `/summary`. Exclu par
    défaut (`./dev test -m live` pour le lancer) : c'est une automatisation de formulaire, plus
    lourde et plus fragile qu'un appel d'API, à ne pas déclencher en boucle.

    Ne vérifie pas un prix précis — il change en continu — mais qu'un **total** aller-retour est
    bien relevé, avec une date de retour, pas un prix d'aller seul par classe tarifaire.
    """
    depart = date.today() + timedelta(days=97)
    requete = SearchQuery(
        origin="YUL",
        destination="CUN",
        depart_date=depart,
        return_date=depart + timedelta(days=7),
    )

    offres = TransatProvider().search(requete)

    assert len(offres) == 1
    (offre,) = offres
    assert offre.price_cad > 0
    assert offre.return_date == requete.return_date
    assert offre.airline
