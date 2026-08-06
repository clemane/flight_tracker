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
