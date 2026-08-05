import pytest

from scrappervol.core.types import DatePolicyKind, TripType
from scrappervol.web.forms import (
    RouteFormError,
    build_policy_params,
    parse_airports,
    validate_route_form,
)


def test_les_aeroports_sont_normalises_en_majuscules():
    assert parse_airports("yul, yqb") == ["YUL", "YQB"]


def test_les_separateurs_multiples_sont_acceptes():
    assert parse_airports("YUL YQB,CDG") == ["YUL", "YQB", "CDG"]


def test_les_entrees_vides_sont_ecartees():
    assert parse_airports("YUL,,  ,YQB") == ["YUL", "YQB"]


def test_une_liste_daeroports_vide_leve():
    with pytest.raises(RouteFormError):
        validate_route_form(
            {
                "label": "X",
                "origins": "",
                "destinations": "CDG",
                "date_policy": "fixed",
                "depart": "2027-03-12",
            }
        )


def test_un_libelle_vide_leve():
    with pytest.raises(RouteFormError):
        validate_route_form(
            {
                "label": "",
                "origins": "YUL",
                "destinations": "CDG",
                "date_policy": "fixed",
                "depart": "2027-03-12",
            }
        )


def test_les_parametres_fixed_sont_construits():
    params = build_policy_params(
        DatePolicyKind.FIXED,
        {"depart": "2027-03-12", "retour": "2027-03-22", "flex_days": "3"},
    )

    assert params == {"depart": "2027-03-12", "retour": "2027-03-22", "flex_days": 3}


def test_fixed_sans_date_de_depart_leve():
    with pytest.raises(RouteFormError):
        build_policy_params(DatePolicyKind.FIXED, {"depart": ""})


def test_un_aller_retour_sans_date_de_retour_leve():
    """Le garde-fou central de l'application : ScrapperVol surveille des voyages de vacances,
    donc des aller-retours. Sans retour, `_dates_fixed` interroge un aller simple et son prix,
    deux fois plus bas, s'installe comme référence du trajet."""
    with pytest.raises(RouteFormError, match="aller simple"):
        build_policy_params(
            DatePolicyKind.FIXED,
            {"depart": "2027-03-12", "retour": "", "trip_type": "round_trip"},
        )


def test_un_aller_simple_assume_se_passe_de_date_de_retour():
    params = build_policy_params(
        DatePolicyKind.FIXED, {"depart": "2027-03-12", "trip_type": "one_way"}
    )

    assert params == {"depart": "2027-03-12"}


def test_un_code_daeroport_mal_forme_leve():
    with pytest.raises(RouteFormError, match="trois lettres"):
        parse_airports("YUL, Paris")


def test_un_code_de_deux_lettres_leve():
    """Un code IATA fait toujours trois lettres ; en accepter deux laisserait passer une saisie
    tronquée droit vers une source qui répondrait « aucun vol » sans jamais le signaler."""
    with pytest.raises(RouteFormError, match="trois lettres"):
        parse_airports("YU")


def test_une_duree_de_sejour_negative_leve():
    with pytest.raises(RouteFormError, match="nuits"):
        build_policy_params(DatePolicyKind.FLEXIBLE, {"sejour_min": "-3", "sejour_max": "14"})


def test_les_parametres_window_sont_construits():
    params = build_policy_params(
        DatePolicyKind.WINDOW,
        {"mois": "2027-03, 2027-04", "sejour_min": "8", "sejour_max": "12"},
    )

    assert params == {"mois": ["2027-03", "2027-04"], "sejour_min": 8, "sejour_max": 12}


def test_window_sans_mois_leve():
    with pytest.raises(RouteFormError):
        build_policy_params(DatePolicyKind.WINDOW, {"mois": ""})


def test_les_parametres_flexible_sont_construits():
    params = build_policy_params(
        DatePolicyKind.FLEXIBLE,
        {"horizon_mois": "12", "sejour_min": "7", "sejour_max": "14"},
    )

    assert params == {"horizon_mois": 12, "sejour_min": 7, "sejour_max": 14}


def test_un_sejour_min_superieur_au_max_leve():
    with pytest.raises(RouteFormError):
        build_policy_params(
            DatePolicyKind.FLEXIBLE, {"horizon_mois": "12", "sejour_min": "20", "sejour_max": "7"}
        )


def test_un_sejour_min_egal_au_max_est_accepte():
    """Un séjour de durée fixe (min == max) est une saisie légitime, pas une inversion."""
    params = build_policy_params(
        DatePolicyKind.FLEXIBLE, {"horizon_mois": "12", "sejour_min": "8", "sejour_max": "8"}
    )

    assert params["sejour_min"] == params["sejour_max"] == 8


def test_les_valeurs_par_defaut_de_la_politique_flexible():
    params = build_policy_params(DatePolicyKind.FLEXIBLE, {})

    assert params == {"horizon_mois": 12, "sejour_min": 7, "sejour_max": 14}


def test_le_formulaire_complet_produit_les_champs_du_modele():
    champs = validate_route_form(
        {
            "label": "Paris au printemps",
            "origins": "YUL, YQB",
            "destinations": "CDG",
            "date_policy": "fixed",
            "trip_type": "round_trip",
            "passengers": "2",
            "max_stops": "1",
            "target_price_cad": "600",
            "exception_threshold": "0.35",
            "depart": "2027-03-12",
            "retour": "2027-03-22",
            "flex_days": "3",
        }
    )

    assert champs["label"] == "Paris au printemps"
    assert champs["origins"] == ["YUL", "YQB"]
    assert champs["passengers"] == 2
    assert champs["max_stops"] == 1
    assert champs["target_price_cad"] == 600
    assert champs["exception_threshold"] == 0.35
    assert champs["policy_params"]["flex_days"] == 3


def test_les_champs_facultatifs_vides_deviennent_none():
    champs = validate_route_form(
        {
            "label": "X",
            "origins": "YUL",
            "destinations": "CDG",
            "date_policy": "fixed",
            "depart": "2027-03-12",
            "retour": "2027-03-22",
            "max_stops": "",
            "target_price_cad": "",
        }
    )

    assert champs["max_stops"] is None
    assert champs["target_price_cad"] is None


def test_un_seuil_dexception_hors_bornes_leve():
    with pytest.raises(RouteFormError):
        validate_route_form(
            {
                "label": "X",
                "origins": "YUL",
                "destinations": "CDG",
                "date_policy": "fixed",
                "depart": "2027-03-12",
                "exception_threshold": "1.5",
            }
        )


def test_un_seuil_dexception_a_la_borne_leve():
    """Les bornes 0 et 1 sont exclues : un seuil de 1 ne détecterait plus jamais d'aberration.

    Le reste du formulaire doit être par ailleurs valide (retour fourni) : sinon une levée par
    le garde-fou aller-retour masquerait un relâchement de *cette* vérification-ci.
    """
    with pytest.raises(RouteFormError, match="seuil"):
        validate_route_form(
            {
                "label": "X",
                "origins": "YUL",
                "destinations": "CDG",
                "date_policy": "fixed",
                "depart": "2027-03-12",
                "retour": "2027-03-22",
                "exception_threshold": "1",
            }
        )


def test_le_seuil_dexception_par_defaut_est_040():
    champs = validate_route_form(
        {
            "label": "X",
            "origins": "YUL",
            "destinations": "CDG",
            "date_policy": "fixed",
            "depart": "2027-03-12",
            "retour": "2027-03-22",
        }
    )

    assert champs["exception_threshold"] == 0.40


def test_les_passagers_par_defaut_sont_un():
    champs = validate_route_form(
        {
            "label": "X",
            "origins": "YUL",
            "destinations": "CDG",
            "date_policy": "fixed",
            "depart": "2027-03-12",
            "retour": "2027-03-22",
        }
    )

    assert champs["passengers"] == 1


def test_le_type_de_trajet_par_defaut_est_aller_retour():
    """Le défaut de `trip_type` dans `validate_route_form` doit rester cohérent avec celui de
    `build_policy_params` : c'est cette cohérence qui fait fonctionner le garde-fou sur la date
    de retour manquante quand le champ `trip_type` n'est simplement pas soumis."""
    champs = validate_route_form(
        {
            "label": "X",
            "origins": "YUL",
            "destinations": "CDG",
            "date_policy": "fixed",
            "depart": "2027-03-12",
            "retour": "2027-03-22",
        }
    )

    assert champs["trip_type"] is TripType.ROUND_TRIP


def test_la_politique_de_dates_par_defaut_est_flexible():
    champs = validate_route_form({"label": "X", "origins": "YUL", "destinations": "CDG"})

    assert champs["date_policy"] is DatePolicyKind.FLEXIBLE
