from pathlib import Path

from scrappervol.config import Settings


def test_settings_lit_les_variables_denvironnement(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///tmp/test.db")
    monkeypatch.setenv("ALERT_TO", "moi@example.com")
    monkeypatch.setenv("EXCEPTION_THRESHOLD", "0.35")

    settings = Settings()

    assert settings.database_url == "sqlite:///tmp/test.db"
    assert settings.alert_to == "moi@example.com"
    assert settings.exception_threshold == 0.35


def test_settings_expose_des_defauts_utilisables():
    settings = Settings()

    assert settings.timezone == "America/Toronto"
    assert settings.credibility_floor_cad == 50
    assert settings.min_history_days == 14
    assert settings.digest_hour == 18
    assert isinstance(settings.data_dir, Path)


def test_enabled_providers_est_une_liste(monkeypatch):
    monkeypatch.setenv("ENABLED_PROVIDERS", "google_flights,transat")

    settings = Settings()

    assert settings.enabled_providers == ["google_flights", "transat"]


def test_transat_est_active_par_defaut(monkeypatch):
    """Depuis la tâche 21, Air Transat lit le total aller-retour sur la page récapitulative
    `/summary` (au tarif le moins cher), plus le prix d'aller seul de l'étape `/departure` qui
    avait justifié son retrait d'`ENABLED_PROVIDERS` à la tâche 11 : ce test verrouille la
    réactivation, pour qu'un retrait distrait de la liste ne passe pas inaperçu.
    """
    monkeypatch.delenv("ENABLED_PROVIDERS", raising=False)

    settings = Settings()

    assert "transat" in settings.enabled_providers
    assert "google_flights" in settings.enabled_providers
