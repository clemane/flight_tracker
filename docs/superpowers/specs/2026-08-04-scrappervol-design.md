# ScrapperVol — Design

**Date :** 2026-08-04
**Statut :** validé, prêt pour le plan d'implémentation
**Auteur :** Clément

## 1. Objectif

Surveiller en continu le prix d'une liste de trajets aériens choisis, et signaler quand un prix devient
intéressant. L'inspiration est le service québécois *Les Vols d'Alexi*, mais le modèle est inversé :
Alexi diffuse des aubaines à l'aveugle vers 725 000 abonnés sans savoir où chacun veut aller, alors
que ScrapperVol surveille les trajets que son unique utilisateur a réellement l'intention de faire.

Usage strictement personnel, mono-utilisateur, sur la machine de l'auteur.

## 2. Périmètre

**Dans le périmètre**

- Veille de prix sur une liste de trajets déclarés, avec une politique de dates propre à chaque trajet.
- Trois sources de prix : Google Flights, Air Transat, Air Canada.
- Un courriel de synthèse quotidien à 18 h, plus un courriel immédiat pour les prix aberrants.
- Un tableau de bord web local permettant de créer, modifier et supprimer les trajets.
- Un historique de prix conservé durablement, servant de base à la détection d'anomalies.

**Hors périmètre**

- La détection d'aubaines à l'aveugle vers des destinations non déclarées.
- La réservation. L'application n'achète rien : elle renvoie vers la page de réservation de la source.
- Le multi-utilisateur, l'authentification, l'hébergement public.
- Les vols en correspondance auto-connectée (type Kiwi.com) : leur API est fermée aux nouveaux
  partenaires depuis 2025, et aucune alternative gratuite n'existe.

## 3. Décisions de cadrage

| Sujet | Décision | Justification |
|---|---|---|
| Mode | Veille sur trajets déclarés | Plus utile qu'une détection à l'aveugle pour un usage personnel, et bien moins coûteux en requêtes. |
| Dates | Politique par trajet | Certains voyages ont des dates imposées, d'autres sont totalement opportunistes. |
| Origines | YUL et YQB | Les deux aéroports pertinents pour l'auteur. Les origines américaines transfrontalières sont écartées. |
| Hébergement | Local, `docker-compose` | Pas de serveur à gérer ni de coût récurrent. |
| Notification | Courriel + tableau de bord | Choix explicite de l'utilisateur ; ni Telegram ni notification de bureau. |
| Rythme | Digest quotidien à 18 h + exception immédiate | Le digest évite le bruit ; l'exception évite de rater les erreurs de prix, qui durent 2 à 6 h. |
| Sources | Google Flights, Transat, Air Canada | Voir §4. |
| Langage | Python | L'outillage de scraping mature y est concentré. |

### Choix des sources

Google Flights est la colonne vertébrale : une seule intégration couvrant plus de 300 compagnies, dont
Air Canada et Air Transat en tarif régulier. Techniquement, ce n'est pas une API mais un scraper — les
paramètres de recherche sont encodés dans un blob protobuf passé en URL (`tfs`), et le HTML retourné
est parsé. Le mécanisme est stable ; la couche de parsing est la partie fragile.

Air Transat apporte une information que Google Flights ne contient pas : son inventaire charter et
forfait, notamment vers les destinations Sud.

Air Canada est redondant avec Google Flights et constitue la cible la plus difficile (protection
anti-bot commerciale). Il est retenu sur décision explicite de l'utilisateur, qui souhaite une
confirmation des prix directement à la source. Le disjoncteur décrit en §10 garantit que sa fragilité
n'affecte pas les deux autres.

Deux options contractuelles ont été évaluées puis écartées : l'API Amadeus Self-Service (palier gratuit
de 2 000 appels par mois, insuffisant, et mauvaise couverture des transporteurs à bas coût, qui sont
précisément ceux qui bradent) et l'API Tequila de Kiwi.com (fermée aux nouvelles inscriptions).

## 4. Architecture

Un unique conteneur Python, lancé par `docker-compose` avec `restart: unless-stopped`, monté sur un
volume contenant la base SQLite et les captures HTML de débogage. Aucun service externe : au volume
visé — quelques dizaines de trajets, quelques centaines de requêtes par jour — SQLite suffit largement.

| Module | Responsabilité | Dépendances internes |
|---|---|---|
| `core/` | Types partagés : `SearchQuery`, `FlightOffer`, `RoutePolicy` | aucune |
| `providers/` | Un scraper par source, derrière l'interface `PriceProvider` | `core` |
| `storage/` | Modèles SQLModel et accès SQLite | `core` |
| `detection/` | Fonctions pures : plus bas du jour, médiane glissante, seuil d'aberration | `core` |
| `scheduler/` | APScheduler : passages de scan et job du digest | tous |
| `notify/` | Envoi SMTP et gabarits Jinja | `core`, `storage` |
| `web/` | FastAPI, Jinja et HTMX : tableau de bord et CRUD | `core`, `storage`, `detection` |

L'interface commune est le point structurant :

```python
class PriceProvider(Protocol):
    name: str
    def search(self, query: SearchQuery) -> list[FlightOffer]: ...
```

Chaque scraper traduit sa source vers `FlightOffer`, un type normalisé. En conséquence, `detection`,
`notify` et `web` ignorent l'existence de Google Flights, de Transat et d'Air Canada : ils ne
manipulent que des `FlightOffer`. Retirer une source revient à supprimer un fichier ; en ajouter une
revient à en écrire un.

Le choix de FastAPI avec Jinja et HTMX, plutôt qu'un front séparé, évite d'introduire une chaîne de
build JavaScript et un second conteneur dans un projet par ailleurs entièrement Python, tout en
fournissant le CRUD interactif demandé.

## 5. Modèle de données

### `route`

Un trajet représente une **intention de voyage**, pas un couple origine-destination. Il porte donc des
listes d'origines et de destinations, et l'application rapporte le meilleur prix parmi toutes les
combinaisons.

| Colonne | Type | Note |
|---|---|---|
| `id` | int | |
| `label` | str | Ex. « Paris au printemps » |
| `origins` | JSON `list[str]` | Ex. `["YUL", "YQB"]` |
| `destinations` | JSON `list[str]` | Ex. `["CDG", "ORY"]` |
| `date_policy` | enum | `fixed`, `window`, `flexible` |
| `policy_params` | JSON | Paramètres propres à la politique, voir ci-dessous |
| `trip_type` | enum | `round_trip`, `one_way` |
| `passengers` | int | Défaut 1 |
| `max_stops` | int, nullable | `null` = sans limite |
| `target_price_cad` | int, nullable | Seuil absolu facultatif |
| `exception_threshold` | float | Défaut `0.40` — voir §6 |
| `active` | bool | |
| `created_at` | datetime | |

Politiques de dates :

- `fixed` — `{"depart": "2027-03-12", "retour": "2027-03-22", "flex_days": 3}`
- `window` — `{"mois": ["2027-03", "2027-04"], "sejour_min": 8, "sejour_max": 12}`
- `flexible` — `{"horizon_mois": 12, "sejour_min": 7, "sejour_max": 14}`

### `observation`

Une ligne par offre relevée : `id`, `route_id`, `provider`, `observed_at`, `price_cad`,
`currency_original`, `price_original`, `departure_date`, `return_date`, `origin`, `destination`,
`airline`, `stops`, `duration_minutes`, `deep_link`, `offer_hash`, `raw` (JSON).

`offer_hash` est le condensat de `(provider, origin, destination, departure_date, return_date,
airline, stops)`. Il sert à dédupliquer au sein d'un passage et à suivre une même offre dans le temps.

### `daily_low`

Le plus bas du jour par trajet : clé primaire `(route_id, day)`, plus `price_cad`, `observation_id`,
`provider`. Table matérialisée, mise à jour par écrasement lorsqu'un prix inférieur est relevé.

### `provider_health`

`provider`, `last_success_at`, `consecutive_failures`, `disabled_until`, `last_error`,
`offers_last_run`.

### `alert`

`id`, `route_id`, `observation_id` (nullable), `kind` (`digest` ou `exception`), `sent_at`,
`payload` (JSON). Sert de journal et empêche le renvoi d'une alerte déjà émise.

### Rétention

`observation` est purgé au-delà de 90 jours. `daily_low` est conservé indéfiniment : c'est la série
historique qui alimente la détection, et son volume est négligeable (365 lignes par trajet et par an).

## 6. Logique de détection

### Plus bas du jour

À chaque passage, le minimum des offres retenues pour un trajet écrase la valeur de `daily_low` du jour
si elle lui est inférieure.

### Aberration

Calculée sur la série des `daily_low` des 90 derniers jours du trajet, avec la **médiane** et l'**écart
absolu médian (MAD)**, et non la moyenne et l'écart-type. La raison est structurelle : l'écart-type est
lui-même gonflé par les valeurs extrêmes, si bien que plus une aubaine est spectaculaire, plus elle
élargit la bande censée la détecter. Un détecteur fondé sur l'écart-type reste muet précisément quand
il devrait sonner.

Une alerte d'exception est émise si toutes les conditions suivantes sont réunies :

1. Le trajet dispose d'au moins **14 jours** d'historique. En deçà, aucune exception n'est émise :
   sans ce garde-fou, les deux premières semaines produisent une fausse alerte à chaque passage.
2. Le prix est inférieur ou égal à `mediane × (1 − exception_threshold)`, seuil par défaut à 40 %.
3. Le prix est supérieur à un **plancher de crédibilité** de 50 CAD. La première cause de fausse
   alerte n'est pas une vraie erreur de prix, mais un scraper ayant confondu un nombre — typiquement
   lire « 45 » dans un libellé « 45 min d'escale ». Ce plancher évite le courriel nocturne consécutif
   à un défaut de parsing.
4. Aucune alerte n'a déjà été émise pour le même `offer_hash` tant qu'il demeure sous le seuil.

Le champ facultatif `target_price_cad` fonctionne indépendamment : une offre passant sous ce seuil est
mise en avant dans le digest, sans déclencher de courriel immédiat.

## 7. Scrapers

### Cadence

Chaque scraper a son propre intervalle, ce qui permet de ménager les cibles les plus protégées :

| Scraper | Intervalle | Passages par jour |
|---|---|---|
| Google Flights | 4 h | 6 |
| Air Transat | 6 h | 4 |
| Air Canada | 8 h | 3 |

Avec une dizaine de trajets et environ deux requêtes par trajet et par passage, cela représente de
l'ordre de 260 requêtes par jour, réparties entre trois destinataires distincts. Les passages
sont décalés aléatoirement plutôt que déclenchés à l'heure ronde, et les requêtes d'un même passage
sont séquentielles avec une pause aléatoire de 5 à 20 secondes.

### Google Flights

Construction du paramètre `tfs` (protobuf encodé en base64) puis parsing du HTML. La bibliothèque
`fast-flights` est utilisée ; elle propose depuis sa version 2.2 un repli sur Playwright local, activé
automatiquement lorsque le parsing direct ne retourne rien. Le marché est fixé au Canada et la devise
au dollar canadien.

Pour les politiques `window` et `flexible`, la grille calendaire de Google Flights retourne le prix le
plus bas par date sur une fenêtre d'environ deux mois en une seule requête, ce qui évite d'énumérer les
combinaisons de dates.

### Air Transat

Playwright, sur le moteur de recherche public du site. Cible l'inventaire vol sec et forfait.

### Air Canada

Playwright avec contexte persistant, agent utilisateur et fenêtre réalistes, et `playwright-stealth`.
C'est la cible la plus protégée du lot ; sa faisabilité effective doit être confirmée par un essai
technique court en début d'implémentation. Si l'essai échoue, le scraper est abandonné sans impact sur
le reste du système : l'information qu'il apporte est déjà couverte par Google Flights.

### Devise

Toutes les offres sont normalisées en dollars canadiens. Les trois sources servent nativement en CAD
depuis une adresse IP canadienne. La devise d'origine est néanmoins conservée en base, afin de détecter
toute dérive.

## 8. Notifications

Envoi par SMTP, configuré par variables d'environnement (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`,
`SMTP_PASSWORD`, `SMTP_FROM`, `ALERT_TO`). Gabarits Jinja, en HTML avec une version texte.

### Digest quotidien — 18 h, heure de `America/Toronto`

Objet : `ScrapperVol — <n> trouvailles du <date>`.

Une **trouvaille** se définit comme un trajet dont le plus bas du jour est passé sous son
`target_price_cad`, ou d'au moins 15 % sous sa médiane sur 90 jours. `<n>` compte ces trajets ; il
vaut `0` les jours sans rien de notable, et le courriel est alors envoyé quand même, en version courte.

Contenu, un bloc par trajet actif : libellé, meilleur prix du jour, transporteur, dates, écart relatif
à la médiane sur 90 jours, écart par rapport à la veille, et lien de réservation. Les trajets sont
triés par écart à la médiane, les meilleures affaires en tête.

Tant qu'un trajet n'a pas atteint 14 jours d'historique, sa médiane n'est pas significative : le bloc
affiche alors le prix du jour et la mention « historique en constitution », sans écart ni classement,
et le trajet ne peut pas compter comme trouvaille.

Le pied de courriel porte **systématiquement** l'état de santé des trois scrapers. Si l'un d'eux est
muet depuis plus de 48 h, un bandeau le signale en tête de message. Cette exigence découle du risque
principal du système, décrit en §10.

Aucun courriel n'est envoyé si aucun trajet n'est actif.

### Alerte d'exception — immédiate

Objet : `ScrapperVol — <destination> à <prix> $ (<écart> % sous la médiane)`.

Contenu : l'offre, son contexte historique et le lien de réservation.

## 9. Interface web

FastAPI servant des gabarits Jinja, avec HTMX pour l'interactivité. Écoute sur `127.0.0.1:8080`,
donc accessible uniquement depuis la machine hôte. **Aucune authentification** : l'application n'est
pas exposée au réseau, et un mécanisme d'authentification n'apporterait ici qu'une complexité inutile.

Trois pages :

- **Tableau de bord** — liste des trajets, plus bas du jour, écart à la médiane, et un graphe
  d'historique par trajet.
- **Trajets** — création, modification, suppression, activation et désactivation. Formulaire adapté
  dynamiquement à la politique de dates choisie.
- **Santé** — dernier succès par scraper, nombre d'échecs consécutifs, dernière erreur, et nombre
  d'offres relevées par passage sur 7 jours.

## 10. Résilience et gestion d'erreurs

**Le risque principal de ce système n'est pas la panne, mais la panne silencieuse** : un digest arrivant
fidèlement chaque soir en annonçant qu'il n'y a rien à signaler, alors que les scrapers sont hors
service depuis des semaines. Les mécanismes ci-dessous en découlent.

**Isolation.** Chaque appel à `search()` est encapsulé ; une exception ne peut pas interrompre le
passage en cours. L'échec est enregistré dans `provider_health`.

**Disjoncteur par scraper.** Après trois échecs consécutifs, le scraper est mis au repos avec un délai
doublant à chaque déclenchement — 1 h, 2 h, 4 h, plafonné à 24 h — puis réveillé par une requête-sonde.
Ce mécanisme évite de marteler une protection anti-bot, ce qui transformerait un blocage temporaire en
bannissement durable.

**Traitement du succès vide.** Une recherche qui retourne zéro offre sur un trajet qui en retournait
la veille est comptée comme un **échec**, non comme un succès. Sans cette règle, une dérive de
sélecteur — techniquement un HTTP 200 sans exception — passerait inaperçue.

**Captures de débogage.** La dernière réponse HTML de chaque scraper est conservée sur le volume, afin
de permettre une réparation sans avoir à reproduire le problème.

**Absence de réessai immédiat.** Un échec est simplement reporté au passage suivant.

**Visibilité.** L'état de santé est présent dans le digest quotidien et sur la page Santé.

## 11. Tests

Par ordre d'importance :

1. **Parsing sur fixtures HTML.** Une réponse réelle par scraper est enregistrée dans
   `tests/fixtures/`, et le parsing est testé hors ligne. Lorsqu'une source modifie son HTML, il
   suffit de remplacer la fixture et de relancer : le test désigne le champ qui a changé. C'est ce qui
   ramène une réparation à une vingtaine de minutes.
2. **Détection.** Fonctions pures testées sur des séries de prix synthétiques, en couvrant
   explicitement les cas à risque : historique trop court (aucune alerte), prix sous le plancher de
   crédibilité (aucune alerte), aberration franche (alerte), même aberration au passage suivant
   (silence).
3. **Fumée réseau.** Marqué `live` et exclu par défaut, lancé manuellement pour vérifier que les
   scrapers visent encore juste. Volontairement hors intégration continue : un test dépendant d'un
   site tiers casse de lui-même et finit par être ignoré, ce qui est pire que son absence.

## 12. Déploiement et configuration

`docker-compose` avec un unique service, `restart: unless-stopped`, un volume pour `data/`
(base SQLite et captures HTML), et le port `8080` publié sur `127.0.0.1` uniquement.

Configuration par variables d'environnement dans un fichier `.env` non versionné : paramètres SMTP,
destinataire des alertes, fuseau horaire, intervalles par scraper, seuils par défaut.

Les trajets ne sont pas configurés par fichier mais stockés en base et gérés depuis l'interface web.

## 13. Risques connus

| Risque | Portée | Atténuation |
|---|---|---|
| Dérive du HTML d'une source | Le scraper concerné cesse de produire | Détection du succès vide, tests sur fixtures, captures de débogage |
| Blocage anti-bot d'Air Canada | Perte d'une source redondante | Faible cadence, Playwright furtif, disjoncteur ; information déjà couverte par Google Flights |
| Faux positifs de parsing | Alerte injustifiée | Plancher de crédibilité, anti-répétition |
| Conditions d'utilisation | Le scraping automatisé contrevient aux CGU des sources visées | Usage strictement personnel, volume très faible, aucune redistribution ni revente |
| Angle mort d'inventaire | Certaines offres restent invisibles | Accepté en phase 1 ; une source supplémentaire ne sera ajoutée que sur constat concret de manque |

## 14. Suites possibles

Non retenues en phase 1, à envisager seulement sur besoin démontré : un scraper Sunwing pour les
destinations Sud, la prise en compte des origines transfrontalières (BTV, PBG) avec conversion USD, et
la surveillance de trajets sans destination déclarée, à la manière d'Alexi.
