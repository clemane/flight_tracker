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


SERIE_ETALEE = [450, 480, 510, 540, 570, 600, 600, 600, 600, 630, 660, 690, 720, 750]


def test_le_veto_du_score_z_se_joue_bien_a_moins_trois_virgule_cinq():
    """Verrouille la valeur du seuil, que les autres tests laissent flotter.

    Les scores z qu'ils atteignent valent -135, -247, -54 pour les cas qui déclenchent et
    -1.10 pour celui qui refuse : n'importe quel seuil entre -54 et -1.1 les laisse tous
    verts. Le seuil pourrait donc dériver de -3.5 à -20 sans qu'une seule ligne rougisse,
    et la détection deviendrait sourde en silence.

    Ce test se place à la frontière. SERIE_ETALEE a pour médiane 600 et pour MAD 60 ; les
    deux prix ci-dessous sont l'un et l'autre à plus de 50 % sous la médiane, donc tous
    deux admis par le seuil relatif. Seul le veto les sépare : z = -3.597 pour 280,
    z = -3.429 pour 295.

    Ce que ce test ne couvre pas, faute de pouvoir le faire : le cas z == -3.5 exactement,
    qui dirait si la comparaison est large ou stricte. Il faudrait un prix tel que
    (prix - médiane) / MAD vaille -3.5 / 0.6745, soit -5.18902891..., un rapport que deux
    entiers n'atteignent pas. Rendre la comparaison stricte est donc indétectable, et sans
    conséquence : aucune donnée réelle ne tombera jamais sur cette valeur.
    """
    commun = {
        "context": _contexte(SERIE_ETALEE),
        "threshold": 0.40,
        "min_history_days": 14,
        "credibility_floor": 50,
        "already_alerted": False,
    }

    assert is_exception(price_cad=280, **commun) is True
    assert is_exception(price_cad=295, **commun) is False


SERIE_RONDE = [490, 492, 494, 496, 498, 500, 500, 500, 502, 504, 506, 508, 510, 512]


def test_les_bornes_inclusives_se_jouent_bien_a_legalite():
    """Quatre comparaisons du module sont inclusives ; aucun test ne les exerçait à l'égalité.

    Les rendre strictes — `<= credibility_floor` en `<`, `> mediane*(1-threshold)` en `>=`,
    `<= target_price_cad` en `<`, `>= find_threshold` en `>` — laissait toute la suite verte.
    Chacune décide pourtant d'un cas réel : un billet à exactement 50 CAD, un prix pile au
    plancher relatif, une cible atteinte au dollar près.

    SERIE_RONDE a pour médiane 500.0, donc un plancher à 40 % qui vaut 300.0 tout rond : le
    seul moyen d'atteindre l'égalité exacte, `price_cad` étant un entier.
    """
    exception = {
        "context": _contexte(SERIE_STABLE),
        "threshold": 0.40,
        "min_history_days": 14,
        "credibility_floor": 50,
        "already_alerted": False,
    }
    # Un prix pile au plancher de crédibilité est jugé trop beau pour être vrai.
    assert is_exception(price_cad=50, **exception) is False

    relatif = {**exception, "context": _contexte(SERIE_RONDE)}
    # Pile à 40 % sous la médiane, le prix est admis ; un dollar au-dessus, il ne l'est plus.
    assert is_exception(price_cad=300, **relatif) is True
    assert is_exception(price_cad=301, **relatif) is False

    # Une cible atteinte au dollar près est atteinte.
    assert (
        is_find(
            price_cad=500,
            context=_contexte([]),
            target_price_cad=500,
            find_threshold=0.15,
            min_history_days=14,
        )
        is True
    )

    # 510 est exactement 15 % sous 600 : le seuil de trouvaille est lui aussi inclusif.
    assert (
        is_find(
            price_cad=510,
            context=_contexte(SERIE_ETALEE),
            target_price_cad=None,
            find_threshold=0.15,
            min_history_days=14,
        )
        is True
    )


def test_relative_gap_ne_divise_pas_par_une_mediane_nulle():
    """Branche jamais exercée par les autres tests, et pourtant seule à séparer une division
    par zéro d'un résultat neutre. Une médiane nulle ou négative n'a pas de sens ; l'écart
    relatif non plus."""
    assert relative_gap(500, 0.0) == 0.0
    assert relative_gap(500, -10.0) == 0.0
