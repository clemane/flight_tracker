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

L'interface est alors sur <http://127.0.0.1:8080>. Elle n'est accessible que depuis cette
machine : l'application n'a pas d'authentification, et n'a pas vocation à être exposée.

## Les deux usages

L'application répond à deux besoins distincts, qu'il vaut mieux ne pas confondre.

**Chercher un prix maintenant.** La page d'accueil interroge les sources à l'instant où l'on
valide. Google Flights répond en deux secondes, Air Transat en une trentaine, Air Canada en
quatre-vingts — les deux dernières pilotent un vrai navigateur jusqu'au récapitulatif. Les
résultats s'affichent au fur et à mesure, source par source, et l'écran cesse de se rafraîchir de
lui-même quand tout est rentré.

Une recherche **n'écrit rien** : ni relevé, ni historique, ni état de santé des sources. Elle ne
peut donc ni fausser une médiane avec des dates de passage, ni masquer une panne du relevé
automatique, ni en déclencher une. Le bouton « Surveiller ce trajet » est le pont entre les deux
usages : il crée un trajet suivi à partir de la recherche affichée.

**Suivre un trajet dans la durée.** Un trajet déclaré est relevé toutes les 4 à 6 h, entre dans le
digest de 18 h et devient éligible aux alertes une fois 14 jours d'historique accumulés.

## Vérifier que l'installation fonctionne

Le premier passage de collecte n'a lieu qu'au bout de plusieurs heures et le digest ne part qu'à
18 h : au démarrage, le tableau de bord reste donc vide un long moment, sans que rien distingue
une installation qui marche d'une installation en panne. `./dev once` force ces passages.

Le plus rapide est de lancer une recherche depuis la page d'accueil : elle éprouve les trois
sources en moins de deux minutes, sans rien attendre ni rien enregistrer. Si les prix s'affichent,
la collecte fonctionne. Pour éprouver la chaîne complète, jusqu'au digest :

1. Déclarer un trajet sur <http://127.0.0.1:8080/routes>. Un aller-retour à dates fixes, à deux ou
   trois mois d'ici, donne le résultat le plus lisible.
2. `./dev once scan` — compter deux bonnes minutes, Air Transat et Air Canada étant pilotées dans
   un navigateur. La commande affiche, pour chaque source, le nombre d'offres relevées. Une source
   à zéro offre, ou en échec, est le signal à suivre : les journaux et `data/debug/` en disent la
   raison.
3. Recharger la page d'accueil : le trajet doit porter un prix. La page Santé doit montrer les
   trois sources avec un dernier succès à l'instant.
4. `./dev once preview` affiche le digest tel qu'il partirait le soir même.

Les sources étant indépendantes, leurs prix se recoupent : sur un même vol, un écart de quelques
dollars est normal, un facteur 2 signale une lecture faussée — c'est le symptôme d'un tarif lu
pour un seul sens là où il en fallait deux.

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

Trois sources sont actives par défaut. Chaque passage relève les prix des trajets actifs, met à
jour le plus bas du jour et compare à la médiane des 90 derniers jours.

| Source | État | Nature du prix relevé |
|---|---|---|
| Google Flights | active, 4 h | total aller-retour, lu dans la charge utile de la page. Les deux sections de résultats sont fusionnées : la bibliothèque n'en lisait qu'une, celle des « autres vols », et laissait de côté les meilleurs prix. |
| Air Transat | active, 6 h | **total aller-retour** lu sur la page récapitulative `/summary`, au tarif le moins cher — pas un prix d'aller simple : le formulaire est piloté jusqu'à cette étape avant lecture. |
| Air Canada | active, 8 h | **total aller-retour** lu sur le récapitulatif de réservation. Les pages de résultats n'affichent que des tarifs « par personne, dans chaque sens », soit environ la moitié du prix à payer. |

Trois sources indépendantes limitent, sans l'éliminer, le risque qu'une panne isolée arrête toute
la collecte ; la page Santé et le digest quotidien existent précisément pour qu'une panne de l'une
d'elles ne passe pas inaperçue.

Air Canada est la plus lente des trois — environ 80 secondes, contre deux pour Google Flights :
elle mène un parcours de réservation complet, aller puis retour, faute de page affichant un vrai
total. Elle exige aussi un navigateur **à fenêtre** : le site déroute vers une page d'erreur
générique tout navigateur sans fenêtre. Le conteneur démarre pour cela un serveur X sans écran
(`docker-entrypoint.sh`) ; hors conteneur, il faut un `DISPLAY` valide. Compte rendu :
`docs/superpowers/notes/2026-08-06-air-canada-reouverture.md`.

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

**Sources Playwright** (Air Transat, Air Canada, et toute source ajoutée sur ce socle) : celles-là
cassent bien par changement de HTML.

1. `python scripts/capture_fixture.py transat` pour recapturer la page.
2. Comparer avec l'ancienne fixture, identifier le sélecteur qui ne mord plus.
3. Corriger dans `scrappervol/providers/<source>.py`, puis `./dev test tests/providers/`.

Pour Air Canada, une panne subite de **toutes** les recherches, sans changement de HTML visible,
oriente d'abord vers le serveur X plutôt que vers un sélecteur : sans fenêtre, le site déroute
vers une page d'erreur générique. `docker compose exec app sh -c 'ls /tmp/.X11-unix'` doit
répondre `X99`. Les sélecteurs du parcours sont recensés dans
`docs/superpowers/notes/2026-08-06-air-canada-reouverture.md`.

Pour ces seules sources, la dernière page reçue est conservée dans `data/debug/` — Google Flights ne
passant pas par un navigateur, elle n'y dépose rien.
