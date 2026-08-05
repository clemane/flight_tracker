from __future__ import annotations

import threading
from datetime import date

import pytest

from scrappervol.core.types import FlightOffer, SearchQuery
from scrappervol.live.search import SearchRegistry

REQUETE = SearchQuery(
    origin="YUL",
    destination="CUN",
    depart_date=date(2026, 11, 3),
    return_date=date(2026, 11, 10),
)


def _offre(prix: int, provider: str = "source") -> FlightOffer:
    return FlightOffer(
        provider=provider,
        origin="YUL",
        destination="CUN",
        depart_date=date(2026, 11, 3),
        return_date=date(2026, 11, 10),
        price_cad=prix,
        price_original=float(prix),
        currency_original="CAD",
        airline="Air Transat",
        stops=0,
        duration_minutes=425,
        deep_link="https://example.invalid/vol",
    )


class SourceImmediate:
    def __init__(self, nom: str = "rapide", prix: int = 500) -> None:
        self.name = nom
        self._prix = prix

    def search(self, query: SearchQuery) -> list[FlightOffer]:
        return [_offre(self._prix, self.name)]


class SourceRetenue:
    """Source qui ne rend la main qu'une fois relâchée : rend le déroulé déterministe."""

    def __init__(self, nom: str = "lente", prix: int = 900) -> None:
        self.name = nom
        self._prix = prix
        self.relacher = threading.Event()
        self.demarree = threading.Event()

    def search(self, query: SearchQuery) -> list[FlightOffer]:
        self.demarree.set()
        self.relacher.wait(timeout=5)
        return [_offre(self._prix, self.name)]


class SourceCassee:
    name = "cassee"

    def search(self, query: SearchQuery) -> list[FlightOffer]:
        raise RuntimeError("sélecteur introuvable")


@pytest.fixture
def registre():
    r = SearchRegistry(max_workers=4)
    yield r
    r.arreter()


def _attendre(predicat, timeout: float = 5.0) -> bool:
    fin = threading.Event()
    for _ in range(int(timeout / 0.02)):
        if predicat():
            return True
        fin.wait(0.02)
    return predicat()


def test_une_source_rapide_est_lisible_avant_quune_lente_ait_fini(registre):
    """Le cœur du choix « résultats progressifs » : sans cela, une source à 35 s dicterait
    l'attente de toutes les autres."""
    lente = SourceRetenue()
    run = registre.demarrer(REQUETE, [SourceImmediate(), lente])

    assert _attendre(lambda: len(run.offres) == 1)
    assert not run.terminee
    assert run.offres[0].provider == "rapide"

    lente.relacher.set()
    assert _attendre(lambda: run.terminee)
    assert len(run.offres) == 2


def test_une_source_en_erreur_nempeche_pas_les_autres(registre):
    run = registre.demarrer(REQUETE, [SourceImmediate(), SourceCassee()])

    assert _attendre(lambda: run.terminee)
    assert len(run.offres) == 1
    assert [etat.nom for etat in run.erreurs] == ["cassee"]
    assert "RuntimeError" in run.source("cassee").erreur


def test_les_offres_sont_triees_du_moins_cher_au_plus_cher(registre):
    run = registre.demarrer(
        REQUETE,
        [
            SourceImmediate("chere", 900),
            SourceImmediate("moyenne", 700),
            SourceImmediate("bon", 400),
        ],
    )

    assert _attendre(lambda: run.terminee)
    assert [offre.price_cad for offre in run.offres] == [400, 700, 900]


def test_une_recherche_est_terminee_quand_toutes_ses_sources_le_sont(registre):
    """`terminee` pilote l'arrêt du rafraîchissement : s'il passait à vrai trop tôt, la page se
    figerait sur des résultats partiels sans que rien ne le signale."""
    lente = SourceRetenue()
    run = registre.demarrer(REQUETE, [SourceImmediate(), lente, SourceCassee()])

    assert lente.demarree.wait(timeout=5)
    assert not run.terminee

    lente.relacher.set()
    assert _attendre(lambda: run.terminee)


def test_une_recherche_est_retrouvable_par_son_identifiant(registre):
    run = registre.demarrer(REQUETE, [SourceImmediate()])

    assert registre.get(run.id) is run
    assert registre.get("inconnu") is None


def test_la_purge_oublie_les_recherches_expirees(registre):
    horloge = [0.0]
    r = SearchRegistry(horloge=lambda: horloge[0], ttl_s=100)
    try:
        run = r.demarrer(REQUETE, [SourceImmediate()])
        assert _attendre(lambda: run.terminee)

        horloge[0] = 50
        assert r.purger() == 0
        assert r.get(run.id) is not None

        horloge[0] = 101
        assert r.purger() == 1
        assert r.get(run.id) is None
    finally:
        r.arreter()


def test_la_purge_epargne_une_recherche_encore_en_cours(registre):
    """Une recherche lente ne doit pas être oubliée pendant qu'elle travaille : la page qui la
    suit recevrait « cette recherche a expiré » alors que le scraper tourne encore."""
    horloge = [0.0]
    lente = SourceRetenue()
    r = SearchRegistry(horloge=lambda: horloge[0], ttl_s=10)
    try:
        run = r.demarrer(REQUETE, [lente])
        assert lente.demarree.wait(timeout=5)

        horloge[0] = 1000
        assert r.purger() == 0
        assert r.get(run.id) is not None
    finally:
        lente.relacher.set()
        r.arreter()


def test_la_duree_de_chaque_source_est_relevee(registre):
    """L'interface affiche ce temps par source : c'est lui qui rend visible qu'une source à
    trente secondes n'est pas en panne, mais lente."""
    lente = SourceRetenue()
    run = registre.demarrer(REQUETE, [SourceImmediate(), lente])

    assert lente.demarree.wait(timeout=5)
    assert run.source("rapide").duree_s is not None
    assert run.source("lente").duree_s is None  # tant qu'elle travaille, rien n'est mesuré

    lente.relacher.set()
    assert _attendre(lambda: run.terminee)
    assert run.source("lente").duree_s >= run.source("rapide").duree_s


def test_une_source_sans_resultat_est_un_succes_vide_et_non_une_erreur(registre):
    """Zéro offre n'est pas un échec pour une recherche manuelle : la liaison peut simplement ne
    rien avoir ces jours-là. Confondre les deux afficherait « indisponible » sur une source
    parfaitement fonctionnelle."""

    class SansVol:
        name = "sans_vol"

        def search(self, query):
            return []

    run = registre.demarrer(REQUETE, [SansVol()])

    assert _attendre(lambda: run.terminee)
    assert run.source("sans_vol").statut == "termine"
    assert run.erreurs == []
