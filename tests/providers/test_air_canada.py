from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from scrappervol.core.types import SearchQuery, TripType
from scrappervol.providers.air_canada import (
    NOM,
    AirCanadaProvider,
    _duree_en_minutes,
    _escales,
    _prix_en_cad,
    _rang_cellule,
    _valider_portee,
    _verifier_etape_recapitulatif,
    parse_review_trip,
)
from scrappervol.providers.base import EmptyResultError, ProviderError

FIXTURE = Path(__file__).parent.parent / "fixtures" / "air_canada_review_trip_yul_cun.html"

REQUETE = SearchQuery(
    origin="YUL",
    destination="CUN",
    depart_date=date(2026, 11, 3),
    return_date=date(2026, 11, 10),
)


@pytest.fixture
def html_recapitulatif() -> str:
    return FIXTURE.read_text(encoding="utf-8")


class TestPrixEnCad:
    def test_lit_le_total_masque_du_recapitulatif(self) -> None:
        assert _prix_en_cad("Total général 540,71CAD 540,71\xa0$ CA") == 541

    def test_arrondit_au_dollar_le_plus_proche(self) -> None:
        assert _prix_en_cad("319,49CAD") == 319
        assert _prix_en_cad("319,50CAD") == 320

    def test_la_virgule_est_decimale_et_l_espace_separe_les_milliers(self) -> None:
        """Sans cette distinction, « 1 716,49CAD » deviendrait 171 649 $ — cent fois trop."""
        assert _prix_en_cad("1 716,49CAD") == 1716
        assert _prix_en_cad("1\xa0716,49CAD") == 1716
        assert _prix_en_cad("1 716,49CAD") == 1716

    def test_un_montant_sans_decimales_reste_lisible(self) -> None:
        assert _prix_en_cad("319CAD") == 319

    @pytest.mark.parametrize("texte", ["", "aucun prix", "540,71 $ CA", "0CAD"])
    def test_rend_none_plutot_que_de_deviner(self, texte: str) -> None:
        assert _prix_en_cad(texte) is None


class TestEscales:
    def test_sans_escale_vaut_zero(self) -> None:
        assert _escales("Sans escale 5h Ceci est un vol sans escale durée totale 5 heures") == 0

    def test_compte_les_escales_annoncees(self) -> None:
        assert _escales("1 escale 13h 30m mer. nov. 11 Ce vol comprend une escale") == 1
        assert _escales("2 escales 20h") == 2

    def test_ne_confond_pas_les_chiffres_de_l_horaire_avec_des_escales(self) -> None:
        """Le texte d'un bloc de vol est truffé de chiffres (heures, durées, dates)."""
        assert _escales("Sans escale 5h 08:00 YUL 13:00 CUN durée totale 5 heures") == 0

    @pytest.mark.parametrize("texte", ["", "08:00 YUL 13:00 CUN"])
    def test_rend_none_si_rien_n_est_annonce(self, texte: str) -> None:
        assert _escales(texte) is None


class TestDuree:
    def test_lit_les_heures_seules(self) -> None:
        assert _duree_en_minutes("Sans escale 5h") == 300

    def test_lit_les_heures_et_minutes(self) -> None:
        assert _duree_en_minutes("1 escale 13h 30m") == 810

    @pytest.mark.parametrize("texte", ["", "aucune durée"])
    def test_rend_none_plutot_que_zero(self, texte: str) -> None:
        assert _duree_en_minutes(texte) is None


class TestRangCellule:
    def test_extrait_l_index_du_vol(self) -> None:
        assert _rang_cellule("button-cell-container cabin-fare-12-Y") == 12

    def test_rend_none_sur_une_classe_etrangere(self) -> None:
        assert _rang_cellule("button-cell-container") is None
        assert _rang_cellule("") is None


class TestVerifierEtapeRecapitulatif:
    def test_accepte_l_url_du_recapitulatif(self) -> None:
        _verifier_etape_recapitulatif("https://www.aircanada.com/booking/ca/fr/aco/review-trip")

    def test_refuse_une_page_de_resultats(self) -> None:
        """Rester sur la page aller signifie que le parcours n'a pas abouti."""
        with pytest.raises(ProviderError, match="récapitulatif non atteint"):
            _verifier_etape_recapitulatif(
                "https://www.aircanada.com/booking/ca/fr/aco/availability/rt/outbound"
            )


class TestValiderPortee:
    def test_accepte_l_aller_retour_a_un_passager(self) -> None:
        _valider_portee(REQUETE)

    def test_refuse_l_aller_simple(self) -> None:
        requete = SearchQuery(
            origin="YUL",
            destination="CUN",
            depart_date=date(2026, 11, 3),
            trip_type=TripType.ONE_WAY,
        )
        with pytest.raises(ProviderError, match="aller-retour"):
            _valider_portee(requete)

    def test_refuse_plusieurs_passagers(self) -> None:
        with pytest.raises(ProviderError, match="passager"):
            _valider_portee(
                SearchQuery(
                    origin="YUL",
                    destination="CUN",
                    depart_date=date(2026, 11, 3),
                    return_date=date(2026, 11, 10),
                    passengers=2,
                )
            )


class TestParseReviewTrip:
    def test_rend_une_seule_offre_aller_retour(self, html_recapitulatif: str) -> None:
        offres = parse_review_trip(html_recapitulatif, REQUETE)
        assert len(offres) == 1

    def test_le_prix_est_le_total_du_recapitulatif(self, html_recapitulatif: str) -> None:
        """540,71 $ facturés — et non 319 $, le tarif affiché pour le seul aller."""
        offre = parse_review_trip(html_recapitulatif, REQUETE)[0]
        assert offre.price_cad == 541
        assert offre.currency_original == "CAD"

    def test_le_prix_depasse_le_tarif_d_un_seul_sens(self, html_recapitulatif: str) -> None:
        """Garde contre la régression qui guette cette source : lire un demi-prix."""
        offre = parse_review_trip(html_recapitulatif, REQUETE)[0]
        assert offre.price_cad > 400

    def test_additionne_les_escales_des_deux_troncons(self, html_recapitulatif: str) -> None:
        offre = parse_review_trip(html_recapitulatif, REQUETE)[0]
        assert offre.raw["stops_per_leg"] == [0, 1]
        assert offre.stops == 1

    def test_additionne_les_durees_des_deux_troncons(self, html_recapitulatif: str) -> None:
        offre = parse_review_trip(html_recapitulatif, REQUETE)[0]
        assert offre.duration_minutes == 300 + 810

    def test_signale_les_troncons_confies_a_rouge(self, html_recapitulatif: str) -> None:
        offre = parse_review_trip(html_recapitulatif, REQUETE)[0]
        assert offre.airline == "Air Canada Rouge"

    def test_reprend_les_dates_de_la_requete(self, html_recapitulatif: str) -> None:
        offre = parse_review_trip(html_recapitulatif, REQUETE)[0]
        assert offre.provider == NOM
        assert offre.origin == "YUL"
        assert offre.destination == "CUN"
        assert offre.depart_date == date(2026, 11, 3)
        assert offre.return_date == date(2026, 11, 10)

    def test_ecarte_un_recapitulatif_portant_sur_un_autre_trajet(
        self, html_recapitulatif: str
    ) -> None:
        """Un parcours qui a dérivé produit un prix parfaitement lisible, mais faux."""
        autre = SearchQuery(
            origin="YYZ",
            destination="CDG",
            depart_date=date(2026, 11, 3),
            return_date=date(2026, 11, 10),
        )
        assert parse_review_trip(html_recapitulatif, autre) == []

    def test_ecarte_une_page_sans_total(self) -> None:
        assert parse_review_trip("<html><body>rien</body></html>", REQUETE) == []

    def test_ecarte_un_total_illisible(self, html_recapitulatif: str) -> None:
        abime = html_recapitulatif.replace("540,71CAD", "prix sur demande")
        assert parse_review_trip(abime, REQUETE) == []

    def test_ecarte_un_recapitulatif_sans_les_deux_troncons(self, html_recapitulatif: str) -> None:
        ampute = html_recapitulatif.replace("ac-ui-flight-block-summary-pres", "div", 2)
        assert parse_review_trip(ampute, REQUETE) == []

    def test_ecarte_un_troncon_dont_les_escales_sont_illisibles(
        self, html_recapitulatif: str
    ) -> None:
        abime = html_recapitulatif.replace("Sans escale", "").replace("sans escale", "")
        assert parse_review_trip(abime, REQUETE) == []


class TestSearch:
    def test_traduit_le_html_en_offre(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fournisseur = AirCanadaProvider()
        monkeypatch.setattr(
            fournisseur, "_fetch", lambda query: FIXTURE.read_text(encoding="utf-8")
        )
        offres = fournisseur.search(REQUETE)
        assert len(offres) == 1
        assert offres[0].price_cad == 541

    def test_une_page_sans_offre_leve_empty_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fournisseur = AirCanadaProvider()
        monkeypatch.setattr(fournisseur, "_fetch", lambda query: "<html></html>")
        with pytest.raises(EmptyResultError):
            fournisseur.search(REQUETE)

    def test_toute_panne_est_traduite_en_provider_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def tomber(query: SearchQuery) -> str:
            raise RuntimeError("navigateur perdu")

        fournisseur = AirCanadaProvider()
        monkeypatch.setattr(fournisseur, "_fetch", tomber)
        with pytest.raises(ProviderError, match="échec de la requête"):
            fournisseur.search(REQUETE)


@pytest.mark.live
def test_fumee_reseau_air_canada() -> None:
    """Touche le vrai site Air Canada, du formulaire jusqu'au récapitulatif. Exclu par défaut
    (`./dev test -m live` pour le lancer) : le parcours ouvre un navigateur à fenêtre et dure une
    minute et demie, il n'a pas sa place dans la suite ordinaire.

    Ne vérifie pas un prix précis — il bouge en continu — mais qu'un **total** aller-retour est
    relevé, et non le tarif d'un seul sens. D'où le plancher : les pages de résultats annoncent
    des tarifs « par personne, dans chaque sens », et un demi-prix sur cette liaison passerait
    sous les 300 $.
    """
    depart = date.today() + timedelta(days=90)
    requete = SearchQuery(
        origin="YUL",
        destination="CUN",
        depart_date=depart,
        return_date=depart + timedelta(days=7),
    )

    offres = AirCanadaProvider().search(requete)

    assert len(offres) == 1
    (offre,) = offres
    assert offre.price_cad > 300
    assert offre.return_date == requete.return_date
    assert offre.airline.startswith("Air Canada")
