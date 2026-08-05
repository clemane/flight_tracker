"""Recherche d'aéroports : ce que l'utilisateur tape doit mener où il veut aller."""

from __future__ import annotations

import pytest

from scrappervol.core.airports import chercher, par_code


def codes(requete: str, limite: int = 8) -> list[str]:
    return [a.iata for a in chercher(requete, limite=limite)]


def test_le_code_iata_exact_arrive_en_tete():
    assert codes("YUL")[0] == "YUL"


def test_la_casse_de_la_saisie_est_indifferente():
    assert codes("yul")[0] == "YUL"


def test_le_debut_du_nom_de_ville_suffit():
    assert codes("montr")[0] == "YUL"


def test_laeroport_le_mieux_desservi_passe_devant_ses_homonymes():
    """« Paris » ne doit pas proposer d'abord l'aérodrome de Paris, Texas."""
    assert codes("paris")[0] == "CDG"


def test_une_ville_qui_commence_par_la_saisie_precede_celle_qui_la_contient():
    """Ingolstadt, que personne ne dessert, passe avant Pékin et ses mille liaisons pour « ing » :
    le rang prime sur le trafic, sinon les correspondances de milieu de mot noieraient les bonnes.
    """
    resultats = codes("ing", limite=8)
    assert resultats.index("IGS") < resultats.index("PEK")


def test_les_accents_absents_de_la_saisie_ne_genent_pas():
    assert codes("quebec")[0] == "YQB"


def test_les_accents_presents_dans_la_saisie_ne_genent_pas():
    assert codes("québec")[0] == "YQB"


def test_le_nom_francais_dune_ville_est_reconnu():
    """La source est en anglais : sans alias, « Lisbonne » ne trouverait rien."""
    assert codes("lisbonne")[0] == "LIS"


def test_le_nom_francais_dun_pays_ramene_ses_aeroports():
    assert "CUN" in codes("mexique")


def test_un_synonyme_courant_est_reconnu():
    assert "JFK" in codes("nyc")


def test_une_ville_sans_correspondance_ne_ramene_rien():
    assert codes("zzzzzz") == []


def test_la_saisie_vide_ne_ramene_rien():
    assert chercher("") == []
    assert chercher("   ") == []


def test_le_nombre_de_suggestions_est_borne():
    assert len(chercher("san", limite=3)) == 3


def test_la_ville_est_affichee_en_francais_quand_on_la_connait():
    """« Cancun, Mexico » se lirait en français comme la ville de Mexico."""
    cun = par_code("CUN")
    assert cun is not None
    assert cun.ville_affichee == "Cancún"
    assert cun.pays_affiche == "Mexique"


def test_le_nom_anglais_sert_de_repli_sans_traduction():
    atl = par_code("ATL")
    assert atl is not None
    assert atl.ville_affichee == "Atlanta"


def test_le_libelle_ne_repete_pas_la_ville():
    """« Cancún — Cancún » n'apprendrait rien ; le nom d'aéroport n'est ajouté que s'il diffère."""
    assert par_code("CUN").libelle == "Cancún"
    assert par_code("YUL").libelle.startswith("Montréal — ")


def test_par_code_tolere_la_casse_et_les_espaces():
    assert par_code(" cun ").iata == "CUN"


def test_par_code_sur_un_code_inconnu_ne_leve_pas():
    assert par_code("ZZZ") is None


@pytest.mark.parametrize(
    ("saisie", "attendu"),
    [
        ("cancun", "CUN"),
        ("cancún", "CUN"),
        # Aucune graphie ne s'écrit « genes » : seul le repli des accents fait correspondre.
        ("genes", "GOA"),
        ("punta cana", "PUJ"),
        ("varadero", "VRA"),
        ("montego", "MBJ"),
        ("londres", "LHR"),
        ("tokyo", "NRT"),
    ],
)
def test_les_destinations_courantes_repondent(saisie: str, attendu: str):
    assert codes(saisie)[0] == attendu
