import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from scrappervol.core.types import FlightOffer, SearchQuery

# Les tables ne s'inscrivent dans `SQLModel.metadata` qu'à l'import de leur module. Sans cet
# import ici, `create_all` ne crée rien tant qu'aucun fichier de test n'a lui-même importé les
# modèles — un fichier qui n'en a pas besoin échoue alors sur « no such table », selon l'ordre
# de collecte.
from scrappervol.storage import models  # noqa: F401


@pytest.fixture
def engine():
    moteur = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(moteur)
    return moteur


@pytest.fixture
def session(engine):
    with Session(engine) as session:
        yield session


class FausseSource:
    """Source contrôlable : retourne des offres, ou lève, ou reste muette."""

    def __init__(self, name="fausse", offres=None, exception=None, muette=False):
        self.name = name
        self._offres = offres if offres is not None else []
        self._exception = exception
        self._muette = muette
        self.appels: list[SearchQuery] = []

    def search(self, query: SearchQuery) -> list[FlightOffer]:
        self.appels.append(query)
        if self._exception is not None:
            raise self._exception
        if self._muette:
            return []
        return [
            FlightOffer(
                provider=self.name,
                origin=query.origin,
                destination=query.destination,
                depart_date=query.depart_date,
                return_date=query.return_date,
                price_cad=offre_prix,
                price_original=float(offre_prix),
                currency_original="CAD",
                airline=compagnie,
                stops=0,
                duration_minutes=420,
                deep_link="https://example.com",
                raw={},
            )
            for offre_prix, compagnie in self._offres
        ]


@pytest.fixture
def fausse_source():
    return FausseSource


@pytest.fixture
def sans_pause():
    """Remplace la pause entre requêtes ; les tests ne doivent jamais dormir."""
    appels: list[float] = []
    return appels.append, appels


class FauxMailer:
    """Collecte les envois au lieu de les transmettre à un vrai serveur SMTP."""

    def __init__(self) -> None:
        self.envois: list[tuple[str, str]] = []

    def send(self, mail, to: str) -> None:
        self.envois.append((mail.subject, to))


@pytest.fixture
def faux_mailer():
    return FauxMailer()
