# Air Canada : essai infructueux, source écartée

Contexte : tâche 12 (`.superpowers/sdd/2026-08-04-scrappervol-implementation/task-12-brief.md`),
essai borné pour ajouter Air Canada comme source de prix. Budget de 90 minutes, porte de sortie
explicite si le critère n'est pas atteint. Cette note documente pourquoi l'essai s'arrête ici, pour
éviter de le refondre à l'aveugle.

## Critère de réussite (rappel)

1. **Stabilité** : deux chargements de la page de résultats, espacés d'au moins cinq minutes,
   produisant chacun au moins trois prix en CAD pour le même trajet aller-retour.
2. **Nature du prix** : total aller-retour, pas un prix de vol aller.

Requête d'essai : YUL → CUN, aller-retour, départ J+90 (2026-11-03), retour J+97 (2026-11-10),
1 adulte.

## Ce qui a été essayé

### URL directe (deep link) — bloquée au niveau de la bordure (edge)

`https://www.aircanada.com/ca/fr/aco/home.html` renvoie systématiquement une page **403 Access
Denied** signée Akamai (`errors.edgesuite.net`), à la fois :

- en `httpx` brut (sans navigateur) — capture : `data/debug/air_canada_recon_httpx.html`
  (référence `18.2a182117.1785929810.11befc04`)
- en Playwright headless avec `stealth=True` — capture : `data/debug/air_canada_recon.html`
  (référence `18.2a182117.1785929819.11bf459a`)

Le fait que le blocage soit strictement identique (même page, même formulation) qu'on soit un
simple client HTTP ou un navigateur complet avec masquage de `navigator.webdriver` indique un
blocage sur le chemin lui-même (règle de bordure Akamai), pas une détection comportementale du
navigateur.

### Domaine racine — accessible, widget de réservation atteint après hydratation

`https://www.aircanada.com/` charge normalement (200 OK). Le contenu utile n'existe pas avant
hydratation Angular : capturé vide (`<ac-home-root></ac-home-root>`, 71 873 caractères,
`data/debug/air_canada_recon_racine.html`) puis, après une attente de 9 s + tentative de
`networkidle`, le widget de réservation complet apparaît (1 076 700 caractères,
`data/debug/air_canada_recon_hydrate.html`).

Un bandeau de consentement cookies OneTrust (`#onetrust-accept-btn-handler`) est présent, sur le
même modèle que `transat.py`.

### Formulaire de réservation — piloté avec succès, mécaniquement complet

Le widget par défaut est déjà en aller-retour avec 1 adulte (contrairement à Air Transat, aucune
interaction supplémentaire n'a été nécessaire pour ces deux réglages).

Sélecteurs relevés et validés (utiles même si la source n'est pas activée — évite de les redécouvrir
si l'essai est repris) :

- Conteneur origine : `#flightsOriginLocationbkmgLocationContainer` (clic pour ouvrir le champ)
- Champ origine : `#flightsOriginLocation` — nécessite un effacement explicite
  (`Control+a` puis `Delete`) avant la saisie, sinon la valeur par défaut préremplie (« Saguenay »)
  reste concaténée au code saisi et aucune suggestion ne correspond.
- Suggestions origine : `<li id="flightsOriginLocationSearchResult0">` (premier résultat)
- Conteneur/champ destination : mêmes noms avec `flightsOriginDestination*` au lieu de
  `flightsOriginLocation*`.
- Recherche de suggestions confirmée **côté client** (aucun appel réseau dédié observé via
  l'écoute des réponses HTTP pendant la saisie) — donc pas un point de blocage anti-bot en soi.
- Calendrier : `#bkmg-desktop_travelDates-formfield-1` (ouverture), cellules cliquables
  `#bkmg-desktop_travelDates-date-{AAAA-MM-JJ}`, navigation mensuelle
  `#bkmg-desktop_travelDates_nextMonth`, confirmation de la plage
  `#bkmg-desktop_travelDates_1_confirmDates` (sans ce clic, le panneau du calendrier reste ouvert
  et intercepte les clics sur le bouton de recherche).
- Soumission : `#bkmg-desktop_findButton`.

Chaque étape (aéroports, dates, confirmation) a été vérifiée par le texte du résumé de recherche
affiché sur la page suivante, qui reprenait correctement « YUL CUN aller-retour … partant le
nov. 3 … retour le nov. 10 … 1 passager » — la saisie n'est donc pas en cause.

### Soumission de la recherche — échec reproductible, 3 fois, sessions indépendantes

Les trois tentatives complètes (navigateur relancé de zéro à chaque fois, aucune réutilisation de
contexte) aboutissent toutes à la même page d'erreur générique :

`https://www.aircanada.com/booking/ca/fr/aco/error`

Message affiché (identique aux trois passages) :

> Un problème est survenu, veuillez réessayer. Malheureusement, nous éprouvons des problèmes
> techniques en ce moment. Veuillez réessayer plus tard. (BKRW-DBS-999)

Captures :

- `data/debug/air_canada_recon_attempt1_avant_soumission.html` (avant le clic, pour retrouver le
  bouton de confirmation des dates)
- `data/debug/air_canada_recon_attempt1b_resultats.html` (532 963 caractères)
- `data/debug/air_canada_recon_attempt2_resultats.html` (533 587 caractères)
- `data/debug/air_canada_recon_10_resultats.html` (533 532 caractères, tentative 3, avec écoute
  réseau)

Dans les trois cas : **0 motif de prix trouvé** dans le HTML capturé, marqueur anti-bot Akamai
présent dans la page.

**Diagnostic réseau (tentative 3)** : écoute de toutes les réponses HTTP contenant
`search`/`availability`/`shopping`/`offers`/`flightresult`/`book` sur le domaine `aircanada.com`
pendant la soumission — 77 appels capturés, **aucun avec un code ≥ 400**. La route Angular
`/booking/ca/fr/aco/search` elle-même répond 200 (c'est le document de route SPA, pas un appel de
disponibilité). Aucun appel réseau distinct de type « recherche de disponibilité/tarifs » n'a été
observé échouer — ni même identifié comme tel dans la liste (essentiellement des balises
analytiques Google/Facebook/DoubleClick/Bing et des ressources statiques du bundle Angular).

Autrement dit : **la page d'erreur n'est pas la conséquence visible d'un appel réseau qui échoue**.
Elle est produite côté client, sans code d'erreur HTTP à observer. C'est cohérent avec les scripts
de détection anti-bot obfusqués repérés au chargement de la page d'accueil (chemins du type
`/f0flw_j8-/DtqDr/HeCQ/E1LE4mauYOOuGtOuYt/XCItclc/bB0xEl/4bCw4C`) : un score de confiance calculé
côté client (fingerprinting) peut suffire à faire dérouter l'application vers l'écran d'erreur
générique, sans jamais renvoyer d'échec HTTP identifiable — un choix délibéré pour ne pas révéler
au script qui l'observe *pourquoi* il est bloqué.

## Nombres X et Y

- **X (prix Air Canada relevé)** : aucun. La recherche n'a jamais abouti à une page de résultats
  avec des prix, sur trois tentatives indépendantes.
- **Y (Google Flights, vols AC, mêmes dates)** : non relevé. Le recoupement n'a de sens que pour
  vérifier la *nature* d'un prix obtenu ; sans X, il n'y a rien à comparer. La condition 1
  (stabilité — au moins trois prix par chargement) échoue déjà à elle seule, ce qui suffit à
  entraîner l'abandon indépendamment de la condition 2.

## Raison exacte de l'arrêt

La condition 1 du critère de réussite (stabilité : ≥ 3 prix CAD par chargement, sur deux
chargements espacés de 5 minutes) n'est pas atteignable dans l'état actuel : la recherche
n'aboutit jamais à une page de résultats, échouant de façon reproductible et identique sur trois
tentatives indépendantes (sessions et navigateurs relancés de zéro à chaque fois), avec un message
d'erreur générique ne révélant aucune cause exploitable. Combiné au blocage de bordure (Akamai)
observé sur l'URL profonde initialement envisagée et aux scripts de détection anti-bot obfusqués
présents sur la page d'accueil, le faisceau d'indices pointe vers une protection anti-automatisation
au niveau du flux de réservation lui-même — exactement le résultat que le brief de tâche annonçait
comme plausible pour Air Canada (« réputé mieux protégé qu'Air Transat »).

Conformément à la consigne d'économie de requêtes (éviter une rafale qui risquerait un blocage
d'adresse IP et rendrait le verdict ininterprétable), l'essai s'arrête à trois tentatives de
soumission complètes plutôt que de multiplier les essais pour un diagnostic supplémentaire dont la
valeur marginale serait faible : la page d'erreur ne renvoie ni code HTTP en échec ni contenu
distinctif à analyser plus avant sans franchir la mesure d'économie demandée.

## Ce qui n'a pas été tenté

- Variation d'empreinte navigateur (autres user-agents, résolutions, fuseaux horaires) — écartée
  pour rester dans le budget de requêtes et parce que le blocage semble porter sur un score de
  confiance construit sur plusieurs signaux, pas sur un seul paramètre isolé.
  Ne serait à envisager que dans un essai ultérieur dédié, avec un budget de requêtes plus large et
  probablement des outils anti-détection plus poussés que `stealth=True`.
- Utilisation d'un service tiers de résolution de CAPTCHA/anti-bot (hors-scope pour un essai borné
  de ce projet).

## Conclusion

Air Canada n'est pas ajouté comme source. `enabled_providers` dans `scrappervol/config.py` reste
inchangé (`air_canada` n'y a jamais figuré). Google Flights (tâche 10) couvre déjà les vols Air
Canada avec un prix aller-retour agrégé et comparable, ce qui reste la voie d'accès pratique aux
tarifs Air Canada pour ce projet.
