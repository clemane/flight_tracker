from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scrappervol.config import Settings
from scrappervol.providers.base import ProviderError

logger = logging.getLogger(__name__)

AGENT_UTILISATEUR = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def debug_path(settings: Settings, provider_name: str) -> Path:
    dossier = Path(settings.data_dir) / "debug"
    dossier.mkdir(parents=True, exist_ok=True)
    return dossier / f"{provider_name}.html"


def fetch_html(
    url: str,
    settings: Settings,
    provider_name: str,
    wait_selector: str | None = None,
    interact: Callable[[Any], None] | None = None,
    stealth: bool = False,
    timeout_ms: int = 45_000,
    headless: bool = True,
) -> str:
    """Charge une page avec Chromium et retourne son HTML, en conservant une capture de débogage.

    `interact` reçoit la page après chargement et avant `wait_selector` : c'est par là qu'on
    remplit un formulaire quand le site n'expose pas de page de résultats adressable par URL.

    `headless=False` demande un navigateur à fenêtre, donc un serveur X (le conteneur en démarre
    un au lancement, voir docker-entrypoint.sh). C'est ce dont Air Canada a besoin : son parcours
    de réservation déroute vers une page d'erreur générique quand le navigateur est sans fenêtre,
    et aboutit normalement sinon.

    La capture de débogage est écrite dans tous les cas, succès comme échec : c'est elle qui permet
    de réparer une dérive de sélecteur sans avoir à reproduire le problème (§10 du design). Une
    dérive de sélecteur ne lève pas d'exception — elle rend une liste vide, ce qui ressemble
    exactement à « pas de vol disponible ».
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as erreur:
        raise ProviderError(f"Playwright indisponible : {erreur}") from erreur

    html = ""
    try:
        with sync_playwright() as playwright:
            navigateur = playwright.chromium.launch(
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            contexte = navigateur.new_context(
                user_agent=AGENT_UTILISATEUR,
                viewport={"width": 1440, "height": 900},
                locale="fr-CA",
                timezone_id=settings.timezone,
            )
            page = contexte.new_page()
            if stealth:
                page.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                )
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            if interact is not None:
                interact(page)
            if wait_selector:
                page.wait_for_selector(wait_selector, timeout=timeout_ms)
            html = page.content()
            contexte.close()
            navigateur.close()
    except Exception as erreur:  # noqa: BLE001 — traduit vers l'exception du domaine
        if html:
            debug_path(settings, provider_name).write_text(html, encoding="utf-8")
        raise ProviderError(f"échec du chargement de {url} : {erreur}") from erreur

    debug_path(settings, provider_name).write_text(html, encoding="utf-8")
    return html
