from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

from scrappervol.config import Settings
from scrappervol.notify.render import RenderedMail

logger = logging.getLogger(__name__)


class Mailer(Protocol):
    def send(self, mail: RenderedMail, to: str) -> None: ...


def build_message(mail: RenderedMail, sender: str, to: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = mail.subject
    message["From"] = sender
    message["To"] = to
    message.set_content(mail.text)
    message.add_alternative(mail.html, subtype="html")
    return message


class NullMailer:
    """Utilisé quand aucun hôte SMTP n'est configuré : journalise au lieu d'envoyer."""

    def send(self, mail: RenderedMail, to: str) -> None:
        logger.info("courriel non envoyé (SMTP non configuré) : %s → %s", mail.subject, to)


class SmtpMailer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def send(self, mail: RenderedMail, to: str) -> None:
        message = build_message(mail, self._settings.smtp_from, to)
        try:
            with smtplib.SMTP(
                self._settings.smtp_host, self._settings.smtp_port, timeout=30
            ) as session:
                session.starttls()
                if self._settings.smtp_user and self._settings.smtp_password:
                    session.login(self._settings.smtp_user, self._settings.smtp_password)
                session.send_message(message)
        except Exception as erreur:  # noqa: BLE001 — traduit en erreur explicite pour l'appelant
            raise RuntimeError(f"envoi SMTP impossible : {erreur}") from erreur
        logger.info("courriel envoyé : %s → %s", mail.subject, to)


def build_mailer(settings: Settings) -> Mailer:
    return SmtpMailer(settings) if settings.smtp_host else NullMailer()
