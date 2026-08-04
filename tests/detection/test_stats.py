import pytest

from scrappervol.detection.stats import mad, median, modified_z


def test_mediane_dune_serie_impaire():
    assert median([3, 1, 2]) == 2


def test_mediane_dune_serie_paire_est_la_moyenne_des_deux_centrales():
    assert median([1, 2, 3, 4]) == 2.5


def test_mediane_dune_serie_vide_leve_une_erreur():
    with pytest.raises(ValueError):
        median([])


def test_la_mediane_resiste_a_une_valeur_extreme():
    """C'est la raison d'être du choix : une aubaine ne doit pas déplacer la référence."""
    normale = [500, 510, 520, 530, 540]
    avec_aubaine = [*normale, 120]

    assert abs(median(avec_aubaine) - median(normale)) < 40


def test_mad_dune_serie_plate_est_nul():
    assert mad([500, 500, 500]) == 0.0


def test_mad_mesure_la_dispersion_typique():
    assert mad([1, 2, 3, 4, 5]) == 1.0


def test_mad_ignore_une_valeur_extreme():
    """Contrairement à l'écart-type, le MAD n'est pas déstabilisé par une valeur extrême.

    Le brief affirmait une égalité stricte entre le MAD d'une série et celui de la même série
    augmentée d'une valeur extrême — ce n'est pas garanti, même à implémentation correcte :
    ajouter un point fait passer l'effectif d'impair à pair, ce qui change la façon dont la
    médiane (et donc le MAD) est calculée : une seule valeur centrale contre la moyenne des deux
    valeurs centrales. Preuve avec l'exemple du brief : mad([500, 502, 504, 506, 508]) vaut 2.0
    mais mad([500, 502, 504, 506, 508, 5]) vaut 3.0, avec l'implémentation même proposée par le
    brief. Ce test vérifie donc ce que le MAD garantit réellement : une valeur extrême ne le
    fait bouger que d'un cran, jamais de l'ordre de grandeur — à comparer à l'écart-type, qui
    passe ici de ~5 à ~151 pour ce seul point ajouté.
    """
    sans_aubaine = [500, 502, 504, 506, 508, 510, 512, 514, 516]
    avec_aubaine = [*sans_aubaine, 5]

    assert abs(mad(avec_aubaine) - mad(sans_aubaine)) <= 2


def test_mad_est_nul_des_que_plus_de_la_moitie_des_valeurs_egalent_la_mediane():
    """Un MAD nul n'implique pas une série plate.

    Un tarif d'avion a typiquement un plancher stable la plupart des jours, entrecoupé de
    quelques pics. Ici 6 valeurs sur 9 valent 400 (une majorité stricte) alors que les trois
    autres s'étalent de 250 à 900 : la série est loin d'être plate, mais le MAD tombe quand
    même à zéro parce que plus de la moitié des écarts à la médiane sont nuls.
    """
    serie = [400, 400, 400, 400, 400, 400, 850, 900, 250]

    assert mad(serie) == 0.0
    assert modified_z(400, serie) is None


def test_modified_z_est_negatif_sous_la_mediane():
    serie = [500, 505, 510, 515, 520]

    assert modified_z(400, serie) < 0


def test_modified_z_est_nul_a_la_mediane():
    serie = [500, 505, 510, 515, 520]

    assert modified_z(510, serie) == 0


def test_modified_z_signale_franchement_une_aubaine_sur_serie_stable():
    serie = [600, 601, 600, 602, 599, 601, 600]

    assert modified_z(400, serie) <= -3.5


def test_modified_z_reste_modere_sur_serie_volatile():
    serie = [300, 900, 450, 1100, 380, 950, 500]

    assert modified_z(240, serie) > -3.5


def test_modified_z_est_indefini_sur_serie_plate():
    assert modified_z(400, [600, 600, 600]) is None


def test_modified_z_est_indefini_sur_une_serie_dun_seul_element():
    """Une série à un seul point n'a pas de dispersion : le MAD est nul par construction
    (l'unique écart à la médiane vaut 0), donc le score doit rester indéfini plutôt que de
    diviser par zéro ou de produire un score artificiellement extrême."""
    assert mad([500]) == 0.0
    assert modified_z(400, [500]) is None


def test_modified_z_vaut_exactement_la_formule_diglewicz_hoaglin():
    """Verrouille la valeur numérique, donc la constante 0.6745 elle-même.

    Les autres tests ne vérifient que le signe du score et le franchissement du seuil, avec
    des marges telles qu'ils survivent à une constante fausse : remplacer 0.6745 par 1.0 les
    laisse tous verts. Or cette constante n'est pas cosmétique. Elle ramène le score à
    l'échelle d'un score z normal ; sans elle, tout score est gonflé d'un facteur ~1.48, ce
    qui revient à desserrer le seuil de -3.5 de la détection jusqu'à ~-5.2 sans que rien ne
    le dise. La détection deviendrait silencieusement plus sourde — le mode de panne que ce
    projet redoute le plus. Le test fixe donc une valeur calculée à la main :
    médiane([10, 12, 14, 16, 18]) = 14, MAD = 2, 0.6745 * (8 - 14) / 2 = -2.0235.
    """
    assert modified_z(8, [10, 12, 14, 16, 18]) == pytest.approx(-2.0235)


def test_mad_refuse_une_serie_vide():
    """La garde de `mad` n'est autrement jamais exercée : elle est masquée par l'appel interne
    à `median`, qui lève déjà. La tester ici la rend intentionnelle plutôt qu'accidentelle."""
    with pytest.raises(ValueError, match="MAD indéfini"):
        mad([])
