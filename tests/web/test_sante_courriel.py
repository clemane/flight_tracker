"""Surveillance du canal de sortie.

Le système savait dire qu'une source s'était tue, jamais qu'il était devenu incapable de joindre
son destinataire. Les deux pannes sont pourtant symétriques, et la seconde est la plus coûteuse :
une source muette laisse les autres travailler, un canal muet annule tout le reste.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from scrappervol.config import Settings
from scrappervol.storage.models import NotifyHealth
from scrappervol.web.app import create_app, get_now, get_session

MAINTENANT = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)

REGLAGE_REEL = {"smtp_host": "smtp.fastmail.com", "alert_to": "clement@courriel.ca"}

# Toujours explicite : `Settings()` hérite du `.env` du conteneur, où `SMTP_HOST` est renseigné.
# Un test qui compterait sur le défaut du modèle lirait donc la configuration de la machine.
REGLAGE_VIDE = {"smtp_host": "", "alert_to": ""}


def _client(engine, session, **reglages) -> TestClient:
    application = create_app(engine, Settings(enabled_providers=["google_flights"], **reglages))
    application.dependency_overrides[get_session] = lambda: session
    application.dependency_overrides[get_now] = lambda: MAINTENANT
    return TestClient(application)


class TestAffichage:
    def test_une_configuration_absente_est_annoncee_en_toutes_lettres(self, engine, session):
        corps = _client(engine, session, **REGLAGE_VIDE).get("/health").text

        assert 'data-etat="non-configure"' in corps
        assert "SMTP_HOST" in corps

    def test_le_fichier_dexemple_jamais_rempli_compte_comme_absent(self, engine, session):
        """Le piège que le dépôt tend lui-même : `.env.example` livre un hôte en `example.com`.

        Sans ce test, la seule façon de découvrir que rien ne partirait était d'attendre une
        aubaine — c'est-à-dire de la perdre.
        """
        client = _client(engine, session, smtp_host="smtp.example.com", alert_to="moi@example.com")

        corps = client.get("/health").text

        assert 'data-etat="non-configure"' in corps
        assert "smtp.example.com" in corps

    def test_une_configuration_reelle_sans_envoi_est_annoncee_prete(self, engine, session):
        corps = _client(engine, session, **REGLAGE_REEL).get("/health").text

        assert 'data-etat="ok"' in corps
        assert "clement@courriel.ca" in corps
        assert "Aucun envoi à ce jour" in corps

    def test_le_dernier_envoi_reussi_est_date(self, engine, session):
        session.add(NotifyHealth(channel="email", last_success_at=MAINTENANT))
        session.commit()

        corps = _client(engine, session, **REGLAGE_REEL).get("/health").text

        assert "2026-08-06 12:00" in corps

    def test_des_echecs_consecutifs_sont_montres_avec_leur_cause(self, engine, session):
        session.add(
            NotifyHealth(
                channel="email",
                consecutive_failures=3,
                last_failure_at=MAINTENANT,
                last_error="Connection refused",
            )
        )
        session.commit()

        corps = _client(engine, session, **REGLAGE_REEL).get("/health").text

        assert 'data-etat="en-panne"' in corps
        assert "Connection refused" in corps
        assert "3" in corps

    def test_une_panne_denvoi_ne_masque_pas_le_tableau_des_sources(self, engine, session):
        """Les deux surveillances cohabitent : diagnostiquer l'une exige de voir l'autre."""
        session.add(NotifyHealth(channel="email", consecutive_failures=1, last_error="refusé"))
        session.commit()

        corps = _client(engine, session, **REGLAGE_REEL).get("/health").text

        assert 'data-etat="en-panne"' in corps
        assert 'data-source="google_flights"' in corps


class TestEnvoiDeTest:
    def test_un_envoi_reussi_est_confirme_et_enregistre(self, engine, session, monkeypatch):
        facteur = MagicMock()
        monkeypatch.setattr("scrappervol.web.routes.build_mailer", lambda _: facteur)

        corps = _client(engine, session, **REGLAGE_REEL).post("/health/test-courriel").text

        assert 'data-test-resultat="succes"' in corps
        facteur.send.assert_called_once()
        assert facteur.send.call_args.args[1] == "clement@courriel.ca"

        sante = session.get(NotifyHealth, "email")
        assert sante is not None
        assert sante.last_success_at == MAINTENANT
        assert sante.consecutive_failures == 0

    def test_un_refus_du_serveur_est_affiche_et_enregistre(self, engine, session, monkeypatch):
        facteur = MagicMock()
        facteur.send.side_effect = RuntimeError("envoi SMTP impossible : [Errno 111] refusé")
        monkeypatch.setattr("scrappervol.web.routes.build_mailer", lambda _: facteur)

        corps = _client(engine, session, **REGLAGE_REEL).post("/health/test-courriel").text

        assert 'data-test-resultat="echec"' in corps
        assert "Errno 111" in corps

        sante = session.get(NotifyHealth, "email")
        assert sante is not None
        assert sante.consecutive_failures == 1
        assert sante.last_failure_at == MAINTENANT
        assert sante.last_success_at is None

    def test_sans_configuration_rien_nest_tente(self, engine, session, monkeypatch):
        """Inutile d'ouvrir une session SMTP vers un hôte vide : le diagnostic est déjà connu."""
        facteur = MagicMock()
        monkeypatch.setattr("scrappervol.web.routes.build_mailer", lambda _: facteur)

        corps = _client(engine, session, **REGLAGE_VIDE).post("/health/test-courriel").text

        assert 'data-test-resultat="echec"' in corps
        assert "SMTP_HOST" in corps
        facteur.send.assert_not_called()

    def test_un_succes_efface_le_compteur_dechecs(self, engine, session, monkeypatch):
        """Sans remise à zéro, la page resterait rouge après le réglage du problème."""
        session.add(NotifyHealth(channel="email", consecutive_failures=5, last_error="refusé"))
        session.commit()
        monkeypatch.setattr("scrappervol.web.routes.build_mailer", lambda _: MagicMock())

        client = _client(engine, session, **REGLAGE_REEL)
        corps = client.post("/health/test-courriel").text

        assert 'data-etat="ok"' in corps
        sante = session.get(NotifyHealth, "email")
        assert sante is not None
        assert sante.consecutive_failures == 0
        assert sante.last_error is None

    def test_les_echecs_saccumulent(self, engine, session, monkeypatch):
        facteur = MagicMock()
        facteur.send.side_effect = RuntimeError("refusé")
        monkeypatch.setattr("scrappervol.web.routes.build_mailer", lambda _: facteur)
        client = _client(engine, session, **REGLAGE_REEL)

        for _ in range(3):
            client.post("/health/test-courriel")

        sante = session.get(NotifyHealth, "email")
        assert sante is not None
        assert sante.consecutive_failures == 3

    @pytest.mark.parametrize("methode", ["get", "put", "delete"])
    def test_le_test_denvoi_nest_accessible_quen_post(self, engine, session, methode):
        """Un GET rendrait l'envoi déclenchable par un simple préchargement de lien."""
        client = _client(engine, session, **REGLAGE_REEL)

        assert getattr(client, methode)("/health/test-courriel").status_code == 405
