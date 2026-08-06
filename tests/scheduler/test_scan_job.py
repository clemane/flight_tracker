from datetime import UTC, date, datetime, timedelta

import pytest
from sqlmodel import Session, select

from scrappervol.config import Settings
from scrappervol.core.types import DatePolicyKind, FlightOffer
from scrappervol.scheduler.jobs import run_scan
from scrappervol.storage import repo
from scrappervol.storage.models import (
    Alert,
    AlertKind,
    DailyLow,
    Observation,
    ProviderHealth,
    Route,
)

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


def test_un_prix_sensiblement_sous_la_mediane_mais_pas_assez_ne_declenche_rien(
    session, reglages, fausse_source, faux_mailer, sans_pause
):
    """Encadre le seuil de 40 % par le haut, là où le test à 550 ne l'encadrait que de loin.

    Un seuil relâché à 15 % — la valeur de `find_threshold`, une confusion plausible — laissait
    toute la suite verte : 550 contre 600 ne fait que 8,3 %, en deçà des deux seuils. À 500 contre
    600, soit 16,7 %, les deux valeurs se séparent enfin : le prix est bien plus bas que d'ordinaire
    sans être l'aubaine que le design veut signaler.
    """
    trajet = _trajet(session)
    _historique(session, trajet.id, prix=600, jours=30)
    dormir, _ = sans_pause

    resultat = run_scan(
        session,
        fausse_source(name="google_flights", offres=[(500, "Air Transat")]),
        reglages,
        faux_mailer,
        MAINTENANT,
        sleeper=dormir,
    )

    assert resultat.exceptions_sent == 0
    assert faux_mailer.envois == []


def test_un_envoi_echoue_nest_pas_compte_comme_une_alerte_emise(
    session, reglages, fausse_source, sans_pause, caplog
):
    """`exceptions_sent` doit compter les courriels partis, pas les aubaines détectées.

    Deux gardes ici. La première : un `return True` sur le chemin d'échec gonflerait le compteur
    sans qu'aucun courriel ne parte — et c'est ce compteur que l'ordonnanceur (tâche 17) et le
    tableau de bord (tâche 18) rendront visible. La seconde : sans trace journalisée, un serveur
    SMTP muet resterait parfaitement invisible, ce que le design nomme le risque principal.
    """

    class MailerCasse:
        def send(self, mail, to):
            raise RuntimeError("SMTP injoignable")

    trajet = _trajet(session)
    _historique(session, trajet.id, prix=600, jours=30)
    dormir, _ = sans_pause

    with caplog.at_level("ERROR", logger="scrappervol.scheduler.jobs"):
        resultat = run_scan(
            session,
            fausse_source(name="google_flights", offres=[(299, "A")]),
            reglages,
            MailerCasse(),
            MAINTENANT,
            sleeper=dormir,
        )

    assert resultat.exceptions_sent == 0
    assert "SMTP injoignable" in caplog.text


def test_le_prix_du_jour_nentre_pas_dans_sa_propre_reference(
    session, reglages, fausse_source, faux_mailer, sans_pause
):
    """L'historique s'arrête à la veille, jour courant exclu. `daily_low_history` le garantit et
    ses propres tests le vérifient, mais rien ne vérifiait la borne que lui passe l'appelant.

    Treize jours d'historique, soit un de moins que les quatorze exigés : aucune alerte ne doit
    partir. Le plus bas du jour vient pourtant d'être posé quelques lignes plus tôt dans le même
    passage — s'il était admis dans sa propre référence, le compte atteindrait quatorze et
    l'alerte partirait. Un trajet tout juste trop jeune pour être jugé se mettrait ainsi à
    déclencher des alertes en se comptant lui-même.
    """
    trajet = _trajet(session)
    _historique(session, trajet.id, prix=600, jours=13)
    dormir, _ = sans_pause

    resultat = run_scan(
        session,
        fausse_source(name="google_flights", offres=[(299, "A")]),
        reglages,
        faux_mailer,
        MAINTENANT,
        sleeper=dormir,
    )

    assert resultat.exceptions_sent == 0
    assert faux_mailer.envois == []


def test_lalerte_enregistree_porte_le_prix_reellement_alerte(
    session, reglages, fausse_source, faux_mailer, sans_pause
):
    """Le contenu du `payload` n'était vérifié par aucun test : seule son existence l'était.
    C'est pourtant la trace d'audit de ce qui a été annoncé, et `offer_hash` y sert à ne pas
    répéter l'alerte."""
    trajet = _trajet(session)
    _historique(session, trajet.id, prix=600, jours=30)
    dormir, _ = sans_pause

    run_scan(
        session,
        fausse_source(name="google_flights", offres=[(299, "A")]),
        reglages,
        faux_mailer,
        MAINTENANT,
        sleeper=dormir,
    )

    alerte = session.exec(select(Alert)).one()
    assert alerte.kind == AlertKind.EXCEPTION
    assert alerte.payload["price_cad"] == 299
    assert alerte.payload["offer_hash"]


class TestAtomiciteDuPassage:
    """Ce qu'un passage laisse réellement en base, selon qu'il aboutit ou non.

    Ces épreuves tournent sur `base_isolee` — une base sur fichier — et non sur la base en mémoire
    des autres tests : celle-ci tient sur une connexion unique, où une seconde session lit les
    écritures non validées. La garantie d'atomicité y serait invérifiable.
    """

    def _scan(self, session, reglages, fausse_source, faux_mailer, dormir, prix=612):
        return run_scan(
            session,
            fausse_source(name="google_flights", offres=[(prix, "Air Transat")]),
            reglages,
            faux_mailer,
            MAINTENANT,
            sleeper=dormir,
        )

    def test_un_incident_en_cours_de_passage_ne_laisse_rien(
        self, base_isolee, reglages, fausse_source, faux_mailer, sans_pause, monkeypatch
    ) -> None:
        """Sans atomicité, l'incident laissait des observations privées du plus bas du jour
        qu'elles justifiaient — lacune définitive, le plus bas d'un jour ne se recalculant pas au
        passage suivant."""
        dormir, _ = sans_pause
        with Session(base_isolee) as ecriture:
            _trajet(ecriture)

            def echouer(*args, **kwargs):
                raise RuntimeError("incident au milieu du passage")

            monkeypatch.setattr(repo, "upsert_daily_low", echouer)
            with pytest.raises(RuntimeError):
                self._scan(ecriture, reglages, fausse_source, faux_mailer, dormir)

        with Session(base_isolee) as lecture:
            assert lecture.exec(select(Observation)).all() == []
            assert lecture.exec(select(DailyLow)).all() == []

    def test_un_incident_apres_le_plus_bas_ne_le_laisse_pas_derriere(
        self, base_isolee, reglages, fausse_source, faux_mailer, sans_pause, monkeypatch
    ) -> None:
        """Le plus bas du premier trajet est écrit, puis le second trajet fait tout échouer.

        Aucune alerte n'est en jeu ici : rien ne doit donc subsister, pas même ce qui avait
        réussi avant l'incident.
        """
        dormir, _ = sans_pause
        vrai_upsert = repo.upsert_daily_low
        appels = {"n": 0}

        def echouer_au_second(*args, **kwargs):
            appels["n"] += 1
            if appels["n"] == 2:
                raise RuntimeError("incident après le premier plus bas")
            return vrai_upsert(*args, **kwargs)

        with Session(base_isolee) as ecriture:
            _trajet(ecriture)
            _trajet(ecriture, label="Nice")
            monkeypatch.setattr(repo, "upsert_daily_low", echouer_au_second)
            with pytest.raises(RuntimeError):
                self._scan(ecriture, reglages, fausse_source, faux_mailer, dormir)

        assert appels["n"] == 2
        assert faux_mailer.envois == []
        with Session(base_isolee) as lecture:
            assert lecture.exec(select(DailyLow)).all() == []
            assert lecture.exec(select(Observation)).all() == []

    def test_un_passage_reussi_valide_bien_ses_ecritures(
        self, base_isolee, reglages, fausse_source, faux_mailer, sans_pause
    ) -> None:
        """Contre-épreuve : l'atomicité ne doit pas se payer en écritures jamais validées."""
        dormir, _ = sans_pause
        with Session(base_isolee) as ecriture:
            _trajet(ecriture)
            self._scan(ecriture, reglages, fausse_source, faux_mailer, dormir)

        with Session(base_isolee) as lecture:
            assert len(lecture.exec(select(Observation)).all()) == 1
            assert len(lecture.exec(select(DailyLow)).all()) == 1

    def test_la_trace_dune_alerte_envoyee_survit_a_un_incident_posterieur(
        self, base_isolee, reglages, fausse_source, faux_mailer, sans_pause, monkeypatch
    ) -> None:
        """Un courriel parti ne se rattrape pas par un `rollback`.

        Si sa trace tombait avec le reste du passage, l'anti-répétition perdrait la mémoire de
        l'envoi et la même alerte repartirait au scan suivant.
        """
        dormir, _ = sans_pause
        vrai_upsert = repo.upsert_daily_low
        appels = {"n": 0}

        def echouer_au_second(*args, **kwargs):
            appels["n"] += 1
            if appels["n"] == 2:
                raise RuntimeError("incident après l'envoi de la première alerte")
            return vrai_upsert(*args, **kwargs)

        with Session(base_isolee) as ecriture:
            premier = _trajet(ecriture)
            _trajet(ecriture, label="Nice")
            _historique(ecriture, premier.id, prix=600, jours=30)
            monkeypatch.setattr(repo, "upsert_daily_low", echouer_au_second)
            with pytest.raises(RuntimeError):
                self._scan(ecriture, reglages, fausse_source, faux_mailer, dormir, prix=299)

        assert len(faux_mailer.envois) == 1
        with Session(base_isolee) as lecture:
            alertes = lecture.exec(select(Alert)).all()
            assert len(alertes) == 1
            assert alertes[0].payload["price_cad"] == 299

    def test_la_session_reste_utilisable_apres_un_incident(
        self, base_isolee, reglages, fausse_source, faux_mailer, sans_pause, monkeypatch
    ) -> None:
        """L'incident annulé, la session doit pouvoir resservir sans traîner ce qu'elle a écrit.

        Sans annulation explicite, les écritures du passage avorté restent en attente dans la
        session : le passage suivant les valide avec les siennes, et l'observation d'un scan qui
        a échoué se retrouve en base — exactement l'état bancal que l'atomicité devait interdire.
        Le piège est que l'incident vient du code applicatif et non du pilote SQL : la transaction
        n'est pas marquée en échec, rien ne proteste, et la fuite passe inaperçue.
        """
        dormir, _ = sans_pause
        vrai_upsert = repo.upsert_daily_low

        with Session(base_isolee) as ecriture:
            _trajet(ecriture)

            def echouer(*args, **kwargs):
                raise RuntimeError("incident")

            monkeypatch.setattr(repo, "upsert_daily_low", echouer)
            with pytest.raises(RuntimeError):
                self._scan(ecriture, reglages, fausse_source, faux_mailer, dormir)

            monkeypatch.setattr(repo, "upsert_daily_low", vrai_upsert)
            resultat = self._scan(ecriture, reglages, fausse_source, faux_mailer, dormir)
            assert resultat.offers_recorded == 1

        with Session(base_isolee) as lecture:
            assert len(lecture.exec(select(DailyLow)).all()) == 1
            # Une seule : celle du second passage. L'observation du passage avorté ne doit pas
            # avoir été emportée dans la validation de celui qui a réussi.
            assert len(lecture.exec(select(Observation)).all()) == 1

    def test_le_succes_dune_source_est_acquis_meme_si_la_suite_echoue(
        self, base_isolee, reglages, fausse_source, faux_mailer, sans_pause, monkeypatch
    ) -> None:
        """La source a répondu : son horodatage de succès ne doit pas tomber avec l'incident qui
        suit, sous peine de la faire passer pour muette et de déclencher une fausse alerte de
        panne au digest."""
        dormir, _ = sans_pause

        def echouer(*args, **kwargs):
            raise RuntimeError("incident après la relève")

        with Session(base_isolee) as ecriture:
            _trajet(ecriture)
            monkeypatch.setattr(repo, "upsert_daily_low", echouer)
            with pytest.raises(RuntimeError):
                self._scan(ecriture, reglages, fausse_source, faux_mailer, dormir)

        with Session(base_isolee) as lecture:
            sante = lecture.get(ProviderHealth, "google_flights")
            assert sante is not None
            assert sante.last_success_at is not None
            assert sante.consecutive_failures == 0

    def test_la_sante_dune_source_en_panne_est_validee_malgre_l_echec(
        self, base_isolee, reglages, fausse_source, faux_mailer, sans_pause
    ) -> None:
        """Le disjoncteur ne vaut que si son compteur survit au passage qui l'a incrémenté."""
        dormir, _ = sans_pause
        with Session(base_isolee) as ecriture:
            _trajet(ecriture)
            resultat = run_scan(
                ecriture,
                fausse_source(name="google_flights", exception=RuntimeError("source cassée")),
                reglages,
                faux_mailer,
                MAINTENANT,
                sleeper=dormir,
            )
            assert resultat.failed

        with Session(base_isolee) as lecture:
            sante = lecture.get(ProviderHealth, "google_flights")
            assert sante is not None
            assert sante.consecutive_failures == 1
            assert sante.last_error
