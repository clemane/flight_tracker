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

Une seule source est active par défaut : **Google Flights**, interrogée toutes les 4 h. Chaque
passage relève les prix des trajets actifs, met à jour le plus bas du jour et compare à la médiane
des 90 derniers jours.

Le code en contient deux autres, désactivées, et il faut savoir pourquoi avant de les rallumer :

| Source | État | Raison |
|---|---|---|
| Google Flights | active, 4 h | — |
| Air Transat | présente mais retirée de `ENABLED_PROVIDERS` | elle ne publie qu'un prix d'**aller simple**. Ce prix, environ moitié moindre qu'un aller-retour, deviendrait le plus bas du trajet et effondrerait la médiane de référence : plus aucune aubaine ne serait jamais détectée, et le tableau de bord resterait vert. La rallumer suppose d'abord de relever un prix d'aller-retour complet. |
| Air Canada | abandonnée | le site refuse toute soumission automatisée (`BKRW-DBS-999`, puis 403 Akamai sur l'URL directe). Compte rendu dans `docs/superpowers/notes/2026-08-05-air-canada-abandon.md`. |

Une seule source signifie qu'une panne de Google Flights arrête toute la collecte. C'est le risque
assumé de cette version ; la page Santé et le digest quotidien existent précisément pour qu'il ne
passe pas inaperçu.

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
