# Le balayage de dates avait faussé la détection — 7 août 2026

## Ce qui s'est passé

Le design fonde l'alerte sur une comparaison dans le temps : le plus bas d'aujourd'hui contre
la médiane des plus bas passés. Cette lecture repose sur une hypothèse tacite — qu'on regarde
chaque jour **les mêmes dates de départ**. Elle était vraie quand tous les trajets étaient à
dates fixes.

Le balayage de dates l'a rendue fausse. Depuis, la rotation couvre des dates différentes à
chaque passage, et le plus bas quotidien ne mesure plus la même chose d'un jour à l'autre.

## La mesure

Relevé sur la base réelle, avec 633 observations :

| Trajet | Politique | Dates couvertes | Planchers | Écart max |
|---|---|---|---|---|
| Cancún en novembre | `fixed` | 5 | 481 – 514 $ | 6 % |
| YUL → CDG | `fixed` | 7 | 1063 – 1122 $ | 5 % |
| Paris, à l aubaine | `flexible` | 88 | 611 – **1425 $** | **57 %** |

Le seuil d'alerte est à 40 %. Sur le trajet flexible, **passer d'une date à l'autre suffit à le
franchir** — sans qu'aucun prix n'ait bougé. Selon que la rotation tombe sur octobre ou sur la
semaine de Noël, le plus bas bascule de moitié. Dans un sens cela fabrique une alerte, dans
l'autre cela masque une aubaine réelle.

Les trajets à dates fixes ne sont pas touchés : le balayage ±3 jours reste dans un voisinage
immédiat, et 5 à 6 % d'écart ne franchit aucun seuil.

## Ce qui a été fait

Un second critère, `is_calendar_exception`, qui compare un prix **aux autres dates relevées au
même moment** plutôt qu'au passé. La rotation n'a aucune prise dessus, et il ne demande pas
d'historique.

Les deux lectures cohabitent, dans cet ordre :

1. **La comparaison dans le temps garde la main** quand l'historique est disponible. Elle
   mesure une baisse réelle, là où l'éventail ne mesure qu'un écart entre saisons.
2. **L'éventail prend le relais** sinon.

Le courriel dit laquelle des deux l'a déclenché. Sans cela il aurait annoncé une médiane
« calculée sur N jours d'historique » alors qu'elle porte sur des dates de départ — et sur un
trajet neuf, N vaut zéro.

## Le plancher de huit dates

Le critère ne s'ouvre qu'à partir de huit dates. Deux raisons, la seconde découverte en mesurant.

D'abord, en deçà, la dispersion ne veut rien dire. Le MAD sur un petit échantillon resserré
produit des scores spectaculaires et vides de sens : les huit dates de Cancún tiennent en trente
dollars, et la plus basse — à **6 % de la médiane** — obtient un score de **-4,89**, très
au-delà du seuil de -3,5. Seul le cumul avec le seuil relatif de 40 % empêche l'alerte. Le
plancher de dates est la deuxième barrière.

Ensuite, ce plancher tient le critère à l'écart des trajets à dates fixes, où le balayage ne
couvre qu'un voisinage de 5 à 7 dates et où la comparaison dans le temps garde tout son sens.
Le critère s'active donc exactement là où l'autre défaille.

## Vérification

Simulé sur les trois trajets réels avant mise en service : **aucune alerte**. Un horizon ouvert
va naturellement du simple au double, et sa date la moins chère existe toujours — la traiter
comme une aubaine reviendrait à alerter tous les jours. Sur les 88 dates de « Paris, à
l aubaine », aucune n'est assez détachée du lot.

## Effet de bord utile

La détection ne dépend plus d'un historique de quatorze jours pour les trajets à horizon
ouvert. Une aubaine franche relevée le premier jour est désormais visible.

## Suite — la lecture temporelle perd la parole là où elle est fausse

Le premier correctif avait ajouté la lecture calendaire mais laissé la lecture temporelle
branchée partout, y compris sur les trajets où l'on venait d'établir qu'elle compare des dates
différentes. Elle y aurait produit un faux signal dès les quatorze jours d'historique atteints.

`is_history_comparable` mesure maintenant la dispersion de l'éventail et lui retire la parole
au-delà de 8 %. Sur les relevés du jour :

| Trajet | dates | dispersion | lecture |
|---|---|---|---|
| Cancún en novembre (fixe) | 5 | 0,8 % | temporelle |
| YUL → CDG (fixe) | 7 | 0,5 % | temporelle |
| Paris, à l'aubaine (flexible) | 88 | 13,8 % | coupée → éventail des dates |

**Le choix de la mesure était le vrai sujet.** L'étendue paraissait l'évidence, mais elle est
sensible aux valeurs extrêmes — c'est-à-dire à l'aubaine elle-même. Injecter une aubaine à
moitié prix dans les relevés de Cancún fait passer son étendue de 6 % à 50 % : s'y fier aurait
coupé la détection au moment précis où elle doit parler. Le MAD, mesuré sur les mêmes données,
ne bouge pas (13,8 % → 13,8 % sur Paris). C'est vérifié, pas supposé.

Le veto ne s'applique qu'au-dessus de huit dates, seuil à partir duquel la lecture calendaire
peut prendre le relais : aucun trajet ne se retrouve sans détection.

## Ce qui reste ouvert

Le seuil de 8 % est **calibré, non dérivé** : il est posé dans l'intervalle qui sépare les
trajets à dates fixes observés (sous 1,5 %) de l'horizon ouvert (13,8 %), et volontairement
bas, parce que se taire à tort laisse le relais calendaire agir alors qu'alerter à tort détruit
la confiance dans l'outil. Un trajet à horizon intermédiaire — trois ou quatre mois — tomberait
dans une zone grise que rien n'a encore éclairée.

Le MAD ne voit pas la bimodalité : un trajet dont la moitié des dates seraient à moitié prix
passerait pour homogène alors que sa série temporelle serait très instable. Les relevés observés
étalent leurs prix continûment, sans rien de tel, mais la limite est réelle et notée dans la
docstring de la fonction.

Enfin, la voie de fond reste non empruntée : suivre le plus bas **par date de départ** plutôt
que par trajet donnerait une comparaison temporelle juste partout. Chaque date n'étant visitée
que tous les dix jours environ, l'historique par date serait trop clairsemé pour une médiane, et
il faudrait raisonner en nombre de points plutôt qu'en jours. Ce n'est pas fait.
