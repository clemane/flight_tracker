# Air Canada : réouverture et mise en service

Fait suite à [2026-08-05-air-canada-abandon.md](2026-08-05-air-canada-abandon.md), qui concluait
à une source hors de portée. Cette note documente ce qui a débloqué le parcours, ce qu'il faut
savoir pour le réparer s'il dérive, et la nature exacte du prix relevé.

## Ce qui débloque tout : une fenêtre

L'essai du 5 août pilotait Chromium en mode sans fenêtre (`headless=True`), avec un masquage de
`navigator.webdriver`. Chaque soumission retombait sur `BKRW-DBS-999`, une page d'erreur produite
côté client, sans réponse HTTP en échec à observer.

Le même parcours, mené par le même Chromium **avec fenêtre** (`headless=False`) sur un serveur X
sans écran, atteint la page de résultats du premier coup. Aucun autre changement n'était
nécessaire : ni user-agent particulier, ni outil anti-détection, ni service tiers.

Le conteneur démarre donc un `Xvfb` avant la commande applicative
(`docker-entrypoint.sh`, `DISPLAY=:99`), et `fetch_html` accepte un paramètre `headless`.
Les deux autres sources restent en mode sans fenêtre : elles n'ont rien à y gagner.

## Le piège du prix : des tarifs par sens

La page de résultats le dit en toutes lettres, sous le carrousel de dates :

> Les tarifs sont par personne, **dans chaque sens**, pour l'achat d'un billet aller-retour, et
> comprennent les taxes, les frais et les suppléments.

Un tarif affiché vaut donc environ **la moitié** du prix à payer. C'est le même piège que celui
rencontré sur Air Transat. Vérification faite sur YUL → CUN, 3–10 novembre 2026 :

| Relevé | Montant |
| --- | --- |
| Tarif affiché, page aller | 319 $ |
| Tarif affiché, page retour | 222 $ |
| Somme | 541 $ |
| **Total du récapitulatif** | **540,71 $** |

La somme retombe sur le total à l'arrondi près, mais chaque tarif pris isolément est un demi-prix.
Le provider lit donc le total du récapitulatif (`review-trip`), seule page du parcours à le porter.

## Validation croisée

Vols directs uniquement (`max_stops=0`), YUL → CUN, mêmes dates :

- **X** (scraper Air Canada) : 581 $
- **Y** (Google Flights, vols Air Canada) : 579 $
- **X / Y = 1,003**

Sans contrainte d'escales, le parcours retient au retour un vol Air Canada Rouge à une escale de
13 h 30 pour 541 $ — moins cher, mais incomparable à un direct. D'où le filtre sur `max_stops`
appliqué avant de choisir un vol : sans lui, l'historique d'une route mélangerait deux réalités.

## Parcours et sélecteurs

Accueil `https://www.aircanada.com/` (l'URL profonde `/ca/fr/aco/home.html` reste un 403 Akamai,
et `/availability/rt/outbound` en navigation directe renvoie sur l'accueil : le parcours vit dans
une session, il faut passer par le formulaire).

1. **Témoins** : `#onetrust-accept-btn-handler`.
2. **Aéroports** : conteneur `#flightsOriginLocationbkmgLocationContainer`, champ
   `#flightsOriginLocation`, première suggestion `#flightsOriginLocationSearchResult0` ; mêmes
   noms en `flightsOriginDestination*` pour la destination. Le champ arrive prérempli — l'effacer
   (`Control+a`, `Delete`) avant de taper, sinon la frappe s'y concatène.
3. **Dates** : ouverture `#bkmg-desktop_travelDates-formfield-1`, cellules
   `#bkmg-desktop_travelDates-date-{AAAA-MM-JJ}`, mois suivant
   `#bkmg-desktop_travelDates_nextMonth`, confirmation
   `#bkmg-desktop_travelDates_1_confirmDates` (sans ce clic, le calendrier intercepte le suivant).
4. **Recherche** : `#bkmg-desktop_findButton` → `/availability/rt/outbound`.
5. **Choix d'un vol** : cellules `button[class*='cabin-fare-'][class*='-Y']` (`Y` = Économique).
   La classe porte le rang du vol (`cabin-fare-12-Y`), ce qui permet de la rattacher au bloc
   `ac-ui-flight-block-summary-pres` de même rang pour y lire les escales.
6. **Tarif** : le clic sur une cellule déplie un panneau ; ses boutons « Sélectionner » sont
   rangés du moins cher au plus cher.
7. **Modale de vente incitative** : `ac-ui-avail-fare-upsell-modal-pres`. Elle intercepte les
   clics suivants tant qu'elle est ouverte. Cocher sa case
   (`input[type=checkbox]`) puis cliquer l'unique bouton de son pied de page
   (`.basic-footer button`, « Continuez avec le De base ») — les autres montent en gamme.
8. **Retour** : mêmes étapes sur `/availability/rt/inbound`, puis `/review-trip`.
9. **Total** : `.total-price`, dont le texte masqué porte « Total général 540,71CAD ».

## Deux pièges de sélecteur

- **Ne pas cibler « Sélectionner » par son texte.** Un sélecteur `:text-is('Sélectionner')` ne
  remonte aucun élément alors que cinq boutons sont affichés : la normalisation Unicode du « é »
  servi par le site ne correspond pas à celle d'un littéral Python. Le code cible `lectionner`.
- **Lire les prix dans le texte masqué, pas dans l'affichage.** Les cellules exposent `319CAD`
  (destiné aux lecteurs d'écran) à côté de `319 $` (avec espace fine insécable et mention de
  devise). La première forme est stable, la seconde est de la présentation.

Un clic peut échouer sur « element is outside of the viewport » malgré le défilement, sur ces
pages très longues : le provider retombe alors sur un clic déclenché depuis la page.

## Coût

Environ 80 secondes par recherche, contre un appel d'API pour Google Flights. C'est la source la
plus lente des trois, parce qu'elle mène un parcours de réservation complet — c'est le prix à
payer pour un total aller-retour réel.
