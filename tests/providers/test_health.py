from datetime import UTC, datetime, timedelta

from scrappervol.providers.health import backoff_until, is_disabled
from scrappervol.storage.models import ProviderHealth

MAINTENANT = datetime(2026, 8, 4, 14, 0, tzinfo=UTC)


def test_pas_de_repos_avant_trois_echecs():
    assert backoff_until(0, MAINTENANT) is None
    assert backoff_until(1, MAINTENANT) is None
    assert backoff_until(2, MAINTENANT) is None


def test_le_troisieme_echec_pose_une_heure_de_repos():
    assert backoff_until(3, MAINTENANT) == MAINTENANT + timedelta(hours=1)


def test_le_repos_double_a_chaque_echec_supplementaire():
    assert backoff_until(4, MAINTENANT) == MAINTENANT + timedelta(hours=2)
    assert backoff_until(5, MAINTENANT) == MAINTENANT + timedelta(hours=4)
    assert backoff_until(6, MAINTENANT) == MAINTENANT + timedelta(hours=8)
    assert backoff_until(7, MAINTENANT) == MAINTENANT + timedelta(hours=16)


def test_le_repos_plafonne_a_vingt_quatre_heures():
    """Marteler une protection anti-bot transforme un blocage temporaire en bannissement durable."""
    assert backoff_until(8, MAINTENANT) == MAINTENANT + timedelta(hours=24)
    assert backoff_until(50, MAINTENANT) == MAINTENANT + timedelta(hours=24)


def test_une_source_sans_repos_est_active():
    assert is_disabled(ProviderHealth(provider="transat"), MAINTENANT) is False


def test_une_source_au_repos_est_inactive():
    sante = ProviderHealth(provider="transat", disabled_until=MAINTENANT + timedelta(hours=1))

    assert is_disabled(sante, MAINTENANT) is True


def test_une_source_dont_le_repos_est_echu_redevient_active():
    sante = ProviderHealth(provider="transat", disabled_until=MAINTENANT - timedelta(minutes=1))

    assert is_disabled(sante, MAINTENANT) is False


def test_une_source_dont_le_repos_expire_a_l_instant_pile_redevient_active():
    """Frontière exacte de disabled_until == now : le repos doit déjà être terminé.

    Un repos qui s'achève à cet instant précis est un repos terminé, pas un repos en
    cours. Faire attendre une minute de plus une source qui a atteint son terme ne
    protège rien de plus : ça retarde seulement la détection de son retour, et un
    `>` au lieu d'un `>=` dans is_disabled produirait ce délai injustifié sans qu'aucun
    test à grande marge (+1 h / -1 min) ne le révèle.
    """
    sante = ProviderHealth(provider="transat", disabled_until=MAINTENANT)

    assert is_disabled(sante, MAINTENANT) is False
