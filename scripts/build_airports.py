"""Reconstruit la base d'aéroports embarquée, à partir des données OpenFlights.

    python scripts/build_airports.py

Écrit `scrappervol/data/aeroports.csv`. À relancer si la liste vieillit — elle bouge peu.

Deux jeux de données sont téléchargés : `airports.dat` pour les aéroports, `routes.dat` pour
compter les liaisons desservies. Ce compte sert d'ordre de pertinence : sans lui, chercher
« Paris » proposerait d'abord un aérodrome du Texas, la liste étant alphabétique. Les données
sont publiées par OpenFlights sous Open Database License.
"""

from __future__ import annotations

import collections
import csv
import re
import urllib.request
from pathlib import Path

SOURCE = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/"
SORTIE = Path(__file__).resolve().parent.parent / "scrappervol" / "data" / "aeroports.csv"
_IATA = re.compile(r"^[A-Z]{3}$")

# Noms français des villes et pays dont la graphie anglaise ne se devine pas. Sans eux,
# « Lisbonne » ou « Mexique » ne trouvent rien : la source est entièrement en anglais. Ces noms
# servent aussi à l'affichage — « Cancun, Mexico » se lirait autrement en français, où Mexico
# désigne une ville.
ALIAS = {
    # villes
    "London": "Londres",
    "Lisbon": "Lisbonne",
    "Seville": "Séville",
    "Athens": "Athènes",
    "Vienna": "Vienne",
    "Copenhagen": "Copenhague",
    "Warsaw": "Varsovie",
    "Moscow": "Moscou",
    "Cairo": "Le Caire",
    "Marrakesh": "Marrakech",
    "Algiers": "Alger",
    "Geneva": "Genève",
    "Brussels": "Bruxelles",
    "Venice": "Venise",
    "Naples": "Naples",
    "Barcelona": "Barcelone",
    "Edinburgh": "Édimbourg",
    "Frankfurt": "Francfort",
    "Hamburg": "Hambourg",
    "Munich": "Munich",
    "Cologne": "Cologne",
    "Prague": "Prague",
    "Bucharest": "Bucarest",
    "Belgrade": "Belgrade",
    "Istanbul": "Istanbul",
    "Dubai": "Dubaï",
    "Singapore": "Singapour",
    "Seoul": "Séoul",
    "Beijing": "Pékin",
    "Mumbai": "Bombay",
    "Johannesburg": "Johannesbourg",
    "Cape Town": "Le Cap",
    "Havana": "La Havane",
    "Santo Domingo": "Saint-Domingue",
    "Mexico City": "Mexico",
    "New Orleans": "La Nouvelle-Orléans",
    "Quebec": "Québec",
    "Montreal": "Montréal",
    # graphies accentuées que la source rend sans accent
    "Cancun": "Cancún",
    "Merida": "Mérida",
    "Mazatlan": "Mazatlán",
    "Queretaro": "Querétaro",
    "Leon": "León",
    "Holguin": "Holguín",
    "Camaguey": "Camagüey",
    "San Jose": "San José",
    "San Andres": "San Andrés",
    "Bogota": "Bogotá",
    "Medellin": "Medellín",
    "Asuncion": "Asunción",
    "Sao Paulo": "São Paulo",
    "Malaga": "Málaga",
    "Dusseldorf": "Düsseldorf",
    "Reykjavik": "Reykjavík",
    "Nurnberg": "Nuremberg",
    "Turin": "Turin",
    "Genoa": "Gênes",
    "Florence": "Florence",
    "Milan": "Milan",
    "Rome": "Rome",
    # pays
    "United States": "États-Unis",
    "United Kingdom": "Royaume-Uni",
    "Mexico": "Mexique",
    "Spain": "Espagne",
    "Italy": "Italie",
    "Germany": "Allemagne",
    "Greece": "Grèce",
    "Portugal": "Portugal",
    "Netherlands": "Pays-Bas",
    "Belgium": "Belgique",
    "Switzerland": "Suisse",
    "Austria": "Autriche",
    "Ireland": "Irlande",
    "Iceland": "Islande",
    "Norway": "Norvège",
    "Sweden": "Suède",
    "Denmark": "Danemark",
    "Finland": "Finlande",
    "Poland": "Pologne",
    "Czech Republic": "Tchéquie",
    "Hungary": "Hongrie",
    "Croatia": "Croatie",
    "Turkey": "Turquie",
    "Morocco": "Maroc",
    "Tunisia": "Tunisie",
    "Egypt": "Égypte",
    "Senegal": "Sénégal",
    "Ivory Coast": "Côte d'Ivoire",
    "South Africa": "Afrique du Sud",
    "Kenya": "Kenya",
    "Brazil": "Brésil",
    "Argentina": "Argentine",
    "Chile": "Chili",
    "Peru": "Pérou",
    "Colombia": "Colombie",
    "Costa Rica": "Costa Rica",
    "Panama": "Panama",
    "Cuba": "Cuba",
    "Jamaica": "Jamaïque",
    "Dominican Republic": "République dominicaine",
    "Haiti": "Haïti",
    "Bahamas": "Bahamas",
    "Barbados": "Barbade",
    "Saint Lucia": "Sainte-Lucie",
    "Guadeloupe": "Guadeloupe",
    "Martinique": "Martinique",
    "Japan": "Japon",
    "China": "Chine",
    "South Korea": "Corée du Sud",
    "Thailand": "Thaïlande",
    "Vietnam": "Viêt Nam",
    "India": "Inde",
    "Indonesia": "Indonésie",
    "Philippines": "Philippines",
    "Australia": "Australie",
    "New Zealand": "Nouvelle-Zélande",
    "United Arab Emirates": "Émirats arabes unis",
    "Israel": "Israël",
    "Lebanon": "Liban",
    "Saudi Arabia": "Arabie saoudite",
    "Russia": "Russie",
    "Ukraine": "Ukraine",
    "Romania": "Roumanie",
    "Bulgaria": "Bulgarie",
    "Serbia": "Serbie",
    "Algeria": "Algérie",
    "Netherlands Antilles": "Antilles néerlandaises",
}

# Formes que l'on tape sans qu'elles soient le nom officiel : cherchées, jamais affichées.
SYNONYMES = {
    "United States": "USA US Etats Unis",
    "United Kingdom": "Angleterre GB Grande-Bretagne",
    "Netherlands": "Hollande",
    "Czech Republic": "Republique tcheque",
    "Myanmar": "Birmanie",
    "Ivory Coast": "Cote d'Ivoire",
    "New York": "NYC",
    "Mexico City": "Mexico DF",
    "Willemstad": "Curacao Curaçao",
    "Oranjestad": "Aruba",
}

_SUFFIXES = (" International Airport", " Airport", " International", " Intl")


def _telecharger(nom: str) -> list[list[str]]:
    with urllib.request.urlopen(SOURCE + nom, timeout=120) as reponse:  # noqa: S310
        texte = reponse.read().decode("utf-8")
    return list(csv.reader(texte.splitlines()))


def main() -> int:
    liaisons: collections.Counter[str] = collections.Counter()
    for ligne in _telecharger("routes.dat"):
        if len(ligne) > 4:
            liaisons[ligne[2]] += 1
            liaisons[ligne[4]] += 1

    aeroports = []
    for r in _telecharger("airports.dat"):
        if len(r) < 13 or not _IATA.match(r[4]) or r[12] != "airport":
            continue
        _, nom, ville, pays, iata = r[0], r[1], r[2].strip(), r[3].strip(), r[4]
        for suffixe in _SUFFIXES:
            nom = nom.replace(suffixe, "")
        aeroports.append(
            (
                iata,
                ville,
                pays,
                nom.strip(),
                ALIAS.get(ville, ""),
                ALIAS.get(pays, ""),
                " ".join(filter(None, (SYNONYMES.get(ville, ""), SYNONYMES.get(pays, "")))),
                liaisons.get(iata, 0),
            )
        )

    aeroports.sort(key=lambda a: (-a[7], a[1]))

    SORTIE.parent.mkdir(parents=True, exist_ok=True)
    with SORTIE.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["iata", "ville", "pays", "nom", "ville_fr", "pays_fr", "mots", "liaisons"])
        w.writerows(aeroports)

    print(f"{len(aeroports)} aéroports écrits dans {SORTIE} ({SORTIE.stat().st_size / 1024:.0f} ko)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
