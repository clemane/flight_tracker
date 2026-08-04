from __future__ import annotations

import statistics
from collections.abc import Sequence

_CONSTANTE_IGLEWICZ = 0.6745


def median(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("médiane indéfinie sur une série vide")
    return float(statistics.median(values))


def mad(values: Sequence[float]) -> float:
    """Écart absolu médian : la dispersion typique, insensible aux valeurs extrêmes."""
    if not values:
        raise ValueError("MAD indéfini sur une série vide")
    centre = median(values)
    return float(statistics.median([abs(valeur - centre) for valeur in values]))


def modified_z(value: float, values: Sequence[float]) -> float | None:
    """Score z modifié d'Iglewicz-Hoaglin.

    Retourne None quand le MAD est nul : le score diviserait par zéro, donc il est indéfini.
    Ce cas ne se limite pas à une série parfaitement plate. Le MAD tombe à zéro dès que *plus
    de la moitié* des valeurs coïncident avec la médiane — le reste de la série peut être très
    dispersé. Un tarif d'avion prend typiquement cette forme : un plancher stable la plupart
    des jours, entrecoupé de quelques pics ou creux ponctuels. Sur une telle série, ce n'est
    pas un cas marginal : c'est le profil normal, et c'est précisément sur les trajets les plus
    stables — ceux qu'on voudrait le mieux surveiller — que le score sera le plus souvent
    indéfini. L'appelant doit alors se rabattre sur un autre signal (par ex. un seuil relatif) ;
    ne pas court-circuiter ce repli en resserrant ce test à la seule série constante.
    """
    dispersion = mad(values)
    if dispersion == 0:
        return None
    return _CONSTANTE_IGLEWICZ * (value - median(values)) / dispersion
