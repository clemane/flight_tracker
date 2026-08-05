# ScrapperVol — Plan d'implémentation

> **Pour les agents :** SOUS-COMPÉTENCE REQUISE — utiliser `superpowers:subagent-driven-development`
> (recommandé) ou `superpowers:executing-plans` pour exécuter ce plan tâche par tâche.
> Les étapes utilisent la syntaxe à cases à cocher (`- [ ]`) pour le suivi.

**Objectif :** construire un système local qui surveille le prix d'une liste de trajets aériens déclarés
sur trois sources, conserve l'historique, détecte les prix aberrants et prévient par courriel.

**Architecture :** un unique conteneur Python. Sept modules à dépendances orientées : `core` (types purs)
← `storage` / `detection` / `providers` ← `scheduler` / `notify` / `web`. Les scrapers sont derrière une
interface unique `PriceProvider`, si bien que la détection, les courriels et l'interface web ne
manipulent que des `FlightOffer` normalisés et ignorent l'existence des sources.

**Pile technique :** Python 3.13, SQLModel + SQLite, FastAPI + Jinja2 + HTMX, APScheduler 3, Playwright,
`fast-flights`, pytest, ruff, Docker Compose.

**Référence :** `docs/superpowers/specs/2026-08-04-scrappervol-design.md`. Le design fait autorité ; en
cas de contradiction avec ce plan, le design gagne et le plan est corrigé.

## Contraintes globales

Ces règles s'appliquent implicitement à **toutes** les tâches.

- **Environnement de référence : le conteneur.** Tous les tests et commandes passent par `./dev`.
  Aucune tâche n'est validée par un `pytest` exécuté sur l'hôte. Raison : la machine ne dispose que de
  Python 3.10 et 3.14, ni l'une ni l'autre n'étant la version cible ; une divergence de version entre
  développement et exécution est précisément le genre de dette qui se paie au pire moment.
- **Python 3.13** dans l'image. Ne pas viser 3.14 : Playwright et SQLModel n'y sont pas encore éprouvés.
- **Devise :** tous les prix stockés et affichés sont en dollars canadiens, en **entiers**. Les champs
  `price_original` / `currency_original` conservent la valeur servie par la source.
- **Fuseau :** `America/Toronto` pour tout ce qui est visible par l'utilisateur (digest, tableau de
  bord, colonne `day` de `daily_low`). Les horodatages stockés sont en UTC, avec `tzinfo`.
- **Aucun horodatage naïf.** Toute `datetime` porte un fuseau. Les fonctions ne lisent jamais l'heure
  courante elles-mêmes : `now` est un paramètre. C'est ce qui rend la détection et le disjoncteur
  testables sans attendre.
- **Le module `detection` est pur.** Aucune entrée-sortie, aucun accès base, aucune horloge.
- **Style :** `ruff` avec la configuration de la tâche 1. Annotations de type sur toute fonction
  publique. Pas de commentaire qui paraphrase le code.
- **Commits :** un commit par tâche minimum, message en `type: description` (Conventional Commits),
  en français.
- **Seuils par défaut** (surchargeables par `.env`) : plancher de crédibilité **50 CAD**, historique
  minimal **14 jours**, seuil d'aberration **0,40**, seuil de trouvaille **0,15**, rétention des
  observations **90 jours**, fenêtre de détection **90 jours**.

---

## Décisions comblant les zones grises du design

Le design est complet sur les intentions mais laisse trois points ouverts que l'implémentation ne peut
pas éluder. Les décisions suivantes sont prises ici et signalées comme telles.

**1. Rotation des requêtes.** Le design vise « environ deux requêtes par trajet et par passage », mais
un trajet `flexible` sur 12 mois avec deux origines et deux destinations demanderait 24 requêtes par
passage. Le planificateur reçoit donc un entier `rotation` et un plafond `max_queries_per_route`
(défaut 6) : à chaque passage il ne produit qu'une tranche du plan complet, la tranche avançant d'un
passage à l'autre. La couverture est donc étalée dans le temps plutôt que sacrifiée. Le `rotation`
est dérivé du nombre d'heures écoulées depuis l'époque Unix, ce qui le rend déterministe et testable.

**2. Rôle du MAD.** Le §6 du design impose médiane et écart absolu médian, mais n'énonce comme condition
chiffrée qu'un écart relatif de 40 % à la médiane. Le MAD est donc utilisé comme **seconde condition**,
sous la forme du score z modifié d'Iglewicz-Hoaglin : `z = 0.6745 × (prix − médiane) / MAD`, avec alerte
si `z ≤ −3.5`. Les deux conditions doivent être vraies. Effet concret : sur une série très volatile, un
prix à −40 % n'a rien d'exceptionnel et ne déclenche rien ; sur une série stable, le MAD est petit et le
score z sanctionne franchement. Cas limite : si `MAD = 0` (série parfaitement plate), la condition MAD
est ignorée et seul le seuil relatif s'applique.

**3. Portée du `flex_days` de la politique `fixed`.** Il est traduit en fenêtre calendaire
(`calendar_window`) transmise aux providers. Google Flights l'exploite via sa grille de prix ; Transat
et Air Canada, qui n'ont pas d'équivalent bon marché, l'ignorent et interrogent la date centrale. Cette
asymétrie est assumée : elle est portée par chaque provider, pas par le planificateur.

---

## Structure des fichiers

```
scrappervol/
  __init__.py
  config.py                  Réglages issus de l'environnement (source unique)
  core/
    types.py                 SearchQuery, FlightOffer, RoutePolicy, énumérations, offer_hash
    query_planner.py         RoutePolicy + date → liste de SearchQuery (pur)
  storage/
    models.py                Tables SQLModel
    db.py                    Moteur, création du schéma, session
    repo.py                  Toutes les requêtes ; seul module qui écrit en base
  detection/
    stats.py                 médiane, MAD, score z modifié (pur)
    rules.py                 aberration, trouvaille, écart relatif (pur)
  providers/
    base.py                  Protocole PriceProvider, ProviderError
    health.py                Disjoncteur : calcul du repos (pur)
    runner.py                Exécution isolée d'un provider sur les trajets actifs
    google_flights.py        Source principale
    transat.py               Source charter / forfait
    air_canada.py            Source de confirmation (abandonnable)
  notify/
    render.py                Construction et rendu des courriels
    mailer.py                Envoi SMTP
    templates/               Gabarits Jinja HTML et texte
  scheduler/
    jobs.py                  Passage de scan, digest, purge
    app.py                   Câblage APScheduler
  web/
    app.py                   Application FastAPI
    routes.py                Tableau de bord, trajets, santé
    templates/               Gabarits Jinja + HTMX
  main.py                    Point d'entrée : web + ordonnanceur
tests/
  core/ storage/ detection/ providers/ notify/ scheduler/ web/
  fixtures/                  Réponses HTML réelles, pour le parsing hors ligne
  conftest.py                Fixtures partagées (base en mémoire, faux provider, faux mailer)
Dockerfile
docker-compose.yml
dev                          Script d'entrée du développement (exécutable)
requirements.txt
requirements.lock.txt
.env.example
README.md
```

---

## Tâche 1 : socle exécutable

Rend le dépôt capable de faire tourner un test. Tout le reste en dépend, et rien ne peut être vérifié
avant.

**Fichiers :**
- Créer : `Dockerfile`, `docker-compose.yml`, `dev`, `requirements.txt`, `.env.example`
- Créer : `scrappervol/__init__.py`, `scrappervol/config.py`
- Créer : `pyproject.toml` (configuration ruff et pytest uniquement)
- Test : `tests/test_config.py`

**Interfaces :**
- Consomme : rien.
- Produit : `scrappervol.config.Settings` (modèle pydantic-settings) et `get_settings() -> Settings`,
  mis en cache. Champs consommés par les tâches suivantes :
  `database_url: str`, `data_dir: Path`, `timezone: str`, `smtp_host: str`, `smtp_port: int`,
  `smtp_user: str`, `smtp_password: str`, `smtp_from: str`, `alert_to: str`,
  `interval_google_hours: int`, `interval_transat_hours: int`, `interval_air_canada_hours: int`,
  `digest_hour: int`, `exception_threshold: float`, `find_threshold: float`,
  `credibility_floor_cad: int`, `min_history_days: int`, `history_window_days: int`,
  `retention_days: int`, `max_queries_per_route: int`, `request_pause_min_s: int`,
  `request_pause_max_s: int`, `enabled_providers: list[str]`.

- [ ] **Étape 1 : écrire `requirements.txt`**

```
fastapi>=0.115
uvicorn[standard]>=0.32
jinja2>=3.1
python-multipart>=0.0.12
sqlmodel>=0.0.22
pydantic-settings>=2.6
apscheduler>=3.10,<4
playwright>=1.49
fast-flights>=2.2
beautifulsoup4>=4.12
httpx>=0.27
pytest>=8.3
pytest-asyncio>=0.24
ruff>=0.8
```

Les versions sont des planchers, pas des épingles : la tâche fige ensuite l'état réel dans
`requirements.lock.txt`. Ne jamais inventer un numéro de version précis sans l'avoir observé.

- [ ] **Étape 2 : écrire le `Dockerfile`**

```dockerfile
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY . .

CMD ["python", "-m", "scrappervol.main"]
```

- [ ] **Étape 3 : écrire `docker-compose.yml`**

```yaml
services:
  app:
    build: .
    env_file: .env
    environment:
      PYTHONPATH: /app
    volumes:
      - ./data:/app/data
      - ./scrappervol:/app/scrappervol
      - ./tests:/app/tests
    ports:
      - "127.0.0.1:8080:8080"
    restart: unless-stopped
```

Le port n'est publié que sur la boucle locale, conformément au §9 du design. Le code est monté en
volume pour qu'une correction ne demande pas de reconstruire l'image.

- [ ] **Étape 4 : écrire le script `dev` et le rendre exécutable**

```bash
#!/usr/bin/env bash
set -euo pipefail

cmd="${1:-}"
shift || true

case "$cmd" in
  build) docker compose build ;;
  test)  docker compose run --rm app pytest "$@" ;;
  lint)  docker compose run --rm app ruff check scrappervol tests "$@" ;;
  fmt)   docker compose run --rm app ruff format scrappervol tests ;;
  shell) docker compose run --rm app bash ;;
  lock)  docker compose run --rm app pip freeze > requirements.lock.txt ;;
  up)    docker compose up -d ;;
  logs)  docker compose logs -f app ;;
  *)     echo "usage: ./dev {build|test|lint|fmt|shell|lock|up|logs}" >&2; exit 1 ;;
esac
```

```bash
chmod +x dev
```

- [ ] **Étape 5 : écrire `pyproject.toml`**

```toml
[tool.ruff]
line-length = 100
target-version = "py313"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "N"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "live: touche un site tiers ; exclu par défaut",
]
addopts = "-m 'not live'"
```

Le marqueur `live` est exclu par défaut dès maintenant, conformément au §11.3 du design : un test
dépendant d'un site tiers casse de lui-même et finit ignoré, ce qui est pire que son absence.

- [ ] **Étape 6 : écrire `.env.example`**

```dotenv
DATABASE_URL=sqlite:////app/data/scrappervol.db
DATA_DIR=/app/data
TIMEZONE=America/Toronto

SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=scrappervol@example.com
ALERT_TO=moi@example.com

INTERVAL_GOOGLE_HOURS=4
INTERVAL_TRANSAT_HOURS=6
INTERVAL_AIR_CANADA_HOURS=8
DIGEST_HOUR=18

EXCEPTION_THRESHOLD=0.40
FIND_THRESHOLD=0.15
CREDIBILITY_FLOOR_CAD=50
MIN_HISTORY_DAYS=14
HISTORY_WINDOW_DAYS=90
RETENTION_DAYS=90
MAX_QUERIES_PER_ROUTE=6
REQUEST_PAUSE_MIN_S=5
REQUEST_PAUSE_MAX_S=20

ENABLED_PROVIDERS=google_flights,transat,air_canada
```

- [ ] **Étape 7 : préparer l'environnement local**

```bash
cp .env.example .env
mkdir -p data scrappervol/core scrappervol/storage scrappervol/detection \
         scrappervol/providers scrappervol/notify scrappervol/scheduler scrappervol/web \
         tests/core tests/storage tests/detection tests/providers tests/notify \
         tests/scheduler tests/web tests/fixtures
find scrappervol tests -type d -exec touch {}/__init__.py \;
touch scrappervol/__init__.py
```

- [ ] **Étape 8 : écrire le test qui échoue**

`tests/test_config.py` :

```python
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
```

- [ ] **Étape 9 : construire l'image et vérifier que le test échoue**

```bash
./dev build
./dev test tests/test_config.py -v
```

Attendu : `ModuleNotFoundError: No module named 'scrappervol.config'`.

- [ ] **Étape 10 : écrire `scrappervol/config.py`**

```python
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    database_url: str = "sqlite:////app/data/scrappervol.db"
    data_dir: Path = Path("/app/data")
    timezone: str = "America/Toronto"

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "scrappervol@localhost"
    alert_to: str = ""

    interval_google_hours: int = 4
    interval_transat_hours: int = 6
    interval_air_canada_hours: int = 8
    digest_hour: int = 18

    exception_threshold: float = 0.40
    find_threshold: float = 0.15
    credibility_floor_cad: int = 50
    min_history_days: int = 14
    history_window_days: int = 90
    retention_days: int = 90
    max_queries_per_route: int = 6
    request_pause_min_s: int = 5
    request_pause_max_s: int = 20

    enabled_providers: list[str] = ["google_flights", "transat", "air_canada"]

    @field_validator("enabled_providers", mode="before")
    @classmethod
    def _split_providers(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

`env_file=None` est délibéré : le `.env` est injecté par Docker Compose, pas relu par l'application.
Deux chemins de lecture pour la même variable finissent toujours par diverger.

- [ ] **Étape 11 : vérifier que les tests passent**

```bash
./dev test tests/test_config.py -v
./dev lint
```

Attendu : 3 tests passés, ruff sans erreur.

- [ ] **Étape 12 : figer les versions et committer**

```bash
./dev lock
git add -A
git commit -m "feat: socle conteneurisé, configuration et outillage de test"
```

---

## Tâche 2 : types du domaine

Le vocabulaire partagé par tous les modules. Aucune dépendance interne, conformément au §4 du design.

**Fichiers :**
- Créer : `scrappervol/core/types.py`
- Test : `tests/core/test_types.py`

**Interfaces :**
- Consomme : rien.
- Produit :
  - `TripType` (`ROUND_TRIP = "round_trip"`, `ONE_WAY = "one_way"`)
  - `DatePolicyKind` (`FIXED = "fixed"`, `WINDOW = "window"`, `FLEXIBLE = "flexible"`)
  - `SearchQuery(origin, destination, depart_date, return_date, passengers, max_stops, trip_type,
    calendar_window)` — gelé
  - `FlightOffer(provider, origin, destination, depart_date, return_date, price_cad, price_original,
    currency_original, airline, stops, duration_minutes, deep_link, raw)` — gelé, avec la propriété
    `offer_hash: str`
  - `RoutePolicy(origins, destinations, date_policy, policy_params, trip_type, passengers, max_stops)`
  - `compute_offer_hash(provider, origin, destination, depart_date, return_date, airline, stops) -> str`

- [ ] **Étape 1 : écrire le test qui échoue**

`tests/core/test_types.py` :

```python
from datetime import date

import pytest

from scrappervol.core.types import (
    DatePolicyKind,
    FlightOffer,
    RoutePolicy,
    SearchQuery,
    TripType,
    compute_offer_hash,
)


def _offre(**surcharges) -> FlightOffer:
    base = {
        "provider": "google_flights",
        "origin": "YUL",
        "destination": "CDG",
        "depart_date": date(2027, 3, 12),
        "return_date": date(2027, 3, 22),
        "price_cad": 612,
        "price_original": 612.0,
        "currency_original": "CAD",
        "airline": "Air Transat",
        "stops": 0,
        "duration_minutes": 425,
        "deep_link": "https://example.com/offre",
        "raw": {},
    }
    return FlightOffer(**{**base, **surcharges})


def test_offer_hash_est_stable_entre_deux_instances_identiques():
    assert _offre().offer_hash == _offre().offer_hash


def test_offer_hash_distingue_deux_offres_differentes():
    assert _offre().offer_hash != _offre(airline="Air Canada").offer_hash
    assert _offre().offer_hash != _offre(stops=1).offer_hash
    assert _offre().offer_hash != _offre(depart_date=date(2027, 3, 13)).offer_hash


def test_offer_hash_ignore_le_prix():
    """Le condensat suit une offre dans le temps ; c'est justement son prix qui bouge."""
    assert _offre().offer_hash == _offre(price_cad=399).offer_hash


def test_offer_hash_gere_un_aller_simple():
    aller_simple = _offre(return_date=None)
    assert aller_simple.offer_hash != _offre().offer_hash


def test_compute_offer_hash_est_la_meme_fonction_que_la_propriete():
    offre = _offre()
    attendu = compute_offer_hash(
        provider=offre.provider,
        origin=offre.origin,
        destination=offre.destination,
        depart_date=offre.depart_date,
        return_date=offre.return_date,
        airline=offre.airline,
        stops=offre.stops,
    )
    assert offre.offer_hash == attendu


def test_les_offres_sont_immuables():
    with pytest.raises(AttributeError):
        _offre().price_cad = 1


def test_search_query_est_utilisable_comme_cle():
    q = SearchQuery(
        origin="YUL",
        destination="CDG",
        depart_date=date(2027, 3, 12),
        return_date=date(2027, 3, 22),
    )
    assert {q, q} == {q}


def test_route_policy_porte_les_listes_et_les_parametres():
    politique = RoutePolicy(
        origins=["YUL", "YQB"],
        destinations=["CDG", "ORY"],
        date_policy=DatePolicyKind.FIXED,
        policy_params={"depart": "2027-03-12", "retour": "2027-03-22", "flex_days": 3},
        trip_type=TripType.ROUND_TRIP,
        passengers=1,
        max_stops=None,
    )
    assert politique.origins == ["YUL", "YQB"]
    assert politique.date_policy is DatePolicyKind.FIXED
```

- [ ] **Étape 2 : lancer le test et vérifier l'échec**

```bash
./dev test tests/core/test_types.py -v
```

Attendu : `ModuleNotFoundError: No module named 'scrappervol.core.types'`.

- [ ] **Étape 3 : écrire l'implémentation**

`scrappervol/core/types.py` :

```python
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum


class TripType(StrEnum):
    ROUND_TRIP = "round_trip"
    ONE_WAY = "one_way"


class DatePolicyKind(StrEnum):
    FIXED = "fixed"
    WINDOW = "window"
    FLEXIBLE = "flexible"


def compute_offer_hash(
    *,
    provider: str,
    origin: str,
    destination: str,
    depart_date: date,
    return_date: date | None,
    airline: str,
    stops: int,
) -> str:
    graine = "|".join(
        [
            provider,
            origin,
            destination,
            depart_date.isoformat(),
            return_date.isoformat() if return_date else "",
            airline,
            str(stops),
        ]
    )
    return hashlib.sha256(graine.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class SearchQuery:
    origin: str
    destination: str
    depart_date: date
    return_date: date | None = None
    passengers: int = 1
    max_stops: int | None = None
    trip_type: TripType = TripType.ROUND_TRIP
    calendar_window: tuple[date, date] | None = None


@dataclass(frozen=True, slots=True)
class FlightOffer:
    provider: str
    origin: str
    destination: str
    depart_date: date
    return_date: date | None
    price_cad: int
    price_original: float
    currency_original: str
    airline: str
    stops: int
    duration_minutes: int | None
    deep_link: str
    raw: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def offer_hash(self) -> str:
        return compute_offer_hash(
            provider=self.provider,
            origin=self.origin,
            destination=self.destination,
            depart_date=self.depart_date,
            return_date=self.return_date,
            airline=self.airline,
            stops=self.stops,
        )


@dataclass(frozen=True, slots=True)
class RoutePolicy:
    origins: list[str]
    destinations: list[str]
    date_policy: DatePolicyKind
    policy_params: dict
    trip_type: TripType = TripType.ROUND_TRIP
    passengers: int = 1
    max_stops: int | None = None
```

`raw` est exclu de la comparaison et du hachage : c'est une charge de débogage, et deux offres
identiques ne doivent pas différer parce qu'une source a ajouté un champ dans sa réponse brute.

- [ ] **Étape 4 : vérifier que les tests passent**

```bash
./dev test tests/core/test_types.py -v
./dev lint
```

Attendu : 8 tests passés.

- [ ] **Étape 5 : committer**

```bash
git add scrappervol/core/types.py tests/core/test_types.py
git commit -m "feat: types normalisés du domaine et condensat d'offre"
```

---

## Tâche 3 : planificateur de requêtes

Traduit une intention de voyage en requêtes concrètes. C'est la seule pièce qui comprend les trois
politiques de dates, et elle est entièrement pure — donc entièrement testable.

**Fichiers :**
- Créer : `scrappervol/core/query_planner.py`
- Test : `tests/core/test_query_planner.py`

**Interfaces :**
- Consomme : `RoutePolicy`, `SearchQuery`, `DatePolicyKind`, `TripType` (tâche 2).
- Produit :
  - `plan_queries(policy: RoutePolicy, today: date, rotation: int = 0, max_queries: int = 6)
    -> list[SearchQuery]`
  - `rotation_for(now: datetime) -> int`

- [ ] **Étape 1 : écrire le test qui échoue**

`tests/core/test_query_planner.py` :

```python
from datetime import UTC, date, datetime

from scrappervol.core.query_planner import plan_queries, rotation_for
from scrappervol.core.types import DatePolicyKind, RoutePolicy, TripType

AUJOURDHUI = date(2026, 8, 4)


def _politique(**surcharges) -> RoutePolicy:
    base = {
        "origins": ["YUL"],
        "destinations": ["CDG"],
        "date_policy": DatePolicyKind.FIXED,
        "policy_params": {"depart": "2027-03-12", "retour": "2027-03-22", "flex_days": 3},
        "trip_type": TripType.ROUND_TRIP,
        "passengers": 1,
        "max_stops": None,
    }
    return RoutePolicy(**{**base, **surcharges})


def test_fixed_produit_une_requete_avec_les_dates_exactes():
    requetes = plan_queries(_politique(), today=AUJOURDHUI)

    assert len(requetes) == 1
    q = requetes[0]
    assert q.origin == "YUL"
    assert q.destination == "CDG"
    assert q.depart_date == date(2027, 3, 12)
    assert q.return_date == date(2027, 3, 22)


def test_fixed_traduit_flex_days_en_fenetre_calendaire():
    q = plan_queries(_politique(), today=AUJOURDHUI)[0]

    assert q.calendar_window == (date(2027, 3, 9), date(2027, 3, 15))


def test_fixed_sans_flex_days_na_pas_de_fenetre():
    politique = _politique(policy_params={"depart": "2027-03-12", "retour": "2027-03-22"})

    assert plan_queries(politique, today=AUJOURDHUI)[0].calendar_window is None


def test_aller_simple_na_pas_de_date_de_retour():
    politique = _politique(
        trip_type=TripType.ONE_WAY,
        policy_params={"depart": "2027-03-12"},
    )

    assert plan_queries(politique, today=AUJOURDHUI)[0].return_date is None


def test_produit_cartesien_des_origines_et_destinations():
    politique = _politique(origins=["YUL", "YQB"], destinations=["CDG", "ORY"])

    requetes = plan_queries(politique, today=AUJOURDHUI, max_queries=10)

    couples = {(q.origin, q.destination) for q in requetes}
    assert couples == {("YUL", "CDG"), ("YUL", "ORY"), ("YQB", "CDG"), ("YQB", "ORY")}


def test_window_produit_une_requete_par_mois_declare():
    politique = _politique(
        date_policy=DatePolicyKind.WINDOW,
        policy_params={"mois": ["2027-03", "2027-04"], "sejour_min": 8, "sejour_max": 12},
    )

    requetes = plan_queries(politique, today=AUJOURDHUI, max_queries=10)

    assert len(requetes) == 2
    assert {q.depart_date.month for q in requetes} == {3, 4}


def test_window_centre_le_depart_et_deduit_le_retour_du_sejour_moyen():
    politique = _politique(
        date_policy=DatePolicyKind.WINDOW,
        policy_params={"mois": ["2027-03"], "sejour_min": 8, "sejour_max": 12},
    )

    q = plan_queries(politique, today=AUJOURDHUI)[0]

    assert q.depart_date == date(2027, 3, 15)
    assert q.return_date == date(2027, 3, 25)
    assert q.calendar_window == (date(2027, 3, 1), date(2027, 3, 31))


def test_flexible_couvre_lhorizon_par_tranches_de_deux_mois():
    politique = _politique(
        date_policy=DatePolicyKind.FLEXIBLE,
        policy_params={"horizon_mois": 6, "sejour_min": 7, "sejour_max": 14},
    )

    mois_couverts = set()
    for rotation in range(3):
        for q in plan_queries(politique, today=AUJOURDHUI, rotation=rotation, max_queries=10):
            mois_couverts.add((q.depart_date.year, q.depart_date.month))

    assert len(mois_couverts) == 6

    # Et chaque rotation n'explore qu'une tranche. Sans cette seconde assertion, la taille
    # de tranche pourrait passer de 2 à 3 sans rien faire rougir : l'horizon finirait de
    # toute façon couvert, seulement en moins de passages. Or ce nombre règle la charge d'un
    # seul passage, et c'est en envoyant trop de requêtes d'un coup qu'on se fait bloquer.
    for rotation in range(3):
        mois_de_la_rotation = {
            (q.depart_date.year, q.depart_date.month)
            for q in plan_queries(politique, today=AUJOURDHUI, rotation=rotation, max_queries=10)
        }
        assert len(mois_de_la_rotation) == 2


def test_flexible_ne_propose_jamais_une_date_passee():
    politique = _politique(
        date_policy=DatePolicyKind.FLEXIBLE,
        policy_params={"horizon_mois": 12, "sejour_min": 7, "sejour_max": 14},
    )

    for rotation in range(6):
        for q in plan_queries(politique, today=AUJOURDHUI, rotation=rotation, max_queries=10):
            assert q.depart_date > AUJOURDHUI


def test_le_plafond_tronque_le_plan():
    politique = _politique(
        origins=["YUL", "YQB"],
        destinations=["CDG", "ORY", "BRU"],
        date_policy=DatePolicyKind.WINDOW,
        policy_params={"mois": ["2027-03", "2027-04"], "sejour_min": 8, "sejour_max": 12},
    )

    requetes = plan_queries(politique, today=AUJOURDHUI, max_queries=4)

    assert len(requetes) == 4


def test_la_rotation_fait_defiler_le_plan_tronque():
    politique = _politique(
        origins=["YUL", "YQB"],
        destinations=["CDG", "ORY", "BRU"],
        date_policy=DatePolicyKind.WINDOW,
        policy_params={"mois": ["2027-03", "2027-04"], "sejour_min": 8, "sejour_max": 12},
    )

    premier = plan_queries(politique, today=AUJOURDHUI, rotation=0, max_queries=4)
    second = plan_queries(politique, today=AUJOURDHUI, rotation=1, max_queries=4)

    assert premier != second


def test_la_rotation_finit_par_couvrir_tout_le_plan():
    politique = _politique(
        origins=["YUL", "YQB"],
        destinations=["CDG", "ORY", "BRU"],
        date_policy=DatePolicyKind.WINDOW,
        policy_params={"mois": ["2027-03", "2027-04"], "sejour_min": 8, "sejour_max": 12},
    )

    vues = set()
    for rotation in range(3):
        vues.update(plan_queries(politique, today=AUJOURDHUI, rotation=rotation, max_queries=4))

    assert len(vues) == 12


def test_flexible_avec_troncature_finit_par_couvrir_tout_lhorizon():
    """Non-régression : la troncature ne doit pas se figer sur la même moitié du plan à
    l'intérieur d'une tranche flexible, même quand `rotation` change mais reste dans la
    même tranche de deux mois.
    """
    politique = _politique(
        origins=["YUL", "YQB"],
        destinations=["CDG", "ORY", "BRU"],
        date_policy=DatePolicyKind.FLEXIBLE,
        policy_params={"horizon_mois": 12, "sejour_min": 7, "sejour_max": 14},
    )

    couverts = set()
    for rotation in range(24):
        for q in plan_queries(politique, today=AUJOURDHUI, rotation=rotation, max_queries=6):
            couverts.add((q.origin, q.destination, (q.depart_date.year, q.depart_date.month)))

    assert len(couverts) == 72


def test_les_contraintes_du_trajet_sont_reportees_sur_chaque_requete():
    politique = _politique(passengers=2, max_stops=1)

    q = plan_queries(politique, today=AUJOURDHUI)[0]

    assert q.passengers == 2
    assert q.max_stops == 1


def test_une_politique_fixed_deja_passee_ne_produit_rien():
    politique = _politique(policy_params={"depart": "2020-01-01", "retour": "2020-01-10"})

    assert plan_queries(politique, today=AUJOURDHUI) == []


def test_rotation_for_avance_avec_le_temps():
    debut = datetime(2026, 8, 4, 0, 0, tzinfo=UTC)
    plus_tard = datetime(2026, 8, 4, 7, 0, tzinfo=UTC)

    assert rotation_for(plus_tard) == rotation_for(debut) + 7


def test_rotation_for_est_stable_dans_la_meme_heure():
    a = datetime(2026, 8, 4, 3, 5, tzinfo=UTC)
    b = datetime(2026, 8, 4, 3, 55, tzinfo=UTC)

    assert rotation_for(a) == rotation_for(b)
```

- [ ] **Étape 2 : lancer le test et vérifier l'échec**

```bash
./dev test tests/core/test_query_planner.py -v
```

Attendu : `ModuleNotFoundError: No module named 'scrappervol.core.query_planner'`.

- [ ] **Étape 3 : écrire l'implémentation**

`scrappervol/core/query_planner.py` :

```python
from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, timedelta
from itertools import product

from scrappervol.core.types import DatePolicyKind, RoutePolicy, SearchQuery, TripType

_EPOQUE = datetime(1970, 1, 1, tzinfo=UTC)
_MOIS_PAR_TRANCHE = 2


def rotation_for(now: datetime) -> int:
    """Compteur horaire déterministe, servant à faire défiler un plan tronqué."""
    return int((now - _EPOQUE).total_seconds() // 3600)


def _fenetre_du_mois(annee: int, mois: int) -> tuple[date, date]:
    dernier = calendar.monthrange(annee, mois)[1]
    return date(annee, mois, 1), date(annee, mois, dernier)


def _decale_mois(reference: date, decalage: int) -> tuple[int, int]:
    total = reference.month - 1 + decalage
    return reference.year + total // 12, total % 12 + 1


def _sejour_moyen(params: dict) -> int:
    return (int(params.get("sejour_min", 7)) + int(params.get("sejour_max", 14))) // 2


def _dates_fixed(params: dict, trip_type: TripType) -> list[tuple[date, date | None, tuple[date, date] | None]]:
    depart = date.fromisoformat(params["depart"])
    retour = (
        date.fromisoformat(params["retour"])
        if trip_type is TripType.ROUND_TRIP and params.get("retour")
        else None
    )
    flex = int(params.get("flex_days", 0))
    fenetre = (depart - timedelta(days=flex), depart + timedelta(days=flex)) if flex else None
    return [(depart, retour, fenetre)]


def _dates_window(params: dict, trip_type: TripType) -> list[tuple[date, date | None, tuple[date, date] | None]]:
    sejour = _sejour_moyen(params)
    resultat = []
    for mois_iso in params.get("mois", []):
        annee, mois = (int(part) for part in mois_iso.split("-"))
        depart = date(annee, mois, 15)
        retour = depart + timedelta(days=sejour) if trip_type is TripType.ROUND_TRIP else None
        resultat.append((depart, retour, _fenetre_du_mois(annee, mois)))
    return resultat


def _nb_tranches(params: dict) -> int:
    horizon = int(params.get("horizon_mois", 12))
    return max(1, -(-horizon // _MOIS_PAR_TRANCHE))


def _dates_flexible(
    params: dict, today: date, trip_type: TripType, rotation: int
) -> list[tuple[date, date | None, tuple[date, date] | None]]:
    horizon = int(params.get("horizon_mois", 12))
    sejour = _sejour_moyen(params)
    tranche = rotation % _nb_tranches(params)

    resultat = []
    for offset in range(_MOIS_PAR_TRANCHE):
        index_mois = tranche * _MOIS_PAR_TRANCHE + offset + 1
        if index_mois > horizon:
            break
        annee, mois = _decale_mois(today, index_mois)
        depart = date(annee, mois, 15)
        retour = depart + timedelta(days=sejour) if trip_type is TripType.ROUND_TRIP else None
        resultat.append((depart, retour, _fenetre_du_mois(annee, mois)))
    return resultat


def plan_queries(
    policy: RoutePolicy,
    today: date,
    rotation: int = 0,
    max_queries: int = 6,
) -> list[SearchQuery]:
    """Développe une intention de voyage en requêtes concrètes.

    Le plan complet est le produit cartésien des origines, des destinations et des créneaux
    de dates. Quand il dépasse `max_queries`, seule une fenêtre est retournée ; elle avance
    d'un passage à l'autre, si bien que la couverture est étalée dans le temps plutôt
    qu'amputée.

    Cette fenêtre avance au rythme des visites du créneau courant (`rotation // nb_tranches`),
    et non des rotations. Pour `FLEXIBLE`, `rotation` sélectionne d'abord une tranche de mois :
    une tranche donnée n'est revue qu'une rotation sur `nb_tranches`. Indexer la fenêtre sur
    `rotation` la ferait bondir de `nb_tranches * max_queries` entre deux visites — un pas qui
    retombe sur lui-même dès que `len(plan)` divise ce produit, et fige alors la fenêtre pour
    toujours. Avec les valeurs par défaut (6 tranches, `max_queries=6`) et 2 origines x 3
    destinations, la fenêtre resterait collée sur la même moitié du plan de chaque tranche :
    comme le produit cartésien fait varier l'origine le plus lentement, une origine sur deux
    ne serait jamais interrogée, pour aucun mois de l'horizon, sans qu'aucun compteur ni
    aucune erreur ne le signale. Pour `FIXED` et `WINDOW`, `nb_tranches` vaut 1 et ce compteur
    de passages coïncide avec `rotation`.
    """
    params = policy.policy_params or {}

    if policy.date_policy is DatePolicyKind.FIXED:
        creneaux = _dates_fixed(params, policy.trip_type)
        nb_tranches = 1
    elif policy.date_policy is DatePolicyKind.WINDOW:
        creneaux = _dates_window(params, policy.trip_type)
        nb_tranches = 1
    else:
        creneaux = _dates_flexible(params, today, policy.trip_type, rotation)
        nb_tranches = _nb_tranches(params)

    creneaux = [c for c in creneaux if c[0] > today]
    if not creneaux:
        return []

    plan = [
        SearchQuery(
            origin=origine,
            destination=destination,
            depart_date=depart,
            return_date=retour,
            passengers=policy.passengers,
            max_stops=policy.max_stops,
            trip_type=policy.trip_type,
            calendar_window=fenetre,
        )
        for origine, destination, (depart, retour, fenetre) in product(
            policy.origins, policy.destinations, creneaux
        )
    ]

    if len(plan) <= max_queries:
        return plan

    passages = rotation // nb_tranches
    debut = (passages * max_queries) % len(plan)
    doublé = plan + plan
    return doublé[debut : debut + max_queries]
```

- [ ] **Étape 4 : vérifier que les tests passent**

```bash
./dev test tests/core/test_query_planner.py -v
./dev lint
```

Attendu : 16 tests passés.

- [ ] **Étape 5 : committer**

```bash
git add scrappervol/core/query_planner.py tests/core/test_query_planner.py
git commit -m "feat: planificateur de requêtes avec rotation et trois politiques de dates"
```

---

## Tâche 4 : modèles et base de données

**Fichiers :**
- Créer : `scrappervol/storage/models.py`, `scrappervol/storage/db.py`
- Créer : `tests/conftest.py`
- Test : `tests/storage/test_models.py`

**Interfaces :**
- Consomme : `RoutePolicy`, `DatePolicyKind`, `TripType` (tâche 2), `FlightOffer` (tâche 2).
- Produit :
  - Tables `Route`, `Observation`, `DailyLow`, `ProviderHealth`, `Alert` ; énumération `AlertKind`
    (`DIGEST = "digest"`, `EXCEPTION = "exception"`).
  - `Route.to_policy() -> RoutePolicy`
  - `Observation.from_offer(route_id, offer, observed_at) -> Observation`
  - `db.create_engine_for(url: str) -> Engine`
  - `db.init_db(engine: Engine) -> None`
  - `db.session_scope(engine: Engine) -> Iterator[Session]` (gestionnaire de contexte, commit à la
    sortie, rollback sur exception)
  - Fixture pytest `session` : base SQLite en mémoire, schéma créé.

- [ ] **Étape 1 : écrire le test qui échoue**

`tests/storage/test_models.py` :

```python
from datetime import UTC, date, datetime

from sqlmodel import select

from scrappervol.core.types import DatePolicyKind, FlightOffer, TripType
from scrappervol.storage.models import Alert, AlertKind, DailyLow, Observation, ProviderHealth, Route

MAINTENANT = datetime(2026, 8, 4, 14, 0, tzinfo=UTC)


def test_un_trajet_se_persiste_avec_ses_listes_json(session):
    trajet = Route(
        label="Paris au printemps",
        origins=["YUL", "YQB"],
        destinations=["CDG", "ORY"],
        date_policy=DatePolicyKind.FIXED,
        policy_params={"depart": "2027-03-12", "retour": "2027-03-22", "flex_days": 3},
    )
    session.add(trajet)
    session.commit()

    relu = session.exec(select(Route)).one()
    assert relu.origins == ["YUL", "YQB"]
    assert relu.policy_params["flex_days"] == 3
    assert relu.active is True
    assert relu.passengers == 1
    assert relu.exception_threshold == 0.40


def test_to_policy_projette_le_trajet_vers_le_type_du_domaine(session):
    trajet = Route(
        label="Paris",
        origins=["YUL"],
        destinations=["CDG"],
        date_policy=DatePolicyKind.WINDOW,
        policy_params={"mois": ["2027-03"], "sejour_min": 8, "sejour_max": 12},
        passengers=2,
        max_stops=1,
    )

    politique = trajet.to_policy()

    assert politique.origins == ["YUL"]
    assert politique.date_policy is DatePolicyKind.WINDOW
    assert politique.passengers == 2
    assert politique.max_stops == 1
    assert politique.trip_type is TripType.ROUND_TRIP


def test_une_observation_se_construit_depuis_une_offre(session):
    offre = FlightOffer(
        provider="google_flights",
        origin="YUL",
        destination="CDG",
        depart_date=date(2027, 3, 12),
        return_date=date(2027, 3, 22),
        price_cad=612,
        price_original=612.0,
        currency_original="CAD",
        airline="Air Transat",
        stops=0,
        duration_minutes=425,
        deep_link="https://example.com/offre",
        raw={"brut": True},
    )

    observation = Observation.from_offer(route_id=1, offer=offre, observed_at=MAINTENANT)
    session.add(observation)
    session.commit()

    relu = session.exec(select(Observation)).one()
    assert relu.price_cad == 612
    assert relu.offer_hash == offre.offer_hash
    assert relu.raw == {"brut": True}
    assert relu.provider == "google_flights"


def test_daily_low_a_une_cle_composee(session):
    session.add(DailyLow(route_id=1, day=date(2026, 8, 4), price_cad=612, observation_id=1,
                         provider="google_flights"))
    session.commit()

    relu = session.exec(select(DailyLow)).one()
    assert relu.route_id == 1
    assert relu.day == date(2026, 8, 4)


def test_provider_health_a_des_defauts_neutres(session):
    session.add(ProviderHealth(provider="google_flights"))
    session.commit()

    relu = session.exec(select(ProviderHealth)).one()
    assert relu.consecutive_failures == 0
    assert relu.last_success_at is None
    assert relu.disabled_until is None
    assert relu.offers_last_run == 0


def test_une_alerte_journalise_son_type_et_sa_charge(session):
    session.add(
        Alert(
            route_id=1,
            observation_id=7,
            kind=AlertKind.EXCEPTION,
            sent_at=MAINTENANT,
            payload={"offer_hash": "abc123"},
        )
    )
    session.commit()

    relu = session.exec(select(Alert)).one()
    assert relu.kind is AlertKind.EXCEPTION
    assert relu.payload["offer_hash"] == "abc123"
```

`tests/conftest.py` :

```python
import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool


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
```

`StaticPool` est indispensable : sans lui, chaque connexion à `sqlite://` ouvre une base en mémoire
distincte et le schéma créé disparaît entre deux appels.

- [ ] **Étape 2 : lancer le test et vérifier l'échec**

```bash
./dev test tests/storage/test_models.py -v
```

Attendu : `ModuleNotFoundError: No module named 'scrappervol.storage.models'`.

- [ ] **Étape 3 : écrire `scrappervol/storage/models.py`**

```python
from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Column, DateTime
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, SQLModel

from scrappervol.core.types import DatePolicyKind, FlightOffer, RoutePolicy, TripType


class UTCDateTime(TypeDecorator):
    """Colonne DATETIME qui préserve le fuseau à travers SQLite.

    SQLite n'a pas de type date/heure natif : SQLAlchemy sérialise un ``datetime`` en
    chaîne en ignorant son décalage, même avec ``DateTime(timezone=True)``. Un
    ``datetime`` timezone-aware ressort donc naïf après un aller-retour en base — une
    violation silencieuse de l'invariant « aucun horodatage naïf », qu'aucune des
    valeurs stockées ni relues ne signale d'elle-même. Ce type impose l'écriture en
    UTC et ré-attache explicitement le fuseau à la lecture.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("un datetime naïf ne peut pas être persisté : fournis un tzinfo")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class AlertKind(StrEnum):
    DIGEST = "digest"
    EXCEPTION = "exception"


class Route(SQLModel, table=True):
    __tablename__ = "route"

    id: int | None = Field(default=None, primary_key=True)
    label: str
    origins: list[str] = Field(sa_column=Column(JSON), default_factory=list)
    destinations: list[str] = Field(sa_column=Column(JSON), default_factory=list)
    date_policy: DatePolicyKind = DatePolicyKind.FLEXIBLE
    policy_params: dict[str, Any] = Field(sa_column=Column(JSON), default_factory=dict)
    trip_type: TripType = TripType.ROUND_TRIP
    passengers: int = 1
    max_stops: int | None = None
    target_price_cad: int | None = None
    exception_threshold: float = 0.40
    active: bool = True
    created_at: datetime | None = Field(default=None, sa_column=Column(UTCDateTime))

    def to_policy(self) -> RoutePolicy:
        return RoutePolicy(
            origins=list(self.origins),
            destinations=list(self.destinations),
            date_policy=DatePolicyKind(self.date_policy),
            policy_params=dict(self.policy_params),
            trip_type=TripType(self.trip_type),
            passengers=self.passengers,
            max_stops=self.max_stops,
        )


class Observation(SQLModel, table=True):
    __tablename__ = "observation"

    id: int | None = Field(default=None, primary_key=True)
    route_id: int = Field(index=True)
    provider: str = Field(index=True)
    observed_at: datetime = Field(sa_column=Column(UTCDateTime, index=True, nullable=False))
    price_cad: int
    currency_original: str
    price_original: float
    departure_date: date
    return_date: date | None = None
    origin: str
    destination: str
    airline: str
    stops: int
    duration_minutes: int | None = None
    deep_link: str
    offer_hash: str = Field(index=True)
    raw: dict[str, Any] = Field(sa_column=Column(JSON), default_factory=dict)

    @classmethod
    def from_offer(cls, route_id: int, offer: FlightOffer, observed_at: datetime) -> Observation:
        return cls(
            route_id=route_id,
            provider=offer.provider,
            observed_at=observed_at,
            price_cad=offer.price_cad,
            currency_original=offer.currency_original,
            price_original=offer.price_original,
            departure_date=offer.depart_date,
            return_date=offer.return_date,
            origin=offer.origin,
            destination=offer.destination,
            airline=offer.airline,
            stops=offer.stops,
            duration_minutes=offer.duration_minutes,
            deep_link=offer.deep_link,
            offer_hash=offer.offer_hash,
            raw=dict(offer.raw),
        )


class DailyLow(SQLModel, table=True):
    __tablename__ = "daily_low"

    route_id: int = Field(primary_key=True)
    day: date = Field(primary_key=True)
    price_cad: int
    observation_id: int | None = None
    provider: str = ""


class ProviderHealth(SQLModel, table=True):
    __tablename__ = "provider_health"

    provider: str = Field(primary_key=True)
    last_success_at: datetime | None = Field(default=None, sa_column=Column(UTCDateTime))
    consecutive_failures: int = 0
    disabled_until: datetime | None = Field(default=None, sa_column=Column(UTCDateTime))
    last_error: str | None = None
    offers_last_run: int = 0


class Alert(SQLModel, table=True):
    __tablename__ = "alert"

    id: int | None = Field(default=None, primary_key=True)
    route_id: int = Field(index=True)
    observation_id: int | None = None
    kind: AlertKind
    sent_at: datetime = Field(sa_column=Column(UTCDateTime, index=True, nullable=False))
    payload: dict[str, Any] = Field(sa_column=Column(JSON), default_factory=dict)
```

- [ ] **Étape 4 : écrire `scrappervol/storage/db.py`**

```python
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

from scrappervol.storage import models  # noqa: F401  (enregistre les tables sur SQLModel.metadata)


def create_engine_for(url: str) -> Engine:
    if url.startswith("sqlite:///"):
        chemin = Path(url.removeprefix("sqlite:///"))
        chemin.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, connect_args={"check_same_thread": False})


def init_db(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

L'import de `models` marqué `noqa: F401` n'est pas un oubli : sans lui, `SQLModel.metadata` est vide au
moment de `create_all` et la base est créée sans aucune table.

- [ ] **Étape 5 : écrire `tests/storage/test_db.py`**

```python
"""Garde-fous sur `session_scope`.

Cette fonction est le dernier rempart entre une erreur au milieu d'un scan et un
historique de prix à moitié écrit. Une transaction avortée qui laisserait ses lignes
derrière elle ne lèverait aucune erreur et ne remplirait aucun journal : elle
fausserait seulement, et pour toujours, la base de comparaison sur laquelle repose la
détection d'aubaines. D'où ces tests, que le brief de la tâche 4 ne demandait pas.
"""

import pytest
from sqlmodel import Session, select

from scrappervol.storage.db import session_scope
from scrappervol.storage.models import Route


def test_session_scope_commite_en_sortie_normale(engine):
    with session_scope(engine) as session:
        session.add(Route(label="YUL-CDG"))

    with Session(engine) as verification:
        trajets = verification.exec(select(Route)).all()
    assert [trajet.label for trajet in trajets] == ["YUL-CDG"]


def test_session_scope_annule_tout_si_une_exception_survient(engine):
    with session_scope(engine) as session:
        session.add(Route(label="déjà en base"))

    with (
        pytest.raises(RuntimeError, match="panne au milieu du scan"),
        session_scope(engine) as session,
    ):
        session.add(Route(label="ne doit pas survivre"))
        session.flush()  # la ligne existe dans la transaction avant l'échec
        raise RuntimeError("panne au milieu du scan")

    with Session(engine) as verification:
        trajets = verification.exec(select(Route)).all()
    assert [trajet.label for trajet in trajets] == ["déjà en base"]


def test_session_scope_propage_lexception_au_lieu_de_lavaler(engine):
    with pytest.raises(ValueError, match="remonte jusquici"), session_scope(engine):
        raise ValueError("remonte jusquici")
```

`session_scope` mérite ses propres tests parce qu'il est le dernier rempart entre une erreur au
milieu d'un scan et un historique de prix à moitié écrit : rien ne relit la base pour vérifier
qu'elle est cohérente. Trois mutations ont été jouées contre ces tests pour vérifier qu'ils
mordent : remplacer le `rollback()` par un `commit()` les fait rougir (corruption des données),
retirer le `raise` les fait rougir deux fois (exception avalée), mais retirer le seul
`rollback()` ne change rien — `session.close()` annule déjà la transaction sous SQLAlchemy. Le
`rollback()` explicite documente l'intention, il ne la porte pas.

- [ ] **Étape 6 : vérifier que les tests passent**

```bash
./dev test tests/storage/test_models.py -v
./dev lint
```

Attendu : 6 tests passés.

- [ ] **Étape 7 : committer**

```bash
git add scrappervol/storage tests/storage tests/conftest.py
git commit -m "feat: modèles SQLModel et amorçage de la base SQLite"
```

---

## Tâche 5 : dépôt de données

Le seul module qui écrit en base. Concentrer les requêtes ici évite qu'un jour un job et une vue web
mettent à jour `daily_low` selon deux règles différentes.

**Fichiers :**
- Créer : `scrappervol/storage/repo.py`
- Test : `tests/storage/test_repo.py`

**Interfaces :**
- Consomme : modèles et `session_scope` (tâche 4), `FlightOffer` (tâche 2).
- Produit :
  - `active_routes(session) -> list[Route]`
  - `record_observations(session, route_id, offers, observed_at) -> list[Observation]` — déduplique par
    `offer_hash` à l'intérieur du lot, la plus basse gagnant
  - `upsert_daily_low(session, route_id, day, observation) -> DailyLow | None` — retourne la ligne
    seulement si elle a été créée ou abaissée
  - `daily_low_history(session, route_id, before_day, window_days) -> list[int]`
  - `daily_low_for(session, route_id, day) -> DailyLow | None`
  - `purge_observations(session, now, retention_days) -> int`
  - `get_or_create_health(session, provider) -> ProviderHealth`
  - `record_provider_success(session, provider, offers_count, at) -> ProviderHealth`
  - `record_provider_failure(session, provider, error, at, disabled_until) -> ProviderHealth`
  - `exception_already_sent(session, route_id, offer_hash) -> bool`
  - `record_alert(session, route_id, observation_id, kind, payload, at) -> Alert`

- [ ] **Étape 1 : écrire le test qui échoue**

`tests/storage/test_repo.py` :

```python
from datetime import UTC, date, datetime, timedelta

from scrappervol.core.types import FlightOffer
from scrappervol.storage import repo
from scrappervol.storage.models import AlertKind, DailyLow, Observation, Route

MAINTENANT = datetime(2026, 8, 4, 14, 0, tzinfo=UTC)
AUJOURDHUI = date(2026, 8, 4)


def _offre(price_cad: int, **surcharges) -> FlightOffer:
    base = {
        "provider": "google_flights",
        "origin": "YUL",
        "destination": "CDG",
        "depart_date": date(2027, 3, 12),
        "return_date": date(2027, 3, 22),
        "price_cad": price_cad,
        "price_original": float(price_cad),
        "currency_original": "CAD",
        "airline": "Air Transat",
        "stops": 0,
        "duration_minutes": 425,
        "deep_link": "https://example.com",
        "raw": {},
    }
    return FlightOffer(**{**base, **surcharges})


def _trajet(session, **surcharges) -> Route:
    trajet = Route(label="Paris", origins=["YUL"], destinations=["CDG"], **surcharges)
    session.add(trajet)
    session.commit()
    session.refresh(trajet)
    return trajet


def test_active_routes_ignore_les_trajets_desactives(session):
    _trajet(session)
    _trajet(session, active=False)

    assert len(repo.active_routes(session)) == 1


def test_record_observations_persiste_chaque_offre(session):
    trajet = _trajet(session)

    resultat = repo.record_observations(
        session, trajet.id, [_offre(612), _offre(700, airline="Air Canada")], MAINTENANT
    )

    assert len(resultat) == 2
    assert all(o.id is not None for o in resultat)


def test_record_observations_deduplique_dans_le_lot_en_gardant_la_moins_chere(session):
    trajet = _trajet(session)

    resultat = repo.record_observations(session, trajet.id, [_offre(612), _offre(540)], MAINTENANT)

    assert len(resultat) == 1
    assert resultat[0].price_cad == 540


def test_upsert_daily_low_cree_la_ligne_absente(session):
    trajet = _trajet(session)
    observation = repo.record_observations(session, trajet.id, [_offre(612)], MAINTENANT)[0]

    ligne = repo.upsert_daily_low(session, trajet.id, AUJOURDHUI, observation)

    assert ligne is not None
    assert ligne.price_cad == 612
    assert ligne.provider == "google_flights"


def test_upsert_daily_low_ecrase_un_prix_superieur(session):
    trajet = _trajet(session)
    haute = repo.record_observations(session, trajet.id, [_offre(612)], MAINTENANT)[0]
    repo.upsert_daily_low(session, trajet.id, AUJOURDHUI, haute)
    basse = repo.record_observations(
        session, trajet.id, [_offre(480, airline="Air France")], MAINTENANT
    )[0]

    ligne = repo.upsert_daily_low(session, trajet.id, AUJOURDHUI, basse)

    assert ligne is not None
    assert ligne.price_cad == 480


def test_upsert_daily_low_ne_remonte_jamais_un_prix(session):
    trajet = _trajet(session)
    basse = repo.record_observations(session, trajet.id, [_offre(480)], MAINTENANT)[0]
    repo.upsert_daily_low(session, trajet.id, AUJOURDHUI, basse)
    haute = repo.record_observations(
        session, trajet.id, [_offre(900, airline="Air France")], MAINTENANT
    )[0]

    assert repo.upsert_daily_low(session, trajet.id, AUJOURDHUI, haute) is None
    assert repo.daily_low_for(session, trajet.id, AUJOURDHUI).price_cad == 480


def test_daily_low_history_retourne_la_fenetre_hors_jour_courant(session):
    trajet = _trajet(session)
    for decalage in range(5):
        session.add(
            DailyLow(
                route_id=trajet.id,
                day=AUJOURDHUI - timedelta(days=decalage),
                price_cad=500 + decalage,
                provider="google_flights",
            )
        )
    session.commit()

    historique = repo.daily_low_history(session, trajet.id, before_day=AUJOURDHUI, window_days=90)

    assert historique == [501, 502, 503, 504]


def test_daily_low_history_exclut_ce_qui_precede_la_fenetre(session):
    trajet = _trajet(session)
    session.add(DailyLow(route_id=trajet.id, day=AUJOURDHUI - timedelta(days=200), price_cad=300,
                         provider="google_flights"))
    session.add(DailyLow(route_id=trajet.id, day=AUJOURDHUI - timedelta(days=2), price_cad=500,
                         provider="google_flights"))
    session.commit()

    assert repo.daily_low_history(session, trajet.id, AUJOURDHUI, window_days=90) == [500]


def test_purge_supprime_les_observations_anciennes_et_garde_daily_low(session):
    trajet = _trajet(session)
    ancienne = Observation.from_offer(trajet.id, _offre(612), MAINTENANT - timedelta(days=120))
    recente = Observation.from_offer(trajet.id, _offre(500, airline="X"), MAINTENANT)
    session.add(ancienne)
    session.add(recente)
    session.add(DailyLow(route_id=trajet.id, day=date(2025, 1, 1), price_cad=400,
                         provider="google_flights"))
    session.commit()

    supprimees = repo.purge_observations(session, now=MAINTENANT, retention_days=90)

    assert supprimees == 1
    assert repo.daily_low_for(session, trajet.id, date(2025, 1, 1)) is not None


def test_succes_remet_le_compteur_dechecs_a_zero(session):
    repo.record_provider_failure(session, "transat", "timeout", MAINTENANT, None)
    repo.record_provider_failure(session, "transat", "timeout", MAINTENANT, None)

    sante = repo.record_provider_success(session, "transat", offers_count=12, at=MAINTENANT)

    assert sante.consecutive_failures == 0
    assert sante.last_success_at == MAINTENANT
    assert sante.offers_last_run == 12
    assert sante.disabled_until is None


def test_echec_incremente_le_compteur_et_retient_lerreur(session):
    repo.record_provider_failure(session, "transat", "premier", MAINTENANT, None)
    sante = repo.record_provider_failure(session, "transat", "second", MAINTENANT, None)

    assert sante.consecutive_failures == 2
    assert sante.last_error == "second"


def test_echec_peut_poser_une_mise_au_repos(session):
    jusqua = MAINTENANT + timedelta(hours=1)

    sante = repo.record_provider_failure(session, "transat", "bloqué", MAINTENANT, jusqua)

    assert sante.disabled_until == jusqua


def test_exception_already_sent_ne_voit_que_les_alertes_dexception(session):
    trajet = _trajet(session)
    repo.record_alert(session, trajet.id, None, AlertKind.DIGEST, {"offer_hash": "abc"}, MAINTENANT)

    assert repo.exception_already_sent(session, trajet.id, "abc") is False

    repo.record_alert(session, trajet.id, 1, AlertKind.EXCEPTION, {"offer_hash": "abc"}, MAINTENANT)

    assert repo.exception_already_sent(session, trajet.id, "abc") is True
    assert repo.exception_already_sent(session, trajet.id, "autre") is False


def test_upsert_daily_low_prix_egal_ne_bouge_rien(session):
    """Un prix égal n'est ni une création, ni un abaissement : `None` doit revenir, et la
    ligne existante (observation_id, provider) doit rester intacte. Un garde-fou avec `<`
    au lieu de `<=` laisserait passer ce cas silencieusement : même prix, mais la ligne
    serait réécrite et une valeur non-None reviendrait, ce qui romprait le contrat
    « retourne la ligne seulement si elle a été créée ou abaissée » sans jamais faire
    dériver le prix vers le haut — donc sans qu'aucun autre test ne le remarque.
    """
    trajet = _trajet(session)
    premiere = repo.record_observations(session, trajet.id, [_offre(480)], MAINTENANT)[0]
    repo.upsert_daily_low(session, trajet.id, AUJOURDHUI, premiere)
    egale = repo.record_observations(
        session, trajet.id, [_offre(480, airline="Air France")], MAINTENANT
    )[0]

    resultat = repo.upsert_daily_low(session, trajet.id, AUJOURDHUI, egale)

    assert resultat is None
    ligne = repo.daily_low_for(session, trajet.id, AUJOURDHUI)
    assert ligne.price_cad == 480
    assert ligne.observation_id == premiere.id
    assert ligne.provider == "google_flights"


def test_daily_low_history_est_vide_si_seul_le_jour_courant_existe(session):
    """Preuve ciblée et directe du point 2 : si le seul `DailyLow` connu est celui du jour
    courant, l'historique doit être vide. Un `<=` au lieu d'un `<` sur `day` ferait
    entrer le prix du jour dans sa propre médiane — l'aubaine la plus grosse serait celle
    qui paraîtrait le moins anormale.
    """
    trajet = _trajet(session)
    session.add(
        DailyLow(route_id=trajet.id, day=AUJOURDHUI, price_cad=100, provider="google_flights")
    )
    session.commit()

    assert repo.daily_low_history(session, trajet.id, AUJOURDHUI, window_days=90) == []


def test_exception_already_sent_est_borne_au_trajet(session):
    """Preuve ciblée du point 4 : deux trajets peuvent partager un `offer_hash` (même
    itinéraire suivi par deux règles différentes, par exemple). Une alerte d'exception
    envoyée pour le trajet A ne doit jamais éteindre l'alerte du trajet B — sinon une
    aubaine réelle sur B ne serait jamais signalée.
    """
    trajet_a = _trajet(session)
    trajet_b = _trajet(session)
    repo.record_alert(
        session, trajet_a.id, 1, AlertKind.EXCEPTION, {"offer_hash": "partage"}, MAINTENANT
    )

    assert repo.exception_already_sent(session, trajet_a.id, "partage") is True
    assert repo.exception_already_sent(session, trajet_b.id, "partage") is False

```

- [ ] **Étape 2 : lancer le test et vérifier l'échec**

```bash
./dev test tests/storage/test_repo.py -v
```

Attendu : `ImportError` sur `scrappervol.storage.repo`.

- [ ] **Étape 3 : écrire l'implémentation**

`scrappervol/storage/repo.py` :

```python
from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any

from sqlmodel import Session, col, delete, select

from scrappervol.core.types import FlightOffer
from scrappervol.storage.models import Alert, AlertKind, DailyLow, Observation, ProviderHealth, Route


def active_routes(session: Session) -> list[Route]:
    return list(session.exec(select(Route).where(col(Route.active).is_(True))).all())


def record_observations(
    session: Session,
    route_id: int,
    offers: Sequence[FlightOffer],
    observed_at: datetime,
) -> list[Observation]:
    """Persiste un lot d'offres, en ne gardant que la moins chère par `offer_hash`."""
    meilleures: dict[str, FlightOffer] = {}
    for offre in offers:
        connue = meilleures.get(offre.offer_hash)
        if connue is None or offre.price_cad < connue.price_cad:
            meilleures[offre.offer_hash] = offre

    observations = [
        Observation.from_offer(route_id, offre, observed_at) for offre in meilleures.values()
    ]
    for observation in observations:
        session.add(observation)
    session.commit()
    for observation in observations:
        session.refresh(observation)
    return observations


def upsert_daily_low(
    session: Session,
    route_id: int,
    day: date,
    observation: Observation,
) -> DailyLow | None:
    """Écrase le plus bas du jour si l'observation est meilleure. Retourne None sinon."""
    existante = session.get(DailyLow, (route_id, day))
    if existante is not None and existante.price_cad <= observation.price_cad:
        return None

    ligne = existante or DailyLow(route_id=route_id, day=day, price_cad=observation.price_cad)
    ligne.price_cad = observation.price_cad
    ligne.observation_id = observation.id
    ligne.provider = observation.provider
    session.add(ligne)
    session.commit()
    session.refresh(ligne)
    return ligne


def daily_low_history(
    session: Session,
    route_id: int,
    before_day: date,
    window_days: int = 90,
) -> list[int]:
    """Prix des plus bas quotidiens de la fenêtre, du plus récent au plus ancien, jour courant exclu."""
    debut = before_day - timedelta(days=window_days)
    lignes = session.exec(
        select(DailyLow)
        .where(DailyLow.route_id == route_id)
        .where(col(DailyLow.day) < before_day)
        .where(col(DailyLow.day) >= debut)
        .order_by(col(DailyLow.day).desc())
    ).all()
    return [ligne.price_cad for ligne in lignes]


def daily_low_for(session: Session, route_id: int, day: date) -> DailyLow | None:
    return session.get(DailyLow, (route_id, day))


def purge_observations(session: Session, now: datetime, retention_days: int = 90) -> int:
    limite = now - timedelta(days=retention_days)
    resultat = session.exec(delete(Observation).where(col(Observation.observed_at) < limite))
    session.commit()
    return int(resultat.rowcount or 0)


def get_or_create_health(session: Session, provider: str) -> ProviderHealth:
    sante = session.get(ProviderHealth, provider)
    if sante is None:
        sante = ProviderHealth(provider=provider)
        session.add(sante)
        session.commit()
        session.refresh(sante)
    return sante


def record_provider_success(
    session: Session, provider: str, offers_count: int, at: datetime
) -> ProviderHealth:
    sante = get_or_create_health(session, provider)
    sante.last_success_at = at
    sante.consecutive_failures = 0
    sante.disabled_until = None
    sante.last_error = None
    sante.offers_last_run = offers_count
    session.add(sante)
    session.commit()
    session.refresh(sante)
    return sante


def record_provider_failure(
    session: Session,
    provider: str,
    error: str,
    at: datetime,
    disabled_until: datetime | None,
) -> ProviderHealth:
    sante = get_or_create_health(session, provider)
    sante.consecutive_failures += 1
    sante.last_error = error
    sante.disabled_until = disabled_until
    sante.offers_last_run = 0
    session.add(sante)
    session.commit()
    session.refresh(sante)
    return sante


def exception_already_sent(session: Session, route_id: int, offer_hash: str) -> bool:
    alertes = session.exec(
        select(Alert)
        .where(Alert.route_id == route_id)
        .where(Alert.kind == AlertKind.EXCEPTION)
    ).all()
    return any(alerte.payload.get("offer_hash") == offer_hash for alerte in alertes)


def record_alert(
    session: Session,
    route_id: int,
    observation_id: int | None,
    kind: AlertKind,
    payload: dict[str, Any],
    at: datetime,
) -> Alert:
    alerte = Alert(
        route_id=route_id,
        observation_id=observation_id,
        kind=kind,
        sent_at=at,
        payload=payload,
    )
    session.add(alerte)
    session.commit()
    session.refresh(alerte)
    return alerte
```

- [ ] **Étape 4 : vérifier que les tests passent**

```bash
./dev test tests/storage/test_repo.py -v
./dev lint
```

Attendu : 13 tests passés.

- [ ] **Étape 5 : committer**

```bash
git add scrappervol/storage/repo.py tests/storage/test_repo.py
git commit -m "feat: dépôt de données, plus bas du jour, santé des sources et journal d'alertes"
```

---

## Tâche 6 : statistiques robustes

Fonctions pures, sans dépendance. La justification du choix médiane/MAD plutôt que moyenne/écart-type
est au §6 du design : l'écart-type est gonflé par les valeurs extrêmes, donc plus une aubaine est
spectaculaire, plus elle élargit la bande censée la détecter.

**Fichiers :**
- Créer : `scrappervol/detection/stats.py`
- Test : `tests/detection/test_stats.py`

**Interfaces :**
- Consomme : rien.
- Produit :
  - `median(values: Sequence[float]) -> float` — lève `ValueError` sur une séquence vide
  - `mad(values: Sequence[float]) -> float` — écart absolu médian, `0.0` sur une série plate
  - `modified_z(value: float, values: Sequence[float]) -> float | None` — score z modifié
    d'Iglewicz-Hoaglin ; `None` si le MAD est nul (score non défini)

- [ ] **Étape 1 : écrire le test qui échoue**

`tests/detection/test_stats.py` :

```python
import pytest

from scrappervol.detection.stats import mad, median, modified_z


def test_mediane_dune_serie_impaire():
    assert median([3, 1, 2]) == 2


def test_mediane_dune_serie_paire_est_la_moyenne_des_deux_centrales():
    assert median([1, 2, 3, 4]) == 2.5


def test_mediane_dune_serie_vide_leve_une_erreur():
    with pytest.raises(ValueError):
        median([])


def test_la_mediane_resiste_a_une_valeur_extreme():
    """C'est la raison d'être du choix : une aubaine ne doit pas déplacer la référence."""
    normale = [500, 510, 520, 530, 540]
    avec_aubaine = [*normale, 120]

    assert abs(median(avec_aubaine) - median(normale)) < 40


def test_mad_dune_serie_plate_est_nul():
    assert mad([500, 500, 500]) == 0.0


def test_mad_mesure_la_dispersion_typique():
    assert mad([1, 2, 3, 4, 5]) == 1.0


def test_mad_ignore_une_valeur_extreme():
    """Contrairement à l'écart-type, le MAD n'est pas déstabilisé par une valeur extrême.

    Le brief affirmait une égalité stricte entre le MAD d'une série et celui de la même série
    augmentée d'une valeur extrême — ce n'est pas garanti, même à implémentation correcte :
    ajouter un point fait passer l'effectif d'impair à pair, ce qui change la façon dont la
    médiane (et donc le MAD) est calculée : une seule valeur centrale contre la moyenne des deux
    valeurs centrales. Preuve avec l'exemple du brief : mad([500, 502, 504, 506, 508]) vaut 2.0
    mais mad([500, 502, 504, 506, 508, 5]) vaut 3.0, avec l'implémentation même proposée par le
    brief. Ce test vérifie donc ce que le MAD garantit réellement : une valeur extrême ne le
    fait bouger que d'un cran, jamais de l'ordre de grandeur — à comparer à l'écart-type, qui
    passe ici de ~5 à ~151 pour ce seul point ajouté.
    """
    sans_aubaine = [500, 502, 504, 506, 508, 510, 512, 514, 516]
    avec_aubaine = [*sans_aubaine, 5]

    assert abs(mad(avec_aubaine) - mad(sans_aubaine)) <= 2


def test_modified_z_est_negatif_sous_la_mediane():
    serie = [500, 505, 510, 515, 520]

    assert modified_z(400, serie) < 0


def test_modified_z_est_nul_a_la_mediane():
    serie = [500, 505, 510, 515, 520]

    assert modified_z(510, serie) == 0


def test_modified_z_signale_franchement_une_aubaine_sur_serie_stable():
    serie = [600, 601, 600, 602, 599, 601, 600]

    assert modified_z(400, serie) <= -3.5


def test_modified_z_reste_modere_sur_serie_volatile():
    serie = [300, 900, 450, 1100, 380, 950, 500]

    assert modified_z(240, serie) > -3.5


def test_modified_z_est_indefini_sur_serie_plate():
    assert modified_z(400, [600, 600, 600]) is None

def test_mad_est_nul_des_que_plus_de_la_moitie_des_valeurs_egalent_la_mediane():
    """Un MAD nul n'implique pas une série plate.

    Un tarif d'avion a typiquement un plancher stable la plupart des jours, entrecoupé de
    quelques pics. Ici 6 valeurs sur 9 valent 400 (une majorité stricte) alors que les trois
    autres s'étalent de 250 à 900 : la série est loin d'être plate, mais le MAD tombe quand
    même à zéro parce que plus de la moitié des écarts à la médiane sont nuls.
    """
    serie = [400, 400, 400, 400, 400, 400, 850, 900, 250]

    assert mad(serie) == 0.0
    assert modified_z(400, serie) is None


def test_modified_z_est_indefini_sur_une_serie_dun_seul_element():
    """Une série à un seul point n'a pas de dispersion : le MAD est nul par construction
    (l'unique écart à la médiane vaut 0), donc le score doit rester indéfini plutôt que de
    diviser par zéro ou de produire un score artificiellement extrême."""
    assert mad([500]) == 0.0
    assert modified_z(400, [500]) is None


def test_modified_z_vaut_exactement_la_formule_diglewicz_hoaglin():
    """Verrouille la valeur numérique, donc la constante 0.6745 elle-même.

    Les autres tests ne vérifient que le signe du score et le franchissement du seuil, avec
    des marges telles qu'ils survivent à une constante fausse : remplacer 0.6745 par 1.0 les
    laisse tous verts. Or cette constante n'est pas cosmétique. Elle ramène le score à
    l'échelle d'un score z normal ; sans elle, tout score est gonflé d'un facteur ~1.48, ce
    qui revient à desserrer le seuil de -3.5 de la détection jusqu'à ~-5.2 sans que rien ne
    le dise. La détection deviendrait silencieusement plus sourde — le mode de panne que ce
    projet redoute le plus. Le test fixe donc une valeur calculée à la main :
    médiane([10, 12, 14, 16, 18]) = 14, MAD = 2, 0.6745 * (8 - 14) / 2 = -2.0235.
    """
    assert modified_z(8, [10, 12, 14, 16, 18]) == pytest.approx(-2.0235)


def test_mad_refuse_une_serie_vide():
    """La garde de `mad` n'est autrement jamais exercée : elle est masquée par l'appel interne
    à `median`, qui lève déjà. La tester ici la rend intentionnelle plutôt qu'accidentelle."""
    with pytest.raises(ValueError, match="MAD indéfini"):
        mad([])

```

- [ ] **Étape 2 : lancer le test et vérifier l'échec**

```bash
./dev test tests/detection/test_stats.py -v
```

Attendu : `ModuleNotFoundError: No module named 'scrappervol.detection.stats'`.

- [ ] **Étape 3 : écrire l'implémentation**

`scrappervol/detection/stats.py` :

```python
from __future__ import annotations

import statistics
from collections.abc import Sequence

_CONSTANTE_IGLEWICZ = 0.6745


def median(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("médiane indéfinie sur une série vide")
    return float(statistics.median(values))


def mad(values: Sequence[float]) -> float:
    """Écart absolu médian : la dispersion typique, insensible aux valeurs extrêmes."""
    if not values:
        raise ValueError("MAD indéfini sur une série vide")
    centre = median(values)
    return float(statistics.median([abs(valeur - centre) for valeur in values]))


def modified_z(value: float, values: Sequence[float]) -> float | None:
    """Score z modifié d'Iglewicz-Hoaglin.

    Retourne None quand le MAD est nul : le score diviserait par zéro, donc il est indéfini.
    Ce cas ne se limite pas à une série parfaitement plate. Le MAD tombe à zéro dès que *plus
    de la moitié* des valeurs coïncident avec la médiane — le reste de la série peut être très
    dispersé. Un tarif d'avion prend typiquement cette forme : un plancher stable la plupart
    des jours, entrecoupé de quelques pics ou creux ponctuels. Sur une telle série, ce n'est
    pas un cas marginal : c'est le profil normal, et c'est précisément sur les trajets les plus
    stables — ceux qu'on voudrait le mieux surveiller — que le score sera le plus souvent
    indéfini. L'appelant doit alors se rabattre sur un autre signal (par ex. un seuil relatif) ;
    ne pas court-circuiter ce repli en resserrant ce test à la seule série constante.
    """
    dispersion = mad(values)
    if dispersion == 0:
        return None
    return _CONSTANTE_IGLEWICZ * (value - median(values)) / dispersion
```

- [ ] **Étape 4 : vérifier que les tests passent**

```bash
./dev test tests/detection/test_stats.py -v
./dev lint
```

Attendu : 12 tests passés.

- [ ] **Étape 5 : committer**

```bash
git add scrappervol/detection/stats.py tests/detection/test_stats.py
git commit -m "feat: statistiques robustes fondées sur la médiane et le MAD"
```

---

## Tâche 7 : règles de détection

Les quatre conditions du §6 du design, plus la définition de la trouvaille du §8. Pur, sans horloge ni
base : c'est ce qui permet de tester en une milliseconde les scénarios qui, en production, prennent
trois mois à se produire.

**Fichiers :**
- Créer : `scrappervol/detection/rules.py`
- Test : `tests/detection/test_rules.py`

**Interfaces :**
- Consomme : `median`, `mad`, `modified_z` (tâche 6).
- Produit :
  - `PriceContext(daily_lows: list[int])` avec les propriétés `days_of_history: int`,
    `median_price: float | None`, `has_significant_history(min_days: int) -> bool`
  - `relative_gap(price_cad: int, median_price: float) -> float` — fraction sous la médiane, positive
    quand le prix est plus bas
  - `is_exception(price_cad, context, threshold, min_history_days, credibility_floor,
    already_alerted) -> bool`
  - `is_find(price_cad, context, target_price_cad, find_threshold, min_history_days) -> bool`

- [ ] **Étape 1 : écrire le test qui échoue**

`tests/detection/test_rules.py` :

```python
from scrappervol.detection.rules import PriceContext, is_exception, is_find, relative_gap

SERIE_STABLE = [600, 605, 598, 602, 601, 599, 603, 600, 604, 597, 601, 602, 600, 599, 605, 598]


def _contexte(prix: list[int]) -> PriceContext:
    return PriceContext(daily_lows=prix)


def test_days_of_history_compte_les_jours_observes():
    assert _contexte([600, 610, 620]).days_of_history == 3


def test_median_price_est_none_sans_historique():
    assert _contexte([]).median_price is None


def test_relative_gap_mesure_la_fraction_sous_la_mediane():
    assert relative_gap(300, 600.0) == 0.5
    assert relative_gap(600, 600.0) == 0.0
    assert relative_gap(900, 600.0) == -0.5


def test_aberration_franche_sur_serie_stable_declenche():
    assert (
        is_exception(
            price_cad=300,
            context=_contexte(SERIE_STABLE),
            threshold=0.40,
            min_history_days=14,
            credibility_floor=50,
            already_alerted=False,
        )
        is True
    )


def test_historique_trop_court_ne_declenche_jamais():
    """Sans ce garde-fou, les deux premières semaines produisent une fausse alerte à chaque passage."""
    assert (
        is_exception(
            price_cad=100,
            context=_contexte([600, 610, 605]),
            threshold=0.40,
            min_history_days=14,
            credibility_floor=50,
            already_alerted=False,
        )
        is False
    )


def test_prix_sous_le_plancher_de_credibilite_ne_declenche_pas():
    """Un « 45 » lu dans « 45 min d'escale » ne doit pas réveiller l'utilisateur la nuit."""
    assert (
        is_exception(
            price_cad=45,
            context=_contexte(SERIE_STABLE),
            threshold=0.40,
            min_history_days=14,
            credibility_floor=50,
            already_alerted=False,
        )
        is False
    )


def test_prix_juste_au_dessus_du_plancher_declenche():
    assert (
        is_exception(
            price_cad=51,
            context=_contexte(SERIE_STABLE),
            threshold=0.40,
            min_history_days=14,
            credibility_floor=50,
            already_alerted=False,
        )
        is True
    )


def test_baisse_insuffisante_ne_declenche_pas():
    assert (
        is_exception(
            price_cad=500,
            context=_contexte(SERIE_STABLE),
            threshold=0.40,
            min_history_days=14,
            credibility_floor=50,
            already_alerted=False,
        )
        is False
    )


def test_une_alerte_deja_emise_reste_silencieuse():
    assert (
        is_exception(
            price_cad=300,
            context=_contexte(SERIE_STABLE),
            threshold=0.40,
            min_history_days=14,
            credibility_floor=50,
            already_alerted=True,
        )
        is False
    )


def test_serie_volatile_ne_declenche_pas_a_moins_quarante_pourcent():
    """Sur une série qui oscille du simple au triple, -40 % est le régime normal, pas une aubaine."""
    volatile = [300, 900, 450, 1100, 380, 950, 500, 1000, 320, 880, 460, 1050, 400, 920, 480, 990]

    assert (
        is_exception(
            price_cad=240,
            context=_contexte(volatile),
            threshold=0.40,
            min_history_days=14,
            credibility_floor=50,
            already_alerted=False,
        )
        is False
    )


def test_serie_parfaitement_plate_retombe_sur_le_seuil_relatif():
    plate = [600] * 20

    assert (
        is_exception(
            price_cad=300,
            context=_contexte(plate),
            threshold=0.40,
            min_history_days=14,
            credibility_floor=50,
            already_alerted=False,
        )
        is True
    )


def test_le_seuil_est_configurable_par_trajet():
    assert (
        is_exception(
            price_cad=480,
            context=_contexte(SERIE_STABLE),
            threshold=0.20,
            min_history_days=14,
            credibility_floor=50,
            already_alerted=False,
        )
        is True
    )


def test_trouvaille_quand_le_prix_passe_sous_la_cible():
    assert (
        is_find(
            price_cad=450,
            context=_contexte(SERIE_STABLE),
            target_price_cad=500,
            find_threshold=0.15,
            min_history_days=14,
        )
        is True
    )


def test_trouvaille_sous_la_cible_meme_sans_historique_significatif():
    """La cible est un seuil absolu voulu par l'utilisateur : elle ne dépend pas de la statistique."""
    assert (
        is_find(
            price_cad=450,
            context=_contexte([600, 610]),
            target_price_cad=500,
            find_threshold=0.15,
            min_history_days=14,
        )
        is True
    )


def test_trouvaille_quand_le_prix_est_quinze_pourcent_sous_la_mediane():
    assert (
        is_find(
            price_cad=500,
            context=_contexte(SERIE_STABLE),
            target_price_cad=None,
            find_threshold=0.15,
            min_history_days=14,
        )
        is True
    )


def test_pas_de_trouvaille_statistique_sans_historique_significatif():
    assert (
        is_find(
            price_cad=100,
            context=_contexte([600, 610, 605]),
            target_price_cad=None,
            find_threshold=0.15,
            min_history_days=14,
        )
        is False
    )


def test_prix_ordinaire_nest_pas_une_trouvaille():
    assert (
        is_find(
            price_cad=595,
            context=_contexte(SERIE_STABLE),
            target_price_cad=None,
            find_threshold=0.15,
            min_history_days=14,
        )
        is False
    )


SERIE_ETALEE = [450, 480, 510, 540, 570, 600, 600, 600, 600, 630, 660, 690, 720, 750]


def test_le_veto_du_score_z_se_joue_bien_a_moins_trois_virgule_cinq():
    """Verrouille la valeur du seuil, que les autres tests laissent flotter.

    Les scores z qu'ils atteignent valent -135, -247, -54 pour les cas qui déclenchent et
    -1.10 pour celui qui refuse : n'importe quel seuil entre -54 et -1.1 les laisse tous
    verts. Le seuil pourrait donc dériver de -3.5 à -20 sans qu'une seule ligne rougisse,
    et la détection deviendrait sourde en silence.

    Ce test se place à la frontière. SERIE_ETALEE a pour médiane 600 et pour MAD 60 ; les
    deux prix ci-dessous sont l'un et l'autre à plus de 50 % sous la médiane, donc tous
    deux admis par le seuil relatif. Seul le veto les sépare : z = -3.597 pour 280,
    z = -3.429 pour 295.

    Ce que ce test ne couvre pas, faute de pouvoir le faire : le cas z == -3.5 exactement,
    qui dirait si la comparaison est large ou stricte. Il faudrait un prix tel que
    (prix - médiane) / MAD vaille -3.5 / 0.6745, soit -5.18902891..., un rapport que deux
    entiers n'atteignent pas. Rendre la comparaison stricte est donc indétectable, et sans
    conséquence : aucune donnée réelle ne tombera jamais sur cette valeur.
    """
    commun = {
        "context": _contexte(SERIE_ETALEE),
        "threshold": 0.40,
        "min_history_days": 14,
        "credibility_floor": 50,
        "already_alerted": False,
    }

    assert is_exception(price_cad=280, **commun) is True
    assert is_exception(price_cad=295, **commun) is False



SERIE_RONDE = [490, 492, 494, 496, 498, 500, 500, 500, 502, 504, 506, 508, 510, 512]


def test_les_bornes_inclusives_se_jouent_bien_a_legalite():
    """Quatre comparaisons du module sont inclusives ; aucun test ne les exerçait à l'égalité.

    Les rendre strictes — `<= credibility_floor` en `<`, `> mediane*(1-threshold)` en `>=`,
    `<= target_price_cad` en `<`, `>= find_threshold` en `>` — laissait toute la suite verte.
    Chacune décide pourtant d'un cas réel : un billet à exactement 50 CAD, un prix pile au
    plancher relatif, une cible atteinte au dollar près.

    SERIE_RONDE a pour médiane 500.0, donc un plancher à 40 % qui vaut 300.0 tout rond : le
    seul moyen d'atteindre l'égalité exacte, `price_cad` étant un entier.
    """
    exception = {
        "context": _contexte(SERIE_STABLE),
        "threshold": 0.40,
        "min_history_days": 14,
        "credibility_floor": 50,
        "already_alerted": False,
    }
    # Un prix pile au plancher de crédibilité est jugé trop beau pour être vrai.
    assert is_exception(price_cad=50, **exception) is False

    relatif = {**exception, "context": _contexte(SERIE_RONDE)}
    # Pile à 40 % sous la médiane, le prix est admis ; un dollar au-dessus, il ne l'est plus.
    assert is_exception(price_cad=300, **relatif) is True
    assert is_exception(price_cad=301, **relatif) is False

    # Une cible atteinte au dollar près est atteinte.
    assert (
        is_find(
            price_cad=500,
            context=_contexte([]),
            target_price_cad=500,
            find_threshold=0.15,
            min_history_days=14,
        )
        is True
    )

    # 510 est exactement 15 % sous 600 : le seuil de trouvaille est lui aussi inclusif.
    assert (
        is_find(
            price_cad=510,
            context=_contexte(SERIE_ETALEE),
            target_price_cad=None,
            find_threshold=0.15,
            min_history_days=14,
        )
        is True
    )


def test_relative_gap_ne_divise_pas_par_une_mediane_nulle():
    """Branche jamais exercée par les autres tests, et pourtant seule à séparer une division
    par zéro d'un résultat neutre. Une médiane nulle ou négative n'a pas de sens ; l'écart
    relatif non plus."""
    assert relative_gap(500, 0.0) == 0.0
    assert relative_gap(500, -10.0) == 0.0

```

- [ ] **Étape 2 : lancer le test et vérifier l'échec**

```bash
./dev test tests/detection/test_rules.py -v
```

Attendu : `ModuleNotFoundError: No module named 'scrappervol.detection.rules'`.

- [ ] **Étape 3 : écrire l'implémentation**

`scrappervol/detection/rules.py` :

```python
from __future__ import annotations

from dataclasses import dataclass

from scrappervol.detection.stats import median, modified_z

SEUIL_Z_MODIFIE = -3.5


@dataclass(frozen=True, slots=True)
class PriceContext:
    """Historique des plus bas quotidiens d'un trajet, jour courant exclu."""

    daily_lows: list[int]

    @property
    def days_of_history(self) -> int:
        return len(self.daily_lows)

    @property
    def median_price(self) -> float | None:
        return median(self.daily_lows) if self.daily_lows else None

    def has_significant_history(self, min_days: int) -> bool:
        return self.days_of_history >= min_days


def relative_gap(price_cad: int, median_price: float) -> float:
    """Fraction sous la médiane. Positive quand le prix est plus bas qu'à l'ordinaire."""
    if median_price <= 0:
        return 0.0
    return (median_price - price_cad) / median_price


def is_exception(
    price_cad: int,
    context: PriceContext,
    threshold: float,
    min_history_days: int,
    credibility_floor: int,
    already_alerted: bool,
) -> bool:
    """Les quatre conditions du §6 du design, plus la condition MAD.

    L'ordre des tests suit le coût croissant : les rejets francs d'abord.
    """
    if already_alerted:
        return False
    if price_cad <= credibility_floor:
        return False
    if not context.has_significant_history(min_history_days):
        return False

    mediane = context.median_price
    if mediane is None:
        return False
    if price_cad > mediane * (1 - threshold):
        return False

    score = modified_z(price_cad, context.daily_lows)
    if score is None:
        return True
    return score <= SEUIL_Z_MODIFIE


def is_find(
    price_cad: int,
    context: PriceContext,
    target_price_cad: int | None,
    find_threshold: float,
    min_history_days: int,
) -> bool:
    """Trouvaille au sens du §8 : sous la cible absolue, ou nettement sous la médiane."""
    if target_price_cad is not None and price_cad <= target_price_cad:
        return True
    if not context.has_significant_history(min_history_days):
        return False

    mediane = context.median_price
    if mediane is None:
        return False
    return relative_gap(price_cad, mediane) >= find_threshold
```

- [ ] **Étape 4 : vérifier que les tests passent**

```bash
./dev test tests/detection/test_rules.py -v
./dev lint
```

Attendu : 17 tests passés.

- [ ] **Étape 5 : committer**

```bash
git add scrappervol/detection/rules.py tests/detection/test_rules.py
git commit -m "feat: règles d'aberration et de trouvaille avec garde-fous"
```

---

## Tâche 8 : interface des sources et disjoncteur

**Fichiers :**
- Créer : `scrappervol/providers/base.py`, `scrappervol/providers/health.py`
- Test : `tests/providers/test_health.py`

**Interfaces :**
- Consomme : `SearchQuery`, `FlightOffer` (tâche 2), `ProviderHealth` (tâche 4).
- Produit :
  - `base.PriceProvider` — protocole `name: str` et `search(query: SearchQuery) -> list[FlightOffer]`
  - `base.ProviderError` — exception de base des scrapers
  - `base.EmptyResultError(ProviderError)` — levée par le runner sur un succès vide suspect
  - `health.backoff_until(consecutive_failures: int, now: datetime) -> datetime | None`
  - `health.is_disabled(health: ProviderHealth, now: datetime) -> bool`

- [ ] **Étape 1 : écrire le test qui échoue**

`tests/providers/test_health.py` :

```python
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

```

- [ ] **Étape 2 : lancer le test et vérifier l'échec**

```bash
./dev test tests/providers/test_health.py -v
```

Attendu : `ModuleNotFoundError: No module named 'scrappervol.providers.health'`.

- [ ] **Étape 3 : écrire `scrappervol/providers/base.py`**

```python
from __future__ import annotations

from typing import Protocol, runtime_checkable

from scrappervol.core.types import FlightOffer, SearchQuery


class ProviderError(Exception):
    """Échec d'une source. Toute exception d'un scraper doit être traduite en celle-ci."""


class EmptyResultError(ProviderError):
    """Zéro offre là où la veille en produisait : traité comme un échec, pas comme un succès."""


@runtime_checkable
class PriceProvider(Protocol):
    name: str

    def search(self, query: SearchQuery) -> list[FlightOffer]: ...
```

- [ ] **Étape 4 : écrire `scrappervol/providers/health.py`**

```python
from __future__ import annotations

from datetime import datetime, timedelta

from scrappervol.storage.models import ProviderHealth

ECHECS_AVANT_REPOS = 3
REPOS_INITIAL_H = 1
REPOS_MAX_H = 24


def backoff_until(consecutive_failures: int, now: datetime) -> datetime | None:
    """Fin du repos imposé à une source, ou None si elle n'a pas encore assez échoué.

    Le délai double à chaque échec au-delà du seuil, plafonné à 24 h.
    """
    if consecutive_failures < ECHECS_AVANT_REPOS:
        return None
    exposant = consecutive_failures - ECHECS_AVANT_REPOS
    heures = min(REPOS_INITIAL_H * 2**exposant, REPOS_MAX_H)
    return now + timedelta(hours=heures)


def is_disabled(health: ProviderHealth, now: datetime) -> bool:
    return health.disabled_until is not None and health.disabled_until > now
```

- [ ] **Étape 5 : vérifier que les tests passent**

```bash
./dev test tests/providers/test_health.py -v
./dev lint
```

Attendu : 7 tests passés.

- [ ] **Étape 6 : committer**

```bash
git add scrappervol/providers/base.py scrappervol/providers/health.py tests/providers/test_health.py
git commit -m "feat: interface des sources et disjoncteur à repos exponentiel"
```

---

## Tâche 9 : exécution isolée d'une source

Le cœur de la résilience du §10 : une source qui casse ne doit ni interrompre un passage, ni faire
croire qu'il n'y a rien à signaler.

**Fichiers :**
- Créer : `scrappervol/providers/runner.py`
- Modifier : `tests/conftest.py` (ajout des fausses sources)
- Test : `tests/providers/test_runner.py`

**Interfaces :**
- Consomme : `PriceProvider`, `ProviderError` (tâche 8), `backoff_until`, `is_disabled` (tâche 8),
  `plan_queries`, `rotation_for` (tâche 3), le dépôt (tâche 5), `Settings` (tâche 1).
- Produit :
  - `RunReport(provider, offers_by_route: dict[int, list[FlightOffer]], queries_run: int,
    failed: bool, error: str | None, skipped: bool)`
  - `run_provider(session, provider, settings, now, sleeper=time.sleep) -> RunReport`
  - Fixtures pytest `fausse_source` (usine paramétrable) et `offre_test`.

- [ ] **Étape 1 : ajouter les fausses sources à `tests/conftest.py`**

Ajouter l'import en tête de fichier, avec les autres — ruff refuse un import en milieu de module :

```python
from scrappervol.core.types import FlightOffer, SearchQuery
```

puis, à la suite du fichier :

```python
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
```

- [ ] **Étape 2 : écrire le test qui échoue**

`tests/providers/test_runner.py` :

```python
from datetime import UTC, datetime, timedelta

import pytest

from scrappervol.config import Settings
from scrappervol.core.types import DatePolicyKind
from scrappervol.providers.base import ProviderError
from scrappervol.providers.runner import run_provider
from scrappervol.storage import repo
from scrappervol.storage.models import ProviderHealth, Route

MAINTENANT = datetime(2026, 8, 4, 14, 0, tzinfo=UTC)


@pytest.fixture
def reglages():
    return Settings(max_queries_per_route=2, request_pause_min_s=0, request_pause_max_s=0)


def _trajet(session, **surcharges) -> Route:
    base = {
        "label": "Paris",
        "origins": ["YUL"],
        "destinations": ["CDG"],
        "date_policy": DatePolicyKind.FIXED,
        "policy_params": {"depart": "2027-03-12", "retour": "2027-03-22"},
    }
    trajet = Route(**{**base, **surcharges})
    session.add(trajet)
    session.commit()
    session.refresh(trajet)
    return trajet


def test_les_offres_sont_regroupees_par_trajet(session, reglages, fausse_source, sans_pause):
    trajet = _trajet(session)
    source = fausse_source(name="google_flights", offres=[(612, "Air Transat")])
    dormir, _ = sans_pause

    rapport = run_provider(session, source, reglages, MAINTENANT, sleeper=dormir)

    assert rapport.failed is False
    assert list(rapport.offers_by_route) == [trajet.id]
    assert rapport.offers_by_route[trajet.id][0].price_cad == 612


def test_les_trajets_inactifs_sont_ignores(session, reglages, fausse_source, sans_pause):
    _trajet(session, active=False)
    source = fausse_source(offres=[(612, "Air Transat")])
    dormir, _ = sans_pause

    rapport = run_provider(session, source, reglages, MAINTENANT, sleeper=dormir)

    assert rapport.offers_by_route == {}
    assert source.appels == []


def test_une_exception_est_capturee_et_nabout_pas_hors_du_runner(
    session, reglages, fausse_source, sans_pause
):
    _trajet(session)
    source = fausse_source(exception=ProviderError("sélecteur introuvable"))
    dormir, _ = sans_pause

    rapport = run_provider(session, source, reglages, MAINTENANT, sleeper=dormir)

    assert rapport.failed is True
    assert "sélecteur introuvable" in rapport.error


def test_une_exception_inattendue_est_aussi_capturee(session, reglages, fausse_source, sans_pause):
    _trajet(session)
    source = fausse_source(exception=RuntimeError("panne inattendue"))
    dormir, _ = sans_pause

    rapport = run_provider(session, source, reglages, MAINTENANT, sleeper=dormir)

    assert rapport.failed is True


def test_un_echec_incremente_la_sante_et_pose_un_repos_au_troisieme(
    session, reglages, fausse_source, sans_pause
):
    _trajet(session)
    source = fausse_source(name="transat", exception=ProviderError("boum"))
    dormir, _ = sans_pause

    for _ in range(3):
        run_provider(session, source, reglages, MAINTENANT, sleeper=dormir)

    sante = repo.get_or_create_health(session, "transat")
    assert sante.consecutive_failures == 3
    assert sante.disabled_until == MAINTENANT + timedelta(hours=1)


def test_une_source_au_repos_nest_pas_interrogee(session, reglages, fausse_source, sans_pause):
    _trajet(session)
    session.add(
        ProviderHealth(provider="transat", disabled_until=MAINTENANT + timedelta(hours=2),
                       consecutive_failures=3)
    )
    session.commit()
    source = fausse_source(name="transat", offres=[(612, "Air Transat")])
    dormir, _ = sans_pause

    rapport = run_provider(session, source, reglages, MAINTENANT, sleeper=dormir)

    assert rapport.skipped is True
    assert source.appels == []


def test_le_repos_echu_laisse_repasser_la_source(session, reglages, fausse_source, sans_pause):
    _trajet(session)
    session.add(
        ProviderHealth(provider="transat", disabled_until=MAINTENANT - timedelta(minutes=1),
                       consecutive_failures=3)
    )
    session.commit()
    source = fausse_source(name="transat", offres=[(612, "Air Transat")])
    dormir, _ = sans_pause

    rapport = run_provider(session, source, reglages, MAINTENANT, sleeper=dormir)

    assert rapport.skipped is False
    assert source.appels != []


def test_un_succes_remet_la_sante_a_zero(session, reglages, fausse_source, sans_pause):
    _trajet(session)
    dormir, _ = sans_pause
    run_provider(
        session, fausse_source(name="transat", exception=ProviderError("boum")), reglages,
        MAINTENANT, sleeper=dormir,
    )

    run_provider(
        session, fausse_source(name="transat", offres=[(612, "Air Transat")]), reglages,
        MAINTENANT, sleeper=dormir,
    )

    sante = repo.get_or_create_health(session, "transat")
    assert sante.consecutive_failures == 0
    assert sante.offers_last_run > 0


def test_zero_offre_est_un_succes_la_premiere_fois(session, reglages, fausse_source, sans_pause):
    """Un trajet qui n'a jamais rien donné peut légitimement ne rien donner."""
    _trajet(session)
    source = fausse_source(name="transat", muette=True)
    dormir, _ = sans_pause

    rapport = run_provider(session, source, reglages, MAINTENANT, sleeper=dormir)

    assert rapport.failed is False


def test_zero_offre_apres_un_passage_fructueux_est_un_echec(
    session, reglages, fausse_source, sans_pause
):
    """Une dérive de sélecteur renvoie un HTTP 200 sans exception : sans cette règle, elle passe
    inaperçue et le digest annonce fidèlement qu'il n'y a rien à signaler."""
    _trajet(session)
    dormir, _ = sans_pause
    run_provider(
        session, fausse_source(name="transat", offres=[(612, "Air Transat")]), reglages,
        MAINTENANT, sleeper=dormir,
    )

    rapport = run_provider(
        session, fausse_source(name="transat", muette=True), reglages,
        MAINTENANT + timedelta(hours=6), sleeper=dormir,
    )

    assert rapport.failed is True
    assert "aucune offre" in rapport.error.lower()

    # Le rapport n'est qu'un compte rendu en mémoire : ce qui protège vraiment la veille, c'est
    # l'échec inscrit en base. Remplacer ici record_provider_failure par record_provider_success
    # laisse le rapport intact et n'arme jamais le disjoncteur — la panne silencieuse exacte que
    # cette règle existe pour attraper.
    sante = repo.get_or_create_health(session, "transat")
    assert sante.consecutive_failures == 1
    assert sante.last_error is not None
    assert "aucune offre" in sante.last_error.lower()
    assert sante.offers_last_run == 0


def test_le_plafond_de_requetes_est_respecte(session, reglages, fausse_source, sans_pause):
    _trajet(session, origins=["YUL", "YQB"], destinations=["CDG", "ORY", "BRU"],
            date_policy=DatePolicyKind.WINDOW,
            policy_params={"mois": ["2027-03", "2027-04"], "sejour_min": 8, "sejour_max": 12})
    source = fausse_source(offres=[(612, "Air Transat")])
    dormir, _ = sans_pause

    run_provider(session, source, reglages, MAINTENANT, sleeper=dormir)

    assert len(source.appels) == reglages.max_queries_per_route


def test_une_pause_est_observee_entre_les_requetes(session, reglages, fausse_source, sans_pause):
    _trajet(session, origins=["YUL", "YQB"])
    source = fausse_source(offres=[(612, "Air Transat")])
    dormir, appels = sans_pause

    run_provider(session, source, reglages, MAINTENANT, sleeper=dormir)

    assert len(appels) == len(source.appels)


def test_desactiver_ses_trajets_ne_condamne_pas_les_sources(
    session, reglages, fausse_source, sans_pause
):
    """Une source qu'on n'interroge pas ne peut pas échouer.

    Sans la garde sur `queries_run`, le passage qui suit la désactivation conclut « aucune offre
    alors que le passage précédent en produisait », incrémente le compteur d'échecs et pose un
    repos qui double jusqu'à 24 h. Les sources dorment alors au moment précis où l'utilisateur
    réactive un trajet, et la page de santé annonce trois pannes qui n'existent pas.
    """
    trajet = _trajet(session)
    dormir, _ = sans_pause
    run_provider(
        session,
        fausse_source(name="transat", offres=[(612, "Air Transat")]),
        reglages,
        MAINTENANT,
        sleeper=dormir,
    )

    trajet.active = False
    session.add(trajet)
    session.commit()

    rapport = run_provider(
        session,
        fausse_source(name="transat", muette=True),
        reglages,
        MAINTENANT + timedelta(hours=6),
        sleeper=dormir,
    )

    assert rapport.queries_run == 0
    assert rapport.failed is False
    assert repo.get_or_create_health(session, "transat").disabled_until is None


def test_la_pause_entre_requetes_respecte_lintervalle_configure(session, fausse_source, sans_pause):
    """La fixture `reglages` met les pauses à zéro : aucun test ne distingue alors une vraie pause
    de son absence. Celui-ci configure un intervalle non nul exprès.

    Sans pause, le scraper enchaîne les requêtes à pleine vitesse et se fait bannir de la
    source. C'est une panne qui ne se voit pas en test — elle ne se voit qu'en production, une
    fois l'adresse bloquée, et elle prive la veille de sa source la plus riche.
    """
    reglages_lents = Settings(max_queries_per_route=2, request_pause_min_s=3, request_pause_max_s=7)
    _trajet(session, origins=["YUL", "YQB"])
    source = fausse_source(offres=[(612, "Air Transat")])
    dormir, appels = sans_pause

    run_provider(session, source, reglages_lents, MAINTENANT, sleeper=dormir)

    assert len(appels) >= 2, "il faut au moins deux requêtes pour observer une pause"
    assert appels[0] == 0, "la première requête ne doit pas attendre"
    assert all(3 <= pause <= 7 for pause in appels[1:]), appels


def test_le_plafond_de_requetes_est_applique_par_trajet(
    session, reglages, fausse_source, sans_pause
):
    """`max_queries_per_route` borne chaque trajet, pas l'ensemble du passage.

    `plan_queries` est appelé à l'intérieur de la boucle sur les trajets actifs, avec le même
    plafond à chaque itération : deux trajets actifs avec un plafond de 2 doivent produire
    4 requêtes, pas 2. Ce plafond borne la charge infligée à une source pour *un* trajet donné ;
    le nombre de trajets suivis est un choix de l'utilisateur, pas une raison d'en surveiller
    chacun avec moins d'attention. Un seul trajet actif ne peut pas distinguer un plafond « par
    trajet » d'un plafond « global » : il en faut deux.
    """
    premier = _trajet(session, label="Paris", origins=["YUL", "YQB"])
    second = _trajet(session, label="Rome", origins=["YUL", "YQB"], destinations=["FCO"])
    source = fausse_source(offres=[(612, "Air Transat")])
    dormir, _ = sans_pause

    rapport = run_provider(session, source, reglages, MAINTENANT, sleeper=dormir)

    assert rapport.queries_run == 4
    assert set(rapport.offers_by_route) == {premier.id, second.id}
```

- [ ] **Étape 3 : lancer le test et vérifier l'échec**

```bash
./dev test tests/providers/test_runner.py -v
```

Attendu : `ModuleNotFoundError: No module named 'scrappervol.providers.runner'`.

- [ ] **Étape 4 : écrire l'implémentation**

`scrappervol/providers/runner.py` :

```python
from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlmodel import Session

from scrappervol.config import Settings
from scrappervol.core.query_planner import plan_queries, rotation_for
from scrappervol.core.types import FlightOffer
from scrappervol.providers.base import PriceProvider
from scrappervol.providers.health import backoff_until, is_disabled
from scrappervol.storage import repo

logger = logging.getLogger(__name__)


@dataclass
class RunReport:
    provider: str
    offers_by_route: dict[int, list[FlightOffer]] = field(default_factory=dict)
    queries_run: int = 0
    failed: bool = False
    error: str | None = None
    skipped: bool = False


def run_provider(
    session: Session,
    provider: PriceProvider,
    settings: Settings,
    now: datetime,
    sleeper: Callable[[float], None] = time.sleep,
) -> RunReport:
    """Interroge une source sur tous les trajets actifs, sans jamais laisser échapper d'exception.

    Le contrat est strict : quoi qu'il arrive dans le scraper, cette fonction retourne un rapport.
    C'est ce qui garantit qu'une source cassée n'emporte pas les deux autres.
    """
    rapport = RunReport(provider=provider.name)
    sante = repo.get_or_create_health(session, provider.name)

    if is_disabled(sante, now):
        rapport.skipped = True
        logger.info("source %s au repos jusqu'à %s", provider.name, sante.disabled_until)
        return rapport

    produisait_avant = sante.offers_last_run > 0
    rotation = rotation_for(now)
    premiere_requete = True

    try:
        for trajet in repo.active_routes(session):
            requetes = plan_queries(
                trajet.to_policy(),
                today=now.date(),
                rotation=rotation,
                max_queries=settings.max_queries_per_route,
            )
            for requete in requetes:
                if not premiere_requete:
                    sleeper(
                        random.uniform(settings.request_pause_min_s, settings.request_pause_max_s)
                    )
                else:
                    sleeper(0)
                premiere_requete = False

                offres = provider.search(requete)
                rapport.queries_run += 1
                if offres:
                    rapport.offers_by_route.setdefault(trajet.id, []).extend(offres)
    except Exception as erreur:  # noqa: BLE001 — l'isolation est le but de cette fonction
        rapport.failed = True
        rapport.error = f"{type(erreur).__name__}: {erreur}"
        logger.warning("échec de la source %s : %s", provider.name, rapport.error)
        repo.record_provider_failure(
            session,
            provider.name,
            rapport.error,
            now,
            backoff_until(sante.consecutive_failures + 1, now),
        )
        return rapport

    total = sum(len(offres) for offres in rapport.offers_by_route.values())

    if total == 0 and produisait_avant and rapport.queries_run > 0:
        rapport.failed = True
        rapport.error = "aucune offre alors que le passage précédent en produisait"
        repo.record_provider_failure(
            session,
            provider.name,
            rapport.error,
            now,
            backoff_until(sante.consecutive_failures + 1, now),
        )
        return rapport

    repo.record_provider_success(session, provider.name, total, now)
    return rapport
```

L'appel `sleeper(0)` sur la première requête paraît inutile ; il permet aux tests de compter les
pauses sans distinguer un cas particulier, et il coûte zéro en production.

- [ ] **Étape 5 : vérifier que les tests passent**

```bash
./dev test tests/providers/ -v
./dev lint
```

Attendu : 19 tests passés (7 de la tâche 8, 12 ici).

- [ ] **Étape 6 : committer**

```bash
git add scrappervol/providers/runner.py tests/providers/test_runner.py tests/conftest.py
git commit -m "feat: exécution isolée des sources avec détection du succès vide"
```

---

## Tâche 10 : source Google Flights

**Ce brief remplace intégralement celui extrait du plan** : le plan visait l'API `fast-flights` v2,
alors que `requirements.lock.txt` résout **3.0.2**, dont l'API est incompatible. Voir la section
finale. Tout le code ci-dessous a été vérifié contre une réponse réelle de Google Flights capturée
le 4 août 2026.

La colonne vertébrale du système. La traduction des données est la partie fragile : elle est isolée
dans une fonction pure, testable hors ligne sur une fixture, conformément au §11.1 du design.

**Fichiers :**
- Créer : `scrappervol/providers/google_flights.py`
- Créer : `tests/providers/test_google_flights.py`
- Créer : `scripts/capture_fixture.py` (hors suite de tests)
- **Déjà fourni** : `tests/fixtures/google_flights_yul_cdg.json` — ne pas le régénérer

**Interfaces :**
- Consomme : `SearchQuery`, `FlightOffer` (tâche 2), `ProviderError`, `EmptyResultError` (tâche 8).
- Produit :
  - `GoogleFlightsProvider` : `name = "google_flights"`, `search(query) -> list[FlightOffer]`
  - `to_offers(resultats, query) -> list[FlightOffer]` — fonction pure
  - Lève `EmptyResultError` quand aucune offre n'est exploitable ; `ProviderError` sur échec réseau.

## Ce que renvoie réellement fast-flights 3.0.2

Requête réelle YUL→CDG aller-retour, 4 résultats :

```
type='AC'  price=765  airlines=['Air Canada']  segments=2  escales=1
type='DL'  price=851  airlines=['Delta']       segments=2  escales=1
type='AF'  price=966  airlines=['Air France']  segments=1  escales=0
type='TS'  price=1134 airlines=['Air Transat'] segments=1  escales=0
```

Faits constatés, non supposés, qui commandent l'implémentation :

1. **`price` est déjà un `int`**, dans la devise passée à `create_query(currency="CAD")`. Aucun
   texte à analyser. Le `parse_price` du plan (sept formats : `"$1,234"`, `"C$980"`…) n'a plus
   d'objet — ne l'écris pas.
2. **Il n'existe pas de champ `stops`** : les escales se dérivent, `len(flights) - 1`.
3. **`flights` ne contient que les segments de l'aller**, même en aller-retour. La date de retour
   est donc introuvable dans la réponse.
4. **`duration` est par segment, en minutes.** Aucune durée totale n'est fournie.
5. **`airlines` est une liste de noms** (`['Air Canada']`). `type` porte le code IATA de la
   compagnie principale, ou `'multi'`.
6. **`SimpleDatetime.time` est de longueur variable** : `[16, 40]` d'ordinaire, mais `[17]` quand
   les minutes sont nulles — observé sur 1 segment sur 12. Un `heure, minute = sd.time` lèverait
   `ValueError`. L'implémentation ci-dessous n'utilise pas l'heure et ne rencontre donc pas le
   piège ; **si tu ajoutes un jour l'heure de départ, souviens-toi de ce cas.**
   `SimpleDatetime.date` est toujours de longueur 3.
7. Il n'y a plus de `current_price` (l'indicateur *low/typical/high* de la v2). Ne le cherche pas.

## Décisions déjà prises — ne les rouvre pas

- **`duration_minutes` = somme des durées de segments**, soit le **temps de vol**, pas le
  porte-à-porte. Le calcul « arrivée − départ » est tentant et **faux** : les deux horodatages sont
  en heures locales de deux fuseaux différents, sans fuseau indiqué. Pour Air Canada ci-dessus il
  donnerait 19 h 10 là où le temps de vol est de 8 h 46.
- **`depart_date` vient du premier segment**, avec repli sur la requête si absente. Si Google
  décale le vol d'un jour, l'enregistrer sous la date demandée corromprait l'historique et ferait
  collisionner deux vols distincts sous la même `offer_hash`.
- **`return_date` vient de la requête** : absente de la réponse (point 3).
- **`max_stops` est appliqué deux fois** : poussé à Google via `create_query(max_stops=…)` et
  revérifié dans `to_offers`. Google n'est pas tenu d'honorer le paramètre ; la source est hostile,
  on ne la croit pas sur parole.
- **`to_offers` prend des dictionnaires**, pas les dataclasses de la bibliothèque : `search()`
  convertit via `dataclasses.asdict`. La fonction reste pure, testable hors réseau depuis la
  fixture, et `raw` est rempli gratuitement.
- **La devise n'est pas vérifiable.** Rien dans la charge utile ne l'atteste. C'est accepté : le
  plancher de crédibilité de 50 CAD (tâche 7) rattrape l'ordre de grandeur aberrant qu'un
  basculement de devise produirait. Écris-le en commentaire, ne bâtis pas de détection illusoire.

- [ ] **Étape 1 : le script de capture**

`scripts/capture_fixture.py` :

```python
"""Capture une réponse réelle d'une source et l'enregistre en fixture.

Usage : ./dev shell puis  python scripts/capture_fixture.py google_flights

Ce script touche le réseau ; il n'est jamais lancé par la suite de tests. Il sert à rafraîchir la
fixture le jour où Google change la forme de ses données — c'est-à-dire le jour où la source se met
à mentir en silence plutôt qu'à échouer franchement.
"""

import dataclasses
import json
import sys
from datetime import date, timedelta
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
FIXTURES = RACINE / "tests" / "fixtures"


def capture_google_flights() -> None:
    from fast_flights import FlightQuery, Passengers, create_query, get_flights

    depart = date.today() + timedelta(days=90)
    retour = depart + timedelta(days=10)

    requete = create_query(
        flights=[
            FlightQuery(date=depart.isoformat(), from_airport="YUL", to_airport="CDG"),
            FlightQuery(date=retour.isoformat(), from_airport="CDG", to_airport="YUL"),
        ],
        trip="round-trip",
        seat="economy",
        passengers=Passengers(adults=1),
        currency="CAD",
    )
    resultats = get_flights(requete)

    charge = {
        "query": {
            "origin": "YUL",
            "destination": "CDG",
            "depart": depart.isoformat(),
            "retour": retour.isoformat(),
        },
        "results": [dataclasses.asdict(vol) for vol in resultats],
    }
    FIXTURES.mkdir(parents=True, exist_ok=True)
    cible = FIXTURES / "google_flights_yul_cdg.json"
    cible.write_text(json.dumps(charge, indent=2, ensure_ascii=False, default=str))
    print(f"écrit : {cible}  ({len(charge['results'])} résultats)")


if __name__ == "__main__":
    source = sys.argv[1] if len(sys.argv) > 1 else "google_flights"
    if source == "google_flights":
        capture_google_flights()
    else:
        raise SystemExit(f"source inconnue : {source}")
```

- [ ] **Étape 2 : la fixture est déjà en place**

`tests/fixtures/google_flights_yul_cdg.json` contient la capture réelle du 4 août 2026 (4 résultats,
765 à 1134 CAD, escales 0 et 1). **Ne relance pas la capture** : la tâche doit rester rejouable hors
ligne, et une nouvelle capture changerait les valeurs sous les tests.

Vérifie seulement qu'elle est là :

```bash
./dev run python -c "import json; d=json.load(open('tests/fixtures/google_flights_yul_cdg.json')); print(len(d['results']), 'résultats')"
```

Si `./dev run` n'existe pas, utilise `./dev shell` puis la commande à l'intérieur.

- [ ] **Étape 3 : écrire le test qui échoue**

`tests/providers/test_google_flights.py` :

```python
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from scrappervol.core.types import SearchQuery, TripType
from scrappervol.providers.base import EmptyResultError
from scrappervol.providers.google_flights import GoogleFlightsProvider, to_offers

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "google_flights_yul_cdg.json"


def _segment(an: int, mois: int, jour: int, duree: int | None) -> dict:
    return {
        "from_airport": {"name": "Montréal", "code": "YUL"},
        "to_airport": {"name": "Paris", "code": "CDG"},
        # `time` à une seule composante : la forme réellement observée quand les minutes sont
        # nulles. Elle est ici pour que le cas soit déjà couvert le jour où l'heure sera lue.
        "departure": {"date": [an, mois, jour], "time": [17]},
        "arrival": {"date": [an, mois, jour + 1], "time": [5, 55]},
        "duration": duree,
        "plane_type": "Boeing 787",
    }


_SEGMENT = _segment(2026, 11, 2, 415)


@pytest.fixture
def donnees_reelles():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def requete(donnees_reelles):
    q = donnees_reelles["query"]
    return SearchQuery(
        origin=q["origin"],
        destination=q["destination"],
        depart_date=date.fromisoformat(q["depart"]),
        return_date=date.fromisoformat(q["retour"]),
        trip_type=TripType.ROUND_TRIP,
    )


def test_to_offers_traduit_la_fixture_reelle(donnees_reelles, requete):
    """Le contrat de base, mesuré sur des données que Google a réellement renvoyées."""
    offres = to_offers(donnees_reelles["results"], requete)

    assert len(offres) == len(donnees_reelles["results"])
    for offre in offres:
        assert offre.provider == "google_flights"
        assert offre.origin == "YUL"
        assert offre.destination == "CDG"
        assert offre.price_cad > 0
        assert offre.currency_original == "CAD"
        assert offre.airline
        assert offre.stops >= 0
        assert offre.deep_link.startswith("https://")


def test_to_offers_lit_le_prix_entier_sans_analyser_de_texte(donnees_reelles, requete):
    """fast-flights 3 rend un int : price_cad et price_original en découlent directement.

    Le plan prévoyait d'analyser des chaînes comme "C$1,234". Cette étape n'existe plus ; si
    quelqu'un la réintroduisait « pour être sûr », ce test le rattraperait.
    """
    offres = to_offers(donnees_reelles["results"], requete)
    attendus = [r["price"] for r in donnees_reelles["results"]]

    assert [o.price_cad for o in offres] == attendus
    assert [o.price_original for o in offres] == [float(p) for p in attendus]


def test_to_offers_derive_les_escales_du_nombre_de_segments(donnees_reelles, requete):
    """Il n'existe pas de champ `stops` : un vol direct a un segment."""
    offres = to_offers(donnees_reelles["results"], requete)
    attendu = [len(r["flights"]) - 1 for r in donnees_reelles["results"]]

    assert [o.stops for o in offres] == attendu
    assert 0 in attendu, "la fixture doit contenir au moins un vol direct"
    assert 1 in attendu, "la fixture doit contenir au moins un vol avec escale"


def test_to_offers_somme_les_durees_de_segments(donnees_reelles, requete):
    """duration_minutes est le temps de vol cumulé, jamais l'écart entre deux heures locales."""
    offres = to_offers(donnees_reelles["results"], requete)
    attendu = [sum(s["duration"] for s in r["flights"]) for r in donnees_reelles["results"]]

    assert [o.duration_minutes for o in offres] == attendu


def test_to_offers_prend_la_date_de_depart_du_premier_segment(donnees_reelles, requete):
    """La date réelle de l'offre prime sur la date demandée.

    Si Google décale le vol d'un jour, l'enregistrer sous la date demandée fabriquerait un
    historique qui compare deux vols différents sous la même empreinte.
    """
    an, mois, jour = donnees_reelles["results"][0]["flights"][0]["departure"]["date"]

    offres = to_offers(donnees_reelles["results"], requete)

    assert offres[0].depart_date == date(an, mois, jour)


def test_to_offers_garde_la_date_du_segment_quand_elle_differe_de_la_demande(requete):
    """Le cas qui distingue vraiment les deux sources de date.

    Dans la fixture réelle, Google renvoie le vol à la date demandée : les deux branches donnent
    alors le même résultat et le test précédent passe même si l'implémentation ignore la réponse.
    Ici la réponse s'écarte de la demande, ce qui est le cas qui compte — un vol décalé enregistré
    sous la date demandée fabriquerait un historique comparant deux vols différents sous la même
    empreinte.
    """
    assert requete.depart_date != date(2026, 11, 5), "la fixture doit différer de la date forcée"
    brut = [
        {
            "type": "AC",
            "price": 700,
            "airlines": ["Air Canada"],
            "flights": [_segment(2026, 11, 5, 415)],
        }
    ]

    (offre,) = to_offers(brut, requete)

    assert offre.depart_date == date(2026, 11, 5)


def test_to_offers_refuse_un_booleen_en_guise_de_prix(requete):
    """En Python, True est un int qui vaut 1 : sans garde explicite, il passerait pour un prix.

    Une offre à 1 CAD serait ensuite écartée par le plancher de crédibilité, mais elle aurait
    d'abord pollué l'historique des plus bas du jour — la médiane sert justement à survivre à ça,
    autant ne pas la mettre à l'épreuve pour rien.
    """
    brut = [{"type": "AC", "price": True, "airlines": ["Air Canada"], "flights": [_SEGMENT]}]

    assert to_offers(brut, requete) == []


def test_to_offers_se_rabat_sur_la_date_demandee_si_la_reponse_nen_porte_pas(requete):
    """Une date illisible ne doit pas faire disparaître l'offre : on retombe sur la requête."""
    segment = _segment(2026, 11, 2, 415)
    segment["departure"]["date"] = None
    brut = [{"type": "AC", "price": 700, "airlines": ["Air Canada"], "flights": [segment]}]

    (offre,) = to_offers(brut, requete)

    assert offre.depart_date == requete.depart_date


def test_to_offers_reporte_la_date_de_retour_de_la_requete(donnees_reelles, requete):
    """La réponse ne contient que l'aller : le retour ne peut venir que de la requête."""
    offres = to_offers(donnees_reelles["results"], requete)

    assert all(o.return_date == requete.return_date for o in offres)


def test_to_offers_joint_les_compagnies(requete):
    """airlines est une liste : un vol partagé ne doit pas perdre la moitié de son information."""
    brut = [
        {
            "type": "multi",
            "price": 700,
            "airlines": ["Air Canada", "Lufthansa"],
            "flights": [_segment(2026, 11, 2, 300), _segment(2026, 11, 3, 120)],
        }
    ]

    (offre,) = to_offers(brut, requete)

    assert offre.airline == "Air Canada, Lufthansa"


def test_to_offers_se_rabat_sur_le_type_si_les_compagnies_manquent(requete):
    """Une offre sans nom de compagnie reste exploitable : airline entre dans offer_hash."""
    brut = [{"type": "AC", "price": 700, "airlines": [], "flights": [_SEGMENT]}]

    (offre,) = to_offers(brut, requete)

    assert offre.airline == "AC"


def test_to_offers_conserve_la_reponse_brute(donnees_reelles, requete):
    """raw est la seule pièce à conviction le jour où un prix aberrant est enregistré."""
    offres = to_offers(donnees_reelles["results"], requete)

    assert offres[0].raw == donnees_reelles["results"][0]


@pytest.mark.parametrize(
    "brut",
    [
        {"type": "AC", "price": None, "airlines": ["Air Canada"], "flights": [_SEGMENT]},
        {"type": "AC", "price": 0, "airlines": ["Air Canada"], "flights": [_SEGMENT]},
        {"type": "AC", "price": -50, "airlines": ["Air Canada"], "flights": [_SEGMENT]},
        {"type": "AC", "price": "1 234", "airlines": ["Air Canada"], "flights": [_SEGMENT]},
        {"type": "AC", "price": 700, "airlines": ["Air Canada"], "flights": []},
    ],
    ids=["prix absent", "prix nul", "prix négatif", "prix texte", "sans segment"],
)
def test_to_offers_ignore_les_entrees_inexploitables(brut, requete):
    """Une entrée douteuse est écartée, jamais devinée : un prix inventé devient une fausse alerte,
    et une fausse alerte coûte plus cher qu'une offre manquée."""
    assert to_offers([brut], requete) == []


def test_to_offers_tolere_une_duree_absente(requete):
    """Sans durée fiable, duration_minutes vaut None — le champ est nullable exprès."""
    brut = [
        {
            "type": "AC",
            "price": 700,
            "airlines": ["Air Canada"],
            "flights": [_segment(2026, 11, 2, None)],
        }
    ]

    (offre,) = to_offers(brut, requete)

    assert offre.duration_minutes is None
    assert offre.price_cad == 700


def test_to_offers_respecte_le_plafond_descales(donnees_reelles):
    """Google n'est pas tenu d'honorer max_stops : on revérifie côté maison."""
    q = donnees_reelles["query"]
    requete_directe = SearchQuery(
        origin=q["origin"],
        destination=q["destination"],
        depart_date=date.fromisoformat(q["depart"]),
        return_date=date.fromisoformat(q["retour"]),
        max_stops=0,
    )

    offres = to_offers(donnees_reelles["results"], requete_directe)

    assert offres, "la fixture contient des vols directs"
    assert all(o.stops == 0 for o in offres)
    assert len(offres) < len(donnees_reelles["results"]), "des vols avec escale ont dû être écartés"


def test_le_provider_leve_empty_result_quand_rien_nest_exploitable(monkeypatch, requete):
    """Le silence doit être bruyant : c'est ce que la tâche 9 attend pour armer le disjoncteur."""
    source = GoogleFlightsProvider()
    monkeypatch.setattr(source, "_fetch", lambda query: [])

    with pytest.raises(EmptyResultError):
        source.search(requete)


def test_le_provider_traduit_toute_panne_en_provider_error(monkeypatch, requete):
    """fast-flights lève des exceptions non documentées : le runner attend un type maison."""

    def tombe(query):
        raise RuntimeError("connexion interrompue")

    source = GoogleFlightsProvider()
    monkeypatch.setattr(source, "_fetch", tombe)

    with pytest.raises(ProviderError, match="google_flights"):
        source.search(requete)


def test_le_provider_expose_son_nom():
    assert GoogleFlightsProvider().name == "google_flights"
```

N'oublie pas d'ajouter `ProviderError` à l'import de `scrappervol.providers.base`.

- [ ] **Étape 4 : lancer le test et vérifier l'échec**

```bash
./dev test tests/providers/test_google_flights.py 2>&1 | grep -v Container
```

Attendu : `ModuleNotFoundError: No module named 'scrappervol.providers.google_flights'`.
**Colle cette sortie dans ton rapport.**

- [ ] **Étape 5 : écrire l'implémentation**

`scrappervol/providers/google_flights.py` :

```python
from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any
from urllib.parse import quote_plus

from scrappervol.core.types import FlightOffer, SearchQuery, TripType
from scrappervol.providers.base import EmptyResultError, ProviderError

NOM = "google_flights"

# On demande cette devise à Google et on croit la réponse sur parole : rien dans la charge utile ne
# l'atteste. Un basculement silencieux vers l'USD passerait donc inaperçu ici — c'est le plancher de
# crédibilité de la détection qui rattrape l'ordre de grandeur aberrant.
DEVISE = "CAD"


def _date_du_segment(segment: Mapping[str, Any]) -> date | None:
    """Date de départ réelle d'un segment, ou None si la réponse ne la porte pas.

    `SimpleDatetime.date` est un triplet (année, mois, jour), toujours observé complet. On reste
    défensif : cette fonction lit une source hostile, pas une structure maison.
    """
    brut = (segment.get("departure") or {}).get("date")
    if not isinstance(brut, (list, tuple)) or len(brut) != 3:
        return None
    try:
        return date(int(brut[0]), int(brut[1]), int(brut[2]))
    except (TypeError, ValueError):
        return None


def _lien(query: SearchQuery, depart: date) -> str:
    """Lien de recherche Google Flights.

    fast-flights ne rend pas d'URL par offre : le lien pointe la recherche, pas le billet.
    """
    termes = f"Flights from {query.origin} to {query.destination} on {depart.isoformat()}"
    if query.return_date:
        termes += f" through {query.return_date.isoformat()}"
    return f"https://www.google.com/travel/flights?q={quote_plus(termes)}"


def to_offers(resultats: Sequence[Mapping[str, Any]], query: SearchQuery) -> list[FlightOffer]:
    """Traduit la réponse de fast-flights en offres du domaine.

    Fonction pure : ni réseau ni horloge, ce qui permet de la tester sur une capture réelle. Toute
    entrée dont le prix ou les segments sont inexploitables est **écartée**, jamais complétée par
    défaut : une offre devinée devient une fausse alerte, et une fausse alerte coûte plus cher que
    l'offre manquée.
    """
    offres: list[FlightOffer] = []
    for brut in resultats:
        prix = brut.get("price")
        if not isinstance(prix, int) or isinstance(prix, bool) or prix <= 0:
            continue

        segments = list(brut.get("flights") or ())
        if not segments:
            continue

        escales = len(segments) - 1
        if query.max_stops is not None and escales > query.max_stops:
            continue

        durees = [s.get("duration") for s in segments]
        duree = sum(durees) if all(isinstance(d, int) for d in durees) else None

        compagnies = [c for c in (brut.get("airlines") or ()) if c]
        compagnie = ", ".join(compagnies) if compagnies else str(brut.get("type") or "?")

        depart = _date_du_segment(segments[0]) or query.depart_date

        offres.append(
            FlightOffer(
                provider=NOM,
                origin=query.origin,
                destination=query.destination,
                depart_date=depart,
                return_date=query.return_date,
                price_cad=prix,
                price_original=float(prix),
                currency_original=DEVISE,
                airline=compagnie,
                stops=escales,
                duration_minutes=duree,
                deep_link=_lien(query, depart),
                raw=dict(brut),
            )
        )
    return offres


class GoogleFlightsProvider:
    """Source Google Flights, via fast-flights 3."""

    name = NOM

    def _fetch(self, query: SearchQuery) -> list[Mapping[str, Any]]:
        """Appelle la bibliothèque et rend des dictionnaires bruts. Isolé pour les tests."""
        from fast_flights import FlightQuery, Passengers, create_query, get_flights

        vols = [
            FlightQuery(
                date=query.depart_date.isoformat(),
                from_airport=query.origin,
                to_airport=query.destination,
            )
        ]
        if query.trip_type is TripType.ROUND_TRIP and query.return_date:
            vols.append(
                FlightQuery(
                    date=query.return_date.isoformat(),
                    from_airport=query.destination,
                    to_airport=query.origin,
                )
            )

        requete = create_query(
            flights=vols,
            trip="round-trip" if len(vols) == 2 else "one-way",
            seat="economy",
            passengers=Passengers(adults=query.passengers),
            currency=DEVISE,
            max_stops=query.max_stops,
        )
        return [dataclasses.asdict(vol) for vol in get_flights(requete)]

    def search(self, query: SearchQuery) -> list[FlightOffer]:
        try:
            bruts = self._fetch(query)
        except Exception as exc:  # noqa: BLE001 — traduire toute panne est le contrat de l'interface
            raise ProviderError(f"{NOM} : échec de la requête ({exc})") from exc

        offres = to_offers(bruts, query)
        if not offres:
            raise EmptyResultError(
                f"{NOM} : aucune offre exploitable pour "
                f"{query.origin}->{query.destination} le {query.depart_date}"
            )
        return offres
```

Le `# noqa: BLE001` de `search` est légitime, pour la même raison qu'à la tâche 9 : `fast-flights`
lève des exceptions non documentées, et les traduire en `ProviderError` est le contrat de
l'interface. C'est la **seule** exception à la règle « aucun noqa ».

- [ ] **Étape 6 : vérifier que les tests passent**

```bash
./dev test 2>&1 | grep -v Container
./dev lint 2>&1 | grep -v Container
```

État actuel : **114 tests**. Attends-toi à 130 environ après cette tâche.

- [ ] **Étape 7 : ajouter un test de fumée réseau marqué `live`**

Dans le même fichier de test :

```python
@pytest.mark.live
def test_fumee_reseau_google_flights():
    """Touche le vrai Google. Exclu de la suite par défaut : `./dev test -m live` pour le lancer.

    Ce test ne vérifie pas des valeurs — elles changent toutes les heures — mais que le contrat de
    forme tient encore. C'est le seul garde-fou contre le scénario que ce projet redoute : la source
    change de format, la suite reste verte sur sa fixture figée, et la veille s'éteint en silence.
    """
    depart = date.today() + timedelta(days=90)
    requete = SearchQuery(
        origin="YUL",
        destination="CDG",
        depart_date=depart,
        return_date=depart + timedelta(days=10),
    )

    offres = GoogleFlightsProvider().search(requete)

    assert offres
    assert all(o.price_cad > 0 for o in offres)
    assert all(o.airline for o in offres)
```

Déclare le marqueur dans `pyproject.toml`, sous `[tool.pytest.ini_options]`, et exclus-le par
défaut :

```toml
markers = ["live: touche le réseau réel ; exclu par défaut"]
addopts = "-m 'not live'"
```

Si `addopts` existe déjà, **complète-le** au lieu de l'écraser. Vérifie ensuite les deux sens :
`./dev test` ne doit pas lancer le test `live`, et `./dev test -m live` doit le lancer.

**Le test `live` a le droit d'échouer** (Google peut bloquer la requête) : ce n'est pas un blocage
pour la tâche. Rapporte simplement ce que tu observes.

- [ ] **Étape 8 : committer**

```
feat: source Google Flights via fast-flights 3

L'API v3 ne ressemble pas à la v2 que visait le plan : le prix est déjà un entier dans la
devise demandée, les escales se dérivent du nombre de segments, et la durée est par segment.
Le parseur de prix textuel prévu n'a plus d'objet.

to_offers est pure et testée sur une capture réelle, pour que la traduction reste vérifiable
sans réseau. Le test de fumée `live`, exclu par défaut, est le seul point qui détecte un
changement de format côté Google — celui qui laisserait la suite verte pendant que la veille
s'éteint.
```

## Pourquoi ce brief remplace celui du plan

Le plan visait `fast-flights` **2.x** : `get_flights(flight_data=[FlightData(…)], trip=…, seat=…,
fetch_mode="fallback")`, résultat portant `.flights` et `.current_price`, prix sous forme de texte.

`requirements.txt` demande `fast-flights>=2.2` et `requirements.lock.txt` a résolu **3.0.2**, dont
l'API est incompatible : `FlightData`, `Result`, `Flight` et `fetch_mode` n'existent plus ;
`create_query(...)` puis `get_flights(query)` les remplacent. Le script de capture du plan aurait
échoué dès l'import, et `to_offers` visait des champs absents.

| point | plan (v2) | réel (3.0.2) |
|---|---|---|
| prix | texte à analyser, 7 formats | `int`, devise demandée |
| escales | champ `stops` | `len(flights) - 1` |
| durée | champ direct | par segment, à sommer |
| compagnie | `airline: str` | `airlines: list[str]` |
| indicateur | `current_price` | absent |
| devise | conversion à faire | `currency="CAD"` à la requête |

**Vérifié, ne le re-teste pas à l'aveugle :** l'API ci-dessus a été appelée pour de bon, la fixture
en est le produit. Si un test échoue, regarde l'implémentation, pas le brief.

**Hors de ton périmètre :** `requirements.txt` porte encore `fast-flights>=2.2`, une borne qui
autorise une v2 incompatible. La resserrer touche au socle de la tâche 1 et sera tranchée à la
tâche 20. Signale-le dans ton rapport, ne le corrige pas ici.

## Tâche 11 : source Air Transat

Apporte l'inventaire charter et forfait, absent de Google Flights (§4 du design).

**Ce brief remplace intégralement celui extrait du plan.** Le plan supposait une URL de recherche
en GET qui **n'existe pas** : `https://www.airtransat.com/fr-CA/reservation-vol?...` renvoie
**HTTP 404 « Page introuvable »**. Vérifié en chargeant réellement le site. Voir la section finale.

## Cadre : essai borné, porte de sortie écrite d'avance

Cette tâche est un **essai**, pas une livraison garantie. Air Transat n'expose aucune page de
résultats paramétrable : il faut piloter un formulaire à autocomplétion puis attendre le rendu
d'une application JavaScript. C'est faisable, mais peut échouer sur une protection anti-robot que
rien ne permet d'anticiper.

**Budget : 90 minutes.** Au-delà, arrête, quel que soit le sentiment d'être « presque au but ».

**Critère de réussite, à ne pas renégocier en cours de route :** obtenir un HTML contenant au
moins **trois prix distincts en dollars canadiens** pour un aller-retour YUL→CUN, **deux fois de
suite, à au moins cinq minutes d'intervalle**. La double capture n'est pas une précaution
excessive : elle distingue une page réellement obtenue d'un coup de chance sur une session pas
encore signalée comme robot.

Le critère est écrit d'avance parce qu'à mi-essai on est toujours « presque au but », et que sans
seuil posé à froid on continue indéfiniment.

**Si le critère n'est pas atteint dans le budget :**
1. Écris `docs/superpowers/notes/2026-08-04-air-transat-abandon.md` : ce que tu as tenté, où ça a
   bloqué, la dernière capture HTML obtenue, et ce qu'il faudrait pour aboutir.
2. Ne committe **aucun** code de source Air Transat inutilisable. `playwright_base.py` est utile
   indépendamment : committe-le seul, avec ses tests.
3. Rends le verdict **`BLOCKED_BY_DESIGN`** en expliquant le critère et pourquoi il n'est pas atteint.

Ce n'est pas un échec de ta part : l'utilisateur a explicitement choisi « essai borné, repli
assumé ». Le repli est Google Flights seul, qui fonctionne déjà — disjoncteur et digest compris.
Une source en moins dégrade la couverture, elle ne casse rien. **Un abandon propre et documenté
vaut mieux qu'un scraper fragile qui rendra des données fausses en silence** : c'est précisément le
mode de panne que ce projet redoute le plus.

## Ce qui est déjà établi — ne le refais pas

Relevé en chargeant le site pour de bon depuis le conteneur :

- **Chromium fonctionne** dans l'image : il se lance, charge la page, rend 428 Ko. L'outillage
  n'est pas en cause.
- `https://www.airtransat.com/fr-CA/reservation-vol?...` → **404**. N'y retourne pas.
- `https://www.airtransat.com/fr-CA` → **200**, redirige vers `/fr-CA?search=flight`.
- Le seul formulaire de la page porte `action="/fr-CA" method="post"`. **Aucun** des 349 liens de
  la page ne mène à une page de résultats paramétrable.
- Le formulaire de recherche, champs visibles relevés sur pièce :

| champ | sélecteur |
|---|---|
| origine | `#departureOriginDropdown-input` (autocomplétion) |
| destination | `#departureDestinationDropdown-input` (autocomplétion) |
| date aller | `#datePickerDeparture` |
| date retour | `#datePickerReturn` |
| adultes | `#travelers-adults-input` |

Les champs d'aéroport sont des listes à autocomplétion : taper `YUL` ne suffit pas, il faut
**attendre la liste de suggestions puis en sélectionner une**, sinon le formulaire part avec un
champ vide. Même chose pour les dates, qui passent par un sélecteur de calendrier.

- [ ] **Étape 1 : `scrappervol/providers/playwright_base.py`**

Ce module est utile même si Air Transat échoue : écris-le et teste-le en premier.

```python
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scrappervol.config import Settings
from scrappervol.providers.base import ProviderError

logger = logging.getLogger(__name__)

AGENT_UTILISATEUR = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def debug_path(settings: Settings, provider_name: str) -> Path:
    dossier = Path(settings.data_dir) / "debug"
    dossier.mkdir(parents=True, exist_ok=True)
    return dossier / f"{provider_name}.html"


def fetch_html(
    url: str,
    settings: Settings,
    provider_name: str,
    wait_selector: str | None = None,
    interact: Callable[[Any], None] | None = None,
    stealth: bool = False,
    timeout_ms: int = 45_000,
) -> str:
    """Charge une page avec Chromium et retourne son HTML, en conservant une capture de débogage.

    `interact` reçoit la page après chargement et avant `wait_selector` : c'est par là qu'on
    remplit un formulaire quand le site n'expose pas de page de résultats adressable par URL.

    La capture de débogage est écrite dans tous les cas, succès comme échec : c'est elle qui permet
    de réparer une dérive de sélecteur sans avoir à reproduire le problème (§10 du design). Une
    dérive de sélecteur ne lève pas d'exception — elle rend une liste vide, ce qui ressemble
    exactement à « pas de vol disponible ».
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as erreur:
        raise ProviderError(f"Playwright indisponible : {erreur}") from erreur

    html = ""
    try:
        with sync_playwright() as playwright:
            navigateur = playwright.chromium.launch(headless=True)
            contexte = navigateur.new_context(
                user_agent=AGENT_UTILISATEUR,
                viewport={"width": 1440, "height": 900},
                locale="fr-CA",
                timezone_id=settings.timezone,
            )
            page = contexte.new_page()
            if stealth:
                page.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                )
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            if interact is not None:
                interact(page)
            if wait_selector:
                page.wait_for_selector(wait_selector, timeout=timeout_ms)
            html = page.content()
            contexte.close()
            navigateur.close()
    except Exception as erreur:  # noqa: BLE001 — traduit vers l'exception du domaine
        if html:
            debug_path(settings, provider_name).write_text(html, encoding="utf-8")
        raise ProviderError(f"échec du chargement de {url} : {erreur}") from erreur

    debug_path(settings, provider_name).write_text(html, encoding="utf-8")
    return html
```

Tests, dans `tests/providers/test_playwright_base.py` — ils ne lancent aucun navigateur :

```python
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
```

Vérifie le RED, puis le vert, puis **committe cette étape séparément** : elle est acquise quoi
qu'il arrive ensuite.

```
feat: socle Playwright partagé avec capture de débogage
```

- [ ] **Étape 2 : l'essai — atteindre une page de résultats**

Ceci est de la **reconnaissance**, pas du TDD : tu explores un site que personne n'a cartographié.
Travaille dans un script jetable, pas dans la suite de tests.

Écris `/tmp/essai_transat.py` (hors dépôt) et lance-le par
`docker compose run --rm app python /tmp/essai_transat.py`. Procède par paliers, en imprimant ce
que tu observes à chaque étape plutôt qu'en enchaînant à l'aveugle :

1. Charger `https://www.airtransat.com/fr-CA?search=flight`, attendre le rendu.
2. Remplir `#departureOriginDropdown-input` avec `YUL`, **attendre la liste de suggestions**,
   imprimer ce qu'elle contient, en sélectionner une.
3. Idem pour `#departureDestinationDropdown-input` avec `CUN`.
4. Remplir les dates. Si le calendrier refuse la saisie directe, il faudra naviguer dedans :
   imprime sa structure avant de deviner.
5. Soumettre, attendre la navigation, **imprimer l'URL d'arrivée** — si elle contient les
   paramètres de recherche, note-la : elle rendrait les passages suivants adressables directement,
   ce qui simplifierait tout.
6. Imprimer le nombre de motifs de prix trouvés dans le HTML final.

Pour repérer les prix sans connaître les sélecteurs :

```python
import re
prix = re.findall(r"\d[\d\s ,]{2,}\s*\$", html)
print(f"{len(prix)} motifs de prix, dont : {prix[:8]}")
```

Écris la capture obtenue dans un fichier à chaque tentative, même ratée : c'est la seule façon de
comprendre un blocage anti-robot, qui se manifeste par une page plausible et vide, pas par une
erreur.

- [ ] **Étape 3 : la porte de sortie**

Compare ce que tu as obtenu au critère écrit en tête de ce brief : **trois prix distincts, deux
fois de suite, à cinq minutes d'intervalle.**

- **Critère atteint** → continue à l'étape 4.
- **Critère non atteint dans les 90 minutes** → applique la procédure d'abandon décrite en tête,
  rends `BLOCKED_BY_DESIGN`, et arrête-toi là. N'improvise pas une source à moitié fonctionnelle.

Sois honnête sur ce point : c'est le seul endroit du projet où s'arrêter est un résultat valide.

- [ ] **Étape 4 : figer la fixture** *(si et seulement si le critère est atteint)*

Ajoute à `scripts/capture_fixture.py` une fonction `capture_transat()` qui rejoue exactement la
séquence d'interaction qui a marché et écrit `tests/fixtures/transat_yul_cun.html`. Étends le point
d'entrée du script pour accepter `transat`.

Comme pour Google Flights, la fixture est capturée **une fois** et versionnée : la suite de tests
doit rester rejouable hors ligne.

- [ ] **Étape 5 : relever les vrais sélecteurs**

Ouvre la fixture et identifie : le conteneur d'un résultat, le nœud du prix, celui des escales,
celui de la durée, celui de la compagnie.

**N'invente aucun sélecteur — lis-les dans la fixture.** Le brief que celui-ci remplace proposait
`"[data-testid='flight-result'], .flight-result, article.flight"` : trois variantes en alternative,
ce qui est l'aveu qu'aucune n'avait été vérifiée. Un sélecteur inventé qui ne correspond à rien
produit une liste vide, c'est-à-dire un scraper qui annonce « aucun vol » pour toujours.

Note-les dans ton rapport, avec l'extrait de HTML qui les justifie.

- [ ] **Étape 6 : les tests, puis l'implémentation**

Écris d'abord `tests/providers/test_transat.py` contre la fixture, en suivant le modèle de la
tâche 10 : une fonction pure `parse_results(html, query) -> list[FlightOffer]`, testée hors ligne,
et une classe `TransatProvider` qui l'alimente.

Les mêmes exigences qu'à la tâche 10 s'appliquent, et elles y ont toutes attrapé un défaut réel :

- Une entrée dont le prix est illisible est **écartée**, jamais devinée.
- `search` traduit l'absence de résultat en `EmptyResultError` — le silence doit être bruyant,
  c'est ce que le disjoncteur de la tâche 9 attend pour s'armer.
- **Un test dont l'assertion serait vraie même si l'implémentation ignorait la donnée testée ne
  teste rien.** À la tâche 10, le test de la date de départ passait par coïncidence, la fixture
  portant la même date que la requête. Pour chaque champ que tu extrais du HTML, demande-toi : si
  je remplaçais cette extraction par une valeur reprise de la requête, mon test rougirait-il ?
- Prévois le cas « le site a changé » : un HTML sans aucun résultat doit donner une liste vide,
  pas une exception obscure.

- [ ] **Étape 7 : committer**

```
feat: source Air Transat par pilotage du formulaire

Le site n'expose aucune page de résultats adressable par URL — celle que supposait le plan
renvoie un 404. La recherche passe donc par le formulaire à autocomplétion de l'accueil,
piloté par Playwright, et la fixture est capturée une fois pour que l'analyse reste
vérifiable hors ligne.
```

## Pourquoi ce brief remplace celui du plan

Le plan prévoyait `build_search_url(query)` produisant
`https://www.airtransat.com/fr-CA/reservation-vol?origin=YUL&destination=CUN&departureDate=…`,
puis une capture de cette URL et un relevé de sélecteurs dans le HTML obtenu.

Vérifié en chargeant l'adresse : **HTTP 404, titre « Page not found / Page introuvable »**, zéro
motif de prix, zéro nœud correspondant à l'un quelconque des sélecteurs prévus. La fixture aurait
donc été une page d'erreur, et l'étape « relever les sélecteurs dans la fixture » aurait porté sur
du vide — sans que rien ne le signale, le brief ne prévoyant aucune vérification du contenu obtenu.

Les sélecteurs du plan étaient de toute façon inventés :
`"[data-testid='flight-result'], .flight-result, article.flight"`. Ils sont remplacés ici par une
étape de relevé sur pièce.

**Ce qui reste vrai du plan :** l'architecture. `playwright_base.py` avec sa capture de débogage,
la séparation entre une fonction d'analyse pure et une classe qui l'alimente, la traduction des
pannes en `ProviderError`. Seule la façon d'atteindre la page de résultats change.

## Résultat de l'essai borné

**Critère de réussite atteint.** Deux passages réels indépendants, à 7 min 32 s d'intervalle
(15 h 38 et 15 h 45, heure de l'Est, le 4 août 2026), ont chacun produit une page de résultats
portant les mêmes trois prix CAD distincts pour YUL→CUN : 297, 687, 1 229. Le pilotage du
formulaire fonctionne et la fixture est versionnée.

**Mais le prix obtenu n'est pas comparable à celui des autres sources.** La page atteinte est
l'étape « departure » du parcours de réservation : elle affiche un prix « à partir de » pour le
**vol aller seul**, avant le choix du vol retour. `google_flights.py` rend au contraire un prix
aller-retour déjà agrégé.

Cela compte parce que `DailyLow` a pour clé primaire `(route_id, day)` — **sans le provider** —
et que `upsert_daily_low` ne conserve que le prix le plus bas, toutes sources confondues. Un prix
aller-seul étant structurellement plus bas qu'un aller-retour sur le même trajet, activer cette
source ferait de Transat le plus bas du jour en permanence. L'historique s'aligne alors sur des
prix aller-seul, la médiane de référence s'effondre, et **plus aucune aubaine aller-retour ne peut
être détectée** : la baisse réelle reste au-dessus d'une référence devenue artificiellement basse.

Le système paraîtrait sain — santé des sources au vert, digest quotidien envoyé — tout en ne
détectant plus jamais rien. C'est la panne silencieuse que le design nomme comme risque principal,
et elle serait provoquée par notre propre code.

**Conséquence retenue :** `transat` est retiré de `enabled_providers` par défaut. Le code, ses
tests et la fixture restent en place, prêts à servir si le parcours est un jour poussé jusqu'au
prix aller-retour total. La couverture n'en souffre presque pas : la capture réelle de la tâche 10
montre que Google Flights renvoie déjà les vols Air Transat (TS à 1 134 CAD sur YUL→CDG), avec un
prix agrégé et comparable.

**Précision d'usage, donnée par l'utilisateur le 5 août 2026 :** ScrapperVol surveille des
voyages de **vacances**, donc des aller-retours. Un prix de vol aller seul n'est pas une donnée
imparfaite qu'on pourrait exploiter faute de mieux — elle ne répond pas à la question posée. Ce
n'est donc pas seulement l'incomparabilité entre sources qui disqualifie cette page, c'est le fait
qu'elle ne mesure pas ce qu'on cherche.

Cette précision durcit le critère de réussite de toute source à venir : il porte désormais sur la
**nature** du prix obtenu — aller-retour total — et pas seulement sur sa présence et sa stabilité.

À reprendre à la **tâche 20** (mise en service) si la source est un jour réactivée.

## Tâche 12 : essai borné Air Canada

### Cadre — à lire avant toute chose

Cette tâche est un **essai borné**, pas une implémentation garantie. Elle a une porte de sortie
explicite et l'emprunter est un résultat honorable, pas un échec personnel.

**Budget : 90 minutes de travail effectif.** Passé ce délai sans avoir atteint le critère ci-dessous,
applique la procédure d'abandon et arrête-toi. Ne rogne pas le critère pour rentrer dans le budget :
mieux vaut un abandon net qu'une source qui ment.

### Critère de réussite — écrit d'avance, non négociable

Deux conditions, toutes deux nécessaires :

1. **Stabilité** : deux chargements de la page de résultats, espacés d'au moins cinq minutes,
   produisent chacun au moins trois prix en CAD pour le même trajet aller-retour.

2. **Nature du prix** : le prix obtenu est un **total aller-retour**, pas un prix de vol aller.

La condition 2 est celle qui a fait échouer la tâche 11, et c'est la plus importante. ScrapperVol
surveille des voyages de **vacances** : un prix de vol aller seul ne répond pas à la question posée.
Ce n'est pas une donnée dégradée qu'on pourrait exploiter faute de mieux, c'est une donnée hors
sujet. S'y ajoute une raison technique : `daily_low` étant indexé par `(route_id, day)` **sans le
provider** et ne conservant que le prix le plus bas, une source aller-seul deviendrait le plus bas
permanent du trajet — médiane de référence effondrée, aubaines indétectables, et aucun voyant au
rouge pour le signaler.

**Comment vérifier la condition 2 sans te fier à l'apparence de la page.** Beaucoup de sites de
compagnies affichent « à partir de 297 $ » sur un écran qui est en réalité l'étape de choix du vol
aller. Utilise un recoupement externe :

```
Air Canada,     YUL→CUN, départ J+90, retour J+97, 1 adulte → prix relevé : X
Google Flights, même trajet, mêmes dates, vols AC           → prix relevé : Y
```

Si `X` est du même ordre que `Y` (entre 0,75·Y et 1,25·Y), c'est un aller-retour. Si `X` tourne
autour de la moitié de `Y`, c'est un aller seul → **condition 2 non remplie → abandon**.

`GoogleFlightsProvider` (tâche 10, `scrappervol/providers/google_flights.py`) te donne `Y` en
quelques lignes. Fais ce relevé **avant** de conclure, et écris les deux nombres dans ton rapport.

### Procédure d'abandon

Si le budget est épuisé, ou si l'une des deux conditions n'est pas remplie :

1. Écris `docs/superpowers/notes/2026-08-05-air-canada-abandon.md` : URL essayées, ce que tu as
   observé (page de défi anti-bot, CAPTCHA, blocage d'adresse IP, prix aller-seul, formulaire
   impilotable…), les nombres X et Y si tu les as, et la raison exacte de l'arrêt.
2. Ne touche pas à `enabled_providers` : `air_canada` n'y figure déjà plus.
3. Commit : `docs: essai Air Canada infructueux, source écartée`.
4. Rends un verdict `BLOCKED_BY_DESIGN` en expliquant ce qui bloque.

Cette note vaut mieux que la mémoire : dans six mois, elle évitera de refaire l'essai à l'aveugle.

### Ce que tu sais déjà, et qui doit guider l'essai

**Ne présume pas d'une URL adressable.** Le brief d'origine prévoyait `build_search_url(query)`
produisant une URL de résultats. C'est exactement ce que le plan supposait pour Air Transat, et
l'URL en question renvoyait un **404** : la fixture aurait été une page d'erreur et le relevé des
sélecteurs aurait porté sur du vide, sans que rien ne le signale. **Vérifie d'abord ce que tu
obtiens réellement**, avant d'écrire quoi que ce soit qui en dépende. Si l'accès passe par le
formulaire, `fetch_html` accepte un paramètre `interact` (voir `transat.py`).

**Le trajet d'essai est un aller-retour.** Le brief d'origine proposait
`SearchQuery(origin="YUL", destination="YYZ", depart_date=date(2027, 2, 10))` — sans `return_date`,
donc un aller simple. C'est le piège même que cette tâche doit éviter. Utilise **YUL→CUN,
aller-retour, départ J+90, retour J+97, 1 adulte**.

**Air Canada est réputé mieux protégé qu'Air Transat.** Un défi anti-bot ou un blocage est un
résultat plausible et acceptable — c'est la raison d'être de la porte de sortie. `fetch_html`
accepte `stealth=True`.

**Sauvegarde le HTML à chaque tentative, même ratée.** `playwright_base.py` écrit déjà une capture
de débogage. Une page de défi sauvegardée est une preuve ; la même page non sauvegardée n'est
qu'une impression.

**Sois économe en requêtes.** Deux chargements espacés de cinq minutes suffisent au critère. Une
rafale de tentatives est le meilleur moyen de faire bloquer l'adresse IP et de rendre le verdict
ininterprétable.

### Étapes

- [ ] **Étape 1 : reconnaissance**

Dans `/tmp/essai_air_canada.py`, hors du dépôt. Procède par paliers en imprimant ce que tu observes
à chaque fois : combien de nœuds correspondent, combien de prix trouvés, quel titre de page. Ne
passe au palier suivant qu'une fois le précédent confirmé.

- [ ] **Étape 2 : relevé croisé et porte de sortie**

Relève `X` (Air Canada) et `Y` (Google Flights, vols AC, mêmes dates). Écris les deux dans ton
rapport. Applique le critère. Si l'une des conditions manque → procédure d'abandon, et tu t'arrêtes.

- [ ] **Étape 3, cas de réussite : capturer la fixture**

Une seule capture, versionnée dans `tests/fixtures/air_canada_yul_cun.html`, plus une fonction
`capture_air_canada()` dans `scripts/capture_fixture.py`, sur le modèle de `capture_transat()`.

- [ ] **Étape 4, cas de réussite : relever les vrais sélecteurs**

Lis-les **dans la fixture**, n'en invente aucun. Le brief d'origine proposait
`"[data-testid='flight-result'], .flight-result, article.flight"` : trois variantes en alternative,
ce qui est l'aveu qu'aucune n'avait été vérifiée. Note chaque sélecteur retenu dans ton rapport avec
l'extrait de HTML qui le justifie.

- [ ] **Étape 5, cas de réussite : tests puis implémentation**

`tests/providers/test_air_canada.py` d'abord, sur le modèle de `test_transat.py` : une fonction pure
`parse_results(html, query) -> list[FlightOffer]` testable hors ligne, une classe
`AirCanadaProvider` qui l'alimente, un test `live` marqué `@pytest.mark.live`.

Quatre exigences, chacune ayant attrapé un défaut réel aux tâches 10 et 11 :

- **Une assertion vraie même si l'implémentation ignorait la donnée testée ne teste rien.** À la
  tâche 10, le test de la date de départ passait par coïncidence, la fixture portant la même date
  que la requête. Pour chaque champ extrait du HTML, demande-toi : si je remplaçais cette extraction
  par une valeur reprise de la requête, mon test rougirait-il ? Si non, change la fixture ou la
  requête jusqu'à ce qu'il rougisse.
- **Ne matche pas un message d'erreur sur une sous-chaîne qui apparaît ailleurs.** À la tâche 11,
  `pytest.raises(ProviderError, match="transat")` passait même sans la garde testée, parce que le
  message d'échec de `fetch_html` contient l'URL `airtransat.com`. Pour tester une garde qui doit
  agir **avant** le réseau, remplace `fetch_html` par une sentinelle qui lève `AssertionError` si
  elle est appelée — voir `_interdire_le_reseau` dans `tests/providers/test_transat.py`.
- Un prix illisible est **écarté**, jamais deviné ni remplacé par une valeur par défaut.
- `search` traduit l'absence de résultat en `EmptyResultError` : le silence doit être bruyant, c'est
  ce que le disjoncteur de la tâche 9 attend pour s'armer.

- [ ] **Étape 6, cas de réussite : activer la source**

Ajoute `"air_canada"` à `enabled_providers` dans `scrappervol/config.py` — et seulement là. Ajoute
un test qui verrouille cette activation en disant **pourquoi** elle est justifiée, en citant les
deux nombres X et Y du relevé croisé. Le commentaire existant dans `config.py` explique pourquoi
`transat` en est absent : complète-le, ne l'efface pas.

- [ ] **Étape 7 : committer**

```
feat: source Air Canada

<une ligne sur la façon dont la page de résultats est atteinte>
Prix vérifié aller-retour total par recoupement avec Google Flights : X CAD contre Y CAD pour
les mêmes vols aux mêmes dates.
```

### Rappels d'environnement

- Tout passe par `./dev test`, `./dev lint`, `./dev fmt`. Jamais `pytest` ni `ruff` sur l'hôte.
- **Jamais `./dev build`** (dix minutes).
- Filtre la sortie avec `grep -v Container`, **jamais** `grep -v "^\["`.
- Aucun `# noqa`, sauf `BLE001` sur les `except Exception` d'isolation des sources.
- La suite est à 169 tests, 2 deselected, lint propre. Laisse-la dans cet état ou meilleur.


## Tâche 13 : construction et rendu des courriels

Sépare nettement ce qui est calculé de ce qui est envoyé : le rendu est pur, donc vérifiable ligne à
ligne sans ouvrir un serveur SMTP.

**Fichiers :**
- Créer : `scrappervol/notify/render.py`
- Créer : `scrappervol/notify/templates/digest.html.j2`, `digest.txt.j2`, `exception.html.j2`,
  `exception.txt.j2`
- Test : `tests/notify/test_render.py`

**Interfaces :**
- Consomme : rien hors de la bibliothèque standard et Jinja2.
- Produit :
  - `RenderedMail(subject: str, html: str, text: str)`
  - `RouteBlock(label, price_cad, airline, origin, destination, depart_date, return_date, provider,
    deep_link, median_price, gap_vs_median, gap_vs_yesterday, is_find, history_building)`
  - `ProviderStatus(name, last_success_at, consecutive_failures, hours_silent, is_stale)`
  - `DigestData(day, blocks, providers)` avec les propriétés `find_count: int`,
    `has_stale_provider: bool`, `sorted_blocks: list[RouteBlock]`
  - `ExceptionData(label, origin, destination, depart_date, return_date, price_cad, airline, provider,
    deep_link, median_price, gap_vs_median, history_days)`
  - `render_digest(data: DigestData) -> RenderedMail`
  - `render_exception(data: ExceptionData) -> RenderedMail`

- [ ] **Étape 1 : écrire le test qui échoue**

`tests/notify/test_render.py` :

```python
from datetime import UTC, date, datetime, timedelta

from scrappervol.notify.render import (
    DigestData,
    ExceptionData,
    ProviderStatus,
    RouteBlock,
    render_digest,
    render_exception,
)

JOUR = date(2026, 8, 4)
MAINTENANT = datetime(2026, 8, 4, 18, 0, tzinfo=UTC)


def _bloc(**surcharges) -> RouteBlock:
    base = {
        "label": "Paris au printemps",
        "price_cad": 480,
        "airline": "Air Transat",
        "origin": "YUL",
        "destination": "CDG",
        "depart_date": date(2027, 3, 12),
        "return_date": date(2027, 3, 22),
        "provider": "google_flights",
        "deep_link": "https://example.com/offre",
        "median_price": 600.0,
        "gap_vs_median": 0.20,
        "gap_vs_yesterday": -35,
        "is_find": True,
        "history_building": False,
    }
    return RouteBlock(**{**base, **surcharges})


def _sante(**surcharges) -> ProviderStatus:
    base = {
        "name": "google_flights",
        "last_success_at": MAINTENANT - timedelta(hours=2),
        "consecutive_failures": 0,
        "hours_silent": 2.0,
        "is_stale": False,
    }
    return ProviderStatus(**{**base, **surcharges})


def test_le_sujet_annonce_le_nombre_de_trouvailles():
    donnees = DigestData(day=JOUR, blocks=[_bloc(), _bloc(is_find=False)], providers=[_sante()])

    assert render_digest(donnees).subject == "ScrapperVol — 1 trouvaille du 2026-08-04"


def test_le_sujet_accorde_le_pluriel():
    donnees = DigestData(day=JOUR, blocks=[_bloc(), _bloc()], providers=[_sante()])

    assert "2 trouvailles" in render_digest(donnees).subject


def test_le_digest_part_meme_sans_trouvaille():
    donnees = DigestData(day=JOUR, blocks=[_bloc(is_find=False)], providers=[_sante()])

    rendu = render_digest(donnees)

    assert "0 trouvaille" in rendu.subject
    assert rendu.html
    assert rendu.text


def test_le_digest_montre_le_prix_le_transporteur_et_le_lien():
    rendu = render_digest(DigestData(day=JOUR, blocks=[_bloc()], providers=[_sante()]))

    assert "480" in rendu.html
    assert "Air Transat" in rendu.html
    assert "https://example.com/offre" in rendu.html
    assert "Paris au printemps" in rendu.html


def test_le_digest_affiche_lecart_a_la_mediane():
    rendu = render_digest(DigestData(day=JOUR, blocks=[_bloc()], providers=[_sante()]))

    assert "20" in rendu.html


def test_les_trajets_sont_tries_par_ecart_decroissant():
    faible = _bloc(label="Faible", gap_vs_median=0.05)
    forte = _bloc(label="Forte", gap_vs_median=0.35)
    donnees = DigestData(day=JOUR, blocks=[faible, forte], providers=[_sante()])

    assert [b.label for b in donnees.sorted_blocks] == ["Forte", "Faible"]


def test_un_trajet_sans_historique_significatif_est_signale_et_non_classe():
    en_construction = _bloc(
        label="Neuf", median_price=None, gap_vs_median=None, history_building=True, is_find=False
    )
    donnees = DigestData(day=JOUR, blocks=[_bloc(), en_construction], providers=[_sante()])

    rendu = render_digest(donnees)

    assert "historique en constitution" in rendu.html
    assert donnees.sorted_blocks[-1].label == "Neuf"


def test_un_trajet_en_construction_ne_compte_pas_comme_trouvaille():
    donnees = DigestData(
        day=JOUR,
        blocks=[_bloc(history_building=True, is_find=True, gap_vs_median=None)],
        providers=[_sante()],
    )

    assert donnees.find_count == 0


def test_le_pied_porte_toujours_letat_des_sources():
    donnees = DigestData(
        day=JOUR,
        blocks=[_bloc()],
        providers=[_sante(name="google_flights"), _sante(name="transat"), _sante(name="air_canada")],
    )

    rendu = render_digest(donnees)

    assert "google_flights" in rendu.html
    assert "transat" in rendu.html
    assert "air_canada" in rendu.html
    assert "google_flights" in rendu.text


def test_une_source_muette_depuis_48h_declenche_un_bandeau_en_tete():
    """Le risque principal n'est pas la panne, mais le digest fidèle annonçant qu'il n'y a rien."""
    donnees = DigestData(
        day=JOUR,
        blocks=[_bloc()],
        providers=[_sante(), _sante(name="transat", hours_silent=72.0, is_stale=True)],
    )

    rendu = render_digest(donnees)

    assert donnees.has_stale_provider is True
    assert "transat" in rendu.html
    assert rendu.html.index("muette") < rendu.html.index("Paris au printemps")


def test_sans_source_muette_aucun_bandeau():
    donnees = DigestData(day=JOUR, blocks=[_bloc()], providers=[_sante()])

    assert "muette" not in render_digest(donnees).html


def test_la_version_texte_ne_contient_aucune_balise():
    rendu = render_digest(DigestData(day=JOUR, blocks=[_bloc()], providers=[_sante()]))

    assert "<p" not in rendu.text
    assert "</" not in rendu.text


def test_la_version_texte_nest_pas_echappee():
    """L'échappement HTML sur le gabarit texte transformerait les apostrophes en entités."""
    rendu = render_digest(
        DigestData(day=JOUR, blocks=[_bloc(label="Paris l'hiver")], providers=[_sante()])
    )

    assert "Paris l'hiver" in rendu.text
    assert "&#39;" not in rendu.text


def test_le_sujet_dexception_porte_la_destination_le_prix_et_lecart():
    donnees = ExceptionData(
        label="Paris au printemps",
        origin="YUL",
        destination="CDG",
        depart_date=date(2027, 3, 12),
        return_date=date(2027, 3, 22),
        price_cad=299,
        airline="Air Transat",
        provider="google_flights",
        deep_link="https://example.com/offre",
        median_price=600.0,
        gap_vs_median=0.50,
        history_days=45,
    )

    rendu = render_exception(donnees)

    assert rendu.subject == "ScrapperVol — CDG à 299 $ (50 % sous la médiane)"
    assert "299" in rendu.html
    assert "https://example.com/offre" in rendu.html
    assert "45" in rendu.html
```

- [ ] **Étape 2 : lancer le test et vérifier l'échec**

```bash
./dev test tests/notify/test_render.py -v
```

Attendu : `ModuleNotFoundError: No module named 'scrappervol.notify.render'`.

- [ ] **Étape 3 : écrire les gabarits**

`scrappervol/notify/templates/digest.html.j2` :

```jinja
<!doctype html>
<html lang="fr">
<head><meta charset="utf-8"><title>{{ subject }}</title></head>
<body style="font-family: -apple-system, Segoe UI, Roboto, sans-serif; color: #222; max-width: 680px;">
{% if data.has_stale_provider %}
<p style="background:#fdecea;border-left:4px solid #c0392b;padding:12px;">
  Attention : une source est <strong>muette</strong> depuis plus de 48 h —
  {% for p in data.providers if p.is_stale %}{{ p.name }}{% if not loop.last %}, {% endif %}{% endfor %}.
  Les prix ci-dessous sont donc incomplets.
</p>
{% endif %}

<h1 style="font-size:20px;">{{ data.find_count }} trouvaille{{ 's' if data.find_count > 1 else '' }}
  le {{ data.day }}</h1>

{% if not data.blocks %}
<p>Aucun trajet actif.</p>
{% endif %}

{% for bloc in data.sorted_blocks %}
<div style="border-top:1px solid #eee;padding:12px 0;">
  <h2 style="font-size:16px;margin:0 0 4px;">
    {{ bloc.label }}
    {% if bloc.is_find and not bloc.history_building %}
      <span style="color:#27ae60;">— trouvaille</span>
    {% endif %}
  </h2>
  {% if bloc.price_cad is none %}
    <p style="margin:0;color:#888;">Aucun prix relevé aujourd'hui.</p>
  {% else %}
    <p style="margin:0;font-size:18px;"><strong>{{ bloc.price_cad }} $</strong>
      — {{ bloc.airline }} · {{ bloc.origin }} → {{ bloc.destination }}</p>
    <p style="margin:4px 0;color:#555;">
      {{ bloc.depart_date }}{% if bloc.return_date %} → {{ bloc.return_date }}{% endif %}
      · relevé sur {{ bloc.provider }}
    </p>
    {% if bloc.history_building %}
      <p style="margin:4px 0;color:#888;">historique en constitution</p>
    {% else %}
      <p style="margin:4px 0;color:#555;">
        {{ (bloc.gap_vs_median * 100) | round | int }} % sous la médiane de 90 jours
        ({{ bloc.median_price | round | int }} $)
        {% if bloc.gap_vs_yesterday is not none %}
          · {{ bloc.gap_vs_yesterday }} $ par rapport à hier
        {% endif %}
      </p>
    {% endif %}
    <p style="margin:8px 0;"><a href="{{ bloc.deep_link }}">Voir l'offre</a></p>
  {% endif %}
</div>
{% endfor %}

<hr style="border:none;border-top:1px solid #eee;margin-top:24px;">
<h3 style="font-size:13px;color:#666;">État des sources</h3>
<ul style="font-size:13px;color:#666;">
{% for p in data.providers %}
  <li>{{ p.name }} —
    {% if p.last_success_at %}dernier succès il y a {{ p.hours_silent | round(1) }} h{% else %}jamais{% endif %}
    {% if p.consecutive_failures %} · {{ p.consecutive_failures }} échec(s) consécutif(s){% endif %}
  </li>
{% endfor %}
</ul>
</body>
</html>
```

`scrappervol/notify/templates/digest.txt.j2` :

```jinja
{% if data.has_stale_provider %}ATTENTION : source muette depuis plus de 48 h — {% for p in data.providers if p.is_stale %}{{ p.name }}{% if not loop.last %}, {% endif %}{% endfor %}. Les prix ci-dessous sont incomplets.

{% endif %}{{ data.find_count }} trouvaille{{ 's' if data.find_count > 1 else '' }} le {{ data.day }}
{% if not data.blocks %}
Aucun trajet actif.
{% endif %}
{% for bloc in data.sorted_blocks %}
{{ bloc.label }}{% if bloc.is_find and not bloc.history_building %} — TROUVAILLE{% endif %}

{% if bloc.price_cad is none %}  aucun prix relevé aujourd'hui
{% else %}  {{ bloc.price_cad }} $ — {{ bloc.airline }} · {{ bloc.origin }} vers {{ bloc.destination }}
  {{ bloc.depart_date }}{% if bloc.return_date %} au {{ bloc.return_date }}{% endif %} · source {{ bloc.provider }}
{% if bloc.history_building %}  historique en constitution
{% else %}  {{ (bloc.gap_vs_median * 100) | round | int }} % sous la médiane de 90 jours ({{ bloc.median_price | round | int }} $){% if bloc.gap_vs_yesterday is not none %} · {{ bloc.gap_vs_yesterday }} $ vs hier{% endif %}

{% endif %}  {{ bloc.deep_link }}
{% endif %}
{% endfor %}

--
État des sources
{% for p in data.providers %}- {{ p.name }} : {% if p.last_success_at %}dernier succès il y a {{ p.hours_silent | round(1) }} h{% else %}jamais{% endif %}{% if p.consecutive_failures %} · {{ p.consecutive_failures }} échec(s){% endif %}

{% endfor %}
```

`scrappervol/notify/templates/exception.html.j2` :

```jinja
<!doctype html>
<html lang="fr">
<head><meta charset="utf-8"><title>{{ subject }}</title></head>
<body style="font-family: -apple-system, Segoe UI, Roboto, sans-serif; color:#222; max-width:680px;">
<h1 style="font-size:20px;">{{ data.destination }} à {{ data.price_cad }} $</h1>
<p style="font-size:16px;">
  {{ data.label }} — {{ data.airline }} · {{ data.origin }} → {{ data.destination }}<br>
  {{ data.depart_date }}{% if data.return_date %} → {{ data.return_date }}{% endif %}
  · relevé sur {{ data.provider }}
</p>
<p style="background:#eafaf1;border-left:4px solid #27ae60;padding:12px;">
  {{ (data.gap_vs_median * 100) | round | int }} % sous la médiane de
  {{ data.median_price | round | int }} $, calculée sur {{ data.history_days }} jours d'historique.
</p>
<p><a href="{{ data.deep_link }}" style="font-size:16px;">Réserver</a></p>
<p style="color:#888;font-size:12px;">Les erreurs de prix durent en général de 2 à 6 heures.</p>
</body>
</html>
```

`scrappervol/notify/templates/exception.txt.j2` :

```jinja
{{ data.destination }} à {{ data.price_cad }} $

{{ data.label }} — {{ data.airline }} · {{ data.origin }} vers {{ data.destination }}
{{ data.depart_date }}{% if data.return_date %} au {{ data.return_date }}{% endif %} · source {{ data.provider }}

{{ (data.gap_vs_median * 100) | round | int }} % sous la médiane de {{ data.median_price | round | int }} $, calculée sur {{ data.history_days }} jours d'historique.

{{ data.deep_link }}

Les erreurs de prix durent en général de 2 à 6 heures.
```

- [ ] **Étape 4 : écrire `scrappervol/notify/render.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

DOSSIER_GABARITS = Path(__file__).parent / "templates"

_env = Environment(
    loader=FileSystemLoader(DOSSIER_GABARITS),
    # Seuls les gabarits HTML sont échappés : appliquer l'échappement au gabarit texte
    # transformerait chaque apostrophe française en entité HTML dans le courriel.
    autoescape=select_autoescape(enabled_extensions=("html.j2", "html"), default=False),
    trim_blocks=True,
    lstrip_blocks=True,
)


@dataclass(frozen=True, slots=True)
class RenderedMail:
    subject: str
    html: str
    text: str


@dataclass(frozen=True, slots=True)
class RouteBlock:
    label: str
    price_cad: int | None
    airline: str
    origin: str
    destination: str
    depart_date: date | None
    return_date: date | None
    provider: str
    deep_link: str
    median_price: float | None
    gap_vs_median: float | None
    gap_vs_yesterday: int | None
    is_find: bool
    history_building: bool


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    name: str
    last_success_at: datetime | None
    consecutive_failures: int
    hours_silent: float | None
    is_stale: bool


@dataclass(frozen=True, slots=True)
class DigestData:
    day: date
    blocks: list[RouteBlock]
    providers: list[ProviderStatus]

    @property
    def find_count(self) -> int:
        return sum(1 for bloc in self.blocks if bloc.is_find and not bloc.history_building)

    @property
    def has_stale_provider(self) -> bool:
        return any(p.is_stale for p in self.providers)

    @property
    def sorted_blocks(self) -> list[RouteBlock]:
        """Meilleures affaires en tête ; les trajets sans historique significatif ferment la marche."""
        return sorted(
            self.blocks,
            key=lambda b: (b.history_building, -(b.gap_vs_median or 0.0)),
        )


@dataclass(frozen=True, slots=True)
class ExceptionData:
    label: str
    origin: str
    destination: str
    depart_date: date
    return_date: date | None
    price_cad: int
    airline: str
    provider: str
    deep_link: str
    median_price: float
    gap_vs_median: float
    history_days: int


def render_digest(data: DigestData) -> RenderedMail:
    pluriel = "s" if data.find_count > 1 else ""
    sujet = f"ScrapperVol — {data.find_count} trouvaille{pluriel} du {data.day.isoformat()}"
    return RenderedMail(
        subject=sujet,
        html=_env.get_template("digest.html.j2").render(data=data, subject=sujet),
        text=_env.get_template("digest.txt.j2").render(data=data, subject=sujet),
    )


def render_exception(data: ExceptionData) -> RenderedMail:
    ecart = round(data.gap_vs_median * 100)
    sujet = (
        f"ScrapperVol — {data.destination} à {data.price_cad} $ ({ecart} % sous la médiane)"
    )
    return RenderedMail(
        subject=sujet,
        html=_env.get_template("exception.html.j2").render(data=data, subject=sujet),
        text=_env.get_template("exception.txt.j2").render(data=data, subject=sujet),
    )
```

- [ ] **Étape 5 : vérifier que les tests passent**

```bash
./dev test tests/notify/test_render.py -v
./dev lint
```

Attendu : 14 tests passés. Si l'ordre du bandeau échoue, c'est que le gabarit place l'avertissement
après les blocs : il doit être le premier élément du corps.

- [ ] **Étape 6 : committer**

```bash
git add scrappervol/notify tests/notify/test_render.py
git commit -m "feat: gabarits et rendu du digest et des alertes d'exception"
```

---

## Tâche 14 : envoi SMTP

**Fichiers :**
- Créer : `scrappervol/notify/mailer.py`
- Modifier : `tests/conftest.py` (fixture `faux_mailer`)
- Test : `tests/notify/test_mailer.py`

**Interfaces :**
- Consomme : `RenderedMail` (tâche 13), `Settings` (tâche 1).
- Produit :
  - `Mailer` — protocole `send(mail: RenderedMail, to: str) -> None`
  - `SmtpMailer(settings)` — implémentation SMTP avec STARTTLS
  - `NullMailer()` — journalise sans envoyer ; utilisé quand `SMTP_HOST` est vide
  - `build_mailer(settings) -> Mailer`
  - `build_message(mail: RenderedMail, sender: str, to: str) -> EmailMessage`
  - Fixture pytest `faux_mailer` collectant les envois.

- [ ] **Étape 1 : ajouter la fixture à `tests/conftest.py`**

```python
class FauxMailer:
    def __init__(self) -> None:
        self.envois: list[tuple[str, str]] = []

    def send(self, mail, to: str) -> None:
        self.envois.append((mail.subject, to))


@pytest.fixture
def faux_mailer():
    return FauxMailer()
```

- [ ] **Étape 2 : écrire le test qui échoue**

`tests/notify/test_mailer.py` :

```python
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


def test_le_smtp_mailer_nappelle_pas_login_sans_identifiants():
    reglages = Settings(smtp_host="smtp.example.com", smtp_user="", smtp_password="")
    session = MagicMock()

    with patch("scrappervol.notify.mailer.smtplib.SMTP") as smtp:
        smtp.return_value.__enter__.return_value = session
        SmtpMailer(reglages).send(COURRIEL, "vers@example.com")

    session.login.assert_not_called()


def test_un_echec_denvoi_leve_une_erreur_explicite():
    reglages = Settings(smtp_host="smtp.example.com")

    with patch("scrappervol.notify.mailer.smtplib.SMTP", side_effect=OSError("injoignable")):
        with pytest.raises(RuntimeError, match="envoi SMTP"):
            SmtpMailer(reglages).send(COURRIEL, "vers@example.com")
```

- [ ] **Étape 3 : lancer le test et vérifier l'échec**

```bash
./dev test tests/notify/test_mailer.py -v
```

Attendu : `ModuleNotFoundError: No module named 'scrappervol.notify.mailer'`.

- [ ] **Étape 4 : écrire l'implémentation**

`scrappervol/notify/mailer.py` :

```python
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
```

- [ ] **Étape 5 : vérifier que les tests passent**

```bash
./dev test tests/notify/ -v
./dev lint
```

Attendu : 21 tests passés (14 de la tâche 13, 7 ici).

- [ ] **Étape 6 : committer**

```bash
git add scrappervol/notify/mailer.py tests/notify/test_mailer.py tests/conftest.py
git commit -m "feat: envoi SMTP avec repli silencieux si non configuré"
```

---

## Tâche 15 : passage de scan et alerte d'exception

Le premier assemblage réel : source → dépôt → détection → courriel. C'est ici que se vérifient les
règles du §6 dans leur contexte, et non plus en isolation.

**Fichiers :**
- Créer : `scrappervol/scheduler/jobs.py`
- Test : `tests/scheduler/test_scan_job.py`

**Interfaces :**
- Consomme : `run_provider` (tâche 9), dépôt (tâche 5), `PriceContext`, `is_exception`, `relative_gap`
  (tâche 7), `render_exception`, `ExceptionData` (tâche 13), `Mailer` (tâche 14).
- Produit :
  - `run_scan(session, provider, settings, mailer, now) -> ScanOutcome`
  - `ScanOutcome(provider, offers_recorded, new_lows, exceptions_sent, failed, skipped)`

- [ ] **Étape 1 : écrire le test qui échoue**

`tests/scheduler/test_scan_job.py` :

```python
from datetime import UTC, date, datetime, timedelta

import pytest

from scrappervol.config import Settings
from scrappervol.core.types import DatePolicyKind
from scrappervol.scheduler.jobs import run_scan
from scrappervol.storage import repo
from scrappervol.storage.models import AlertKind, DailyLow, Route

MAINTENANT = datetime(2026, 8, 4, 14, 0, tzinfo=UTC)
AUJOURDHUI = date(2026, 8, 4)


@pytest.fixture
def reglages():
    return Settings(request_pause_min_s=0, request_pause_max_s=0, max_queries_per_route=1)


def _trajet(session, **surcharges) -> Route:
    base = {
        "label": "Paris",
        "origins": ["YUL"],
        "destinations": ["CDG"],
        "date_policy": DatePolicyKind.FIXED,
        "policy_params": {"depart": "2027-03-12", "retour": "2027-03-22"},
    }
    trajet = Route(**{**base, **surcharges})
    session.add(trajet)
    session.commit()
    session.refresh(trajet)
    return trajet


def _historique(session, route_id: int, prix: int, jours: int) -> None:
    for decalage in range(1, jours + 1):
        session.add(
            DailyLow(
                route_id=route_id,
                day=AUJOURDHUI - timedelta(days=decalage),
                price_cad=prix,
                provider="google_flights",
            )
        )
    session.commit()


def test_les_offres_sont_enregistrees_et_le_plus_bas_du_jour_pose(
    session, reglages, fausse_source, faux_mailer, sans_pause
):
    trajet = _trajet(session)
    dormir, _ = sans_pause

    resultat = run_scan(
        session,
        fausse_source(name="google_flights", offres=[(612, "Air Transat"), (700, "Air Canada")]),
        reglages,
        faux_mailer,
        MAINTENANT,
        sleeper=dormir,
    )

    assert resultat.offers_recorded == 2
    assert resultat.new_lows == 1
    assert repo.daily_low_for(session, trajet.id, AUJOURDHUI).price_cad == 612


def test_un_prix_superieur_ne_remplace_pas_le_plus_bas_du_jour(
    session, reglages, fausse_source, faux_mailer, sans_pause
):
    trajet = _trajet(session)
    dormir, _ = sans_pause
    run_scan(session, fausse_source(name="google_flights", offres=[(480, "A")]), reglages,
             faux_mailer, MAINTENANT, sleeper=dormir)

    run_scan(session, fausse_source(name="google_flights", offres=[(900, "B")]), reglages,
             faux_mailer, MAINTENANT + timedelta(hours=4), sleeper=dormir)

    assert repo.daily_low_for(session, trajet.id, AUJOURDHUI).price_cad == 480


def test_une_aberration_declenche_un_courriel_immediat(
    session, reglages, fausse_source, faux_mailer, sans_pause
):
    trajet = _trajet(session)
    _historique(session, trajet.id, prix=600, jours=30)
    dormir, _ = sans_pause

    resultat = run_scan(session, fausse_source(name="google_flights", offres=[(299, "Air Transat")]),
                        reglages, faux_mailer, MAINTENANT, sleeper=dormir)

    assert resultat.exceptions_sent == 1
    assert len(faux_mailer.envois) == 1
    assert "CDG à 299 $" in faux_mailer.envois[0][0]


def test_la_meme_aberration_au_passage_suivant_reste_silencieuse(
    session, reglages, fausse_source, faux_mailer, sans_pause
):
    trajet = _trajet(session)
    _historique(session, trajet.id, prix=600, jours=30)
    dormir, _ = sans_pause
    run_scan(session, fausse_source(name="google_flights", offres=[(299, "Air Transat")]),
             reglages, faux_mailer, MAINTENANT, sleeper=dormir)

    resultat = run_scan(session, fausse_source(name="google_flights", offres=[(299, "Air Transat")]),
                        reglages, faux_mailer, MAINTENANT + timedelta(hours=4), sleeper=dormir)

    assert resultat.exceptions_sent == 0
    assert len(faux_mailer.envois) == 1


def test_aucune_alerte_sans_historique_suffisant(
    session, reglages, fausse_source, faux_mailer, sans_pause
):
    trajet = _trajet(session)
    _historique(session, trajet.id, prix=600, jours=5)
    dormir, _ = sans_pause

    resultat = run_scan(session, fausse_source(name="google_flights", offres=[(150, "Air Transat")]),
                        reglages, faux_mailer, MAINTENANT, sleeper=dormir)

    assert resultat.exceptions_sent == 0
    assert faux_mailer.envois == []


def test_aucune_alerte_sous_le_plancher_de_credibilite(
    session, reglages, fausse_source, faux_mailer, sans_pause
):
    trajet = _trajet(session)
    _historique(session, trajet.id, prix=600, jours=30)
    dormir, _ = sans_pause

    resultat = run_scan(session, fausse_source(name="google_flights", offres=[(45, "Air Transat")]),
                        reglages, faux_mailer, MAINTENANT, sleeper=dormir)

    assert resultat.exceptions_sent == 0
    assert faux_mailer.envois == []


def test_lalerte_emise_est_journalisee(
    session, reglages, fausse_source, faux_mailer, sans_pause
):
    trajet = _trajet(session)
    _historique(session, trajet.id, prix=600, jours=30)
    dormir, _ = sans_pause

    run_scan(session, fausse_source(name="google_flights", offres=[(299, "Air Transat")]),
             reglages, faux_mailer, MAINTENANT, sleeper=dormir)

    from sqlmodel import select

    from scrappervol.storage.models import Alert

    alertes = session.exec(select(Alert)).all()
    assert len(alertes) == 1
    assert alertes[0].kind is AlertKind.EXCEPTION
    assert alertes[0].payload["offer_hash"]


def test_un_echec_de_source_ne_leve_pas_et_nenvoie_rien(
    session, reglages, fausse_source, faux_mailer, sans_pause
):
    _trajet(session)
    dormir, _ = sans_pause

    resultat = run_scan(
        session,
        fausse_source(name="transat", exception=RuntimeError("boum")),
        reglages,
        faux_mailer,
        MAINTENANT,
        sleeper=dormir,
    )

    assert resultat.failed is True
    assert faux_mailer.envois == []


def test_un_echec_denvoi_nempeche_pas_le_reste_du_passage(
    session, reglages, fausse_source, sans_pause
):
    """Un serveur SMTP injoignable ne doit pas faire perdre les prix relevés."""

    class MailerCasse:
        def send(self, mail, to):
            raise RuntimeError("SMTP injoignable")

    trajet = _trajet(session)
    _historique(session, trajet.id, prix=600, jours=30)
    dormir, _ = sans_pause

    resultat = run_scan(session, fausse_source(name="google_flights", offres=[(299, "A")]),
                        reglages, MailerCasse(), MAINTENANT, sleeper=dormir)

    assert resultat.offers_recorded == 1
    assert repo.daily_low_for(session, trajet.id, AUJOURDHUI) is not None
```

- [ ] **Étape 2 : lancer le test et vérifier l'échec**

```bash
./dev test tests/scheduler/test_scan_job.py -v
```

Attendu : `ModuleNotFoundError: No module named 'scrappervol.scheduler.jobs'`.

- [ ] **Étape 3 : écrire l'implémentation**

`scrappervol/scheduler/jobs.py` :

```python
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session

from scrappervol.config import Settings
from scrappervol.detection.rules import PriceContext, is_exception, relative_gap
from scrappervol.notify.mailer import Mailer
from scrappervol.notify.render import ExceptionData, render_exception
from scrappervol.providers.base import PriceProvider
from scrappervol.providers.runner import run_provider
from scrappervol.storage import repo
from scrappervol.storage.models import AlertKind, Observation, Route

logger = logging.getLogger(__name__)


@dataclass
class ScanOutcome:
    provider: str
    offers_recorded: int = 0
    new_lows: int = 0
    exceptions_sent: int = 0
    failed: bool = False
    skipped: bool = False


def run_scan(
    session: Session,
    provider: PriceProvider,
    settings: Settings,
    mailer: Mailer,
    now: datetime,
    sleeper: Callable[[float], None] = time.sleep,
) -> ScanOutcome:
    """Un passage complet d'une source : relève, enregistre, détecte, alerte."""
    rapport = run_provider(session, provider, settings, now, sleeper=sleeper)
    resultat = ScanOutcome(provider=provider.name, failed=rapport.failed, skipped=rapport.skipped)
    if rapport.failed or rapport.skipped:
        return resultat

    jour = now.date()

    for route_id, offres in rapport.offers_by_route.items():
        observations = repo.record_observations(session, route_id, offres, now)
        resultat.offers_recorded += len(observations)
        if not observations:
            continue

        meilleure = min(observations, key=lambda obs: obs.price_cad)
        if repo.upsert_daily_low(session, route_id, jour, meilleure) is not None:
            resultat.new_lows += 1

        trajet = session.get(Route, route_id)
        if trajet is None:
            continue

        if _traiter_exception(session, trajet, meilleure, settings, mailer, now):
            resultat.exceptions_sent += 1

    return resultat


def _traiter_exception(
    session: Session,
    route: Route,
    observation: Observation,
    settings: Settings,
    mailer: Mailer,
    now: datetime,
) -> bool:
    historique = repo.daily_low_history(
        session, route.id, before_day=now.date(), window_days=settings.history_window_days
    )
    contexte = PriceContext(daily_lows=historique)

    deja = repo.exception_already_sent(session, route.id, observation.offer_hash)
    if not is_exception(
        price_cad=observation.price_cad,
        context=contexte,
        threshold=route.exception_threshold,
        min_history_days=settings.min_history_days,
        credibility_floor=settings.credibility_floor_cad,
        already_alerted=deja,
    ):
        return False

    mediane = contexte.median_price or 0.0
    courriel = render_exception(
        ExceptionData(
            label=route.label,
            origin=observation.origin,
            destination=observation.destination,
            depart_date=observation.departure_date,
            return_date=observation.return_date,
            price_cad=observation.price_cad,
            airline=observation.airline,
            provider=observation.provider,
            deep_link=observation.deep_link,
            median_price=mediane,
            gap_vs_median=relative_gap(observation.price_cad, mediane),
            history_days=contexte.days_of_history,
        )
    )

    repo.record_alert(
        session,
        route.id,
        observation.id,
        AlertKind.EXCEPTION,
        {"offer_hash": observation.offer_hash, "price_cad": observation.price_cad},
        now,
    )

    try:
        mailer.send(courriel, settings.alert_to)
    except Exception as erreur:  # noqa: BLE001 — un SMTP en panne ne doit pas coûter les données
        logger.error("alerte non envoyée : %s", erreur)
        return False

    return True
```

L'alerte est journalisée **avant** l'envoi, délibérément : si le serveur SMTP tombe en boucle, mieux
vaut manquer une alerte qu'en envoyer quarante quand il revient.

- [ ] **Étape 4 : vérifier que les tests passent**

```bash
./dev test tests/scheduler/test_scan_job.py -v
./dev lint
```

Attendu : 9 tests passés.

- [ ] **Étape 5 : committer**

```bash
git add scrappervol/scheduler/jobs.py tests/scheduler/test_scan_job.py
git commit -m "feat: passage de scan avec détection et alerte d'exception"
```

---

## Tâche 16 : digest quotidien et purge

**Fichiers :**
- Modifier : `scrappervol/scheduler/jobs.py`
- Test : `tests/scheduler/test_digest_job.py`

**Interfaces :**
- Consomme : dépôt (tâche 5), `is_find`, `PriceContext`, `relative_gap` (tâche 7), `render_digest`,
  `DigestData`, `RouteBlock`, `ProviderStatus` (tâche 13), `Mailer` (tâche 14).
- Produit :
  - `build_digest(session, settings, now) -> DigestData`
  - `send_digest(session, settings, mailer, now) -> bool` — `False` si aucun trajet actif
  - `purge_old_data(session, settings, now) -> int`
  - `detection.rules.SEUIL_SOURCE_MUETTE_H = 48` — ajouté au module de règles, et non à `jobs`, pour
    que l'interface web puisse s'en servir sans dépendre de l'ordonnanceur (§4 du design)

- [ ] **Étape 1 : écrire le test qui échoue**

`tests/scheduler/test_digest_job.py` :

```python
from datetime import UTC, date, datetime, timedelta

import pytest

from scrappervol.config import Settings
from scrappervol.core.types import DatePolicyKind, FlightOffer
from scrappervol.scheduler.jobs import build_digest, purge_old_data, send_digest
from scrappervol.storage import repo
from scrappervol.storage.models import DailyLow, Observation, ProviderHealth, Route

MAINTENANT = datetime(2026, 8, 4, 18, 0, tzinfo=UTC)
AUJOURDHUI = date(2026, 8, 4)


@pytest.fixture
def reglages():
    return Settings(alert_to="moi@example.com")


def _trajet(session, **surcharges) -> Route:
    base = {
        "label": "Paris",
        "origins": ["YUL"],
        "destinations": ["CDG"],
        "date_policy": DatePolicyKind.FIXED,
        "policy_params": {"depart": "2027-03-12", "retour": "2027-03-22"},
    }
    trajet = Route(**{**base, **surcharges})
    session.add(trajet)
    session.commit()
    session.refresh(trajet)
    return trajet


def _offre(prix: int) -> FlightOffer:
    return FlightOffer(
        provider="google_flights",
        origin="YUL",
        destination="CDG",
        depart_date=date(2027, 3, 12),
        return_date=date(2027, 3, 22),
        price_cad=prix,
        price_original=float(prix),
        currency_original="CAD",
        airline="Air Transat",
        stops=0,
        duration_minutes=425,
        deep_link="https://example.com",
        raw={},
    )


def _jour(session, route_id: int, jour: date, prix: int) -> None:
    session.add(DailyLow(route_id=route_id, day=jour, price_cad=prix, provider="google_flights"))
    session.commit()


def test_le_digest_contient_un_bloc_par_trajet_actif(session, reglages):
    _trajet(session, label="Paris")
    _trajet(session, label="Lisbonne")
    _trajet(session, label="Inactif", active=False)

    donnees = build_digest(session, reglages, MAINTENANT)

    assert {b.label for b in donnees.blocks} == {"Paris", "Lisbonne"}


def test_le_bloc_porte_le_plus_bas_du_jour_et_son_contexte(session, reglages):
    trajet = _trajet(session)
    observation = repo.record_observations(session, trajet.id, [_offre(480)], MAINTENANT)[0]
    repo.upsert_daily_low(session, trajet.id, AUJOURDHUI, observation)
    for decalage in range(1, 21):
        _jour(session, trajet.id, AUJOURDHUI - timedelta(days=decalage), 600)

    bloc = build_digest(session, reglages, MAINTENANT).blocks[0]

    assert bloc.price_cad == 480
    assert bloc.airline == "Air Transat"
    assert bloc.median_price == 600
    assert round(bloc.gap_vs_median, 2) == 0.20
    assert bloc.gap_vs_yesterday == -120
    assert bloc.history_building is False


def test_un_trajet_sans_historique_significatif_est_marque_en_construction(session, reglages):
    trajet = _trajet(session)
    observation = repo.record_observations(session, trajet.id, [_offre(480)], MAINTENANT)[0]
    repo.upsert_daily_low(session, trajet.id, AUJOURDHUI, observation)
    for decalage in range(1, 4):
        _jour(session, trajet.id, AUJOURDHUI - timedelta(days=decalage), 600)

    bloc = build_digest(session, reglages, MAINTENANT).blocks[0]

    assert bloc.history_building is True
    assert bloc.is_find is False


def test_un_trajet_sans_prix_du_jour_apparait_quand_meme(session, reglages):
    _trajet(session)

    bloc = build_digest(session, reglages, MAINTENANT).blocks[0]

    assert bloc.price_cad is None


def test_le_digest_compte_les_trouvailles(session, reglages):
    trajet = _trajet(session, target_price_cad=500)
    observation = repo.record_observations(session, trajet.id, [_offre(450)], MAINTENANT)[0]
    repo.upsert_daily_low(session, trajet.id, AUJOURDHUI, observation)
    for decalage in range(1, 21):
        _jour(session, trajet.id, AUJOURDHUI - timedelta(days=decalage), 600)

    assert build_digest(session, reglages, MAINTENANT).find_count == 1


def test_letat_des_sources_est_toujours_present(session, reglages):
    _trajet(session)
    session.add(ProviderHealth(provider="google_flights", last_success_at=MAINTENANT))
    session.commit()

    donnees = build_digest(session, reglages, MAINTENANT)

    assert {p.name for p in donnees.providers} == set(reglages.enabled_providers)


def test_une_source_muette_depuis_plus_de_48h_est_marquee(session, reglages):
    _trajet(session)
    session.add(
        ProviderHealth(provider="transat", last_success_at=MAINTENANT - timedelta(hours=72))
    )
    session.commit()

    donnees = build_digest(session, reglages, MAINTENANT)

    transat = next(p for p in donnees.providers if p.name == "transat")
    assert transat.is_stale is True
    assert donnees.has_stale_provider is True


def test_une_source_qui_na_jamais_reussi_est_marquee(session, reglages):
    _trajet(session)

    donnees = build_digest(session, reglages, MAINTENANT)

    assert all(p.is_stale for p in donnees.providers)


def test_le_digest_est_envoye_et_journalise(session, reglages, faux_mailer):
    _trajet(session)

    assert send_digest(session, reglages, faux_mailer, MAINTENANT) is True
    assert len(faux_mailer.envois) == 1
    assert faux_mailer.envois[0][1] == "moi@example.com"


def test_aucun_digest_sans_trajet_actif(session, reglages, faux_mailer):
    _trajet(session, active=False)

    assert send_digest(session, reglages, faux_mailer, MAINTENANT) is False
    assert faux_mailer.envois == []


def test_la_purge_supprime_les_observations_anciennes(session, reglages):
    trajet = _trajet(session)
    session.add(Observation.from_offer(trajet.id, _offre(612), MAINTENANT - timedelta(days=120)))
    session.add(Observation.from_offer(trajet.id, _offre(500), MAINTENANT))
    session.commit()

    assert purge_old_data(session, reglages, MAINTENANT) == 1


def test_la_purge_epargne_lhistorique_des_plus_bas(session, reglages):
    trajet = _trajet(session)
    _jour(session, trajet.id, date(2024, 1, 1), 400)

    purge_old_data(session, reglages, MAINTENANT)

    assert repo.daily_low_for(session, trajet.id, date(2024, 1, 1)) is not None
```

- [ ] **Étape 2 : lancer le test et vérifier l'échec**

```bash
./dev test tests/scheduler/test_digest_job.py -v
```

Attendu : `ImportError: cannot import name 'build_digest'`.

- [ ] **Étape 3 : ajouter la constante à `scrappervol/detection/rules.py`**

```python
# Au-delà de ce silence, une source est considérée hors service et signalée en tête du digest.
SEUIL_SOURCE_MUETTE_H = 48
```

- [ ] **Étape 4 : compléter `scrappervol/scheduler/jobs.py`**

Compléter les imports en tête de fichier — `Observation` y est déjà importé depuis la tâche 15 :

```python
from datetime import timedelta

from scrappervol.detection.rules import SEUIL_SOURCE_MUETTE_H, is_find
from scrappervol.notify.render import DigestData, ProviderStatus, RouteBlock, render_digest
```

puis, à la suite du fichier :

```python
def _statut_source(session: Session, provider: str, now: datetime) -> ProviderStatus:
    sante = repo.get_or_create_health(session, provider)
    heures = (
        (now - sante.last_success_at).total_seconds() / 3600
        if sante.last_success_at is not None
        else None
    )
    return ProviderStatus(
        name=provider,
        last_success_at=sante.last_success_at,
        consecutive_failures=sante.consecutive_failures,
        hours_silent=heures,
        is_stale=heures is None or heures > SEUIL_SOURCE_MUETTE_H,
    )


def _bloc_trajet(session: Session, route: Route, settings: Settings, now: datetime) -> RouteBlock:
    jour = now.date()
    ligne = repo.daily_low_for(session, route.id, jour)
    historique = repo.daily_low_history(
        session, route.id, before_day=jour, window_days=settings.history_window_days
    )
    contexte = PriceContext(daily_lows=historique)
    en_construction = not contexte.has_significant_history(settings.min_history_days)
    mediane = contexte.median_price

    if ligne is None:
        return RouteBlock(
            label=route.label,
            price_cad=None,
            airline="",
            origin="",
            destination="",
            depart_date=None,
            return_date=None,
            provider="",
            deep_link="",
            median_price=mediane,
            gap_vs_median=None,
            gap_vs_yesterday=None,
            is_find=False,
            history_building=en_construction,
        )

    observation = session.get(Observation, ligne.observation_id) if ligne.observation_id else None
    veille = repo.daily_low_for(session, route.id, jour - timedelta(days=1))

    return RouteBlock(
        label=route.label,
        price_cad=ligne.price_cad,
        airline=observation.airline if observation else "",
        origin=observation.origin if observation else "",
        destination=observation.destination if observation else "",
        depart_date=observation.departure_date if observation else None,
        return_date=observation.return_date if observation else None,
        provider=ligne.provider,
        deep_link=observation.deep_link if observation else "",
        median_price=mediane,
        gap_vs_median=relative_gap(ligne.price_cad, mediane) if mediane else None,
        gap_vs_yesterday=ligne.price_cad - veille.price_cad if veille else None,
        is_find=is_find(
            price_cad=ligne.price_cad,
            context=contexte,
            target_price_cad=route.target_price_cad,
            find_threshold=settings.find_threshold,
            min_history_days=settings.min_history_days,
        ),
        history_building=en_construction,
    )


def build_digest(session: Session, settings: Settings, now: datetime) -> DigestData:
    return DigestData(
        day=now.date(),
        blocks=[
            _bloc_trajet(session, route, settings, now) for route in repo.active_routes(session)
        ],
        providers=[_statut_source(session, nom, now) for nom in settings.enabled_providers],
    )


def send_digest(session: Session, settings: Settings, mailer: Mailer, now: datetime) -> bool:
    """Envoie le digest quotidien. Retourne False si aucun trajet n'est actif (§8 du design)."""
    donnees = build_digest(session, settings, now)
    if not donnees.blocks:
        logger.info("aucun trajet actif, digest non envoyé")
        return False

    courriel = render_digest(donnees)
    try:
        mailer.send(courriel, settings.alert_to)
    except Exception as erreur:  # noqa: BLE001
        logger.error("digest non envoyé : %s", erreur)
        return False

    repo.record_alert(
        session,
        route_id=0,
        observation_id=None,
        kind=AlertKind.DIGEST,
        payload={"find_count": donnees.find_count, "routes": len(donnees.blocks)},
        at=now,
    )
    return True


def purge_old_data(session: Session, settings: Settings, now: datetime) -> int:
    supprimees = repo.purge_observations(session, now, settings.retention_days)
    logger.info("purge : %s observations supprimées", supprimees)
    return supprimees
```

`route_id=0` sur l'alerte de digest est volontaire : le digest ne concerne pas un trajet en
particulier, et la table `alert` sert ici de journal d'envoi.

- [ ] **Étape 5 : vérifier que les tests passent**

```bash
./dev test tests/scheduler/ tests/detection/ -v
./dev lint
```

Attendu : 21 tests d'ordonnancement passés (9 de la tâche 15, 12 ici), et les tests de détection
toujours au vert après l'ajout de la constante.

- [ ] **Étape 6 : committer**

```bash
git add scrappervol/scheduler/jobs.py scrappervol/detection/rules.py \
        tests/scheduler/test_digest_job.py
git commit -m "feat: digest quotidien avec état des sources et purge des observations"
```

---

## Tâche 17 : ordonnanceur

**Fichiers :**
- Créer : `scrappervol/scheduler/app.py`
- Test : `tests/scheduler/test_app.py`

**Interfaces :**
- Consomme : `run_scan`, `send_digest`, `purge_old_data` (tâches 15-16), `Settings` (tâche 1),
  `session_scope` (tâche 4), `build_mailer` (tâche 14).
- Produit :
  - `build_providers(settings) -> list[PriceProvider]`
  - `build_scheduler(engine, settings, mailer) -> BackgroundScheduler` — jobs non démarrés
  - `INTERVALLES: dict[str, str]` associant le nom d'une source au champ de réglage de son intervalle

- [ ] **Étape 1 : écrire le test qui échoue**

`tests/scheduler/test_app.py` :

```python
import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from scrappervol.config import Settings
from scrappervol.scheduler.app import build_providers, build_scheduler


@pytest.fixture
def reglages():
    return Settings(
        enabled_providers=["google_flights", "transat"],
        interval_google_hours=4,
        interval_transat_hours=6,
        digest_hour=18,
        timezone="America/Toronto",
    )


def test_seules_les_sources_activees_sont_construites(reglages):
    sources = build_providers(reglages)

    assert [s.name for s in sources] == ["google_flights", "transat"]


def test_une_source_inconnue_est_ignoree_sans_lever():
    reglages = Settings(enabled_providers=["google_flights", "compagnie_imaginaire"])

    assert [s.name for s in build_providers(reglages)] == ["google_flights"]


def test_un_job_de_scan_par_source_activee(engine, reglages, faux_mailer):
    ordonnanceur = build_scheduler(engine, reglages, faux_mailer)

    scans = [j for j in ordonnanceur.get_jobs() if j.id.startswith("scan:")]
    assert {j.id for j in scans} == {"scan:google_flights", "scan:transat"}


def test_chaque_scan_utilise_lintervalle_de_sa_source(engine, reglages, faux_mailer):
    ordonnanceur = build_scheduler(engine, reglages, faux_mailer)

    google = ordonnanceur.get_job("scan:google_flights")
    transat = ordonnanceur.get_job("scan:transat")

    assert isinstance(google.trigger, IntervalTrigger)
    assert google.trigger.interval.total_seconds() == 4 * 3600
    assert transat.trigger.interval.total_seconds() == 6 * 3600


def test_les_passages_sont_decales_aleatoirement(engine, reglages, faux_mailer):
    """Frapper à l'heure ronde est la signature la plus facile à repérer côté serveur."""
    ordonnanceur = build_scheduler(engine, reglages, faux_mailer)

    assert ordonnanceur.get_job("scan:google_flights").trigger.jitter > 0


def test_le_digest_est_planifie_a_lheure_configuree(engine, reglages, faux_mailer):
    ordonnanceur = build_scheduler(engine, reglages, faux_mailer)

    digest = ordonnanceur.get_job("digest")
    assert isinstance(digest.trigger, CronTrigger)
    assert str(digest.trigger.timezone) == "America/Toronto"
    assert "hour='18'" in str(digest.trigger)


def test_un_job_de_purge_quotidien_existe(engine, reglages, faux_mailer):
    ordonnanceur = build_scheduler(engine, reglages, faux_mailer)

    assert ordonnanceur.get_job("purge") is not None


def test_lordonnanceur_nest_pas_demarre_a_la_construction(engine, reglages, faux_mailer):
    ordonnanceur = build_scheduler(engine, reglages, faux_mailer)

    assert ordonnanceur.running is False
```

- [ ] **Étape 2 : lancer le test et vérifier l'échec**

```bash
./dev test tests/scheduler/test_app.py -v
```

Attendu : `ModuleNotFoundError: No module named 'scrappervol.scheduler.app'`.

- [ ] **Étape 3 : écrire l'implémentation**

`scrappervol/scheduler/app.py` :

```python
from __future__ import annotations

import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import Engine

from scrappervol.config import Settings
from scrappervol.notify.mailer import Mailer
from scrappervol.providers.base import PriceProvider
from scrappervol.scheduler.jobs import purge_old_data, run_scan, send_digest
from scrappervol.storage.db import session_scope

logger = logging.getLogger(__name__)

INTERVALLES = {
    "google_flights": "interval_google_hours",
    "transat": "interval_transat_hours",
    "air_canada": "interval_air_canada_hours",
}

# Décalage aléatoire appliqué à chaque passage, en secondes.
JITTER_S = 1800


def build_providers(settings: Settings) -> list[PriceProvider]:
    """Instancie les sources activées. Une source inconnue ou non importable est ignorée."""
    sources: list[PriceProvider] = []
    for nom in settings.enabled_providers:
        try:
            if nom == "google_flights":
                from scrappervol.providers.google_flights import GoogleFlightsProvider

                sources.append(GoogleFlightsProvider(settings))
            elif nom == "transat":
                from scrappervol.providers.transat import TransatProvider

                sources.append(TransatProvider(settings))
            elif nom == "air_canada":
                from scrappervol.providers.air_canada import AirCanadaProvider

                sources.append(AirCanadaProvider(settings))
            else:
                logger.warning("source inconnue ignorée : %s", nom)
        except ImportError as erreur:
            logger.warning("source %s non importable, ignorée : %s", nom, erreur)
    return sources


def build_scheduler(engine: Engine, settings: Settings, mailer: Mailer) -> BackgroundScheduler:
    """Câble les jobs sans démarrer l'ordonnanceur ; le démarrage appartient au point d'entrée."""
    fuseau = ZoneInfo(settings.timezone)
    ordonnanceur = BackgroundScheduler(timezone=fuseau)

    for source in build_providers(settings):
        heures = getattr(settings, INTERVALLES[source.name])
        ordonnanceur.add_job(
            _job_scan,
            trigger=IntervalTrigger(hours=heures, jitter=JITTER_S),
            args=[engine, settings, mailer, source],
            id=f"scan:{source.name}",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )

    ordonnanceur.add_job(
        _job_digest,
        trigger=CronTrigger(hour=settings.digest_hour, minute=0, timezone=fuseau),
        args=[engine, settings, mailer],
        id="digest",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    ordonnanceur.add_job(
        _job_purge,
        trigger=CronTrigger(hour=3, minute=30, timezone=fuseau),
        args=[engine, settings],
        id="purge",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    return ordonnanceur


def _job_scan(engine: Engine, settings: Settings, mailer: Mailer, provider: PriceProvider) -> None:
    with session_scope(engine) as session:
        resultat = run_scan(session, provider, settings, mailer, datetime.now(UTC))
    logger.info(
        "scan %s : %s offres, %s nouveaux plus bas, %s alertes",
        resultat.provider,
        resultat.offers_recorded,
        resultat.new_lows,
        resultat.exceptions_sent,
    )


def _job_digest(engine: Engine, settings: Settings, mailer: Mailer) -> None:
    with session_scope(engine) as session:
        send_digest(session, settings, mailer, datetime.now(UTC))


def _job_purge(engine: Engine, settings: Settings) -> None:
    with session_scope(engine) as session:
        purge_old_data(session, settings, datetime.now(UTC))
```

`max_instances=1` et `coalesce=True` importent plus qu'il n'y paraît : un scan qui dépasse son
intervalle — cas nominal quand Playwright rame — ne doit jamais être lancé une seconde fois en
parallèle sur la même source.

- [ ] **Étape 4 : vérifier que les tests passent**

```bash
./dev test tests/scheduler/ -v
./dev lint
```

Attendu : 29 tests passés.

- [ ] **Étape 5 : committer**

```bash
git add scrappervol/scheduler/app.py tests/scheduler/test_app.py
git commit -m "feat: ordonnanceur APScheduler avec intervalles par source et décalage aléatoire"
```

---

## Tâche 18 : interface web — tableau de bord et santé

**Fichiers :**
- Créer : `scrappervol/web/app.py`, `scrappervol/web/routes.py`, `scrappervol/web/charts.py`
- Créer : `scrappervol/web/templates/base.html.j2`, `dashboard.html.j2`, `health.html.j2`
- Test : `tests/web/test_dashboard.py`, `tests/web/test_charts.py`

**Interfaces :**
- Consomme : dépôt (tâche 5), `build_digest` et `_statut_source` via `build_digest` (tâche 16),
  `Settings` (tâche 1), `session_scope` (tâche 4).
- Produit :
  - `charts.sparkline_points(prices: list[int], width: int = 240, height: int = 48) -> str`
  - `app.create_app(engine, settings) -> FastAPI`
  - Routes `GET /` (tableau de bord) et `GET /health` (santé)
  - `app.get_session` — dépendance FastAPI, surchargeable en test

- [ ] **Étape 1 : écrire le test des graphes**

`tests/web/test_charts.py` :

```python
from scrappervol.web.charts import sparkline_points


def test_une_serie_vide_ne_produit_aucun_point():
    assert sparkline_points([]) == ""


def test_un_point_unique_est_centre_verticalement():
    points = sparkline_points([500], width=100, height=40)

    assert points == "0,20 100,20"


def test_le_minimum_touche_le_bas_et_le_maximum_le_haut():
    points = sparkline_points([100, 200], width=100, height=40).split()

    assert points[0].endswith(",40")
    assert points[1].endswith(",0")


def test_les_points_sont_repartis_sur_la_largeur():
    points = sparkline_points([100, 150, 200], width=100, height=40).split()

    assert [p.split(",")[0] for p in points] == ["0", "50", "100"]


def test_une_serie_plate_reste_a_mi_hauteur():
    points = sparkline_points([500, 500, 500], width=100, height=40).split()

    assert all(p.endswith(",20") for p in points)
```

- [ ] **Étape 2 : écrire `scrappervol/web/charts.py`**

```python
from __future__ import annotations


def sparkline_points(prices: list[int], width: int = 240, height: int = 48) -> str:
    """Coordonnées d'une polyligne SVG, du plus ancien au plus récent.

    Généré côté serveur : un graphe d'historique n'a pas besoin d'une chaîne de build JavaScript.
    """
    if not prices:
        return ""
    if len(prices) == 1:
        milieu = height / 2
        return f"0,{milieu:g} {width},{milieu:g}"

    minimum, maximum = min(prices), max(prices)
    etendue = maximum - minimum
    pas = width / (len(prices) - 1)

    points = []
    for index, prix in enumerate(prices):
        x = index * pas
        y = height / 2 if etendue == 0 else height - ((prix - minimum) / etendue) * height
        points.append(f"{x:g},{y:g}")
    return " ".join(points)
```

- [ ] **Étape 3 : écrire le test du tableau de bord**

`tests/web/test_dashboard.py` :

```python
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from scrappervol.config import Settings
from scrappervol.core.types import DatePolicyKind
from scrappervol.storage.models import DailyLow, ProviderHealth, Route
from scrappervol.web.app import create_app, get_session

MAINTENANT = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
AUJOURDHUI = date(2026, 8, 4)


@pytest.fixture
def client(engine, session):
    application = create_app(engine, Settings(enabled_providers=["google_flights", "transat"]))
    application.dependency_overrides[get_session] = lambda: session
    return TestClient(application)


def _trajet(session, **surcharges) -> Route:
    base = {
        "label": "Paris au printemps",
        "origins": ["YUL"],
        "destinations": ["CDG"],
        "date_policy": DatePolicyKind.FIXED,
        "policy_params": {"depart": "2027-03-12", "retour": "2027-03-22"},
    }
    trajet = Route(**{**base, **surcharges})
    session.add(trajet)
    session.commit()
    session.refresh(trajet)
    return trajet


def test_le_tableau_de_bord_repond(client):
    reponse = client.get("/")

    assert reponse.status_code == 200
    assert "text/html" in reponse.headers["content-type"]


def test_le_tableau_de_bord_liste_les_trajets(client, session):
    _trajet(session, label="Paris au printemps")
    _trajet(session, label="Lisbonne en octobre")

    corps = client.get("/").text

    assert "Paris au printemps" in corps
    assert "Lisbonne en octobre" in corps


def test_le_tableau_de_bord_montre_le_plus_bas_du_jour(client, session):
    trajet = _trajet(session)
    session.add(DailyLow(route_id=trajet.id, day=AUJOURDHUI, price_cad=480,
                         provider="google_flights"))
    session.commit()

    assert "480" in client.get("/").text


def test_le_tableau_de_bord_affiche_un_graphe_par_trajet(client, session):
    trajet = _trajet(session)
    for decalage in range(1, 15):
        session.add(DailyLow(route_id=trajet.id, day=AUJOURDHUI - timedelta(days=decalage),
                             price_cad=600 + decalage, provider="google_flights"))
    session.commit()

    corps = client.get("/").text

    assert "<polyline" in corps


def test_un_trajet_sans_donnee_ne_casse_pas_la_page(client, session):
    _trajet(session)

    assert client.get("/").status_code == 200


def test_le_tableau_de_bord_vide_est_explicite(client):
    corps = client.get("/").text

    assert "aucun trajet" in corps.lower()


def test_la_page_sante_liste_les_sources(client, session):
    session.add(ProviderHealth(provider="google_flights", last_success_at=MAINTENANT,
                               offers_last_run=42))
    session.commit()

    corps = client.get("/health").text

    assert "google_flights" in corps
    assert "transat" in corps


def test_la_page_sante_montre_les_echecs_et_la_derniere_erreur(client, session):
    session.add(ProviderHealth(provider="transat", consecutive_failures=3,
                               last_error="sélecteur introuvable"))
    session.commit()

    corps = client.get("/health").text

    assert "3" in corps
    assert "sélecteur introuvable" in corps


def test_la_page_sante_signale_une_source_qui_na_jamais_reussi(client):
    assert "jamais" in client.get("/health").text.lower()
```

- [ ] **Étape 4 : lancer les tests et vérifier l'échec**

```bash
./dev test tests/web/ -v
```

Attendu : `ModuleNotFoundError` sur `scrappervol.web.charts` puis `scrappervol.web.app`.

- [ ] **Étape 5 : écrire les gabarits**

`scrappervol/web/templates/base.html.j2` :

```jinja
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block titre %}ScrapperVol{% endblock %}</title>
  <script src="https://unpkg.com/htmx.org@2.0.3"></script>
  <style>
    :root { color-scheme: light dark; }
    body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0 auto;
           max-width: 960px; padding: 24px; line-height: 1.5; }
    nav a { margin-right: 16px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; padding: 8px; border-bottom: 1px solid #ddd; vertical-align: top; }
    .trouvaille { color: #1e8449; font-weight: 600; }
    .muet { color: #c0392b; }
    .discret { color: #777; font-size: 0.9em; }
    form label { display: block; margin: 8px 0 2px; font-size: 0.9em; }
    input, select { padding: 6px; width: 100%; box-sizing: border-box; }
    .ligne { display: flex; gap: 12px; }
    .ligne > div { flex: 1; }
  </style>
</head>
<body>
  <nav>
    <a href="/">Tableau de bord</a>
    <a href="/routes">Trajets</a>
    <a href="/health">Santé</a>
  </nav>
  <h1>{% block titre_page %}{% endblock %}</h1>
  {% block contenu %}{% endblock %}
</body>
</html>
```

`scrappervol/web/templates/dashboard.html.j2` :

```jinja
{% extends "base.html.j2" %}
{% block titre_page %}Tableau de bord{% endblock %}
{% block contenu %}
{% if not lignes %}
  <p>Aucun trajet suivi. <a href="/routes">Créer un trajet.</a></p>
{% else %}
<table>
  <thead>
    <tr><th>Trajet</th><th>Plus bas du jour</th><th>Écart médiane</th><th>90 jours</th></tr>
  </thead>
  <tbody>
  {% for ligne in lignes %}
    <tr>
      <td>
        <strong>{{ ligne.route.label }}</strong><br>
        <span class="discret">
          {{ ligne.route.origins | join(', ') }} → {{ ligne.route.destinations | join(', ') }}
          {% if not ligne.route.active %} · désactivé{% endif %}
        </span>
      </td>
      <td>
        {% if ligne.price_cad is none %}
          <span class="discret">aucun relevé</span>
        {% else %}
          <strong{% if ligne.is_find %} class="trouvaille"{% endif %}>{{ ligne.price_cad }} $</strong>
          <br><span class="discret">{{ ligne.provider }}</span>
        {% endif %}
      </td>
      <td>
        {% if ligne.history_building %}
          <span class="discret">historique en constitution</span>
        {% elif ligne.gap_vs_median is not none %}
          {{ (ligne.gap_vs_median * 100) | round | int }} %
          <br><span class="discret">médiane {{ ligne.median_price | round | int }} $</span>
        {% endif %}
      </td>
      <td>
        {% if ligne.points %}
          <svg width="240" height="48" role="img" aria-label="historique des prix">
            <polyline fill="none" stroke="currentColor" stroke-width="1.5"
                      points="{{ ligne.points }}"></polyline>
          </svg>
        {% endif %}
      </td>
    </tr>
  {% endfor %}
  </tbody>
</table>
{% endif %}
{% endblock %}
```

`scrappervol/web/templates/health.html.j2` :

```jinja
{% extends "base.html.j2" %}
{% block titre_page %}Santé des sources{% endblock %}
{% block contenu %}
<table>
  <thead>
    <tr><th>Source</th><th>Dernier succès</th><th>Échecs consécutifs</th>
        <th>Offres au dernier passage</th><th>Dernière erreur</th></tr>
  </thead>
  <tbody>
  {% for s in sources %}
    <tr{% if s.is_stale %} class="muet"{% endif %}>
      <td>{{ s.provider }}</td>
      <td>{% if s.last_success_at %}{{ s.last_success_at.strftime('%Y-%m-%d %H:%M') }} UTC
          {% else %}jamais{% endif %}</td>
      <td>{{ s.consecutive_failures }}</td>
      <td>{{ s.offers_last_run }}</td>
      <td class="discret">{{ s.last_error or '—' }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
<p class="discret">Une source est signalée en rouge si elle n'a rien produit depuis plus de 48 h.</p>
{% endblock %}
```

- [ ] **Étape 6 : écrire `scrappervol/web/app.py`**

```python
from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine
from sqlmodel import Session

from scrappervol.config import Settings

DOSSIER_GABARITS = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(DOSSIER_GABARITS))


def get_session() -> Iterator[Session]:  # pragma: no cover — surchargé en test et à la création
    raise RuntimeError("dépendance de session non configurée")


def create_app(
    engine: Engine, settings: Settings, lifespan: Callable | None = None
) -> FastAPI:
    """Construit l'application web. `lifespan` sert au point d'entrée pour y greffer l'ordonnanceur."""
    application = FastAPI(title="ScrapperVol", docs_url=None, redoc_url=None, lifespan=lifespan)
    application.state.settings = settings
    application.state.engine = engine

    def _session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    application.dependency_overrides[get_session] = _session

    from scrappervol.web.routes import router

    application.include_router(router)
    return application
```

- [ ] **Étape 7 : écrire `scrappervol/web/routes.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from scrappervol.detection.rules import (
    SEUIL_SOURCE_MUETTE_H,
    PriceContext,
    is_find,
    relative_gap,
)
from scrappervol.storage import repo
from scrappervol.storage.models import ProviderHealth, Route
from scrappervol.web.app import get_session, templates
from scrappervol.web.charts import sparkline_points

router = APIRouter()


@dataclass
class LigneTableau:
    route: Route
    price_cad: int | None
    provider: str
    median_price: float | None
    gap_vs_median: float | None
    is_find: bool
    history_building: bool
    points: str


def _ligne(session: Session, route: Route, settings, now: datetime) -> LigneTableau:
    jour = now.date()
    ligne = repo.daily_low_for(session, route.id, jour)
    historique = repo.daily_low_history(
        session, route.id, before_day=jour, window_days=settings.history_window_days
    )
    contexte = PriceContext(daily_lows=historique)
    mediane = contexte.median_price
    prix = ligne.price_cad if ligne else None

    return LigneTableau(
        route=route,
        price_cad=prix,
        provider=ligne.provider if ligne else "",
        median_price=mediane,
        gap_vs_median=relative_gap(prix, mediane) if prix and mediane else None,
        is_find=(
            is_find(
                price_cad=prix,
                context=contexte,
                target_price_cad=route.target_price_cad,
                find_threshold=settings.find_threshold,
                min_history_days=settings.min_history_days,
            )
            if prix
            else False
        ),
        history_building=not contexte.has_significant_history(settings.min_history_days),
        points=sparkline_points(list(reversed(historique))),
    )


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    settings = request.app.state.settings
    maintenant = datetime.now(UTC)
    trajets = session.exec(select(Route).order_by(Route.id)).all()
    lignes = [_ligne(session, trajet, settings, maintenant) for trajet in trajets]
    return templates.TemplateResponse(request, "dashboard.html.j2", {"lignes": lignes})


@router.get("/health", response_class=HTMLResponse)
def health(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    settings = request.app.state.settings
    maintenant = datetime.now(UTC)

    sources = []
    for nom in settings.enabled_providers:
        sante = session.get(ProviderHealth, nom) or ProviderHealth(provider=nom)
        heures = (
            (maintenant - sante.last_success_at).total_seconds() / 3600
            if sante.last_success_at
            else None
        )
        sante.is_stale = heures is None or heures > SEUIL_SOURCE_MUETTE_H
        sources.append(sante)

    return templates.TemplateResponse(request, "health.html.j2", {"sources": sources})
```

`sante.is_stale` est posé sur l'instance sans être une colonne : SQLModel l'accepte pour un objet non
attaché, et cela évite d'introduire un second type juste pour le gabarit.

- [ ] **Étape 8 : vérifier que les tests passent**

```bash
./dev test tests/web/ -v
./dev lint
```

Attendu : 14 tests passés (5 pour les graphes, 9 pour le tableau de bord).

- [ ] **Étape 9 : committer**

```bash
git add scrappervol/web tests/web
git commit -m "feat: tableau de bord et page de santé avec graphes SVG côté serveur"
```

---

## Tâche 19 : interface web — gestion des trajets

Le CRUD demandé au §9 du design, avec un formulaire qui s'adapte à la politique de dates choisie.

**Fichiers :**
- Modifier : `scrappervol/web/routes.py`
- Créer : `scrappervol/web/forms.py`
- Créer : `scrappervol/web/templates/routes.html.j2`, `route_form.html.j2`, `policy_fields.html.j2`,
  `route_row.html.j2`
- Test : `tests/web/test_routes_crud.py`, `tests/web/test_forms.py`

**Interfaces :**
- Consomme : `Route` (tâche 4), `DatePolicyKind`, `TripType` (tâche 2).
- Produit :
  - `forms.parse_airports(text: str) -> list[str]`
  - `forms.build_policy_params(date_policy, form: dict) -> dict`
  - `forms.RouteFormError(ValueError)`
  - `forms.validate_route_form(form: dict) -> dict` — retourne les champs prêts pour `Route(**champs)`
  - Routes `GET /routes`, `POST /routes`, `GET /routes/{id}/edit`, `POST /routes/{id}`,
    `POST /routes/{id}/toggle`, `POST /routes/{id}/delete`, `GET /routes/policy-fields`

- [ ] **Étape 1 : écrire le test des formulaires**

`tests/web/test_forms.py` :

```python
import pytest

from scrappervol.core.types import DatePolicyKind
from scrappervol.web.forms import (
    RouteFormError,
    build_policy_params,
    parse_airports,
    validate_route_form,
)


def test_les_aeroports_sont_normalises_en_majuscules():
    assert parse_airports("yul, yqb") == ["YUL", "YQB"]


def test_les_separateurs_multiples_sont_acceptes():
    assert parse_airports("YUL YQB,CDG") == ["YUL", "YQB", "CDG"]


def test_les_entrees_vides_sont_ecartees():
    assert parse_airports("YUL,,  ,YQB") == ["YUL", "YQB"]


def test_une_liste_daeroports_vide_leve():
    with pytest.raises(RouteFormError):
        validate_route_form({"label": "X", "origins": "", "destinations": "CDG",
                             "date_policy": "fixed", "depart": "2027-03-12"})


def test_un_libelle_vide_leve():
    with pytest.raises(RouteFormError):
        validate_route_form({"label": "", "origins": "YUL", "destinations": "CDG",
                             "date_policy": "fixed", "depart": "2027-03-12"})


def test_les_parametres_fixed_sont_construits():
    params = build_policy_params(
        DatePolicyKind.FIXED,
        {"depart": "2027-03-12", "retour": "2027-03-22", "flex_days": "3"},
    )

    assert params == {"depart": "2027-03-12", "retour": "2027-03-22", "flex_days": 3}


def test_fixed_sans_date_de_depart_leve():
    with pytest.raises(RouteFormError):
        build_policy_params(DatePolicyKind.FIXED, {"depart": ""})


def test_les_parametres_window_sont_construits():
    params = build_policy_params(
        DatePolicyKind.WINDOW,
        {"mois": "2027-03, 2027-04", "sejour_min": "8", "sejour_max": "12"},
    )

    assert params == {"mois": ["2027-03", "2027-04"], "sejour_min": 8, "sejour_max": 12}


def test_window_sans_mois_leve():
    with pytest.raises(RouteFormError):
        build_policy_params(DatePolicyKind.WINDOW, {"mois": ""})


def test_les_parametres_flexible_sont_construits():
    params = build_policy_params(
        DatePolicyKind.FLEXIBLE,
        {"horizon_mois": "12", "sejour_min": "7", "sejour_max": "14"},
    )

    assert params == {"horizon_mois": 12, "sejour_min": 7, "sejour_max": 14}


def test_un_sejour_min_superieur_au_max_leve():
    with pytest.raises(RouteFormError):
        build_policy_params(DatePolicyKind.FLEXIBLE,
                            {"horizon_mois": "12", "sejour_min": "20", "sejour_max": "7"})


def test_le_formulaire_complet_produit_les_champs_du_modele():
    champs = validate_route_form(
        {
            "label": "Paris au printemps",
            "origins": "YUL, YQB",
            "destinations": "CDG",
            "date_policy": "fixed",
            "trip_type": "round_trip",
            "passengers": "2",
            "max_stops": "1",
            "target_price_cad": "600",
            "exception_threshold": "0.35",
            "depart": "2027-03-12",
            "retour": "2027-03-22",
            "flex_days": "3",
        }
    )

    assert champs["label"] == "Paris au printemps"
    assert champs["origins"] == ["YUL", "YQB"]
    assert champs["passengers"] == 2
    assert champs["max_stops"] == 1
    assert champs["target_price_cad"] == 600
    assert champs["exception_threshold"] == 0.35
    assert champs["policy_params"]["flex_days"] == 3


def test_les_champs_facultatifs_vides_deviennent_none():
    champs = validate_route_form(
        {"label": "X", "origins": "YUL", "destinations": "CDG", "date_policy": "fixed",
         "depart": "2027-03-12", "max_stops": "", "target_price_cad": ""}
    )

    assert champs["max_stops"] is None
    assert champs["target_price_cad"] is None


def test_un_seuil_dexception_hors_bornes_leve():
    with pytest.raises(RouteFormError):
        validate_route_form({"label": "X", "origins": "YUL", "destinations": "CDG",
                             "date_policy": "fixed", "depart": "2027-03-12",
                             "exception_threshold": "1.5"})
```

- [ ] **Étape 2 : écrire `scrappervol/web/forms.py`**

```python
from __future__ import annotations

import re

from scrappervol.core.types import DatePolicyKind, TripType

_SEPARATEURS = re.compile(r"[,\s;]+")


class RouteFormError(ValueError):
    """Saisie invalide dans le formulaire de trajet."""


def parse_airports(text: str) -> list[str]:
    return [code.strip().upper() for code in _SEPARATEURS.split(text or "") if code.strip()]


def _entier(valeur: str | None, defaut: int | None = None) -> int | None:
    if valeur is None or str(valeur).strip() == "":
        return defaut
    try:
        return int(valeur)
    except ValueError as erreur:
        raise RouteFormError(f"nombre attendu, reçu « {valeur} »") from erreur


def build_policy_params(date_policy: DatePolicyKind, form: dict) -> dict:
    if date_policy is DatePolicyKind.FIXED:
        depart = (form.get("depart") or "").strip()
        if not depart:
            raise RouteFormError("une politique à dates fixes exige une date de départ")
        params: dict = {"depart": depart}
        retour = (form.get("retour") or "").strip()
        if retour:
            params["retour"] = retour
        flex = _entier(form.get("flex_days"), 0)
        if flex:
            params["flex_days"] = flex
        return params

    sejour_min = _entier(form.get("sejour_min"), 7) or 7
    sejour_max = _entier(form.get("sejour_max"), 14) or 14
    if sejour_min > sejour_max:
        raise RouteFormError("le séjour minimal dépasse le séjour maximal")

    if date_policy is DatePolicyKind.WINDOW:
        mois = [m.strip() for m in _SEPARATEURS.split(form.get("mois") or "") if m.strip()]
        if not mois:
            raise RouteFormError("une politique par fenêtre exige au moins un mois")
        return {"mois": mois, "sejour_min": sejour_min, "sejour_max": sejour_max}

    horizon = _entier(form.get("horizon_mois"), 12) or 12
    return {"horizon_mois": horizon, "sejour_min": sejour_min, "sejour_max": sejour_max}


def validate_route_form(form: dict) -> dict:
    label = (form.get("label") or "").strip()
    if not label:
        raise RouteFormError("le libellé est obligatoire")

    origines = parse_airports(form.get("origins", ""))
    destinations = parse_airports(form.get("destinations", ""))
    if not origines:
        raise RouteFormError("au moins une origine est requise")
    if not destinations:
        raise RouteFormError("au moins une destination est requise")

    politique = DatePolicyKind(form.get("date_policy") or DatePolicyKind.FLEXIBLE)

    seuil = form.get("exception_threshold")
    seuil = 0.40 if seuil in (None, "") else float(seuil)
    if not 0 < seuil < 1:
        raise RouteFormError("le seuil d'exception doit être strictement compris entre 0 et 1")

    return {
        "label": label,
        "origins": origines,
        "destinations": destinations,
        "date_policy": politique,
        "policy_params": build_policy_params(politique, form),
        "trip_type": TripType(form.get("trip_type") or TripType.ROUND_TRIP),
        "passengers": _entier(form.get("passengers"), 1) or 1,
        "max_stops": _entier(form.get("max_stops"), None),
        "target_price_cad": _entier(form.get("target_price_cad"), None),
        "exception_threshold": seuil,
    }
```

- [ ] **Étape 3 : écrire le test du CRUD**

`tests/web/test_routes_crud.py` :

```python
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from scrappervol.config import Settings
from scrappervol.core.types import DatePolicyKind
from scrappervol.storage.models import Route
from scrappervol.web.app import create_app, get_session


@pytest.fixture
def client(engine, session):
    application = create_app(engine, Settings())
    application.dependency_overrides[get_session] = lambda: session
    return TestClient(application)


FORMULAIRE = {
    "label": "Paris au printemps",
    "origins": "YUL, YQB",
    "destinations": "CDG",
    "date_policy": "fixed",
    "trip_type": "round_trip",
    "passengers": "1",
    "depart": "2027-03-12",
    "retour": "2027-03-22",
    "flex_days": "3",
    "exception_threshold": "0.40",
}


def test_la_page_des_trajets_repond(client):
    assert client.get("/routes").status_code == 200


def test_creation_dun_trajet(client, session):
    reponse = client.post("/routes", data=FORMULAIRE, follow_redirects=False)

    assert reponse.status_code in (200, 303)
    trajet = session.exec(select(Route)).one()
    assert trajet.label == "Paris au printemps"
    assert trajet.origins == ["YUL", "YQB"]
    assert trajet.policy_params["flex_days"] == 3
    assert trajet.active is True


def test_un_formulaire_invalide_est_refuse_et_explique(client, session):
    reponse = client.post("/routes", data={**FORMULAIRE, "label": ""})

    assert reponse.status_code == 422
    assert "libellé" in reponse.text
    assert session.exec(select(Route)).all() == []


def test_modification_dun_trajet(client, session):
    client.post("/routes", data=FORMULAIRE)
    trajet = session.exec(select(Route)).one()

    client.post(f"/routes/{trajet.id}", data={**FORMULAIRE, "label": "Paris en avril"})

    session.refresh(trajet)
    assert trajet.label == "Paris en avril"


def test_le_formulaire_de_modification_est_prerempli(client, session):
    client.post("/routes", data=FORMULAIRE)
    trajet = session.exec(select(Route)).one()

    corps = client.get(f"/routes/{trajet.id}/edit").text

    assert "Paris au printemps" in corps
    assert "YUL, YQB" in corps or "YUL,YQB" in corps


def test_activation_et_desactivation(client, session):
    client.post("/routes", data=FORMULAIRE)
    trajet = session.exec(select(Route)).one()

    client.post(f"/routes/{trajet.id}/toggle")
    session.refresh(trajet)
    assert trajet.active is False

    client.post(f"/routes/{trajet.id}/toggle")
    session.refresh(trajet)
    assert trajet.active is True


def test_suppression_dun_trajet(client, session):
    client.post("/routes", data=FORMULAIRE)
    trajet = session.exec(select(Route)).one()

    client.post(f"/routes/{trajet.id}/delete")

    assert session.exec(select(Route)).all() == []


def test_agir_sur_un_trajet_inexistant_retourne_404(client):
    assert client.post("/routes/999/toggle").status_code == 404
    assert client.post("/routes/999/delete").status_code == 404
    assert client.get("/routes/999/edit").status_code == 404


def test_les_champs_de_politique_sont_servis_a_la_demande(client):
    fixe = client.get("/routes/policy-fields", params={"date_policy": "fixed"}).text
    fenetre = client.get("/routes/policy-fields", params={"date_policy": "window"}).text
    flexible = client.get("/routes/policy-fields", params={"date_policy": "flexible"}).text

    assert "flex_days" in fixe
    assert "mois" in fenetre
    assert "horizon_mois" in flexible
    assert "<html" not in fixe


def test_une_politique_inconnue_retourne_422(client):
    assert client.get("/routes/policy-fields", params={"date_policy": "n_importe_quoi"}).status_code == 422


def test_creation_dun_trajet_en_politique_fenetre(client, session):
    client.post(
        "/routes",
        data={
            "label": "Sud cet hiver",
            "origins": "YUL",
            "destinations": "CUN, PUJ",
            "date_policy": "window",
            "mois": "2027-01, 2027-02",
            "sejour_min": "7",
            "sejour_max": "10",
        },
    )

    trajet = session.exec(select(Route)).one()
    assert trajet.date_policy is DatePolicyKind.WINDOW
    assert trajet.policy_params["mois"] == ["2027-01", "2027-02"]
```

- [ ] **Étape 4 : écrire les gabarits du CRUD**

`scrappervol/web/templates/policy_fields.html.j2` :

```jinja
{% if date_policy == 'fixed' %}
  <div class="ligne">
    <div><label for="depart">Départ</label>
      <input type="date" id="depart" name="depart" value="{{ params.get('depart', '') }}"></div>
    <div><label for="retour">Retour</label>
      <input type="date" id="retour" name="retour" value="{{ params.get('retour', '') }}"></div>
    <div><label for="flex_days">Souplesse (jours)</label>
      <input type="number" id="flex_days" name="flex_days" min="0" max="14"
             value="{{ params.get('flex_days', 0) }}"></div>
  </div>
{% elif date_policy == 'window' %}
  <label for="mois">Mois visés (AAAA-MM, séparés par des virgules)</label>
  <input type="text" id="mois" name="mois" placeholder="2027-03, 2027-04"
         value="{{ params.get('mois', []) | join(', ') }}">
  <div class="ligne">
    <div><label for="sejour_min">Séjour minimal (jours)</label>
      <input type="number" id="sejour_min" name="sejour_min" min="1"
             value="{{ params.get('sejour_min', 8) }}"></div>
    <div><label for="sejour_max">Séjour maximal (jours)</label>
      <input type="number" id="sejour_max" name="sejour_max" min="1"
             value="{{ params.get('sejour_max', 12) }}"></div>
  </div>
{% else %}
  <div class="ligne">
    <div><label for="horizon_mois">Horizon (mois)</label>
      <input type="number" id="horizon_mois" name="horizon_mois" min="1" max="12"
             value="{{ params.get('horizon_mois', 12) }}"></div>
    <div><label for="sejour_min">Séjour minimal (jours)</label>
      <input type="number" id="sejour_min" name="sejour_min" min="1"
             value="{{ params.get('sejour_min', 7) }}"></div>
    <div><label for="sejour_max">Séjour maximal (jours)</label>
      <input type="number" id="sejour_max" name="sejour_max" min="1"
             value="{{ params.get('sejour_max', 14) }}"></div>
  </div>
{% endif %}
```

`scrappervol/web/templates/route_form.html.j2` :

```jinja
{% extends "base.html.j2" %}
{% block titre_page %}{% if route %}Modifier un trajet{% else %}Nouveau trajet{% endif %}{% endblock %}
{% block contenu %}
{% if erreur %}<p class="muet">{{ erreur }}</p>{% endif %}
<form method="post" action="{% if route %}/routes/{{ route.id }}{% else %}/routes{% endif %}">
  <label for="label">Libellé</label>
  <input type="text" id="label" name="label" required
         value="{{ route.label if route else '' }}" placeholder="Paris au printemps">

  <div class="ligne">
    <div><label for="origins">Origines</label>
      <input type="text" id="origins" name="origins" required placeholder="YUL, YQB"
             value="{{ route.origins | join(', ') if route else 'YUL, YQB' }}"></div>
    <div><label for="destinations">Destinations</label>
      <input type="text" id="destinations" name="destinations" required placeholder="CDG, ORY"
             value="{{ route.destinations | join(', ') if route else '' }}"></div>
  </div>

  <label for="date_policy">Politique de dates</label>
  <select id="date_policy" name="date_policy"
          hx-get="/routes/policy-fields" hx-target="#champs-politique" hx-trigger="change"
          hx-include="this">
    {% for valeur, libelle in [('fixed', 'Dates fixes'), ('window', 'Fenêtre de mois'),
                               ('flexible', 'Totalement souple')] %}
      <option value="{{ valeur }}"
        {% if route and route.date_policy == valeur %}selected{% endif %}>{{ libelle }}</option>
    {% endfor %}
  </select>

  <div id="champs-politique">
    {% include "policy_fields.html.j2" %}
  </div>

  <div class="ligne">
    <div><label for="trip_type">Type</label>
      <select id="trip_type" name="trip_type">
        <option value="round_trip"
          {% if route and route.trip_type == 'round_trip' %}selected{% endif %}>Aller-retour</option>
        <option value="one_way"
          {% if route and route.trip_type == 'one_way' %}selected{% endif %}>Aller simple</option>
      </select></div>
    <div><label for="passengers">Passagers</label>
      <input type="number" id="passengers" name="passengers" min="1"
             value="{{ route.passengers if route else 1 }}"></div>
    <div><label for="max_stops">Escales maximales</label>
      <input type="number" id="max_stops" name="max_stops" min="0" placeholder="sans limite"
             value="{{ route.max_stops if route and route.max_stops is not none else '' }}"></div>
  </div>

  <div class="ligne">
    <div><label for="target_price_cad">Prix cible (CAD)</label>
      <input type="number" id="target_price_cad" name="target_price_cad" min="1" placeholder="aucun"
             value="{{ route.target_price_cad if route and route.target_price_cad else '' }}"></div>
    <div><label for="exception_threshold">Seuil d'aberration</label>
      <input type="number" id="exception_threshold" name="exception_threshold"
             step="0.05" min="0.05" max="0.95"
             value="{{ route.exception_threshold if route else 0.40 }}"></div>
  </div>

  <p><button type="submit">{% if route %}Enregistrer{% else %}Créer le trajet{% endif %}</button>
     <a href="/routes">Annuler</a></p>
</form>
{% endblock %}
```

`scrappervol/web/templates/routes.html.j2` :

```jinja
{% extends "base.html.j2" %}
{% block titre_page %}Trajets{% endblock %}
{% block contenu %}
<p><a href="/routes/new">Nouveau trajet</a></p>
{% if not routes %}
  <p>Aucun trajet pour l'instant.</p>
{% else %}
<table>
  <thead><tr><th>Libellé</th><th>Trajet</th><th>Dates</th><th>État</th><th></th></tr></thead>
  <tbody>
  {% for route in routes %}
    {% include "route_row.html.j2" %}
  {% endfor %}
  </tbody>
</table>
{% endif %}
{% endblock %}
```

`scrappervol/web/templates/route_row.html.j2` :

```jinja
<tr>
  <td>{{ route.label }}</td>
  <td class="discret">{{ route.origins | join(', ') }} → {{ route.destinations | join(', ') }}</td>
  <td class="discret">{{ route.date_policy }}
    {% if route.target_price_cad %}<br>cible {{ route.target_price_cad }} ${% endif %}</td>
  <td>{% if route.active %}actif{% else %}<span class="discret">désactivé</span>{% endif %}</td>
  <td>
    <a href="/routes/{{ route.id }}/edit">modifier</a>
    <form method="post" action="/routes/{{ route.id }}/toggle" style="display:inline">
      <button type="submit">{% if route.active %}désactiver{% else %}activer{% endif %}</button>
    </form>
    <form method="post" action="/routes/{{ route.id }}/delete" style="display:inline"
          onsubmit="return confirm('Supprimer ce trajet et son historique ?')">
      <button type="submit">supprimer</button>
    </form>
  </td>
</tr>
```

- [ ] **Étape 5 : compléter `scrappervol/web/routes.py`**

Ajouter aux imports :

```python
from fastapi import Form, HTTPException, Query
from fastapi.responses import RedirectResponse

from scrappervol.core.types import DatePolicyKind
from scrappervol.web.forms import RouteFormError, validate_route_form
```

puis les routes :

```python
@router.get("/routes", response_class=HTMLResponse)
def liste_trajets(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    trajets = session.exec(select(Route).order_by(Route.id)).all()
    return templates.TemplateResponse(request, "routes.html.j2", {"routes": trajets})


@router.get("/routes/new", response_class=HTMLResponse)
def nouveau_trajet(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "route_form.html.j2",
        {"route": None, "date_policy": "fixed", "params": {}, "erreur": None},
    )


@router.get("/routes/policy-fields", response_class=HTMLResponse)
def champs_politique(request: Request, date_policy: str = Query(...)) -> HTMLResponse:
    if date_policy not in {p.value for p in DatePolicyKind}:
        raise HTTPException(status_code=422, detail="politique de dates inconnue")
    return templates.TemplateResponse(
        request, "policy_fields.html.j2", {"date_policy": date_policy, "params": {}}
    )


@router.get("/routes/{route_id}/edit", response_class=HTMLResponse)
def editer_trajet(
    request: Request, route_id: int, session: Session = Depends(get_session)
) -> HTMLResponse:
    trajet = session.get(Route, route_id)
    if trajet is None:
        raise HTTPException(status_code=404, detail="trajet introuvable")
    return templates.TemplateResponse(
        request,
        "route_form.html.j2",
        {
            "route": trajet,
            "date_policy": str(trajet.date_policy),
            "params": trajet.policy_params,
            "erreur": None,
        },
    )


async def _champs_du_formulaire(request: Request) -> dict:
    return dict(await request.form())


@router.post("/routes")
async def creer_trajet(request: Request, session: Session = Depends(get_session)):
    formulaire = await _champs_du_formulaire(request)
    try:
        champs = validate_route_form(formulaire)
    except RouteFormError as erreur:
        return templates.TemplateResponse(
            request,
            "route_form.html.j2",
            {
                "route": None,
                "date_policy": formulaire.get("date_policy", "fixed"),
                "params": {},
                "erreur": str(erreur),
            },
            status_code=422,
        )

    trajet = Route(**champs, created_at=datetime.now(UTC))
    session.add(trajet)
    session.commit()
    return RedirectResponse("/routes", status_code=303)


@router.post("/routes/{route_id}")
async def modifier_trajet(
    request: Request, route_id: int, session: Session = Depends(get_session)
):
    trajet = session.get(Route, route_id)
    if trajet is None:
        raise HTTPException(status_code=404, detail="trajet introuvable")

    formulaire = await _champs_du_formulaire(request)
    try:
        champs = validate_route_form(formulaire)
    except RouteFormError as erreur:
        return templates.TemplateResponse(
            request,
            "route_form.html.j2",
            {
                "route": trajet,
                "date_policy": formulaire.get("date_policy", "fixed"),
                "params": trajet.policy_params,
                "erreur": str(erreur),
            },
            status_code=422,
        )

    for nom, valeur in champs.items():
        setattr(trajet, nom, valeur)
    session.add(trajet)
    session.commit()
    return RedirectResponse("/routes", status_code=303)


@router.post("/routes/{route_id}/toggle")
def basculer_trajet(route_id: int, session: Session = Depends(get_session)):
    trajet = session.get(Route, route_id)
    if trajet is None:
        raise HTTPException(status_code=404, detail="trajet introuvable")
    trajet.active = not trajet.active
    session.add(trajet)
    session.commit()
    return RedirectResponse("/routes", status_code=303)


@router.post("/routes/{route_id}/delete")
def supprimer_trajet(route_id: int, session: Session = Depends(get_session)):
    trajet = session.get(Route, route_id)
    if trajet is None:
        raise HTTPException(status_code=404, detail="trajet introuvable")
    session.delete(trajet)
    session.commit()
    return RedirectResponse("/routes", status_code=303)
```

L'ordre de déclaration compte : `/routes/new` et `/routes/policy-fields` doivent précéder
`/routes/{route_id}/edit`, sinon FastAPI tente de convertir `new` en entier.

- [ ] **Étape 6 : vérifier que les tests passent**

```bash
./dev test tests/web/ -v
./dev lint
```

Attendu : 39 tests passés (14 des tâches précédentes, 14 de formulaires, 11 de CRUD).

- [ ] **Étape 7 : committer**

```bash
git add scrappervol/web tests/web
git commit -m "feat: gestion des trajets avec formulaire adapté à la politique de dates"
```

---

## Tâche 20 : assemblage et mise en service

**Fichiers :**
- Créer : `scrappervol/main.py`, `README.md`
- Modifier : `Dockerfile` (commande de démarrage), `docker-compose.yml` (contrôle de santé)
- Test : `tests/test_main.py`

**Interfaces :**
- Consomme : tout ce qui précède.
- Produit : `main.build_application() -> FastAPI` et `main.main() -> None`.

- [ ] **Étape 1 : écrire le test qui échoue**

`tests/test_main.py` :

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scrappervol.main import build_application


def test_lapplication_se_construit(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SMTP_HOST", "")

    application = build_application()

    assert isinstance(application, FastAPI)


def test_le_schema_est_cree_au_demarrage_et_les_pages_repondent(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("ENABLED_PROVIDERS", "")

    with TestClient(build_application()) as client:
        assert client.get("/").status_code == 200
        assert client.get("/routes").status_code == 200
        assert client.get("/health").status_code == 200

    assert (tmp_path / "test.db").exists()


def test_lordonnanceur_demarre_et_sarrete_avec_lapplication(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("ENABLED_PROVIDERS", "")

    application = build_application()
    with TestClient(application):
        assert application.state.scheduler.running is True

    assert application.state.scheduler.running is False
```

- [ ] **Étape 2 : lancer le test et vérifier l'échec**

```bash
./dev test tests/test_main.py -v
```

Attendu : `ModuleNotFoundError: No module named 'scrappervol.main'`.

- [ ] **Étape 3 : écrire `scrappervol/main.py`**

```python
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from scrappervol.config import Settings
from scrappervol.notify.mailer import build_mailer
from scrappervol.scheduler.app import build_scheduler
from scrappervol.storage.db import create_engine_for, init_db
from scrappervol.web.app import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


def build_application() -> FastAPI:
    """Assemble base, ordonnanceur et interface web en une seule application."""
    settings = Settings()
    engine = create_engine_for(settings.database_url)
    init_db(engine)

    mailer = build_mailer(settings)
    ordonnanceur = build_scheduler(engine, settings, mailer)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        ordonnanceur.start()
        logger.info(
            "ScrapperVol démarré — sources : %s",
            ", ".join(settings.enabled_providers) or "aucune",
        )
        try:
            yield
        finally:
            ordonnanceur.shutdown(wait=False)
            logger.info("ScrapperVol arrêté")

    application = create_app(engine, settings, lifespan=lifespan)
    application.state.scheduler = ordonnanceur
    return application


def main() -> None:
    import uvicorn

    uvicorn.run(build_application(), host="0.0.0.0", port=8080, log_level="info")  # noqa: S104


if __name__ == "__main__":
    main()
```

L'écoute sur `0.0.0.0` à l'intérieur du conteneur est sans risque : c'est `docker-compose.yml` qui
restreint la publication à `127.0.0.1`, conformément au §9 du design. Écouter sur la boucle locale du
conteneur rendrait au contraire le service inatteignable depuis l'hôte.

- [ ] **Étape 4 : ajuster le `Dockerfile` et `docker-compose.yml`**

Remplacer la dernière ligne du `Dockerfile` :

```dockerfile
CMD ["python", "-m", "scrappervol.main"]
```

Ajouter au service `app` de `docker-compose.yml` :

```yaml
    healthcheck:
      test: ["CMD", "python", "-c",
             "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')"]
      interval: 60s
      timeout: 10s
      retries: 3
      start_period: 30s
```

- [ ] **Étape 5 : vérifier que les tests passent**

```bash
./dev test -v
./dev lint
```

Attendu : la suite complète au vert, soit environ 200 tests, tous les tests `live` désélectionnés.

- [ ] **Étape 6 : écrire le `README.md`**

```markdown
# ScrapperVol

Veille de prix sur une liste de trajets aériens déclarés. Usage strictement personnel.

Le design complet est dans `docs/superpowers/specs/2026-08-04-scrappervol-design.md`,
le plan d'implémentation dans `docs/superpowers/plans/`.

## Démarrage

```bash
cp .env.example .env      # renseigner les paramètres SMTP et ALERT_TO
./dev build
./dev up
```

Le tableau de bord est alors sur <http://127.0.0.1:8080>. Il n'est accessible que depuis cette
machine : l'application n'a pas d'authentification, et n'a pas vocation à être exposée.

## Utilisation courante

| Commande | Effet |
|---|---|
| `./dev up` | démarre le service en arrière-plan |
| `./dev logs` | suit les journaux |
| `./dev test` | lance la suite de tests |
| `./dev test -m live` | lance les tests réseau, qui touchent les sites des sources |
| `./dev lint` | vérifie le style |
| `./dev shell` | ouvre un interpréteur dans le conteneur |
| `docker compose down` | arrête le service |

## Fonctionnement

Trois sources sont interrogées à des rythmes distincts (Google Flights toutes les 4 h, Air Transat
toutes les 6 h, Air Canada toutes les 8 h). Chaque passage relève les prix des trajets actifs, met à
jour le plus bas du jour et compare à la médiane des 90 derniers jours.

Un courriel de synthèse part chaque jour à 18 h, heure de Montréal, même les jours sans rien de
notable. Un courriel immédiat part si un prix descend à plus de 40 % sous la médiane — sous réserve
d'au moins 14 jours d'historique et d'un prix supérieur à 50 CAD, deux garde-fous contre les fausses
alertes de parsing.

## Quand une source cesse de produire

C'est le scénario attendu : les sites changent leur HTML. La page Santé et le pied du digest quotidien
le signalent. Pour réparer :

1. `./dev shell` puis `python scripts/capture_fixture.py <source>` pour recapturer une réponse réelle.
2. Comparer avec l'ancienne fixture, identifier le champ qui a changé.
3. Corriger le sélecteur dans `scrappervol/providers/<source>.py`.
4. `./dev test tests/providers/` — le test sur fixture désigne précisément ce qui ne passe plus.

La dernière réponse HTML de chaque source est également conservée dans `data/debug/`.
```

- [ ] **Étape 7 : essai de bout en bout**

```bash
./dev up
./dev logs        # vérifier le message « ScrapperVol démarré »
```

Ouvrir <http://127.0.0.1:8080>, créer un trajet réel (par exemple YUL/YQB → CDG/ORY, politique
`window` sur deux mois), vérifier qu'il apparaît sur le tableau de bord, puis consulter la page Santé.

Déclencher un passage sans attendre l'intervalle :

```bash
docker compose exec app python -c "
from datetime import UTC, datetime
from scrappervol.config import Settings
from scrappervol.notify.mailer import build_mailer
from scrappervol.scheduler.app import build_providers
from scrappervol.scheduler.jobs import run_scan, send_digest
from scrappervol.storage.db import create_engine_for, session_scope

reglages = Settings()
moteur = create_engine_for(reglages.database_url)
mailer = build_mailer(reglages)
with session_scope(moteur) as session:
    for source in build_providers(reglages):
        print(run_scan(session, source, reglages, mailer, datetime.now(UTC)))
    print('digest envoyé :', send_digest(session, reglages, mailer, datetime.now(UTC)))
"
```

Vérifier : des observations sont enregistrées, un plus bas du jour apparaît sur le tableau de bord, la
page Santé montre un dernier succès récent, et le courriel de digest est reçu. Si une source échoue,
sa capture de débogage est dans `data/debug/`.

- [ ] **Étape 8 : committer**

```bash
git add scrappervol/main.py tests/test_main.py README.md Dockerfile docker-compose.yml
git commit -m "feat: point d'entrée, mise en service et documentation d'exploitation"
```

---

## Vérification finale

- [ ] `./dev test` — suite complète au vert
- [ ] `./dev lint` — aucun avertissement
- [ ] `./dev test -m live` — les sources retenues répondent réellement
- [ ] Le tableau de bord, la page Trajets et la page Santé répondent sur `127.0.0.1:8080`
- [ ] Un courriel de digest a été reçu au moins une fois
- [ ] `docker compose down && ./dev up` — le service redémarre et retrouve ses données
- [ ] Les trajets réellement souhaités sont créés et actifs

## Ce que ce plan ne fait pas

Conformément au §2 et au §14 du design : pas de détection à l'aveugle, pas de réservation, pas
d'authentification, pas de multi-utilisateur, pas de source Kiwi.com, pas de scraper Sunwing, pas
d'origines transfrontalières. Ces suites ne seront envisagées que sur besoin démontré.
