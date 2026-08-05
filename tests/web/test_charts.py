from scrappervol.web.charts import sparkline_points


def test_une_serie_vide_ne_produit_aucun_point():
    assert sparkline_points([]) == ""


def test_un_point_unique_est_centre_verticalement():
    points = sparkline_points([500], width=100, height=40)

    assert points == "0,20 100,20"


def test_le_minimum_touche_le_bas_et_le_maximum_le_haut():
    points = sparkline_points([100, 200], width=100, height=40).split()

    assert points[0].endswith(",40")
    assert points[1].endswith(",0")


def test_les_points_sont_repartis_sur_la_largeur():
    points = sparkline_points([100, 150, 200], width=100, height=40).split()

    assert [p.split(",")[0] for p in points] == ["0", "50", "100"]


def test_une_serie_plate_reste_a_mi_hauteur():
    points = sparkline_points([500, 500, 500], width=100, height=40).split()

    assert all(p.endswith(",20") for p in points)
