from datetime import UTC, date, datetime, timedelta

import pytest

from scrappervol.config import Settings
from scrappervol.core.types import DatePolicyKind, FlightOffer
from scrappervol.scheduler.jobs import run_scan
from scrappervol.storage import repo
from scrappervol.storage.models import AlertKind, DailyLow, Route

MAINTENANT = datetime(2026, 8, 4, 14, 0, tzinfo=UTC)
AUJOURDHUI = date(2026, 8, 4)


@pytest.fixture
def reglages():
    return Settings(request_pause_min_s=0, request_pause_max_s=0, max_queries_per_route=1)


def _trajet(session, **surcharges) -> Route:
    base = {
        "label": "Paris",
        "origins": ["YUL"],
        "destinations": ["CDG"],
        "date_policy": DatePolicyKind.FIXED,
        "policy_params": {"depart": "2027-03-12", "retour": "2027-03-22"},
    }
    trajet = Route(**{**base, **surcharges})
    session.add(trajet)
    session.commit()
    session.refresh(trajet)
    return trajet


def _historique(session, route_id: int, prix: int, jours: int) -> None:
    for decalage in range(1, jours + 1):
        session.add(
            DailyLow(
                route_id=route_id,
                day=AUJOURDHUI - timedelta(days=decalage),
                price_cad=prix,
                provider="google_flights",
            )
        )
    session.commit()


def test_les_offres_sont_enregistrees_et_le_plus_bas_du_jour_pose(
    session, reglages, fausse_source, faux_mailer, sans_pause
):
    trajet = _trajet(session)
    dormir, _ = sans_pause

    resultat = run_scan(
        session,
        fausse_source(name="google_flights", offres=[(612, "Air Transat"), (700, "Air Canada")]),
        reglages,
        faux_mailer,
        MAINTENANT,
        sleeper=dormir,
    )

    assert resultat.offers_recorded == 2
    assert resultat.new_lows == 1
    assert repo.daily_low_for(session, trajet.id, AUJOURDHUI).price_cad == 612


def test_un_prix_superieur_ne_remplace_pas_le_plus_bas_du_jour(
    session, reglages, fausse_source, faux_mailer, sans_pause
):
    trajet = _trajet(session)
    dormir, _ = sans_pause
    run_scan(
        session,
        fausse_source(name="google_flights", offres=[(480, "A")]),
        reglages,
        faux_mailer,
        MAINTENANT,
        sleeper=dormir,
    )

    resultat = run_scan(
        session,
        fausse_source(name="google_flights", offres=[(900, "B")]),
        reglages,
        faux_mailer,
        MAINTENANT + timedelta(hours=4),
        sleeper=dormir,
    )

    assert repo.daily_low_for(session, trajet.id, AUJOURDHUI).price_cad == 480
    assert resultat.new_lows == 0  # le prix est enregistré mais ne bat pas le plus bas du jour


def test_une_aberration_declenche_un_courriel_immediat(
    session, reglages, fausse_source, faux_mailer, sans_pause
):
    trajet = _trajet(session)
    _historique(session, trajet.id, prix=600, jours=30)
    dormir, _ = sans_pause

    resultat = run_scan(
        session,
        fausse_source(name="google_flights", offres=[(299, "Air Transat")]),
        reglages,
        faux_mailer,
        MAINTENANT,
        sleeper=dormir,
    )

    assert resultat.exceptions_sent == 1
    assert len(faux_mailer.envois) == 1
    # 299 $ contre une médiane de 600 $ : (600 - 299) / 600 = 50 %. Vérifier l'écart complet
    # verrouille l'ordre des arguments de relative_gap(prix, médiane) — inversés, le calcul
    # donnerait (299 - 600) / 299 ≈ -101 %, mais laisserait "CDG à 299 $" présent quand même.
    assert "CDG à 299 $ (50 % sous la médiane)" in faux_mailer.envois[0][0]


def test_la_meme_aberration_au_passage_suivant_reste_silencieuse(
    session, reglages, fausse_source, faux_mailer, sans_pause
):
    trajet = _trajet(session)
    _historique(session, trajet.id, prix=600, jours=30)
    dormir, _ = sans_pause
    run_scan(
        session,
        fausse_source(name="google_flights", offres=[(299, "Air Transat")]),
        reglages,
        faux_mailer,
        MAINTENANT,
        sleeper=dormir,
    )

    resultat = run_scan(
        session,
        fausse_source(name="google_flights", offres=[(299, "Air Transat")]),
        reglages,
        faux_mailer,
        MAINTENANT + timedelta(hours=4),
        sleeper=dormir,
    )

    assert resultat.exceptions_sent == 0
    assert len(faux_mailer.envois) == 1


def test_un_prix_a_peine_sous_la_mediane_ne_declenche_rien(
    session, reglages, fausse_source, faux_mailer, sans_pause
):
    """Verrouille le seuil relatif de 40 %, que rien n'éprouve autrement dans ce fichier.

    Tous les cas d'alerte ici sont à 299 contre 600, soit 50 % sous la médiane ; tous les cas
    silencieux sont écartés bien avant d'atteindre le seuil — par l'historique trop court, par le
    plancher de crédibilité ou par l'anti-répétition. Un seuil relâché à 5 % laisserait donc la
    suite entièrement verte. 550 contre 600 fait 8,3 % : sous la médiane, mais très loin d'être
    une aubaine.
    """
    trajet = _trajet(session)
    _historique(session, trajet.id, prix=600, jours=30)
    dormir, _ = sans_pause

    resultat = run_scan(
        session,
        fausse_source(name="google_flights", offres=[(550, "Air Transat")]),
        reglages,
        faux_mailer,
        MAINTENANT,
        sleeper=dormir,
    )

    assert resultat.exceptions_sent == 0
    assert faux_mailer.envois == []
    assert resultat.offers_recorded == 1  # le prix est bien relevé, il n'est simplement pas alerté


def test_aucune_alerte_sans_historique_suffisant(
    session, reglages, fausse_source, faux_mailer, sans_pause
):
    trajet = _trajet(session)
    _historique(session, trajet.id, prix=600, jours=5)
    dormir, _ = sans_pause

    resultat = run_scan(
        session,
        fausse_source(name="google_flights", offres=[(150, "Air Transat")]),
        reglages,
        faux_mailer,
        MAINTENANT,
        sleeper=dormir,
    )

    assert resultat.exceptions_sent == 0
    assert faux_mailer.envois == []


def test_aucune_alerte_sous_le_plancher_de_credibilite(
    session, reglages, fausse_source, faux_mailer, sans_pause
):
    trajet = _trajet(session)
    _historique(session, trajet.id, prix=600, jours=30)
    dormir, _ = sans_pause

    resultat = run_scan(
        session,
        fausse_source(name="google_flights", offres=[(45, "Air Transat")]),
        reglages,
        faux_mailer,
        MAINTENANT,
        sleeper=dormir,
    )

    assert resultat.exceptions_sent == 0
    assert faux_mailer.envois == []


def test_lalerte_emise_est_journalisee(session, reglages, fausse_source, faux_mailer, sans_pause):
    trajet = _trajet(session)
    _historique(session, trajet.id, prix=600, jours=30)
    dormir, _ = sans_pause

    run_scan(
        session,
        fausse_source(name="google_flights", offres=[(299, "Air Transat")]),
        reglages,
        faux_mailer,
        MAINTENANT,
        sleeper=dormir,
    )

    from sqlmodel import select

    from scrappervol.storage.models import Alert

    alertes = session.exec(select(Alert)).all()
    assert len(alertes) == 1
    assert alertes[0].kind is AlertKind.EXCEPTION
    assert alertes[0].payload["offer_hash"]


def test_un_echec_de_source_ne_leve_pas_et_nenvoie_rien(
    session, reglages, fausse_source, faux_mailer, sans_pause
):
    _trajet(session)
    dormir, _ = sans_pause

    resultat = run_scan(
        session,
        fausse_source(name="transat", exception=RuntimeError("boum")),
        reglages,
        faux_mailer,
        MAINTENANT,
        sleeper=dormir,
    )

    assert resultat.failed is True
    assert faux_mailer.envois == []
    assert resultat.offers_recorded == 0  # une source en échec ne laisse pas d'offres partielles


def test_un_echec_denvoi_nempeche_pas_le_reste_du_passage(
    session, reglages, fausse_source, sans_pause
):
    """Un serveur SMTP injoignable ne doit pas faire perdre les prix relevés."""

    class MailerCasse:
        def send(self, mail, to):
            raise RuntimeError("SMTP injoignable")

    trajet = _trajet(session)
    _historique(session, trajet.id, prix=600, jours=30)
    dormir, _ = sans_pause

    resultat = run_scan(
        session,
        fausse_source(name="google_flights", offres=[(299, "A")]),
        reglages,
        MailerCasse(),
        MAINTENANT,
        sleeper=dormir,
    )

    assert resultat.offers_recorded == 1
    assert repo.daily_low_for(session, trajet.id, AUJOURDHUI) is not None


def test_une_alerte_non_envoyee_est_retentee_au_passage_suivant(
    session, reglages, fausse_source, faux_mailer, sans_pause
):
    """Sans cette garde, une panne SMTP passagère coûterait l'aubaine définitivement.

    L'alerte serait marquée comme envoyée alors qu'aucun courriel n'est parti, et
    `exception_already_sent` — qui ne distingue pas un envoi réussi d'un envoi échoué — la ferait
    taire à tous les passages suivants. Rien ne la remettrait en file, car il n'y a pas de file :
    les alertes sont recalculées à chaque passage à partir des offres du moment. C'est ce test qui
    impose d'enregistrer l'alerte **après** l'envoi et non avant.
    """

    class MailerCasse:
        def send(self, mail, to):
            raise RuntimeError("SMTP injoignable")

    trajet = _trajet(session)
    _historique(session, trajet.id, prix=600, jours=30)
    dormir, _ = sans_pause
    run_scan(
        session,
        fausse_source(name="google_flights", offres=[(299, "Air Transat")]),
        reglages,
        MailerCasse(),
        MAINTENANT,
        sleeper=dormir,
    )

    resultat = run_scan(
        session,
        fausse_source(name="google_flights", offres=[(299, "Air Transat")]),
        reglages,
        faux_mailer,
        MAINTENANT + timedelta(hours=4),
        sleeper=dormir,
    )

    assert resultat.exceptions_sent == 1
    assert len(faux_mailer.envois) == 1


class SourcePartiellementCassee:
    """Réussit sur la première requête d'un trajet, échoue sur la seconde.

    Reproduit ce que `run_provider` peut produire en cas de panne à mi-passage (DETTE D du
    plan) : un rapport avec `failed=True` **et** des offres déjà collectées dans
    `offers_by_route`, parce que l'exception n'efface pas ce qui a été accumulé avant elle.
    """

    name = "google_flights"

    def __init__(self) -> None:
        self.appels = 0

    def search(self, query):
        self.appels += 1
        if self.appels == 1:
            return [
                FlightOffer(
                    provider=self.name,
                    origin=query.origin,
                    destination=query.destination,
                    depart_date=query.depart_date,
                    return_date=query.return_date,
                    price_cad=612,
                    price_original=612.0,
                    currency_original="CAD",
                    airline="Air Transat",
                    stops=0,
                    duration_minutes=420,
                    deep_link="https://example.com",
                    raw={},
                )
            ]
        raise RuntimeError("boum")


def test_un_echec_apres_des_offres_partielles_ne_laisse_rien_en_base(
    session, faux_mailer, sans_pause
):
    """Sans le garde `if rapport.failed or rapport.skipped: return resultat` placé AVANT la
    boucle d'enregistrement, ce test resterait vert par coïncidence : dans tous les autres
    tests du fichier, la source échoue dès son tout premier appel, donc `offers_by_route`
    est déjà vide et le garde ne joue aucun rôle observable. Ici la source réussit sur une
    première requête du trajet avant d'échouer sur la seconde, pour forcer un rapport
    `failed=True` avec des offres déjà collectées — le seul cas où le garde fait une
    différence.
    """
    reglages_deux_requetes = Settings(
        request_pause_min_s=0, request_pause_max_s=0, max_queries_per_route=2
    )
    _trajet(session, origins=["YUL", "YQB"])
    dormir, _ = sans_pause

    resultat = run_scan(
        session,
        SourcePartiellementCassee(),
        reglages_deux_requetes,
        faux_mailer,
        MAINTENANT,
        sleeper=dormir,
    )

    assert resultat.failed is True
    assert resultat.offers_recorded == 0
    assert faux_mailer.envois == []
