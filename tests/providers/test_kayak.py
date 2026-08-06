import dataclasses
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from scrappervol.core.types import SearchQuery, TripType
from scrappervol.providers.base import EmptyResultError, ProviderError
from scrappervol.providers.kayak import (
    DEVISE,
    KayakProvider,
    _compagnie,
    _duree_minutes,
    _escales,
    _lien,
    _prix_le_plus_bas,
    _sondage_termine,
    _valider_portee,
    parse_poll,
    url_recherche,
)

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "kayak_poll_yul_par.json"

# La requête qui a réellement produit la fixture : capture du sondage ca.kayak.com pour
# YUL->PAR du 3 au 10 novembre 2026, tri par prix croissant, devise canadienne.
REQUETE_REELLE = SearchQuery(
    origin="YUL",
    destination="PAR",
    depart_date=date(2026, 11, 3),
    return_date=date(2026, 11, 10),
    trip_type=TripType.ROUND_TRIP,
)


@pytest.fixture
def sondage():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class TestLectureDuSondageReel:
    def test_les_offres_de_la_capture_sont_lues(self, sondage):
        offres = parse_poll(sondage, REQUETE_REELLE)

        # Trois vols « core » dans la fixture ; les publicités ne doivent pas s'y ajouter.
        assert len(offres) == 3
        assert {o.price_cad for o in offres} == {744, 747, 751}

    def test_les_publicites_sont_ecartees(self, sondage):
        # La fixture contient des encarts publicitaires dans la même liste que les vols, avec
        # leur propre prix. Les confondre inventerait des offres que personne ne vend.
        types = {r.get("type") for r in sondage["results"]}
        assert types - {"core"}, "la fixture doit contenir des encarts non-vols"

        offres = parse_poll(sondage, REQUETE_REELLE)
        codes = {o.raw["result_id"] for o in offres}
        pubs = {r.get("resultId") for r in sondage["results"] if r.get("type") != "core"}
        assert not (codes & pubs)

    def test_une_publicite_reservable_est_ecartee_sur_son_type(self, sondage):
        """C'est le type qui écarte la publicité, pas l'absence de prix.

        Les encarts de la capture n'ont pas d'option de réservation, si bien qu'ils tomberaient
        de toute façon faute de prix lisible. On ne peut pas compter là-dessus : un encart bâti
        comme un vol, et affichant le prix d'appel le plus bas de la page, deviendrait
        instantanément le plus bas du trajet et déclencherait une alerte sur une offre qui
        n'existe pas.
        """
        piege = {
            "type": "inlineAd",
            "resultId": "publicite-appat",
            "legs": [{"segments": [{"id": "s1"}]}],
            "bookingOptions": [
                {"displayPrice": {"price": 99, "currency": "CAD"}, "providerCode": "PUB"}
            ],
        }
        truque = {**sondage, "results": [piege, *sondage["results"]]}

        offres = parse_poll(truque, REQUETE_REELLE)

        assert all(o.raw["result_id"] != "publicite-appat" for o in offres)
        assert all(o.price_cad != 99 for o in offres)

    def test_les_offres_sortent_de_la_moins_chere_a_la_plus_chere(self, sondage):
        offres = parse_poll(sondage, REQUETE_REELLE)
        assert [o.price_cad for o in offres] == sorted(o.price_cad for o in offres)

    def test_le_prix_retenu_est_celui_du_revendeur_le_moins_cher(self, sondage):
        # Le vol à une escale est proposé à 744, 753 et 754 selon l'intermédiaire : c'est
        # précisément cet écart entre revendeurs qui justifie cette source.
        offre = min(parse_poll(sondage, REQUETE_REELLE), key=lambda o: o.price_cad)
        assert offre.price_cad == 744
        assert offre.raw["booking_options"] >= 2

    def test_la_devise_est_canadienne_sans_conversion(self, sondage):
        for offre in parse_poll(sondage, REQUETE_REELLE):
            assert offre.currency_original == DEVISE
            assert offre.price_original == float(offre.price_cad)

    def test_les_escales_sont_celles_du_voyage_entier(self, sondage):
        escales = {o.stops for o in parse_poll(sondage, REQUETE_REELLE)}
        assert escales == {0, 1, 2}

    def test_la_compagnie_et_la_duree_sont_renseignees(self, sondage):
        for offre in parse_poll(sondage, REQUETE_REELLE):
            assert offre.airline and offre.airline != "Inconnue"
            assert offre.duration_minutes and offre.duration_minutes > 0

    def test_le_trajet_et_les_dates_viennent_de_la_requete(self, sondage):
        for offre in parse_poll(sondage, REQUETE_REELLE):
            assert (offre.origin, offre.destination) == ("YUL", "PAR")
            assert offre.depart_date == date(2026, 11, 3)
            assert offre.return_date == date(2026, 11, 10)
            assert offre.provider == "kayak"


class TestFiltreDesEscales:
    def test_max_stops_zero_ne_garde_que_les_directs(self, sondage):
        requete = dataclasses.replace(REQUETE_REELLE, max_stops=0)
        offres = parse_poll(sondage, requete)

        assert offres, "la fixture contient un direct"
        assert all(o.stops == 0 for o in offres)

    def test_max_stops_un_borne_par_le_haut(self, sondage):
        requete = dataclasses.replace(REQUETE_REELLE, max_stops=1)
        assert {o.stops for o in parse_poll(sondage, requete)} == {0, 1}

    def test_sans_contrainte_tout_est_gardé(self, sondage):
        assert len(parse_poll(sondage, REQUETE_REELLE)) == 3

    def test_une_contrainte_impossible_ne_rend_rien(self, sondage):
        # Aucun résultat n'a un nombre d'escales négatif : le filtre doit vider la liste plutôt
        # que se rabattre sur le moins mauvais.
        requete = dataclasses.replace(REQUETE_REELLE, max_stops=-1)
        assert parse_poll(sondage, requete) == []


class TestPrixLePlusBas:
    def test_prend_le_minimum_parmi_les_revendeurs(self):
        resultat = {
            "bookingOptions": [
                {"displayPrice": {"price": 900, "currency": "CAD"}, "providerCode": "A"},
                {"displayPrice": {"price": 195, "currency": "CAD"}, "providerCode": "B"},
                {"displayPrice": {"price": 700, "currency": "CAD"}, "providerCode": "C"},
            ]
        }
        assert _prix_le_plus_bas(resultat) == (195, "B")

    def test_ignore_une_autre_devise_plutot_que_de_convertir(self):
        # Un prix en dollars américains pris pour des canadiens ferait passer un tarif ordinaire
        # pour une aubaine, et déclencherait une alerte sur du vent.
        resultat = {
            "bookingOptions": [
                {"displayPrice": {"price": 500, "currency": "USD"}, "providerCode": "A"},
                {"displayPrice": {"price": 800, "currency": "CAD"}, "providerCode": "B"},
            ]
        }
        assert _prix_le_plus_bas(resultat) == (800, "B")

    def test_rend_none_si_aucune_option_en_devise_locale(self):
        resultat = {
            "bookingOptions": [
                {"displayPrice": {"price": 500, "currency": "EUR"}, "providerCode": "A"},
            ]
        }
        assert _prix_le_plus_bas(resultat) is None

    def test_rend_none_sans_option(self):
        assert _prix_le_plus_bas({}) is None
        assert _prix_le_plus_bas({"bookingOptions": []}) is None

    @pytest.mark.parametrize("montant", [0, -50])
    def test_ecarte_un_montant_non_positif(self, montant):
        resultat = {
            "bookingOptions": [
                {"displayPrice": {"price": montant, "currency": "CAD"}, "providerCode": "A"},
            ]
        }
        assert _prix_le_plus_bas(resultat) is None

    def test_ecarte_un_montant_non_numerique(self):
        resultat = {
            "bookingOptions": [
                {"displayPrice": {"price": "744", "currency": "CAD"}, "providerCode": "A"},
                {"displayPrice": {"price": 900, "currency": "CAD"}, "providerCode": "B"},
            ]
        }
        assert _prix_le_plus_bas(resultat) == (900, "B")

    def test_arrondit_au_dollar(self):
        resultat = {
            "bookingOptions": [
                {"displayPrice": {"price": 744.6, "currency": "CAD"}, "providerCode": "A"},
            ]
        }
        assert _prix_le_plus_bas(resultat) == (745, "A")


class TestEscales:
    def test_un_aller_retour_direct_ne_compte_aucune_escale(self):
        # Deux jambes d'un segment chacune : c'est zéro escale, pas deux.
        resultat = {"legs": [{"segments": [{"id": "a"}]}, {"segments": [{"id": "b"}]}]}
        assert _escales(resultat) == 0

    def test_les_escales_des_deux_sens_s_additionnent(self):
        resultat = {
            "legs": [
                {"segments": [{"id": "a"}, {"id": "b"}]},
                {"segments": [{"id": "c"}, {"id": "d"}, {"id": "e"}]},
            ]
        }
        assert _escales(resultat) == 1 + 2

    def test_une_jambe_vide_ne_compte_pas_negativement(self):
        resultat = {"legs": [{"segments": []}, {"segments": [{"id": "a"}]}]}
        assert _escales(resultat) == 0

    def test_sans_jambe(self):
        assert _escales({}) == 0


class TestDuree:
    def test_additionne_les_jambes(self):
        resultat = {"legs": [{"id": "x"}, {"id": "y"}]}
        jambes = {"x": {"duration": 430}, "y": {"duration": 460}}
        assert _duree_minutes(resultat, jambes) == 890

    def test_rend_none_si_une_jambe_manque(self):
        # Une durée partielle serait présentée comme la durée du voyage : mieux vaut rien.
        resultat = {"legs": [{"id": "x"}, {"id": "absent"}]}
        assert _duree_minutes(resultat, {"x": {"duration": 430}}) is None

    def test_rend_none_si_une_duree_est_illisible(self):
        resultat = {"legs": [{"id": "x"}]}
        assert _duree_minutes(resultat, {"x": {"duration": "430"}}) is None

    def test_rend_none_plutot_que_zero(self):
        resultat = {"legs": [{"id": "x"}]}
        assert _duree_minutes(resultat, {"x": {"duration": 0}}) is None


class TestCompagnie:
    def test_un_seul_transporteur(self):
        resultat = {"legs": [{"segments": [{"id": "s1"}]}]}
        segments = {"s1": {"airline": "AC"}}
        assert _compagnie(resultat, segments, {"AC": {"name": "Air Canada"}}) == "Air Canada"

    def test_plusieurs_transporteurs_sont_joints(self):
        resultat = {"legs": [{"segments": [{"id": "s1"}, {"id": "s2"}]}]}
        segments = {"s1": {"airline": "AC"}, "s2": {"airline": "LX"}}
        compagnies = {"AC": {"name": "Air Canada"}, "LX": {"name": "SWISS"}}
        assert _compagnie(resultat, segments, compagnies) == "Air Canada, SWISS"

    def test_l_ordre_des_segments_ne_change_pas_le_libelle(self):
        # Ce libellé entre dans offer_hash, qui empêche d'alerter deux fois sur la même aubaine :
        # il doit être stable quel que soit l'ordre de lecture des segments.
        compagnies = {"AC": {"name": "Air Canada"}, "LX": {"name": "SWISS"}}
        segments = {"s1": {"airline": "AC"}, "s2": {"airline": "LX"}}
        ordre = {"legs": [{"segments": [{"id": "s1"}, {"id": "s2"}]}]}
        inverse = {"legs": [{"segments": [{"id": "s2"}, {"id": "s1"}]}]}

        avant = _compagnie(ordre, segments, compagnies)
        apres = _compagnie(inverse, segments, compagnies)
        assert avant == apres

    def test_les_noms_sortent_dans_l_ordre_alphabetique(self):
        """L'invariant qui compte vraiment pour offer_hash.

        Les codes transitent par un ensemble, dont l'ordre d'itération dépend de la graine de
        hachage du processus : sans tri explicite, le même vol produirait un libellé différent
        d'un redémarrage à l'autre, donc une empreinte différente, donc une seconde alerte pour
        l'aubaine déjà signalée. Assez de transporteurs ici pour que l'ordre d'ensemble ait peu
        de chances de coïncider avec l'ordre alphabétique.
        """
        codes = ["AC", "LX", "SN", "UA", "BA", "AF"]
        compagnies = {
            "AC": {"name": "Zephyr Air"},
            "LX": {"name": "SWISS"},
            "SN": {"name": "Brussels Airlines"},
            "UA": {"name": "United"},
            "BA": {"name": "British Airways"},
            "AF": {"name": "Air France"},
        }
        segments = {f"s{i}": {"airline": code} for i, code in enumerate(codes)}
        resultat = {"legs": [{"segments": [{"id": f"s{i}"} for i in range(len(codes))]}]}

        noms = _compagnie(resultat, segments, compagnies).split(", ")

        assert noms == sorted(noms)
        assert noms[0] == "Air France"
        assert noms[-1] == "Zephyr Air"

    def test_un_transporteur_repete_n_apparait_qu_une_fois(self):
        resultat = {"legs": [{"segments": [{"id": "s1"}, {"id": "s2"}]}]}
        segments = {"s1": {"airline": "AC"}, "s2": {"airline": "AC"}}
        assert _compagnie(resultat, segments, {"AC": {"name": "Air Canada"}}) == "Air Canada"

    def test_code_inconnu_de_la_table_reste_le_code(self):
        resultat = {"legs": [{"segments": [{"id": "s1"}]}]}
        assert _compagnie(resultat, {"s1": {"airline": "ZZ"}}, {}) == "ZZ"

    def test_sans_segment_lisible(self):
        assert _compagnie({"legs": [{"segments": [{"id": "absent"}]}]}, {}, {}) == "Inconnue"


class TestUrlEtLien:
    def test_l_aller_retour_porte_les_deux_dates(self):
        url = url_recherche(REQUETE_REELLE)
        assert "YUL-PAR/2026-11-03/2026-11-10" in url
        assert "currency=CAD" in url
        assert "sort=price_a" in url

    def test_l_aller_simple_ne_porte_qu_une_date(self):
        requete = SearchQuery(
            origin="YUL",
            destination="PAR",
            depart_date=date(2026, 11, 3),
            trip_type=TripType.ONE_WAY,
        )
        assert "YUL-PAR/2026-11-03?" in url_recherche(requete)

    def test_le_lien_pointe_le_vol_quand_le_document_le_donne(self):
        resultat = {"shareableUrl": "/flights/YUL-PAR/2026-11-03/2026-11-10/fabc"}
        assert _lien(resultat, REQUETE_REELLE) == (
            "https://ca.kayak.com/flights/YUL-PAR/2026-11-03/2026-11-10/fabc"
        )

    def test_le_lien_retombe_sur_la_recherche(self):
        assert _lien({}, REQUETE_REELLE) == url_recherche(REQUETE_REELLE)

    def test_un_lien_absolu_etranger_est_ignore(self):
        # On ne relaie pas une URL arbitraire trouvée dans le flux dans un courriel d'alerte.
        resultat = {"shareableUrl": "https://exemple.invalide/piege"}
        assert _lien(resultat, REQUETE_REELLE) == url_recherche(REQUETE_REELLE)


class TestPortee:
    def test_un_passager_est_accepte(self):
        _valider_portee(REQUETE_REELLE)

    def test_plusieurs_passagers_sont_refuses_avant_le_reseau(self):
        # Kayak chiffre par personne : mêler un prix par personne à des totaux fausserait la
        # médiane du trajet sans que rien ne le signale.
        requete = dataclasses.replace(REQUETE_REELLE, passengers=2)
        with pytest.raises(ProviderError, match="par personne"):
            _valider_portee(requete)

    def test_un_aller_retour_sans_date_de_retour_est_refuse(self):
        requete = SearchQuery(
            origin="YUL",
            destination="PAR",
            depart_date=date(2026, 11, 3),
            trip_type=TripType.ROUND_TRIP,
        )
        with pytest.raises(ProviderError, match="date de retour"):
            _valider_portee(requete)

    def test_l_aller_simple_est_accepte(self):
        requete = SearchQuery(
            origin="YUL",
            destination="PAR",
            depart_date=date(2026, 11, 3),
            trip_type=TripType.ONE_WAY,
        )
        _valider_portee(requete)


class TestStatutDuSondage:
    def test_une_reponse_complete_est_rendue(self):
        assert _sondage_termine('{"status": "complete", "results": []}') == {
            "status": "complete",
            "results": [],
        }

    def test_une_reponse_en_cours_est_refusee(self):
        assert _sondage_termine('{"status": "partial"}') is None

    def test_un_corps_illisible_est_refuse(self):
        assert _sondage_termine("pas du json") is None

    def test_un_corps_json_non_objet_est_refuse(self):
        assert _sondage_termine("[1, 2, 3]") is None


class TestProvider:
    def test_search_rend_les_offres_de_la_capture(self, sondage, monkeypatch):
        provider = KayakProvider()
        monkeypatch.setattr(provider, "_fetch", lambda query: sondage)

        offres = provider.search(REQUETE_REELLE)

        assert len(offres) == 3
        assert offres[0].price_cad == 744

    def test_search_signale_un_relevé_vide(self, monkeypatch):
        # Zéro offre est traité comme un échec, pas comme un succès : c'est ce qui permet au
        # disjoncteur de voir une source devenue muette.
        provider = KayakProvider()
        monkeypatch.setattr(provider, "_fetch", lambda query: {"results": []})

        with pytest.raises(EmptyResultError):
            provider.search(REQUETE_REELLE)

    def test_search_traduit_une_panne_en_erreur_de_source(self, monkeypatch):
        provider = KayakProvider()

        def tomber(query):
            raise RuntimeError("réseau coupé")

        monkeypatch.setattr(provider, "_fetch", tomber)

        with pytest.raises(ProviderError, match="réseau coupé"):
            provider.search(REQUETE_REELLE)

    def test_search_laisse_passer_le_refus_de_portee(self, monkeypatch):
        # Le message d'origine doit survivre : « seul un passager » est plus utile que
        # « échec de la requête ».
        provider = KayakProvider()

        def refuser(query):
            raise ProviderError("kayak : seul un passager est pris en charge")

        monkeypatch.setattr(provider, "_fetch", refuser)

        with pytest.raises(ProviderError, match="seul un passager"):
            provider.search(REQUETE_REELLE)

    def test_le_nom_de_la_source(self):
        assert KayakProvider().name == "kayak"


class TestBorneDuReleve:
    def test_un_releve_pléthorique_est_borné_aux_moins_chers(self):
        # Une page porte une cinquantaine de vols ; on n'enregistre pas le catalogue, mais on ne
        # doit jamais perdre le moins cher au passage.
        resultats = [
            {
                "type": "core",
                "resultId": f"r{i}",
                "legs": [{"segments": [{"id": "s"}]}],
                "bookingOptions": [
                    {"displayPrice": {"price": 1000 - i, "currency": "CAD"}, "providerCode": "P"}
                ],
            }
            for i in range(60)
        ]
        offres = parse_poll({"results": resultats}, REQUETE_REELLE)

        assert len(offres) == 20
        assert offres[0].price_cad == 941  # 1000 - 59, le moins cher du lot


@pytest.mark.live
def test_fumee_reseau_kayak() -> None:
    """Touche le vrai Kayak, du chargement de la page jusqu'à la réponse de sondage. Exclu par
    défaut (`./dev test -m live` pour le lancer) : il ouvre un navigateur et dure une minute.

    Ne vérifie aucun prix précis — ils bougent en continu — mais que le relevé tient debout :
    des dollars canadiens, un total aller-retour plausible sur une liaison transatlantique, et
    des offres classées de la moins chère à la plus chère. Le plancher écarte le cas où l'on
    lirait par mégarde un tarif d'un seul sens, ou une devise étrangère prise pour la nôtre.
    """
    depart = date.today() + timedelta(days=90)
    requete = SearchQuery(
        origin="YUL",
        destination="PAR",
        depart_date=depart,
        return_date=depart + timedelta(days=7),
    )

    offres = KayakProvider().search(requete)

    assert offres
    assert all(o.currency_original == "CAD" for o in offres)
    assert all(200 < o.price_cad < 6000 for o in offres)
    assert [o.price_cad for o in offres] == sorted(o.price_cad for o in offres)
    assert all(o.return_date == requete.return_date for o in offres)
    assert any(o.duration_minutes for o in offres)
