from __future__ import annotations

from dataclasses import dataclass

from scrappervol.detection.stats import mad, median, modified_z

SEUIL_Z_MODIFIE = -3.5

# Au-delà de ce silence, une source est considérée hors service et signalée en tête du digest.
SEUIL_SOURCE_MUETTE_H = 48

# Au-delà de cet âge, un relevé ne dit plus ce que coûte un billet : il dit ce qu'il coûtait.
# Partagé par les deux canaux, qui doivent s'accorder sur ce qu'ils appellent un prix actuel.
FRAICHEUR_RELEVE_J = 7

# En deçà, l'éventail des dates est trop étroit pour que sa dispersion veuille dire quelque
# chose : sur cinq dates voisines resserrées en trente dollars, un écart de six pour cent suffit
# à produire un score de -4,9 qui ne désigne aucune aubaine. Ce plancher tient aussi le critère
# calendaire à l'écart des trajets à dates fixes, où le balayage ne couvre qu'un voisinage
# immédiat et où la comparaison dans le temps garde tout son sens.
MIN_DATES_CALENDRIER = 8

# Au-delà de cette dispersion — écart absolu médian rapporté à la médiane — les dates d'un
# trajet sont trop hétérogènes pour que la série des plus bas quotidiens veuille dire quelque
# chose : la rotation change de dates à chaque passage, et le plus bas suit ce déplacement
# plutôt que le marché.
#
# Calibré sur les relevés du 7 août 2026, non dérivé : les trajets à dates fixes tiennent sous
# 1,5 %, l'horizon ouvert de douze mois est à 13,8 %. Le seuil est posé bas dans cet intervalle
# parce que les deux erreurs ne coûtent pas la même chose — se taire à tort laisse le critère
# calendaire prendre le relais, alerter à tort détruit la confiance dans l'outil.
SEUIL_HOMOGENEITE = 0.08


@dataclass(frozen=True, slots=True)
class PriceContext:
    """Historique des plus bas quotidiens d'un trajet, jour courant exclu."""

    daily_lows: list[int]

    @property
    def days_of_history(self) -> int:
        return len(self.daily_lows)

    @property
    def median_price(self) -> float | None:
        return median(self.daily_lows) if self.daily_lows else None

    def has_significant_history(self, min_days: int) -> bool:
        return self.days_of_history >= min_days


def relative_gap(price_cad: int, median_price: float) -> float:
    """Fraction sous la médiane. Positive quand le prix est plus bas qu'à l'ordinaire."""
    if median_price <= 0:
        return 0.0
    return (median_price - price_cad) / median_price


def is_exception(
    price_cad: int,
    context: PriceContext,
    threshold: float,
    min_history_days: int,
    credibility_floor: int,
    already_alerted: bool,
) -> bool:
    """Les quatre conditions du §6 du design, plus la condition MAD.

    L'ordre des tests suit le coût croissant : les rejets francs d'abord.
    """
    if already_alerted:
        return False
    if price_cad <= credibility_floor:
        return False
    if not context.has_significant_history(min_history_days):
        return False

    mediane = context.median_price
    if mediane is None:
        return False
    if price_cad > mediane * (1 - threshold):
        return False

    score = modified_z(price_cad, context.daily_lows)
    if score is None:
        return True
    return score <= SEUIL_Z_MODIFIE


def is_history_comparable(calendar_prices: list[int]) -> bool:
    """La série des plus bas quotidiens mesure-t-elle encore la même chose d'un jour à l'autre ?

    Elle ne le fait que si les dates couvertes sont assez homogènes. Sur un horizon ouvert, la
    rotation en balaie des différentes à chaque passage, et le plus bas bascule selon qu'elle
    tombe sur la basse saison ou sur Noël — un mouvement qui n'a rien à voir avec le marché.

    La dispersion se mesure ici par l'écart absolu médian, et non par l'étendue, parce qu'une
    aubaine creuse mécaniquement l'écart entre le plus bas et le plus haut. Sur les données du
    7 août 2026, injecter une aubaine à moitié prix fait passer l'étendue d'un trajet à dates
    fixes de 6 % à 50 % : s'y fier reviendrait à couper la détection au moment précis où elle
    doit parler. Le MAD, lui, ne bouge pas.

    En deçà de `MIN_DATES_CALENDRIER`, la réponse est oui faute de mieux : la dispersion n'y est
    pas mesurable, et surtout le critère calendaire ne pourrait pas prendre le relais. Mieux vaut
    une comparaison imparfaite que pas de détection du tout.
    """
    if len(calendar_prices) < MIN_DATES_CALENDRIER:
        return True

    centre = median(calendar_prices)
    if centre <= 0:
        return True
    return mad(calendar_prices) / centre < SEUIL_HOMOGENEITE


def is_calendar_exception(
    price_cad: int,
    calendar_prices: list[int],
    threshold: float,
    credibility_floor: int,
    already_alerted: bool,
) -> bool:
    """Exception lue dans l'éventail des dates plutôt que dans le temps.

    `is_exception` compare le plus bas d'aujourd'hui à ceux des jours passés. Cette lecture
    suppose que l'on regarde chaque jour les mêmes dates de départ — vrai pour un trajet à dates
    fixes, faux dès que l'horizon s'ouvre, puisque la rotation couvre alors des dates
    différentes à chaque passage et qu'un même trajet peut aller du simple au double selon que
    l'on parte en octobre ou à Noël.

    Ce critère-ci compare un prix aux autres dates relevées au même moment. Il ne dépend donc
    d'aucun historique, et rien de ce que fait la rotation ne peut le tromper.

    `calendar_prices` doit contenir le prix retenu, faute de quoi la dispersion serait mesurée
    sur une population dont il est absent.
    """
    if already_alerted:
        return False
    if price_cad <= credibility_floor:
        return False
    if len(calendar_prices) < MIN_DATES_CALENDRIER:
        return False

    # Le plancher de dates ci-dessus garantit une série non vide, sur laquelle `median` est
    # définie ; inutile de se prémunir ici du cas qu'elle signale par une exception.
    if price_cad > median(calendar_prices) * (1 - threshold):
        return False

    score = modified_z(price_cad, calendar_prices)
    if score is None:
        return True
    return score <= SEUIL_Z_MODIFIE


def is_find(
    price_cad: int,
    context: PriceContext,
    target_price_cad: int | None,
    find_threshold: float,
    min_history_days: int,
    credibility_floor: int,
) -> bool:
    """Trouvaille au sens du §8 : sous la cible absolue, ou nettement sous la médiane."""
    if price_cad <= credibility_floor:
        return False
    if target_price_cad is not None and price_cad <= target_price_cad:
        return True
    if not context.has_significant_history(min_history_days):
        return False

    mediane = context.median_price
    if mediane is None:
        return False
    return relative_gap(price_cad, mediane) >= find_threshold
