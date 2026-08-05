import smtplib
from unittest.mock import MagicMock, patch

import pytest

from scrappervol.config import Settings
from scrappervol.notify.mailer import NullMailer, SmtpMailer, build_mailer, build_message
from scrappervol.notify.render import RenderedMail

COURRIEL = RenderedMail(subject="Sujet", html="<p>corps</p>", text="corps")


def test_le_message_porte_les_deux_versions():
    message = build_message(COURRIEL, sender="de@example.com", to="vers@example.com")

    assert message["Subject"] == "Sujet"
    assert message["From"] == "de@example.com"
    assert message["To"] == "vers@example.com"
    assert message.get_body(preferencelist=("plain",)).get_content().strip() == "corps"
    assert "<p>corps</p>" in message.get_body(preferencelist=("html",)).get_content()


def test_build_mailer_retourne_un_null_mailer_sans_hote():
    assert isinstance(build_mailer(Settings(smtp_host="")), NullMailer)


def test_build_mailer_retourne_un_smtp_mailer_avec_hote():
    assert isinstance(build_mailer(Settings(smtp_host="smtp.example.com")), SmtpMailer)


def test_le_null_mailer_journalise_au_lieu_denvoyer(caplog):
    with caplog.at_level("INFO", logger="scrappervol.notify.mailer"):
        NullMailer().send(COURRIEL, "vers@example.com")

    assert "Sujet" in caplog.text
    assert "vers@example.com" in caplog.text


def test_le_smtp_mailer_ouvre_une_session_chiffree_et_envoie():
    reglages = Settings(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="utilisateur",
        smtp_password="secret",
        smtp_from="de@example.com",
    )
    session = MagicMock()

    with patch("scrappervol.notify.mailer.smtplib.SMTP") as smtp:
        smtp.return_value.__enter__.return_value = session
        SmtpMailer(reglages).send(COURRIEL, "vers@example.com")

    smtp.assert_called_once_with("smtp.example.com", 587, timeout=30)
    session.starttls.assert_called_once()
    session.login.assert_called_once_with("utilisateur", "secret")
    session.send_message.assert_called_once()


def test_le_smtp_mailer_envoie_sans_sauthentifier_quand_il_ny_a_pas_didentifiants():
    """`login.assert_not_called()` seul est une assertion purement négative : elle passerait
    aussi si *rien* ne se produisait. Elle laisserait notamment passer un `send_message`
    indenté par mégarde dans le `if` des identifiants — sur un relais local sans
    authentification, plus aucun courriel ne partirait et la suite resterait verte."""
    reglages = Settings(smtp_host="smtp.example.com", smtp_user="", smtp_password="")
    session = MagicMock()

    with patch("scrappervol.notify.mailer.smtplib.SMTP") as smtp:
        smtp.return_value.__enter__.return_value = session
        SmtpMailer(reglages).send(COURRIEL, "vers@example.com")

    session.login.assert_not_called()
    session.send_message.assert_called_once()


def test_le_smtp_mailer_nauthentifie_pas_avec_des_identifiants_partiels():
    """`smtp_user and smtp_password` doit rester un ET : si un seul des deux champs est
    renseigné, l'authentification est incomplète et ne doit pas être tentée. Un `OU` à la
    place laisserait passer un `login("utilisateur", "")` — un échec d'authentification
    silencieusement transformé en tentative bancale plutôt que refusée d'entrée."""
    reglages = Settings(
        smtp_host="smtp.example.com", smtp_user="utilisateur", smtp_password=""
    )
    session = MagicMock()

    with patch("scrappervol.notify.mailer.smtplib.SMTP") as smtp:
        smtp.return_value.__enter__.return_value = session
        SmtpMailer(reglages).send(COURRIEL, "vers@example.com")

    session.login.assert_not_called()
    session.send_message.assert_called_once()


def test_un_serveur_sans_starttls_fait_echouer_lenvoi_plutot_que_de_livrer_en_clair():
    """`starttls()` est appelé sans condition : si le serveur ne le supporte pas, l'envoi doit
    échouer plutôt que de transmettre le mot de passe en clair. Ce test verrouille ce choix pour
    qu'il ne soit pas « réparé » plus tard par quelqu'un qui prendrait l'échec pour un bogue."""
    reglages = Settings(
        smtp_host="smtp.example.com", smtp_user="utilisateur", smtp_password="secret"
    )
    session = MagicMock()
    session.starttls.side_effect = smtplib.SMTPNotSupportedError("STARTTLS non supporté")

    with patch("scrappervol.notify.mailer.smtplib.SMTP") as smtp:
        smtp.return_value.__enter__.return_value = session
        with pytest.raises(RuntimeError, match="envoi SMTP"):
            SmtpMailer(reglages).send(COURRIEL, "vers@example.com")

    session.login.assert_not_called()
    session.send_message.assert_not_called()


def test_un_echec_denvoi_leve_une_erreur_explicite():
    reglages = Settings(smtp_host="smtp.example.com")

    with (
        patch("scrappervol.notify.mailer.smtplib.SMTP", side_effect=OSError("injoignable")),
        pytest.raises(RuntimeError, match="envoi SMTP"),
    ):
        SmtpMailer(reglages).send(COURRIEL, "vers@example.com")


def test_un_echec_denvoi_ne_journalise_jamais_le_mot_de_passe(caplog):
    """`repr(Settings)` contient le mot de passe en clair (vérifié : `smtp_password=...`
    apparaît tel quel). Un gestionnaire d'erreur qui journaliserait les réglages pour
    faciliter le débogage — par exemple `logger.error(..., self._settings)` — écrirait donc
    le secret dans les journaux sans qu'aucune assertion sur le message d'exception ne
    puisse le voir."""
    reglages = Settings(
        smtp_host="smtp.example.com", smtp_user="utilisateur", smtp_password="secret-a-proteger"
    )

    with (
        caplog.at_level("DEBUG", logger="scrappervol.notify.mailer"),
        patch("scrappervol.notify.mailer.smtplib.SMTP", side_effect=OSError("injoignable")),
        pytest.raises(RuntimeError, match="envoi SMTP"),
    ):
        SmtpMailer(reglages).send(COURRIEL, "vers@example.com")

    assert "secret-a-proteger" not in caplog.text
