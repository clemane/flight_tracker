from datetime import date, timedelta
from pathlib import Path

import pytest

from scrappervol.core.types import SearchQuery, TripType
from scrappervol.providers.base import EmptyResultError, ProviderError
from scrappervol.providers.transat import TransatProvider, parse_results

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "transat_yul_cun.html"

# La requête qui correspond exactement à ce qui a produit la fixture réelle (capture du
# 2026-08-04, deuxième passage à 15:45, cinq minutes après un premier passage identique à 15:38).
REQUETE_REELLE = SearchQuery(
    origin="YUL",
    destination="CUN",
    depart_date=date(2026, 11, 9),
    return_date=date(2026, 11, 16),
    trip_type=TripType.ROUND_TRIP,
)


@pytest.fixture
def html_reel():
    return FIXTURE.read_text(encoding="utf-8")


def _carte_html(
    *,
    id_carte: str = "flight-result-card-0",
    origine: str = "Montréal (YUL)",
    destination: str = "Cancun (CUN)",
    airline_label: str = "Exploité par Air Transat",
    duree: str = "4h 50min",
    numeros_vol: tuple[str, ...] = ("TS938",),
    tarifs: tuple[tuple[str, str], ...] = (("eco", "297$"), ("club", "687$")),
    avec_compagnie: bool = True,
    avec_duree: bool = True,
) -> str:
    """Construit une carte de vol minimale mais fidèle aux sélecteurs relevés dans la fixture
    réelle (voir tests/fixtures/transat_yul_cun.html), pour isoler un seul comportement à la fois
    sans dépendre de la mise en page complète de la page réelle."""
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
    tarifs_html = "".join(
        f'<div class="fare"><button class="fare-btn {tier}"><span class="expandFare">'
        f'<span class="expandFare-startFrom">À partir de '
        f'<span class="expandFare-price">{prix}</span></span></span></button></div>'
        for tier, prix in tarifs
    )
    return (
        f'<div id="{id_carte}" class="co-shopResult-flightResult-card">'
        f"{bloc_duree}"
        f'<div class="common-location">'
        f'<span class="cityAirport">{origine}</span>'
        f'<span class="cityAirport">{destination}</span>'
        f"</div>"
        f"{bloc_compagnie}"
        f'<div class="panel-type">{numeros_html}</div>'
        f'<div class="co-shopResult-flightResult-fareClassesBtn">{tarifs_html}</div>'
        f"</div>"
    )


def _page_html(*cartes: str, date_affichee: str | None = "Lun. 9 nov. 2026") -> str:
    curseur = (
        f'<button class="date-slider-dateBtn active" aria-pressed="true" aria-current="date" '
        f'aria-label="{date_affichee}, 297$, taxes et frais incl."></button>'
        if date_affichee
        else ""
    )
    return f"<html><body>{curseur}{''.join(cartes)}</body></html>"


# --- Sur la fixture réelle ---------------------------------------------------------------------


def test_parse_results_traduit_la_fixture_reelle(html_reel):
    """Le contrat de base, mesuré sur la page qu'Air Transat a réellement renvoyée."""
    offres = parse_results(html_reel, REQUETE_REELLE)

    # 2 cartes de vol x 2 classes tarifaires (Économie, Club) observées dans la fixture.
    assert len(offres) == 4
    for offre in offres:
        assert offre.provider == "transat"
        assert offre.origin == "YUL"
        assert offre.destination == "CUN"
        assert offre.price_cad > 0
        assert offre.currency_original == "CAD"
        assert offre.airline == "Air Transat"
        assert offre.stops == 0
        assert offre.deep_link.startswith("https://www.airtransat.com/")


def test_parse_results_produit_au_moins_trois_prix_cad_distincts(html_reel):
    """Le critère de réussite de la tâche 11, transformé en garde permanente : si un jour la page
    change de forme au point de ne plus livrer trois prix distincts, ce test doit rougir plutôt que
    de laisser le silence s'installer."""
    offres = parse_results(html_reel, REQUETE_REELLE)

    prix_distincts = {o.price_cad for o in offres}
    assert len(prix_distincts) >= 3
    assert prix_distincts == {297, 687, 1229}


def test_parse_results_lit_la_date_de_depart_affichee_pas_la_requete(html_reel):
    """Le piège de la tâche 10 : si l'implémentation recopiait `query.depart_date` sans lire la
    page, ce test resterait vert même en cas de régression. Ici la date demandée est délibérément
    fausse ; seule une lecture réelle du curseur de dates de la page peut faire passer ce test."""
    requete_avec_fausse_date = SearchQuery(
        origin="YUL",
        destination="CUN",
        depart_date=date(2020, 1, 1),
        return_date=date(2020, 1, 8),
    )

    offres = parse_results(html_reel, requete_avec_fausse_date)

    assert offres
    assert all(o.depart_date == date(2026, 11, 9) for o in offres)


def test_parse_results_reporte_la_date_de_retour_de_la_requete(html_reel):
    """Cette page ne montre que le vol aller (étape « departure » du parcours) : le retour ne peut
    venir que de la requête, contrairement à la date de départ."""
    offres = parse_results(html_reel, REQUETE_REELLE)

    assert all(o.return_date == REQUETE_REELLE.return_date for o in offres)


def test_parse_results_conserve_le_texte_source_dans_raw(html_reel):
    offres = parse_results(html_reel, REQUETE_REELLE)

    for offre in offres:
        assert offre.raw["card_id"].startswith("flight-result-card-")
        assert offre.raw["fare_tier"] in {"eco", "club"}
        assert "$" in offre.raw["price_text"]


# --- Sur des cartes synthétiques, pour isoler chaque champ --------------------------------------


def test_parse_results_lit_le_prix_dans_le_texte_pas_une_valeur_fixe():
    """Si l'extraction du prix était remplacée par une constante, ce test le détecterait : le prix
    choisi ici (1234$) ne correspond à aucune valeur plausible codée en dur ailleurs."""
    html = _page_html(_carte_html(tarifs=(("eco", "1 234$"),)))

    (offre,) = parse_results(html, REQUETE_REELLE)

    assert offre.price_cad == 1234
    assert offre.price_original == 1234.0


def test_parse_results_lit_un_prix_avec_espace_insecable():
    """ "1&nbsp;229$" tel qu'observé dans la fixture réelle pour la classe Club."""
    html = _page_html(_carte_html(tarifs=(("club", "1\xa0229$"),)))

    (offre,) = parse_results(html, REQUETE_REELLE)

    assert offre.price_cad == 1229


def test_parse_results_lit_la_compagnie_aerienne_dans_la_page():
    """Si l'implémentation renvoyait "Air Transat" en dur, ce test le détecterait."""
    html = _page_html(_carte_html(airline_label="Exploité par Compagnie Fictive"))

    offres = parse_results(html, REQUETE_REELLE)

    assert offres
    assert all(o.airline == "Compagnie Fictive" for o in offres)


def test_parse_results_lit_la_duree_dans_le_texte():
    html = _page_html(_carte_html(duree="6h 15min"))

    offres = parse_results(html, REQUETE_REELLE)

    assert offres
    assert all(o.duration_minutes == 6 * 60 + 15 for o in offres)


def test_parse_results_tolere_une_duree_absente():
    html = _page_html(_carte_html(avec_duree=False))

    offres = parse_results(html, REQUETE_REELLE)

    assert offres
    assert all(o.duration_minutes is None for o in offres)


def test_parse_results_deduit_zero_escale_dun_seul_numero_de_vol():
    html = _page_html(_carte_html(numeros_vol=("TS938",)))

    offres = parse_results(html, REQUETE_REELLE)

    assert offres
    assert all(o.stops == 0 for o in offres)


def test_parse_results_ecarte_une_carte_a_plusieurs_numeros_de_vol():
    """Le balisage d'un vol avec escale n'a jamais été observé dans une fixture réelle (les deux
    seuls vols capturés pour YUL-CUN sont directs). Plutôt que d'inventer une formule sur le nombre
    d'escales, la carte est écartée : mieux vaut manquer une offre que la deviner."""
    html = _page_html(_carte_html(numeros_vol=("TS938", "TS200")))

    assert parse_results(html, REQUETE_REELLE) == []


def test_parse_results_ecarte_une_carte_sans_numero_de_vol():
    html = _page_html(_carte_html(numeros_vol=()))

    assert parse_results(html, REQUETE_REELLE) == []


def test_parse_results_ecarte_une_carte_sans_compagnie():
    html = _page_html(_carte_html(avec_compagnie=False))

    assert parse_results(html, REQUETE_REELLE) == []


def test_parse_results_ecarte_une_carte_dont_les_aeroports_ne_correspondent_pas_a_la_requete():
    """Garde défensive : une carte qui ne parle pas de la paire d'aéroports demandée (page mise en
    cache, glissement de session) ne doit pas devenir une offre pour le mauvais trajet."""
    html = _page_html(_carte_html(origine="Toronto (YYZ)", destination="Cancun (CUN)"))

    assert parse_results(html, REQUETE_REELLE) == []


def test_parse_results_ecarte_un_prix_illisible_sans_perdre_les_autres_tarifs():
    html = _page_html(_carte_html(tarifs=(("eco", "Bientôt disponible"), ("club", "687$"))))

    offres = parse_results(html, REQUETE_REELLE)

    assert len(offres) == 1
    assert offres[0].price_cad == 687
    assert offres[0].raw["fare_tier"] == "club"


def test_parse_results_se_rabat_sur_la_date_demandee_si_le_curseur_est_absent():
    html = _page_html(_carte_html(), date_affichee=None)

    offres = parse_results(html, REQUETE_REELLE)

    assert offres
    assert all(o.depart_date == REQUETE_REELLE.depart_date for o in offres)


def test_parse_results_rend_une_liste_vide_si_aucun_resultat():
    """Une page de résultats sans carte de vol ne doit jamais lever d'exception : c'est au
    provider, pas à cette fonction pure, de traduire l'absence de résultat en échec."""
    html = '<html><body><div class="co-shopResult-empty">Aucun vol trouvé</div></body></html>'

    assert parse_results(html, REQUETE_REELLE) == []


def test_parse_results_cree_une_offre_par_classe_tarifaire():
    html = _page_html(_carte_html(tarifs=(("eco", "300$"), ("club", "800$"), ("chill", "500$"))))

    offres = parse_results(html, REQUETE_REELLE)

    assert {o.raw["fare_tier"] for o in offres} == {"eco", "club", "chill"}
    assert {o.price_cad for o in offres} == {300, 800, 500}


# --- TransatProvider -----------------------------------------------------------------------------


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

    assert len(offres) == 4


def test_le_provider_expose_son_nom():
    assert TransatProvider().name == "transat"


def test_le_provider_refuse_laller_simple_pour_linstant():
    """Portée assumée : seul l'aller-retour a été piloté et vérifié à la tâche 11."""
    requete_aller_simple = SearchQuery(
        origin="YUL",
        destination="CUN",
        depart_date=date(2026, 11, 9),
        return_date=None,
        trip_type=TripType.ONE_WAY,
    )
    source = TransatProvider()

    with pytest.raises(ProviderError, match="transat"):
        source._fetch(requete_aller_simple)


@pytest.mark.live
def test_fumee_reseau_transat():
    """Touche le vrai site Air Transat en pilotant le formulaire. Exclu par défaut
    (`./dev test -m live` pour le lancer) : c'est une automatisation de formulaire, plus lourde et
    plus fragile qu'un appel d'API, à ne pas déclencher en boucle.

    Ne vérifie pas des prix précis — ils changent en continu — mais que le parcours complet
    (autocomplétion origine/destination, calendrier, soumission) aboutit encore à une page avec des
    offres exploitables. C'est le seul garde-fou contre un changement de formulaire qui laisserait
    la fixture verte pendant que la source réelle ne produit plus rien.
    """
    depart = date.today() + timedelta(days=97)
    requete = SearchQuery(
        origin="YUL",
        destination="CUN",
        depart_date=depart,
        return_date=depart + timedelta(days=7),
    )

    offres = TransatProvider().search(requete)

    assert offres
    assert all(o.price_cad > 0 for o in offres)
    assert all(o.airline for o in offres)
