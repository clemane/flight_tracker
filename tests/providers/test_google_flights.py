import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from scrappervol.core.types import SearchQuery, TripType
from scrappervol.providers.base import EmptyResultError, ProviderError
from scrappervol.providers.google_flights import (
    GoogleFlightsProvider,
    _fusionner_sections,
    to_offers,
)

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "google_flights_yul_cdg.json"
SECTIONS = Path(__file__).resolve().parent.parent / "fixtures" / "google_flights_sections.json"


def _prix(section: list) -> list[int]:
    """Prix portés par une section de la charge utile Google."""
    return [vol[1][0][1] for vol in section[0]]


def test_la_fusion_retient_les_meilleurs_vols_que_la_bibliotheque_ignore():
    """Régression : Google range les moins chers dans une section que fast-flights ne lit pas.

    Sur la capture, les « meilleurs vols » s'ouvrent à 1717 $ quand les « autres » commencent à
    2292 $. Ne lire que la seconde faisait rater l'aubaine — soit tout l'objet de cette veille.
    """
    payload = json.loads(SECTIONS.read_text())
    meilleurs, autres = _prix(payload[2]), _prix(payload[3])
    assert min(meilleurs) < min(autres), "la capture doit garder son intérêt : les bas prix en [2]"

    fusionne = _fusionner_sections(payload)

    assert _prix(fusionne[3]) == meilleurs + autres


def test_la_fusion_supporte_une_charge_utile_amputee():
    """Google peut n'en renvoyer qu'une : les vols recueillis doivent survivre au trou."""
    payload = json.loads(SECTIONS.read_text())
    meilleurs = _prix(payload[2])

    ampute = _fusionner_sections(payload[:3])  # la section des « autres vols » manque

    assert _prix(ampute[3]) == meilleurs, "les vols de la section présente doivent être conservés"


def test_la_fusion_encaisse_une_charge_utile_vide():
    """Aucune section exploitable : on ne lève pas, le parseur trouvera simplement zéro vol."""
    assert _fusionner_sections([])[3] == [[]]


def _segment(an: int, mois: int, jour: int, duree: int | None) -> dict:
    return {
        "from_airport": {"name": "Montréal", "code": "YUL"},
        "to_airport": {"name": "Paris", "code": "CDG"},
        # `time` à une seule composante : la forme réellement observée quand les minutes sont
        # nulles. Elle est ici pour que le cas soit déjà couvert le jour où l'heure sera lue.
        "departure": {"date": [an, mois, jour], "time": [17]},
        "arrival": {"date": [an, mois, jour + 1], "time": [5, 55]},
        "duration": duree,
        "plane_type": "Boeing 787",
    }


_SEGMENT = _segment(2026, 11, 2, 415)


@pytest.fixture
def donnees_reelles():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def requete(donnees_reelles):
    q = donnees_reelles["query"]
    return SearchQuery(
        origin=q["origin"],
        destination=q["destination"],
        depart_date=date.fromisoformat(q["depart"]),
        return_date=date.fromisoformat(q["retour"]),
        trip_type=TripType.ROUND_TRIP,
    )


def test_to_offers_traduit_la_fixture_reelle(donnees_reelles, requete):
    """Le contrat de base, mesuré sur des données que Google a réellement renvoyées."""
    offres = to_offers(donnees_reelles["results"], requete)

    assert len(offres) == len(donnees_reelles["results"])
    for offre in offres:
        assert offre.provider == "google_flights"
        assert offre.origin == "YUL"
        assert offre.destination == "CDG"
        assert offre.price_cad > 0
        assert offre.currency_original == "CAD"
        assert offre.airline
        assert offre.stops >= 0
        assert offre.deep_link.startswith("https://")


def test_to_offers_lit_le_prix_entier_sans_analyser_de_texte(donnees_reelles, requete):
    """fast-flights 3 rend un int : price_cad et price_original en découlent directement.

    Le plan prévoyait d'analyser des chaînes comme "C$1,234". Cette étape n'existe plus ; si
    quelqu'un la réintroduisait « pour être sûr », ce test le rattraperait.
    """
    offres = to_offers(donnees_reelles["results"], requete)
    attendus = [r["price"] for r in donnees_reelles["results"]]

    assert [o.price_cad for o in offres] == attendus
    assert [o.price_original for o in offres] == [float(p) for p in attendus]


def test_to_offers_derive_les_escales_du_nombre_de_segments(donnees_reelles, requete):
    """Il n'existe pas de champ `stops` : un vol direct a un segment."""
    offres = to_offers(donnees_reelles["results"], requete)
    attendu = [len(r["flights"]) - 1 for r in donnees_reelles["results"]]

    assert [o.stops for o in offres] == attendu
    assert 0 in attendu, "la fixture doit contenir au moins un vol direct"
    assert 1 in attendu, "la fixture doit contenir au moins un vol avec escale"


def test_to_offers_somme_les_durees_de_segments(donnees_reelles, requete):
    """duration_minutes est le temps de vol cumulé, jamais l'écart entre deux heures locales."""
    offres = to_offers(donnees_reelles["results"], requete)
    attendu = [sum(s["duration"] for s in r["flights"]) for r in donnees_reelles["results"]]

    assert [o.duration_minutes for o in offres] == attendu


def test_to_offers_prend_la_date_de_depart_du_premier_segment(donnees_reelles, requete):
    """La date réelle de l'offre prime sur la date demandée.

    Si Google décale le vol d'un jour, l'enregistrer sous la date demandée fabriquerait un
    historique qui compare deux vols différents sous la même empreinte.
    """
    an, mois, jour = donnees_reelles["results"][0]["flights"][0]["departure"]["date"]

    offres = to_offers(donnees_reelles["results"], requete)

    assert offres[0].depart_date == date(an, mois, jour)


def test_to_offers_garde_la_date_du_segment_quand_elle_differe_de_la_demande(requete):
    """Le cas qui distingue vraiment les deux sources de date.

    Dans la fixture réelle, Google renvoie le vol à la date demandée : les deux branches donnent
    alors le même résultat et le test précédent passe même si l'implémentation ignore la réponse.
    Ici la réponse s'écarte de la demande, ce qui est le cas qui compte — un vol décalé enregistré
    sous la date demandée fabriquerait un historique comparant deux vols différents sous la même
    empreinte.
    """
    assert requete.depart_date != date(2026, 11, 5), "la fixture doit différer de la date forcée"
    brut = [
        {
            "type": "AC",
            "price": 700,
            "airlines": ["Air Canada"],
            "flights": [_segment(2026, 11, 5, 415)],
        }
    ]

    (offre,) = to_offers(brut, requete)

    assert offre.depart_date == date(2026, 11, 5)


def test_to_offers_refuse_un_booleen_en_guise_de_prix(requete):
    """En Python, True est un int qui vaut 1 : sans garde explicite, il passerait pour un prix.

    Une offre à 1 CAD serait ensuite écartée par le plancher de crédibilité, mais elle aurait
    d'abord pollué l'historique des plus bas du jour — la médiane sert justement à survivre à ça,
    autant ne pas la mettre à l'épreuve pour rien.
    """
    brut = [{"type": "AC", "price": True, "airlines": ["Air Canada"], "flights": [_SEGMENT]}]

    assert to_offers(brut, requete) == []


def test_to_offers_se_rabat_sur_la_date_demandee_si_la_reponse_nen_porte_pas(requete):
    """Une date illisible ne doit pas faire disparaître l'offre : on retombe sur la requête."""
    segment = _segment(2026, 11, 2, 415)
    segment["departure"]["date"] = None
    brut = [{"type": "AC", "price": 700, "airlines": ["Air Canada"], "flights": [segment]}]

    (offre,) = to_offers(brut, requete)

    assert offre.depart_date == requete.depart_date


def test_to_offers_reporte_la_date_de_retour_de_la_requete(donnees_reelles, requete):
    """La réponse ne contient que l'aller : le retour ne peut venir que de la requête."""
    offres = to_offers(donnees_reelles["results"], requete)

    assert all(o.return_date == requete.return_date for o in offres)


def test_to_offers_joint_les_compagnies(requete):
    """airlines est une liste : un vol partagé ne doit pas perdre la moitié de son information."""
    brut = [
        {
            "type": "multi",
            "price": 700,
            "airlines": ["Air Canada", "Lufthansa"],
            "flights": [_segment(2026, 11, 2, 300), _segment(2026, 11, 3, 120)],
        }
    ]

    (offre,) = to_offers(brut, requete)

    assert offre.airline == "Air Canada, Lufthansa"


def test_to_offers_se_rabat_sur_le_type_si_les_compagnies_manquent(requete):
    """Une offre sans nom de compagnie reste exploitable : airline entre dans offer_hash."""
    brut = [{"type": "AC", "price": 700, "airlines": [], "flights": [_SEGMENT]}]

    (offre,) = to_offers(brut, requete)

    assert offre.airline == "AC"


def test_to_offers_conserve_la_reponse_brute(donnees_reelles, requete):
    """raw est la seule pièce à conviction le jour où un prix aberrant est enregistré."""
    offres = to_offers(donnees_reelles["results"], requete)

    assert offres[0].raw == donnees_reelles["results"][0]


@pytest.mark.parametrize(
    "brut",
    [
        {"type": "AC", "price": None, "airlines": ["Air Canada"], "flights": [_SEGMENT]},
        {"type": "AC", "price": 0, "airlines": ["Air Canada"], "flights": [_SEGMENT]},
        {"type": "AC", "price": -50, "airlines": ["Air Canada"], "flights": [_SEGMENT]},
        {"type": "AC", "price": "1 234", "airlines": ["Air Canada"], "flights": [_SEGMENT]},
        {"type": "AC", "price": 700, "airlines": ["Air Canada"], "flights": []},
    ],
    ids=["prix absent", "prix nul", "prix négatif", "prix texte", "sans segment"],
)
def test_to_offers_ignore_les_entrees_inexploitables(brut, requete):
    """Une entrée douteuse est écartée, jamais devinée : un prix inventé devient une fausse alerte,
    et une fausse alerte coûte plus cher qu'une offre manquée."""
    assert to_offers([brut], requete) == []


def test_to_offers_tolere_une_duree_absente(requete):
    """Sans durée fiable, duration_minutes vaut None — le champ est nullable exprès."""
    brut = [
        {
            "type": "AC",
            "price": 700,
            "airlines": ["Air Canada"],
            "flights": [_segment(2026, 11, 2, None)],
        }
    ]

    (offre,) = to_offers(brut, requete)

    assert offre.duration_minutes is None
    assert offre.price_cad == 700


def test_to_offers_respecte_le_plafond_descales(donnees_reelles):
    """Google n'est pas tenu d'honorer max_stops : on revérifie côté maison."""
    q = donnees_reelles["query"]
    requete_directe = SearchQuery(
        origin=q["origin"],
        destination=q["destination"],
        depart_date=date.fromisoformat(q["depart"]),
        return_date=date.fromisoformat(q["retour"]),
        max_stops=0,
    )

    offres = to_offers(donnees_reelles["results"], requete_directe)

    assert offres, "la fixture contient des vols directs"
    assert all(o.stops == 0 for o in offres)
    assert len(offres) < len(donnees_reelles["results"]), "des vols avec escale ont dû être écartés"


def test_le_provider_leve_empty_result_quand_rien_nest_exploitable(monkeypatch, requete):
    """Le silence doit être bruyant : c'est ce que la tâche 9 attend pour armer le disjoncteur."""
    source = GoogleFlightsProvider()
    monkeypatch.setattr(source, "_fetch", lambda query: [])

    with pytest.raises(EmptyResultError):
        source.search(requete)


def test_le_provider_traduit_toute_panne_en_provider_error(monkeypatch, requete):
    """fast-flights lève des exceptions non documentées : le runner attend un type maison."""

    def tombe(query):
        raise RuntimeError("connexion interrompue")

    source = GoogleFlightsProvider()
    monkeypatch.setattr(source, "_fetch", tombe)

    with pytest.raises(ProviderError, match="google_flights"):
        source.search(requete)


def test_le_provider_expose_son_nom():
    assert GoogleFlightsProvider().name == "google_flights"


@pytest.mark.live
def test_fumee_reseau_google_flights():
    """Touche le vrai Google. Exclu de la suite par défaut : `./dev test -m live` pour le lancer.

    Ce test ne vérifie pas des valeurs — elles changent toutes les heures — mais que le contrat de
    forme tient encore. C'est le seul garde-fou contre le scénario que ce projet redoute : la source
    change de format, la suite reste verte sur sa fixture figée, et la veille s'éteint en silence.
    """
    depart = date.today() + timedelta(days=90)
    requete = SearchQuery(
        origin="YUL",
        destination="CDG",
        depart_date=depart,
        return_date=depart + timedelta(days=10),
    )

    offres = GoogleFlightsProvider().search(requete)

    assert offres
    assert all(o.price_cad > 0 for o in offres)
    assert all(o.airline for o in offres)
