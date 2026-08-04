import pytest

from scrappervol.config import Settings
from scrappervol.providers.base import ProviderError
from scrappervol.providers.playwright_base import debug_path, fetch_html


def test_debug_path_cree_le_dossier_et_nomme_par_source(tmp_path):
    reglages = Settings(data_dir=tmp_path)

    chemin = debug_path(reglages, "transat")

    assert chemin.parent.is_dir()
    assert chemin.name == "transat.html"


def test_debug_path_est_reutilisable_sans_erreur(tmp_path):
    """Appelé à chaque passage : la création du dossier ne doit pas lever la deuxième fois."""
    reglages = Settings(data_dir=tmp_path)

    assert debug_path(reglages, "transat") == debug_path(reglages, "transat")


def test_playwright_absent_devient_une_provider_error(tmp_path, monkeypatch):
    """Le runner de la tâche 9 n'attrape que ce qu'il sait nommer ; une ImportError nue
    remonterait en `Exception` générique et brouillerait le message de santé."""
    import builtins

    vrai_import = builtins.__import__

    def refuse(nom, *args, **kwargs):
        if nom.startswith("playwright"):
            raise ImportError("pas de playwright ici")
        return vrai_import(nom, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)

    with pytest.raises(ProviderError, match="Playwright indisponible"):
        fetch_html("https://exemple.test", Settings(data_dir=tmp_path), "transat")
