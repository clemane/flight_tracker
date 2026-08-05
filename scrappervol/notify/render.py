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


def render_digest(data: DigestData) -> RenderedMail:
    pluriel = "s" if data.find_count > 1 else ""
    sujet = f"ScrapperVol — {data.find_count} trouvaille{pluriel} du {data.day.isoformat()}"
    return RenderedMail(
        subject=sujet,
        html=_env.get_template("digest.html.j2").render(data=data, subject=sujet),
        text=_env.get_template("digest.txt.j2").render(data=data, subject=sujet),
    )


def render_exception(data: ExceptionData) -> RenderedMail:
    ecart = round(data.gap_vs_median * 100)
    sujet = f"ScrapperVol — {data.destination} à {data.price_cad} $ ({ecart} % sous la médiane)"
    return RenderedMail(
        subject=sujet,
        html=_env.get_template("exception.html.j2").render(data=data, subject=sujet),
        text=_env.get_template("exception.txt.j2").render(data=data, subject=sujet),
    )
