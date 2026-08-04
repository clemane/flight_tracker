from scrappervol.detection.rules import PriceContext, is_exception, is_find, relative_gap

SERIE_STABLE = [600, 605, 598, 602, 601, 599, 603, 600, 604, 597, 601, 602, 600, 599, 605, 598]


def _contexte(prix: list[int]) -> PriceContext:
    return PriceContext(daily_lows=prix)


def test_days_of_history_compte_les_jours_observes():
    assert _contexte([600, 610, 620]).days_of_history == 3


def test_median_price_est_none_sans_historique():
    assert _contexte([]).median_price is None


def test_relative_gap_mesure_la_fraction_sous_la_mediane():
    assert relative_gap(300, 600.0) == 0.5
    assert relative_gap(600, 600.0) == 0.0
    assert relative_gap(900, 600.0) == -0.5


def test_aberration_franche_sur_serie_stable_declenche():
    assert (
        is_exception(
            price_cad=300,
            context=_contexte(SERIE_STABLE),
            threshold=0.40,
            min_history_days=14,
            credibility_floor=50,
            already_alerted=False,
        )
        is True
    )


def test_historique_trop_court_ne_declenche_jamais():
    """Sans ce garde-fou, les deux premières semaines produisent une fausse alerte à chaque
    passage."""
    assert (
        is_exception(
            price_cad=100,
            context=_contexte([600, 610, 605]),
            threshold=0.40,
            min_history_days=14,
            credibility_floor=50,
            already_alerted=False,
        )
        is False
    )


def test_prix_sous_le_plancher_de_credibilite_ne_declenche_pas():
    """Un « 45 » lu dans « 45 min d'escale » ne doit pas réveiller l'utilisateur la nuit."""
    assert (
        is_exception(
            price_cad=45,
            context=_contexte(SERIE_STABLE),
            threshold=0.40,
            min_history_days=14,
            credibility_floor=50,
            already_alerted=False,
        )
        is False
    )


def test_prix_juste_au_dessus_du_plancher_declenche():
    assert (
        is_exception(
            price_cad=51,
            context=_contexte(SERIE_STABLE),
            threshold=0.40,
            min_history_days=14,
            credibility_floor=50,
            already_alerted=False,
        )
        is True
    )


def test_baisse_insuffisante_ne_declenche_pas():
    assert (
        is_exception(
            price_cad=500,
            context=_contexte(SERIE_STABLE),
            threshold=0.40,
            min_history_days=14,
            credibility_floor=50,
            already_alerted=False,
        )
        is False
    )


def test_une_alerte_deja_emise_reste_silencieuse():
    assert (
        is_exception(
            price_cad=300,
            context=_contexte(SERIE_STABLE),
            threshold=0.40,
            min_history_days=14,
            credibility_floor=50,
            already_alerted=True,
        )
        is False
    )


def test_serie_volatile_ne_declenche_pas_a_moins_quarante_pourcent():
    """Sur une série qui oscille du simple au triple, -40 % est le régime normal, pas une
    aubaine."""
    volatile = [300, 900, 450, 1100, 380, 950, 500, 1000, 320, 880, 460, 1050, 400, 920, 480, 990]

    assert (
        is_exception(
            price_cad=240,
            context=_contexte(volatile),
            threshold=0.40,
            min_history_days=14,
            credibility_floor=50,
            already_alerted=False,
        )
        is False
    )


def test_serie_parfaitement_plate_retombe_sur_le_seuil_relatif():
    plate = [600] * 20

    assert (
        is_exception(
            price_cad=300,
            context=_contexte(plate),
            threshold=0.40,
            min_history_days=14,
            credibility_floor=50,
            already_alerted=False,
        )
        is True
    )


def test_le_seuil_est_configurable_par_trajet():
    assert (
        is_exception(
            price_cad=480,
            context=_contexte(SERIE_STABLE),
            threshold=0.20,
            min_history_days=14,
            credibility_floor=50,
            already_alerted=False,
        )
        is True
    )


def test_trouvaille_quand_le_prix_passe_sous_la_cible():
    assert (
        is_find(
            price_cad=450,
            context=_contexte(SERIE_STABLE),
            target_price_cad=500,
            find_threshold=0.15,
            min_history_days=14,
        )
        is True
    )


def test_trouvaille_sous_la_cible_meme_sans_historique_significatif():
    """La cible est un seuil absolu voulu par l'utilisateur : elle ne dépend pas de la
    statistique."""
    assert (
        is_find(
            price_cad=450,
            context=_contexte([600, 610]),
            target_price_cad=500,
            find_threshold=0.15,
            min_history_days=14,
        )
        is True
    )


def test_trouvaille_quand_le_prix_est_quinze_pourcent_sous_la_mediane():
    assert (
        is_find(
            price_cad=500,
            context=_contexte(SERIE_STABLE),
            target_price_cad=None,
            find_threshold=0.15,
            min_history_days=14,
        )
        is True
    )


def test_pas_de_trouvaille_statistique_sans_historique_significatif():
    assert (
        is_find(
            price_cad=100,
            context=_contexte([600, 610, 605]),
            target_price_cad=None,
            find_threshold=0.15,
            min_history_days=14,
        )
        is False
    )


def test_prix_ordinaire_nest_pas_une_trouvaille():
    assert (
        is_find(
            price_cad=595,
            context=_contexte(SERIE_STABLE),
            target_price_cad=None,
            find_threshold=0.15,
            min_history_days=14,
        )
        is False
    )
