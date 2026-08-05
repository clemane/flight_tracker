from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from scrappervol.config import Settings
from scrappervol.core.types import DatePolicyKind
from scrappervol.storage.models import DailyLow, ProviderHealth, Route
from scrappervol.web.app import create_app, get_now, get_session

MAINTENANT = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
AUJOURDHUI = date(2026, 8, 4)


@pytest.fixture
def client(engine, session):
    application = create_app(engine, Settings(enabled_providers=["google_flights", "transat"]))
    application.dependency_overrides[get_session] = lambda: session
    # Sans cette surcharge, les pages liraient l'heure réelle : le plus bas « du jour » ne serait
    # plus trouvé et l'âge du dernier succès franchirait les 48 h deux jours après l'écriture.
    application.dependency_overrides[get_now] = lambda: MAINTENANT
    return TestClient(application)


def _trajet(session, **surcharges) -> Route:
    base = {
        "label": "Paris au printemps",
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


def test_le_tableau_de_bord_repond(client):
    reponse = client.get("/")

    assert reponse.status_code == 200
    assert "text/html" in reponse.headers["content-type"]


def test_le_tableau_de_bord_liste_les_trajets(client, session):
    _trajet(session, label="Paris au printemps")
    _trajet(session, label="Lisbonne en octobre")

    corps = client.get("/").text

    assert "Paris au printemps" in corps
    assert "Lisbonne en octobre" in corps
    # Contrepartie du test de la page vide : un gabarit qui afficherait toujours l'invite de
    # création passerait les deux tests sans que personne ne s'en aperçoive.
    assert "aucun trajet" not in corps.lower()


def test_le_tableau_de_bord_montre_le_plus_bas_du_jour(client, session):
    trajet = _trajet(session)
    session.add(
        DailyLow(route_id=trajet.id, day=AUJOURDHUI, price_cad=480, provider="google_flights")
    )
    session.commit()

    corps = client.get("/").text

    # Le montant seul se retrouverait par accident dans une coordonnée SVG ou une règle CSS.
    assert "480 $" in corps
    assert "aucun relevé" not in corps
    # Savoir *quelle* source a produit le prix conditionne tout diagnostic : deux sources ne
    # relèvent pas le même périmètre de vol, et un prix sans provenance n'est pas vérifiable.
    assert ">google_flights<" in corps


def test_un_prix_sous_la_cible_est_signale_comme_trouvaille(client, session):
    trajet = _trajet(session, target_price_cad=600)
    session.add(
        DailyLow(route_id=trajet.id, day=AUJOURDHUI, price_cad=480, provider="google_flights")
    )
    session.commit()

    assert 'class="trouvaille"' in client.get("/").text


def test_un_prix_ordinaire_nest_pas_signale_comme_trouvaille(client, session):
    trajet = _trajet(session, target_price_cad=300)
    session.add(
        DailyLow(route_id=trajet.id, day=AUJOURDHUI, price_cad=480, provider="google_flights")
    )
    session.commit()

    assert 'class="trouvaille"' not in client.get("/").text


def test_un_prix_sous_le_plancher_de_credibilite_nest_pas_signale_comme_trouvaille(client, session):
    """Le plancher de crédibilité (50 $ par défaut) protège contre les prix aberrants : un relevé
    manifestement erroné ne doit jamais s'afficher comme une aubaine, même sous la cible."""
    trajet = _trajet(session, target_price_cad=100)
    session.add(
        DailyLow(route_id=trajet.id, day=AUJOURDHUI, price_cad=40, provider="google_flights")
    )
    session.commit()

    assert 'class="trouvaille"' not in client.get("/").text


def _historique(session, trajet, jours: dict[int, int]) -> None:
    """Écrit un plus bas quotidien par décalage en arrière d'aujourd'hui : {décalage: prix}."""
    for decalage, prix in jours.items():
        session.add(
            DailyLow(
                route_id=trajet.id,
                day=AUJOURDHUI - timedelta(days=decalage),
                price_cad=prix,
                provider="google_flights",
            )
        )
    session.commit()


def test_un_prix_nettement_sous_la_mediane_est_signale_comme_trouvaille(client, session):
    """Sans cible absolue, une trouvaille se juge au seuil *de trouvaille* (15 %), pas à celui
    des exceptions (40 %). Les deux seuils vivent côte à côte dans les réglages ; confondre l'un
    pour l'autre ne casse rien de visible et ne ferait que rendre le tableau de bord silencieux
    sur les trois quarts des vraies aubaines."""
    trajet = _trajet(session)
    _historique(session, trajet, dict.fromkeys(range(1, 15), 1000))
    session.add(
        DailyLow(route_id=trajet.id, day=AUJOURDHUI, price_cad=750, provider="google_flights")
    )
    session.commit()

    # 25 % sous la médiane : au-dessus du seuil de trouvaille, en dessous de celui d'exception.
    assert 'class="trouvaille"' in client.get("/").text


def test_un_prix_bas_sans_historique_suffisant_nest_pas_signale_comme_trouvaille(client, session):
    """Le garde-fou des 14 jours du §8 : tant que l'historique est court, la médiane n'a aucune
    valeur statistique et « 60 % sous la médiane » ne veut rien dire. Neutraliser ce minimum
    ferait crier à l'aubaine dès le deuxième jour de service."""
    trajet = _trajet(session)
    _historique(session, trajet, dict.fromkeys(range(1, 11), 1000))
    session.add(
        DailyLow(route_id=trajet.id, day=AUJOURDHUI, price_cad=400, provider="google_flights")
    )
    session.commit()

    assert 'class="trouvaille"' not in client.get("/").text


def test_un_historique_trop_court_est_annonce_comme_en_constitution(client, session):
    """Contrepartie visible du test précédent : le tableau de bord doit dire *pourquoi* il ne se
    prononce pas, sinon une colonne vide se confond avec un écart nul."""
    trajet = _trajet(session)
    _historique(session, trajet, dict.fromkeys(range(1, 6), 1000))
    session.add(
        DailyLow(route_id=trajet.id, day=AUJOURDHUI, price_cad=900, provider="google_flights")
    )
    session.commit()

    assert "historique en constitution" in client.get("/").text


def test_le_tableau_de_bord_affiche_lecart_a_la_mediane_et_la_mediane(client, session):
    trajet = _trajet(session)
    _historique(session, trajet, dict.fromkeys(range(1, 15), 1000))
    session.add(
        DailyLow(route_id=trajet.id, day=AUJOURDHUI, price_cad=900, provider="google_flights")
    )
    session.commit()

    corps = client.get("/").text

    assert "10 %" in corps
    assert "médiane 1000 $" in corps
    assert "historique en constitution" not in corps


def test_la_mediane_de_reference_couvre_90_jours(client, session):
    """La fenêtre de comparaison est celle des réglages, pas une valeur commode. Une fenêtre
    rétrécie suivrait les prix récents au lieu de servir de référence : un mois de hausse
    deviendrait la nouvelle normale et plus rien ne ressortirait comme aubaine."""
    trajet = _trajet(session)
    _historique(
        session,
        trajet,
        {**dict.fromkeys(range(1, 8), 400), **dict.fromkeys(range(8, 31), 1000)},
    )
    session.add(
        DailyLow(route_id=trajet.id, day=AUJOURDHUI, price_cad=900, provider="google_flights")
    )
    session.commit()

    corps = client.get("/").text

    # Sur 90 jours la médiane des 30 relevés vaut 1000 ; sur les 7 derniers seulement, elle
    # tomberait à 400 et l'écart affiché basculerait de « 10 % » à « -125 % ».
    assert "médiane 1000 $" in corps


def test_le_tableau_de_bord_affiche_un_graphe_par_trajet(client, session):
    for etiquette in ("Paris au printemps", "Lisbonne en octobre"):
        trajet = _trajet(session, label=etiquette)
        for decalage in range(1, 15):
            session.add(
                DailyLow(
                    route_id=trajet.id,
                    day=AUJOURDHUI - timedelta(days=decalage),
                    price_cad=600 + decalage,
                    provider="google_flights",
                )
            )
    session.commit()

    corps = client.get("/").text

    assert corps.count("<polyline") == 2


def test_le_graphe_va_du_plus_ancien_au_plus_recent(client, session):
    """`daily_low_history` rend les prix du plus récent au plus ancien ; le graphe les veut dans
    l'autre sens. Un `reversed` en trop ou en moins dessinerait la courbe à l'envers — le prix
    monterait à l'écran pendant qu'il descend dans la réalité, ce qui ne se voit pas en lisant
    le code."""
    trajet = _trajet(session)
    session.add(
        DailyLow(
            route_id=trajet.id,
            day=AUJOURDHUI - timedelta(days=2),
            price_cad=800,
            provider="google_flights",
        )
    )
    session.add(
        DailyLow(
            route_id=trajet.id,
            day=AUJOURDHUI - timedelta(days=1),
            price_cad=400,
            provider="google_flights",
        )
    )
    session.commit()

    corps = client.get("/").text

    # Le plus ancien (800) est le maximum : il touche le haut, donc y = 0 au premier point.
    # Le plus récent (400) est le minimum : il touche le bas, à hauteur pleine.
    assert 'points="0,0 240,48"' in corps


def test_un_trajet_sans_donnee_ne_casse_pas_la_page(client, session):
    _trajet(session)

    assert client.get("/").status_code == 200


def test_le_tableau_de_bord_vide_est_explicite(client):
    corps = client.get("/").text

    assert "aucun trajet" in corps.lower()


def test_la_page_sante_liste_les_sources(client, session):
    session.add(
        ProviderHealth(provider="google_flights", last_success_at=MAINTENANT, offers_last_run=42)
    )
    session.commit()

    corps = client.get("/health").text

    assert "google_flights" in corps
    # `transat` est activée dans la fixture mais n'a aucune ligne en base : une source qui n'a
    # jamais tourné doit tout de même apparaître, sans quoi une panne totale serait invisible.
    assert "transat" in corps
    # Le compte d'offres du dernier passage est le seul chiffre qui distingue une source en panne
    # franche d'une source qui répond « 200 OK » sans rien rapporter — la panne silencieuse que
    # le design nomme comme son risque principal.
    assert ">42<" in corps


def test_la_page_sante_montre_les_echecs_et_la_derniere_erreur(client, session):
    session.add(
        ProviderHealth(
            provider="transat", consecutive_failures=3, last_error="sélecteur introuvable"
        )
    )
    session.commit()

    corps = client.get("/health").text

    # `assert "3" in corps` passerait toujours : le « 3 » de htmx.org@2.0.3 est dans le gabarit
    # de base. Il faut viser le contenu de la cellule.
    assert ">3<" in corps
    assert "sélecteur introuvable" in corps


def test_la_page_sante_signale_une_source_qui_na_jamais_reussi(client):
    assert "jamais" in client.get("/health").text.lower()


def test_la_page_sante_affiche_la_date_du_dernier_succes(client, session):
    session.add(ProviderHealth(provider="google_flights", last_success_at=MAINTENANT))
    session.add(ProviderHealth(provider="transat", last_success_at=MAINTENANT))
    session.commit()

    corps = client.get("/health").text

    assert "2026-08-04 12:00" in corps
    assert "jamais" not in corps.lower()


def test_une_source_muette_depuis_plus_de_48h_est_signalee(client, session):
    session.add(
        ProviderHealth(provider="google_flights", last_success_at=MAINTENANT - timedelta(hours=72))
    )
    session.add(ProviderHealth(provider="transat", last_success_at=MAINTENANT))
    session.commit()

    corps = client.get("/health").text

    assert corps.count('class="muet"') == 1


def test_une_source_muette_depuis_exactement_48h_nest_pas_encore_signalee(client, session):
    """Le seuil est « plus de 48 h », pas « 48 h ou plus » : une source pile à 48 h ne doit pas
    encore basculer en rouge, sans quoi un `>=` en trop la ferait apparaître muette une exécution
    trop tôt, sans qu'aucun test ne s'en aperçoive."""
    session.add(
        ProviderHealth(provider="google_flights", last_success_at=MAINTENANT - timedelta(hours=48))
    )
    session.add(ProviderHealth(provider="transat", last_success_at=MAINTENANT))
    session.commit()

    assert 'class="muet"' not in client.get("/health").text


def test_create_app_cable_une_session_reelle_sans_surcharge(engine):
    """Aucun autre test ne le vérifie : la fixture `client` écrase toujours `get_session` après
    coup, ce qui masquerait un `create_app` qui oublierait de le câbler lui-même. Sans ce câblage,
    chaque requête réelle échouerait avec « dépendance de session non configurée »."""
    application = create_app(engine, Settings(enabled_providers=["google_flights"]))
    application.dependency_overrides[get_now] = lambda: MAINTENANT

    reponse = TestClient(application).get("/")

    assert reponse.status_code == 200


def test_une_source_qui_a_repondu_recemment_nest_pas_signalee(client, session):
    session.add(
        ProviderHealth(provider="google_flights", last_success_at=MAINTENANT - timedelta(hours=2))
    )
    session.add(
        ProviderHealth(provider="transat", last_success_at=MAINTENANT - timedelta(hours=47))
    )
    session.commit()

    assert 'class="muet"' not in client.get("/health").text
