from datetime import UTC, date, datetime, timedelta

import pytest

from scrappervol.notify.render import (
    DigestData,
    ExceptionData,
    ProviderStatus,
    RouteBlock,
    format_duree,
    format_escales,
    format_trajet,
    render_digest,
    render_exception,
)

JOUR = date(2026, 8, 4)
MAINTENANT = datetime(2026, 8, 4, 18, 0, tzinfo=UTC)


def _bloc(**surcharges) -> RouteBlock:
    base = {
        "label": "Paris au printemps",
        "price_cad": 480,
        "airline": "Air Transat",
        "origin": "YUL",
        "destination": "CDG",
        "depart_date": date(2027, 3, 12),
        "return_date": date(2027, 3, 22),
        "provider": "google_flights",
        "deep_link": "https://example.com/offre",
        "median_price": 600.0,
        "gap_vs_median": 0.20,
        "gap_vs_yesterday": -35,
        "is_find": True,
        "history_building": False,
    }
    return RouteBlock(**{**base, **surcharges})


def _sante(**surcharges) -> ProviderStatus:
    base = {
        "name": "google_flights",
        "last_success_at": MAINTENANT - timedelta(hours=2),
        "consecutive_failures": 0,
        "hours_silent": 2.0,
        "is_stale": False,
    }
    return ProviderStatus(**{**base, **surcharges})


def test_le_sujet_annonce_le_nombre_de_trouvailles():
    donnees = DigestData(day=JOUR, blocks=[_bloc(), _bloc(is_find=False)], providers=[_sante()])

    assert render_digest(donnees).subject == "ScrapperVol — 1 trouvaille du 2026-08-04"


def test_le_sujet_accorde_le_pluriel():
    donnees = DigestData(day=JOUR, blocks=[_bloc(), _bloc()], providers=[_sante()])

    assert "2 trouvailles" in render_digest(donnees).subject


def test_le_digest_part_meme_sans_trouvaille():
    donnees = DigestData(day=JOUR, blocks=[_bloc(is_find=False)], providers=[_sante()])

    rendu = render_digest(donnees)

    assert "0 trouvaille" in rendu.subject
    assert rendu.html
    assert rendu.text


def test_le_digest_montre_le_prix_le_transporteur_et_le_lien():
    rendu = render_digest(DigestData(day=JOUR, blocks=[_bloc()], providers=[_sante()]))

    assert "480" in rendu.html
    assert "Air Transat" in rendu.html
    assert "https://example.com/offre" in rendu.html
    assert "Paris au printemps" in rendu.html


def test_un_trajet_sans_prix_aujourdhui_est_signale_sans_planter():
    """`price_cad` est `int | None` dans l'interface : un trajet actif dont la source n'a rien
    renvoyé aujourd'hui doit produire un rendu propre, pas planter sur `bloc.airline` ou afficher
    littéralement « None »."""
    bloc = _bloc(price_cad=None)
    rendu = render_digest(DigestData(day=JOUR, blocks=[bloc], providers=[_sante()]))

    assert "Aucun prix relevé aujourd'hui" in rendu.html
    assert "aucun prix relevé aujourd'hui" in rendu.text
    assert "480" not in rendu.html
    assert "480" not in rendu.text


def test_un_aller_simple_naffiche_pas_de_date_de_retour():
    """`return_date` est optionnel dans l'interface : un aller simple ne doit ni afficher
    littéralement « None », ni laisser une flèche pointant vers rien."""
    bloc = _bloc(return_date=None)
    rendu = render_digest(DigestData(day=JOUR, blocks=[bloc], providers=[_sante()]))

    assert "2027-03-12" in rendu.html
    assert "None" not in rendu.html
    assert "2027-03-12 →" not in rendu.html
    assert "None" not in rendu.text
    assert "2027-03-12 au" not in rendu.text


def test_labsence_de_comparaison_avec_hier_ne_saffiche_pas():
    """`gap_vs_yesterday` est optionnel : quand il vaut `None` (pas de relevé la veille), la
    mention « par rapport à hier » / « vs hier » doit disparaître plutôt que d'afficher
    littéralement « None »."""
    bloc = _bloc(gap_vs_yesterday=None)
    rendu = render_digest(DigestData(day=JOUR, blocks=[bloc], providers=[_sante()]))

    assert "par rapport à hier" not in rendu.html
    assert "vs hier" not in rendu.text
    assert "None" not in rendu.html
    assert "None" not in rendu.text


def test_le_digest_affiche_lecart_a_la_mediane():
    """L'écart choisi (37 %) n'apparaît nulle part ailleurs dans le rendu.

    Une assertion `"20" in rendu.html` serait vraie sans que l'écart soit rendu du tout : la date
    du jour (2026-08-04) et la date de départ (2027-03-12) contiennent toutes deux "20".
    """
    bloc = _bloc(gap_vs_median=0.37, median_price=613.0)
    rendu = render_digest(DigestData(day=JOUR, blocks=[bloc], providers=[_sante()]))

    assert "37 %" in rendu.html
    assert "613" in rendu.html
    assert "37 %" in rendu.text


def test_le_digest_dit_au_dessus_quand_le_prix_depasse_la_mediane():
    """Le digest liste tous les trajets actifs, pas seulement les trouvailles : la plupart des
    soirs, la plupart des prix sont au-dessus de leur médiane. Un gabarit qui écrit toujours
    « sous la médiane » afficherait alors « -15 % sous la médiane », qui ne veut rien dire."""
    bloc = _bloc(gap_vs_median=-0.15, is_find=False)
    rendu = render_digest(DigestData(day=JOUR, blocks=[bloc], providers=[_sante()]))

    assert "15 % au-dessus" in rendu.html
    assert "-15" not in rendu.html
    assert "15 % au-dessus" in rendu.text


def test_le_digest_dit_sous_quand_lecart_est_exactement_nul():
    """Frontière `gap_vs_median == 0.0` : le prix du jour égale la médiane, pile. Un gabarit qui
    écrirait `> 0` au lieu de `>= 0` afficherait « au-dessus » pour un prix qui n'est pourtant
    pas au-dessus — seulement égal."""
    bloc = _bloc(gap_vs_median=0.0, median_price=480.0, is_find=False)
    rendu = render_digest(DigestData(day=JOUR, blocks=[bloc], providers=[_sante()]))

    assert "0 % sous" in rendu.html
    assert "au-dessus" not in rendu.html
    assert "0 % sous" in rendu.text
    assert "au-dessus" not in rendu.text


def test_les_trajets_sont_tries_par_ecart_decroissant():
    faible = _bloc(label="Faible", gap_vs_median=0.05)
    forte = _bloc(label="Forte", gap_vs_median=0.35)
    donnees = DigestData(day=JOUR, blocks=[faible, forte], providers=[_sante()])

    assert [b.label for b in donnees.sorted_blocks] == ["Forte", "Faible"]

    # L'ordre doit se retrouver dans le rendu, pas seulement dans la propriété : un gabarit qui
    # itérerait sur `data.blocks` au lieu de `data.sorted_blocks` laisserait l'assertion ci-dessus
    # verte tout en envoyant un digest mal trié.
    rendu = render_digest(donnees)
    assert rendu.html.index("Forte") < rendu.html.index("Faible")
    assert rendu.text.index("Forte") < rendu.text.index("Faible")


def test_un_trajet_sans_historique_significatif_est_signale_et_non_classe():
    en_construction = _bloc(
        label="Neuf", median_price=None, gap_vs_median=None, history_building=True, is_find=False
    )
    donnees = DigestData(day=JOUR, blocks=[_bloc(), en_construction], providers=[_sante()])

    rendu = render_digest(donnees)

    assert "historique en constitution" in rendu.html
    assert donnees.sorted_blocks[-1].label == "Neuf"


def test_un_trajet_en_construction_reste_dernier_meme_face_a_un_ecart_negatif():
    """`gap_vs_median=None` est traité comme 0.0 pour le calcul du score de tri : un trajet réel
    dont le prix dépasse la médiane (`gap_vs_median` négatif) obtient alors un score PLUS
    favorable (0.30 contre 0.0) que le trajet en construction. Si le classement ne s'appuyait
    que sur ce score — sans distinguer `history_building` en premier critère — le trajet en
    construction remonterait devant un trajet cher, ce qui contredit "ferment la marche"."""
    cher = _bloc(label="Cher", gap_vs_median=-0.30, is_find=False)
    en_construction = _bloc(
        label="Neuf", median_price=None, gap_vs_median=None, history_building=True, is_find=False
    )
    donnees = DigestData(day=JOUR, blocks=[cher, en_construction], providers=[_sante()])

    assert donnees.sorted_blocks[-1].label == "Neuf"


def test_un_trajet_en_construction_ne_compte_pas_comme_trouvaille():
    donnees = DigestData(
        day=JOUR,
        blocks=[_bloc(history_building=True, is_find=True, gap_vs_median=None)],
        providers=[_sante()],
    )

    rendu = render_digest(donnees)

    assert donnees.find_count == 0
    # Le compteur ne suffit pas : le badge est posé par les gabarits, pas par `find_count`.
    # Le tiret cadratin distingue le badge du « 0 trouvaille » qui figure déjà dans le titre.
    assert "— trouvaille" not in rendu.html
    assert "— TROUVAILLE" not in rendu.text


def test_un_trajet_avec_historique_porte_le_badge_de_trouvaille():
    """Contrepartie du test précédent : sans elle, un gabarit qui n'afficherait jamais le
    badge passerait aussi."""
    donnees = DigestData(day=JOUR, blocks=[_bloc()], providers=[_sante()])

    rendu = render_digest(donnees)

    assert "— trouvaille" in rendu.html
    assert "— TROUVAILLE" in rendu.text


def test_le_pied_porte_toujours_letat_des_sources():
    donnees = DigestData(
        day=JOUR,
        blocks=[_bloc()],
        providers=[
            _sante(name="google_flights"),
            _sante(name="transat"),
            _sante(name="air_canada"),
        ],
    )

    rendu = render_digest(donnees)

    assert "google_flights" in rendu.html
    assert "transat" in rendu.html
    assert "air_canada" in rendu.html
    assert "google_flights" in rendu.text


def test_une_source_muette_depuis_48h_declenche_un_bandeau_en_tete():
    """Le risque principal n'est pas la panne, mais le digest fidèle annonçant qu'il n'y a rien."""
    donnees = DigestData(
        day=JOUR,
        blocks=[_bloc()],
        providers=[_sante(), _sante(name="transat", hours_silent=72.0, is_stale=True)],
    )

    rendu = render_digest(donnees)

    assert donnees.has_stale_provider is True
    assert "transat" in rendu.html
    assert rendu.html.index("muette") < rendu.html.index("Paris au printemps")
    # Le bandeau existe aussi dans la version texte, avec son propre code de gabarit : rien ne
    # garantit qu'un défaut dans l'un se retrouve dans l'autre.
    assert "muette" in rendu.text
    assert rendu.text.index("muette") < rendu.text.index("Paris au printemps")


def test_sans_source_muette_aucun_bandeau():
    donnees = DigestData(day=JOUR, blocks=[_bloc()], providers=[_sante()])

    rendu = render_digest(donnees)
    assert "muette" not in rendu.html
    assert "muette" not in rendu.text


def test_la_version_texte_ne_contient_aucune_balise():
    rendu = render_digest(DigestData(day=JOUR, blocks=[_bloc()], providers=[_sante()]))

    assert "<p" not in rendu.text
    assert "</" not in rendu.text


def test_la_version_texte_nest_pas_echappee():
    """L'échappement HTML sur le gabarit texte transformerait les apostrophes en entités."""
    rendu = render_digest(
        DigestData(day=JOUR, blocks=[_bloc(label="Paris l'hiver")], providers=[_sante()])
    )

    assert "Paris l'hiver" in rendu.text
    assert "&#39;" not in rendu.text


def test_la_version_html_est_echappee():
    """Le pendant du test précédent, et il compte autant.

    `select_autoescape(..., default=False)` n'active l'échappement que pour les extensions
    listées : une extension mal orthographiée le désactive partout sans que rien ne le signale.
    Or `airline` vient du scraping et `label` est saisi par l'utilisateur à la tâche 19 — ni l'un
    ni l'autre n'est du contenu maîtrisé.
    """
    rendu = render_digest(
        DigestData(
            day=JOUR,
            blocks=[_bloc(label="<script>alert(1)</script>", airline="A & B")],
            providers=[_sante()],
        )
    )

    assert "<script>" not in rendu.html
    assert "&lt;script&gt;" in rendu.html
    assert "A &amp; B" in rendu.html
    # ... et le texte, lui, reste brut.
    assert "<script>alert(1)</script>" in rendu.text


def test_le_sujet_dexception_porte_la_destination_le_prix_et_lecart():
    donnees = ExceptionData(
        label="Paris au printemps",
        origin="YUL",
        destination="CDG",
        depart_date=date(2027, 3, 12),
        return_date=date(2027, 3, 22),
        price_cad=299,
        airline="Air Transat",
        provider="google_flights",
        deep_link="https://example.com/offre",
        median_price=600.0,
        gap_vs_median=0.50,
        history_days=45,
    )

    rendu = render_exception(donnees)

    assert rendu.subject == "ScrapperVol — CDG à 299 $ (50 % sous la médiane)"
    assert "299" in rendu.html
    assert "https://example.com/offre" in rendu.html
    assert "45" in rendu.html


@pytest.mark.parametrize(
    ("escales", "attendu"),
    [(0, "direct"), (1, "1 escale"), (2, "2 escales"), (3, "3 escales")],
)
def test_format_escales(escales: int, attendu: str) -> None:
    assert format_escales(escales) == attendu


@pytest.mark.parametrize(
    ("minutes", "attendu"),
    [
        (300, "5 h"),
        (810, "13 h 30"),
        (65, "1 h 05"),
        (60, "1 h"),
        (None, ""),
        (0, ""),
    ],
)
def test_format_duree(minutes: int | None, attendu: str) -> None:
    assert format_duree(minutes) == attendu


def test_format_trajet_omet_la_duree_absente() -> None:
    """Une durée non relevée ne doit pas laisser de séparateur orphelin dans le courriel."""
    assert format_trajet(1, None) == "1 escale"
    assert format_trajet(0, None) == "direct"
    assert format_trajet(1, 810) == "1 escale · 13 h 30"


def test_lalerte_annonce_la_forme_du_voyage_et_pas_seulement_le_prix() -> None:
    """Sans les escales, une alerte à 541 $ peut cacher treize heures d'escale (cas YUL->CUN)."""
    donnees = ExceptionData(
        label="Cancún en novembre",
        origin="YUL",
        destination="CUN",
        depart_date=date(2026, 11, 3),
        return_date=date(2026, 11, 10),
        price_cad=541,
        airline="Air Canada Rouge",
        provider="air_canada",
        deep_link="https://example.com/offre",
        median_price=620.0,
        gap_vs_median=0.13,
        history_days=45,
        stops=1,
        duration_minutes=810,
    )

    rendu = render_exception(donnees)

    assert "1 escale · 13 h 30" in rendu.text
    assert "1 escale · 13 h 30" in rendu.html


def test_lalerte_dun_vol_direct_le_dit() -> None:
    donnees = ExceptionData(
        label="Cancún en novembre",
        origin="YUL",
        destination="CUN",
        depart_date=date(2026, 11, 3),
        return_date=date(2026, 11, 10),
        price_cad=581,
        airline="Air Canada",
        provider="air_canada",
        deep_link="https://example.com/offre",
        median_price=620.0,
        gap_vs_median=0.06,
        history_days=45,
        stops=0,
        duration_minutes=300,
    )

    rendu = render_exception(donnees)

    assert "direct · 5 h" in rendu.text
    assert "escale" not in rendu.text
