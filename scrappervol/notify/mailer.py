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


# Domaines que la RFC 2606 réserve à la documentation : ils ne résolvent nulle part, par
# construction. Les voir en configuration ne signifie donc pas « mal réglé » mais « jamais réglé ».
_DOMAINES_RESERVES = (".example.com", ".example.org", ".example.net", "example.com", "example.org")


def est_factice(valeur: str) -> bool:
    """La valeur sort-elle du fichier d'exemple plutôt que d'un vrai réglage ?"""
    return any(valeur.lower().endswith(domaine) for domaine in _DOMAINES_RESERVES)


def defaut_de_configuration(settings: Settings) -> str | None:
    """Ce qui empêche d'envoyer, en une phrase, ou None si la configuration est exploitable.

    Distinguer « non configuré » de « en panne » importe : le premier se règle dans le `.env`, le
    second se subit. Tenter malgré tout un envoi vers un domaine réservé garantissait un échec par
    passage, indistinguable dans les journaux d'un vrai incident réseau.
    """
    if not settings.smtp_host:
        return "aucun hôte SMTP configuré (SMTP_HOST)"
    if not settings.alert_to:
        return "aucun destinataire configuré (ALERT_TO)"
    if est_factice(settings.smtp_host):
        return f"l'hôte SMTP est resté celui de l'exemple ({settings.smtp_host})"
    if est_factice(settings.alert_to):
        return f"le destinataire est resté celui de l'exemple ({settings.alert_to})"
    return None


def build_mailer(settings: Settings) -> Mailer:
    defaut = defaut_de_configuration(settings)
    if defaut is None:
        return SmtpMailer(settings)
    logger.warning("les courriels ne partiront pas : %s", defaut)
    return NullMailer()
