from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

DOSSIER_GABARITS = Path(__file__).parent / "templates"

_env = Environment(
    loader=FileSystemLoader(DOSSIER_GABARITS),
    # Seuls les gabarits HTML sont échappés : appliquer l'échappement au gabarit texte
    # transformerait chaque apostrophe française en entité HTML dans le courriel.
    autoescape=select_autoescape(enabled_extensions=("html.j2", "html"), default=False),
    trim_blocks=True,
    lstrip_blocks=True,
)


@dataclass(frozen=True, slots=True)
class RenderedMail:
    subject: str
    html: str
    text: str


def format_escales(stops: int) -> str:
    """« direct », « 1 escale », « 3 escales »."""
    if stops <= 0:
        return "direct"
    return f"{stops} escale{'s' if stops > 1 else ''}"


def format_duree(minutes: int | None) -> str:
    """« 5 h », « 13 h 30 », ou rien du tout si la durée n'a pas été relevée."""
    if minutes is None or minutes <= 0:
        return ""
    heures, reste = divmod(minutes, 60)
    return f"{heures} h {reste:02d}" if reste else f"{heures} h"


def format_trajet(stops: int, duration_minutes: int | None) -> str:
    """La forme du voyage, à côté de son prix : « direct · 5 h », « 1 escale · 13 h 30 ».

    Un prix seul ne dit pas ce qu'on achète. Le plus bas relevé sur une liaison peut être un vol
    à treize heures d'escale (observé sur YUL->CUN), et une alerte qui n'annonce que le montant
    invite à se précipiter sur un billet qu'on n'aurait pas choisi en connaissance de cause.
    """
    duree = format_duree(duration_minutes)
    escales = format_escales(stops)
    return f"{escales} · {duree}" if duree else escales


@dataclass(frozen=True, slots=True)
class RouteBlock:
    label: str
    price_cad: int | None
    airline: str
    origin: str
    destination: str
    depart_date: date | None
    return_date: date | None
    provider: str
    deep_link: str
    median_price: float | None
    gap_vs_median: float | None
    gap_vs_yesterday: int | None
    is_find: bool
    history_building: bool


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    name: str
    last_success_at: datetime | None
    consecutive_failures: int
    hours_silent: float | None
    is_stale: bool


@dataclass(frozen=True, slots=True)
class DigestData:
    day: date
    blocks: list[RouteBlock]
    providers: list[ProviderStatus]

    @property
    def find_count(self) -> int:
        return sum(1 for bloc in self.blocks if bloc.is_find and not bloc.history_building)

    @property
    def has_stale_provider(self) -> bool:
        return any(p.is_stale for p in self.providers)

    @property
    def sorted_blocks(self) -> list[RouteBlock]:
        """Meilleures affaires en tête ; les trajets sans historique significatif ferment la
        marche."""
        return sorted(
            self.blocks,
            key=lambda b: (b.history_building, -(b.gap_vs_median or 0.0)),
        )


@dataclass(frozen=True, slots=True)
class ExceptionData:
    label: str
    origin: str
    destination: str
    depart_date: date
    return_date: date | None
    price_cad: int
    airline: str
    provider: str
    deep_link: str
    median_price: float
    gap_vs_median: float
    history_days: int
    stops: int = 0
    duration_minutes: int | None = None


def render_digest(data: DigestData) -> RenderedMail:
    pluriel = "s" if data.find_count > 1 else ""
    sujet = f"ScrapperVol — {data.find_count} trouvaille{pluriel} du {data.day.isoformat()}"
    return RenderedMail(
        subject=sujet,
        html=_env.get_template("digest.html.j2").render(data=data, subject=sujet),
        text=_env.get_template("digest.txt.j2").render(data=data, subject=sujet),
    )


def render_test(at: datetime) -> RenderedMail:
    """Courriel de vérification de la chaîne de remise.

    Sans gabarit : ce message n'a rien à mettre en forme, et le faire passer par un fichier
    l'exposerait à casser au même endroit que les autres. On veut précisément qu'il aboutisse
    quand tout le reste échoue, pour distinguer un défaut d'envoi d'un défaut de rendu.
    """
    sujet = "ScrapperVol — courriel de test"
    horodatage = at.strftime("%Y-%m-%d %H:%M UTC")
    texte = (
        "Ce message confirme que ScrapperVol sait vous joindre.\n\n"
        f"Émis le {horodatage}.\n"
        "Les alertes d'aubaine et le digest quotidien emprunteront ce même chemin."
    )
    return RenderedMail(
        subject=sujet,
        html=(
            f"<p>Ce message confirme que ScrapperVol sait vous joindre.</p>"
            f"<p>Émis le {horodatage}.<br>"
            f"Les alertes d'aubaine et le digest quotidien emprunteront ce même chemin.</p>"
        ),
        text=texte,
    )


def render_exception(data: ExceptionData) -> RenderedMail:
    ecart = round(data.gap_vs_median * 100)
    sujet = f"ScrapperVol — {data.destination} à {data.price_cad} $ ({ecart} % sous la médiane)"
    trajet = format_trajet(data.stops, data.duration_minutes)
    return RenderedMail(
        subject=sujet,
        html=_env.get_template("exception.html.j2").render(
            data=data, subject=sujet, trajet=trajet
        ),
        text=_env.get_template("exception.txt.j2").render(data=data, subject=sujet, trajet=trajet),
    )
