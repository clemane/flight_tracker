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

## Vérifier que l'installation fonctionne

Le premier passage de collecte n'a lieu qu'au bout de plusieurs heures et le digest ne part qu'à
18 h : au démarrage, le tableau de bord reste donc vide un long moment, sans que rien distingue
une installation qui marche d'une installation en panne. `./dev once` force ces passages.

1. Déclarer un trajet sur <http://127.0.0.1:8080/routes>. Un aller-retour à dates fixes, à deux ou
   trois mois d'ici, donne le résultat le plus lisible.
2. `./dev once scan` — compter une quarantaine de secondes, Air Transat étant pilotée dans un
   navigateur. La commande affiche, pour chaque source, le nombre d'offres relevées. Une source à
   zéro offre, ou en échec, est le signal à suivre : les journaux et `data/debug/` en disent la
   raison.
3. Recharger le tableau de bord : le trajet doit porter un prix. La page Santé doit montrer les
   deux sources avec un dernier succès à l'instant.
4. `./dev once preview` affiche le digest tel qu'il partirait le soir même.

Les deux sources étant indépendantes, leurs prix se recoupent : sur un même vol, un écart de
quelques dollars est normal, un facteur 2 signale une lecture faussée.

Le premier jour, chaque trajet porte la mention « historique en constitution » : la détection
d'aubaines exige 14 jours de relevés, en deçà desquels aucune alerte ne part — c'est voulu, une
médiane calculée sur trois points n'a aucun sens.

Tant que `SMTP_HOST` garde sa valeur d'exemple, aucun courriel ne part. Le rendu reste consultable
par `./dev once preview` ; pour recevoir réellement les courriels, renseigner un serveur SMTP réel
dans `.env` — l'envoi passe par STARTTLS, avec authentification si `SMTP_USER` est renseigné.

## Utilisation courante

| Commande | Effet |
|---|---|
| `./dev up` | démarre le service en arrière-plan |
| `./dev logs` | suit les journaux |
| `./dev test` | lance la suite de tests |
| `./dev test -m live` | lance les tests réseau, qui touchent les sites des sources |
| `./dev lint` | vérifie le style |
| `./dev shell` | ouvre un interpréteur dans le conteneur |
| `./dev once scan` | déclenche un passage de collecte tout de suite |
| `./dev once preview` | affiche le digest du jour sans l'envoyer |
| `./dev once digest` | construit et envoie le digest |
| `docker compose down` | arrête le service |

## Fonctionnement

Deux sources sont actives par défaut : **Google Flights**, interrogée toutes les 4 h, et **Air
Transat**, toutes les 6 h. Chaque passage relève les prix des trajets actifs, met à jour le plus
bas du jour et compare à la médiane des 90 derniers jours.

Le code en contient une troisième, désactivée, et il faut savoir pourquoi avant de la rallumer :

| Source | État | Raison |
|---|---|---|
| Google Flights | active, 4 h | — |
| Air Transat | active, 6 h | le prix relevé est le **total aller-retour** lu sur la page récapitulative `/summary`, au tarif le moins cher disponible — pas un prix d'aller simple : le formulaire est piloté jusqu'à cette étape avant de lire le prix. |
| Air Canada | abandonnée | le site refuse toute soumission automatisée (`BKRW-DBS-999`, puis 403 Akamai sur l'URL directe). Compte rendu dans `docs/superpowers/notes/2026-08-05-air-canada-abandon.md`. |

Deux sources actives limitent, sans l'éliminer, le risque qu'une panne isolée arrête toute la
collecte : Air Canada reste hors service, et la page Santé et le digest quotidien existent
précisément pour qu'une panne de l'une des deux sources actives ne passe pas inaperçue.

Un courriel de synthèse part chaque jour à 18 h, heure de Montréal, même les jours sans rien de
notable. Un courriel immédiat part si un prix descend à plus de 40 % sous la médiane — sous réserve
d'au moins 14 jours d'historique et d'un prix supérieur à 50 CAD, deux garde-fous contre les fausses
alertes de parsing.

## Quand une source cesse de produire

C'est le scénario attendu. La page Santé et le pied du digest quotidien le signalent : une source
sans succès depuis plus de 48 h y apparaît en rouge.

La marche à suivre diffère selon la source, car elles ne cassent pas de la même façon.

**Google Flights** ne s'analyse pas par sélecteurs : la lecture passe par la bibliothèque
`fast-flights`. Une panne vient donc soit de l'API de la bibliothèque, soit du protocole distant.

1. `./dev shell` puis `python scripts/capture_fixture.py google_flights` pour rejouer un appel réel.
2. Si l'appel lui-même échoue, comparer la signature de `create_query` et `get_flights` avec celle
   qu'attend `scrappervol/providers/google_flights.py` : c'est déjà arrivé, la version 3 ayant
   changé l'API sans prévenir.
3. `./dev test tests/providers/test_google_flights.py` rejoue la fixture enregistrée et désigne le
   champ qui ne se lit plus.

**Sources Playwright** (Air Transat, et toute source ajoutée sur ce socle) : celles-là cassent bien
par changement de HTML.

1. `python scripts/capture_fixture.py transat` pour recapturer la page.
2. Comparer avec l'ancienne fixture, identifier le sélecteur qui ne mord plus.
3. Corriger dans `scrappervol/providers/<source>.py`, puis `./dev test tests/providers/`.

Pour ces seules sources, la dernière page reçue est conservée dans `data/debug/` — Google Flights ne
passant pas par un navigateur, elle n'y dépose rien.
