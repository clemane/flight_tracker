from __future__ import annotations


def sparkline_points(prices: list[int], width: int = 240, height: int = 48) -> str:
    """Coordonnées d'une polyligne SVG, du plus ancien au plus récent.

    Généré côté serveur : un graphe d'historique n'a pas besoin d'une chaîne de build JavaScript.
    """
    if not prices:
        return ""
    if len(prices) == 1:
        milieu = height / 2
        return f"0,{milieu:g} {width},{milieu:g}"

    minimum, maximum = min(prices), max(prices)
    etendue = maximum - minimum
    pas = width / (len(prices) - 1)

    points = []
    for index, prix in enumerate(prices):
        x = index * pas
        y = height / 2 if etendue == 0 else height - ((prix - minimum) / etendue) * height
        points.append(f"{x:g},{y:g}")
    return " ".join(points)
