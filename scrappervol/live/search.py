from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Literal

from scrappervol.core.types import FlightOffer, SearchQuery
from scrappervol.providers.base import PriceProvider

logger = logging.getLogger(__name__)

Statut = Literal["attente", "en_cours", "termine", "erreur"]

# Au-delà, une recherche est oubliée. Le registre ne sert qu'à alimenter le rafraîchissement de
# la page pendant qu'elle se déroule ; passé ce délai, plus personne ne la regarde.
TTL_S = 15 * 60


@dataclass(frozen=True, slots=True)
class SourceState:
    """État d'une source dans une recherche. Immuable : les transitions remplacent l'objet."""

    nom: str
    statut: Statut = "attente"
    offres: tuple[FlightOffer, ...] = ()
    erreur: str | None = None
    duree_s: float | None = None

    @property
    def active(self) -> bool:
        return self.statut in ("attente", "en_cours")


class SearchRun:
    """Une recherche en cours, alimentée par un thread par source.

    Les états de source sont immuables et remplacés d'un bloc sous verrou : la page peut donc
    lire l'avancement à tout moment sans jamais tomber sur un état à moitié écrit.
    """

    def __init__(self, id: str, query: SearchQuery, noms: Iterable[str], cree_a: float) -> None:
        self.id = id
        self.query = query
        self.cree_a = cree_a
        self._verrou = threading.Lock()
        self._sources: dict[str, SourceState] = {nom: SourceState(nom=nom) for nom in noms}

    def sources(self) -> list[SourceState]:
        """Les états, dans l'ordre de déclaration des sources."""
        with self._verrou:
            return list(self._sources.values())

    def source(self, nom: str) -> SourceState | None:
        with self._verrou:
            return self._sources.get(nom)

    def maj(self, nom: str, **champs: object) -> None:
        with self._verrou:
            self._sources[nom] = replace(self._sources[nom], **champs)

    @property
    def terminee(self) -> bool:
        return all(not etat.active for etat in self.sources())

    @property
    def offres(self) -> list[FlightOffer]:
        """Toutes les offres trouvées, la moins chère d'abord.

        Les doublons entre sources sont conservés : quand Google Flights et Transat rapportent
        le même vol à un dollar près, l'écart est une information — c'est le recoupement qui
        atteste qu'aucune des deux lectures n'est faussée.
        """
        toutes = [offre for etat in self.sources() for offre in etat.offres]
        return sorted(toutes, key=lambda offre: offre.price_cad)

    @property
    def erreurs(self) -> list[SourceState]:
        return [etat for etat in self.sources() if etat.statut == "erreur"]


class SearchRegistry:
    """Registre des recherches en cours, en mémoire.

    Volontairement en mémoire : une recherche ne survit pas à un redémarrage et n'a pas à le
    faire. Cela suppose un processus unique — ce qui est le cas, `scrappervol.main` lançant un
    uvicorn mono-worker. Avec plusieurs workers, un rafraîchissement pourrait tomber sur le
    processus qui n'héberge pas la recherche, et il faudrait alors sortir cet état du processus.
    """

    def __init__(
        self,
        max_workers: int = 6,
        horloge: Callable[[], float] = time.monotonic,
        ttl_s: float = TTL_S,
    ) -> None:
        self._executeur = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="live")
        self._horloge = horloge
        self._ttl_s = ttl_s
        self._verrou = threading.Lock()
        self._runs: dict[str, SearchRun] = {}

    def get(self, id: str) -> SearchRun | None:
        with self._verrou:
            return self._runs.get(id)

    def purger(self) -> int:
        """Oublie les recherches expirées. Retourne le nombre de recherches retirées."""
        limite = self._horloge() - self._ttl_s
        with self._verrou:
            perimes = [
                id
                for id, run in self._runs.items()
                if run.cree_a < limite and run.terminee  # une recherche encore active survit
            ]
            for id in perimes:
                del self._runs[id]
        return len(perimes)

    def demarrer(self, query: SearchQuery, providers: Sequence[PriceProvider]) -> SearchRun:
        """Lance une recherche et rend la main immédiatement.

        Une tâche est soumise par source : les sources rapides s'affichent sans attendre les
        lentes. Air Transat demande un vrai navigateur et prend une trentaine de secondes là où
        Google Flights répond en deux ; les séquencer rendrait la page inutilisable.
        """
        self.purger()
        run = SearchRun(
            id=uuid.uuid4().hex[:12],
            query=query,
            noms=[source.name for source in providers],
            cree_a=self._horloge(),
        )
        with self._verrou:
            self._runs[run.id] = run

        for source in providers:
            self._executeur.submit(self._executer, run, source)
        return run

    def _executer(self, run: SearchRun, source: PriceProvider) -> None:
        """Interroge une source et consigne son résultat. Ne laisse jamais échapper d'exception.

        Rien n'est écrit en base, pas même l'état de santé de la source : une recherche manuelle
        ne doit ni masquer une panne du relevé automatique en le « réparant », ni en déclencher
        une en ouvrant le disjoncteur. Elle n'a pas non plus à le consulter — une source mise au
        repos par l'ordonnanceur reste interrogeable à la main, puisque c'est l'utilisateur qui
        la demande.
        """
        debut = self._horloge()
        run.maj(source.name, statut="en_cours")
        try:
            offres = source.search(run.query)
        except Exception as erreur:  # noqa: BLE001 — l'isolation est le but de cette fonction
            logger.warning("recherche %s : échec de %s : %s", run.id, source.name, erreur)
            run.maj(
                source.name,
                statut="erreur",
                erreur=f"{type(erreur).__name__}: {erreur}",
                duree_s=self._horloge() - debut,
            )
            return
        run.maj(
            source.name,
            statut="termine",
            offres=tuple(offres),
            duree_s=self._horloge() - debut,
        )

    def arreter(self, wait: bool = False) -> None:
        self._executeur.shutdown(wait=wait, cancel_futures=True)
