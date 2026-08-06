# Reconnaissance des sources candidates — 6 août 2026

Contexte : les trois sources en place (Google Flights, Air Transat, Air Canada) relèvent des prix
cohérents entre eux mais manquent les tarifs des intermédiaires. Douze candidats ont été sondés
pour élargir la couverture.

## Verdict

| Candidat | Réponse directe | Au navigateur | Retenu |
|---|---|---|---|
| **Kayak** | 200, aucun marqueur anti-robot | sondage JSON complet, sans fenêtre | **oui** |
| Momondo | 200, aucun marqueur | non poussé | non — même groupe et même infrastructure que Kayak, donc mêmes résultats |
| Skyscanner | 200 mais 708 o (coquille) | page vide, marqueur « robot » | non — bloqué |
| Kiwi.com | 200, mentions Cloudflare | reste sur l'accueil ; recherche via GraphQL avec résolution de lieux | non — coût élevé, et Kayak le revend déjà |
| Expedia | 429 + DataDome | — | non — bloqué |
| FlightHub, Norse, Lufthansa | 403 Cloudflare | — | non — bloqué |
| Hopper | 200 + DataDome | — | non |
| Air France, British Airways | délai dépassé | — | non |
| PLAY | domaine non résolu | — | non |

## Pourquoi Kayak plutôt qu'une série de scrapers d'agences

La capture de sondage énumère les revendeurs consultés pour une seule recherche YUL→PAR :

> Air Canada, Air Tahiti Nui, Booking.com, BusinessClass, CheapOair, Cheapflightsfares, Expedia,
> FlightHub, Flightnetwork, Gotogate, Gurufare, Justfly.com, KAYAK, Kiwi.com, Lufthansa, Mytrip,
> Ovago, Travelocity, Trip.com, eDreams

Vingt-quatre fournisseurs, dont **Expedia, FlightHub, Kiwi.com et Gotogate** — précisément ceux que
le tableau ci-dessus déclare inaccessibles un par un. Écrire un scraper par agence aurait produit
quatre échecs et un maintien perpétuel ; une seule source les rapporte toutes, par une voie qui ne
bloque pas.

## Ce qui a été appris du format

- La page rendue n'expose ses prix que sous des classes engendrées (`hYzH-price`, `p6Cx-content`,
  `Qk4D`) que Kayak fait tourner. **Ne rien y accrocher** : c'est une panne à échéance. On lit à la
  place `https://www.kayak.com/i/api/search/dynamic/flights/poll`, dont les noms de champs sont
  stables et décrivent le domaine.
- `ca.kayak.com` + `currency=CAD` donne des dollars canadiens partout — 4630 montants relevés dans
  la capture, aucun dans une autre devise. Le domaine `kayak.ca` **n'existe pas**.
- La réponse annonce elle-même la fin de la recherche par `status: "complete"` (vers 40 s, 16
  sondages). Aucun délai fixe n'est nécessaire.
- `results` mêle les vols et les encarts publicitaires. Seul `type: "core"` désigne un vol
  réservable ; les autres portent un prix d'appel qui n'engage personne.
- Le prix n'est pas sur le résultat mais sur chacune de ses `bookingOptions`, en
  `displayPrice.price`. L'écart entre revendeurs pour un même vol atteignait 10 $ sur la capture
  (744 / 753 / 754) : c'est exactement la valeur que cette source apporte.
- `legs`, `segments` et `airlines` sont des tables de référence à la racine du document ; les
  résultats n'y renvoient que par identifiant.
- `priceMode: "per-person"` — d'où la restriction à un passager, sans quoi les observations ne
  seraient plus comparables à celles des autres sources.

## Preuve de valeur

Premier passage réel après câblage, sur YUL→CUN du 3 novembre :

```
google_flights = 534 $    transat = 533 $    kayak = 498 $
```

36 $ sous la meilleure source existante, et 2 nouveaux plus bas enregistrés dès ce passage.

## Le balayage de dates, découvert dans la foulée

Ajouter des sources ne suffisait pas : la veille n'interrogeait **qu'une seule date par
trajet** (routes en politique `fixed`), et la politique `flexible` n'en sondait qu'une par mois,
le 15. Un tarif d'erreur n'existant que certains jours, il ne pouvait pratiquement jamais être
croisé. `calendar_window` était d'ailleurs produite par le planificateur **et lue par personne**.

### Ce que Kayak accepte

| Forme d'URL | Résultat |
|---|---|
| `/YUL-PAR/2026-11-03-flexible-3days/...` | **balaye** — 8129 résultats sur 7 journées |
| `-flexible-5days`, `-7days`, `-10days`, `-14days` | **ignoré en silence** — 1636 résultats, une seule journée |
| `-flexible-calendar` | ignoré, retombe sur la date fixe |
| `/YUL-PAR/2026-11` (mois seul) | page d'erreur |

**Trois jours est le maximum réel.** Au-delà, l'URL est acceptée sans erreur et la demande
abandonnée sans que rien ne le signale : demander plus large ne coûte pas un échec, seulement une
couverture imaginaire. C'est le piège principal de cette source.

### Conséquences retenues

- Le battement ne borne que la **date de départ**. `flex_days: 3` autour d'un départ le 12 mars
  produit `(9 mars, 15 mars)` pour un voyage revenant le 22 : borner aussi le retour rejetterait
  toutes les offres d'un tel trajet.
- L'offre porte la **date réellement trouvée**, lue dans `legs[].departure`. Sans cela, un tarif
  du 5 novembre serait rangé parmi ceux du 3.
- Le planificateur sonde désormais un jalon par semaine (4, 11, 18, 25 et la queue du mois), ce
  qui correspond à ce qu'un battement de trois jours couvre autour de chacun. Le cycle complet
  passe de 24 à 60 passages, à plafond de requêtes inchangé.

### Preuve

YUL→PAR à cent jours, par le provider :

```
dates fixes   647 $ le 14 novembre   (Air Transat)
balayage      613 $ le 15 novembre   (French Bee)
```

Et sur un passage complet après bascule des routes : 124 offres relevées contre 14, sur 16 dates
de départ pour le seul YUL→CDG.

## La grille calendaire de Google Flights

Le design (§7) annonçait : « pour les politiques `window` et `flexible`, la grille calendaire de
Google Flights retourne le prix le plus bas par date sur une fenêtre d'environ deux mois en une
seule requête ». Cette promesse n'a jamais été tenue par l'implémentation, et la reconnaissance du
6 août 2026 montre qu'elle ne l'est pas facilement.

| Piste | Résultat |
|---|---|
| `create_query()` de fast-flights 3.0.2 | aucun paramètre de plage : `flights`, `seat`, `trip`, `passengers`, `language`, `currency`, `max_stops` |
| Charge utile `script.ds:1` de la page de résultats | 31 sections ; aucune ne porte de prix par date |
| `payload[17]`, 59 entrées | liste de **destinations** (aéroports et villes), pas de dates — le compte proche de deux mois est une coïncidence |
| `payload[7][0]`, `payload[30][0]` | `[null, 754]` : le prix minimum global, sans ventilation |
| Capture réseau à l'ouverture du sélecteur de dates | **aucun XHR** intercepté sur `batchexecute` ni `/travel/` |

La grille est donc servie par un appel que cette capture n'a pas identifié, probablement un RPC
`batchexecute` à identifiant distinct. L'accrocher exigerait de reproduire un protocole interne
non documenté, du même ordre de fragilité que les classes CSS obfusquées de Kayak — pour un gain
que le plafond par source atteint autrement.

**Décision : abandonnée au profit du plafond par source.** Porter `max_queries_per_route` de 6 à
12 pour Google Flights seule ramène le cycle de couverture complet de 59 à 29 rotations, sans
dépendre d'aucune structure interne. Mesuré : 65 offres sur un passage contre 4 auparavant.

À rouvrir seulement si la couverture reste insuffisante après plusieurs semaines d'exploitation.

## Piège d'infrastructure : Xvfb et `docker compose restart`

`restart` relance le processus sans recréer le système de fichiers du conteneur. Le socket
`/tmp/.X11-unix/X99` et le verrou `/tmp/.X99-lock` survivent donc au serveur X qui les avait
créés. L'entrypoint testait l'existence du socket pour décider de lancer Xvfb : il concluait
« il tourne » et ne relançait rien.

Conséquence observée : Xvfb mort, Chromium en échec sur « Missing X server », **Air Canada en
panne dès le premier `restart`** — la seule source pour laquelle cet entrypoint existe. Rien ne
le signalait. Corrigé en supprimant les résidus avant le lancement : au démarrage, aucun
processus du cycle précédent n'a survécu, ces fichiers ne peuvent qu'être orphelins.

Rappel qui découle du même mécanisme : `docker-entrypoint.sh` est copié dans l'image, non monté.
Le modifier exige `./dev build`, sinon le conteneur continue d'exécuter l'ancien.
