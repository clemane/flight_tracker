from __future__ import annotations

import csv
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

FICHIER = Path(__file__).resolve().parent.parent / "data" / "aeroports.csv"


@dataclass(frozen=True, slots=True)
class Aeroport:
    iata: str
    ville: str
    pays: str
    nom: str
    ville_fr: str
    pays_fr: str
    mots: str
    liaisons: int

    @property
    def ville_affichee(self) -> str:
        return self.ville_fr or self.ville

    @property
    def pays_affiche(self) -> str:
        """Le nom français quand on l'a : « Cancun, Mexico » désignerait une ville, en français."""
        return self.pays_fr or self.pays

    @property
    def libelle(self) -> str:
        """Ce qui s'affiche dans la liste : la ville d'abord, c'est par là qu'on cherche."""
        if self.nom and self.nom.lower() not in self.ville_affichee.lower():
            return f"{self.ville_affichee} — {self.nom}"
        return self.ville_affichee


def _sans_accents(texte: str) -> str:
    """Replie les accents pour que « Montreal » trouve « Montréal », et l'inverse.

    La source est en anglais et l'utilisateur tape en français : sans ce repli, « Cancún » saisi
    correctement ne trouverait pas « Cancun », et « Montreal » saisi vite ne trouverait pas
    « Montréal ». Les deux graphies doivent mener au même aéroport.
    """
    decompose = unicodedata.normalize("NFD", texte.casefold())
    return "".join(c for c in decompose if unicodedata.category(c) != "Mn")


@lru_cache(maxsize=1)
def _catalogue() -> list[tuple[Aeroport, tuple[str, ...], str]]:
    """Les aéroports, avec leurs formes repliées calculées une fois pour toutes."""
    entrees = []
    with FICHIER.open(encoding="utf-8", newline="") as f:
        for ligne in csv.DictReader(f):
            aeroport = Aeroport(
                iata=ligne["iata"],
                ville=ligne["ville"],
                pays=ligne["pays"],
                nom=ligne["nom"],
                ville_fr=ligne["ville_fr"],
                pays_fr=ligne["pays_fr"],
                mots=ligne["mots"],
                liaisons=int(ligne["liaisons"]),
            )
            # Les graphies de la ville restent séparées : concaténées, « Lisbonne » ne serait plus
            # un début de « lisbon lisbonne » et perdrait son rang.
            villes = tuple(
                _sans_accents(g) for g in (aeroport.ville, aeroport.ville_fr) if g
            )
            reste = _sans_accents(
                f"{aeroport.pays} {aeroport.pays_fr} {aeroport.nom} {aeroport.mots}"
            )
            entrees.append((aeroport, villes, reste))
    return entrees


def chercher(requete: str, limite: int = 8) -> list[Aeroport]:
    """Aéroports correspondant à une saisie, du plus pertinent au moins pertinent.

    Le classement va du plus sûr au plus vague : code exact, puis ville qui commence par la
    saisie, puis ville qui la contient, puis pays ou nom d'aéroport. À rang égal, l'aéroport le
    mieux desservi passe devant — sans quoi « Paris » proposerait d'abord un aérodrome du Texas.
    """
    terme = _sans_accents(requete.strip())
    if not terme:
        return []

    resultats = []
    for aeroport, villes, reste in _catalogue():
        if terme == _sans_accents(aeroport.iata):
            rang = 0
        elif any(v.startswith(terme) for v in villes):
            rang = 1
        elif any(terme in v for v in villes):
            rang = 2
        elif terme in reste:
            rang = 3
        else:
            continue
        resultats.append((rang, -aeroport.liaisons, aeroport.ville, aeroport))

    resultats.sort(key=lambda ligne: ligne[:3])
    return [ligne[3] for ligne in resultats[:limite]]


def par_code(iata: str) -> Aeroport | None:
    code = iata.strip().upper()
    return next((a for a, _, _ in _catalogue() if a.iata == code), None)
