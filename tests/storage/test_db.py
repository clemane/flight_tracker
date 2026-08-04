"""Garde-fous sur `session_scope`.

Cette fonction est le dernier rempart entre une erreur au milieu d'un scan et un
historique de prix à moitié écrit. Une transaction avortée qui laisserait ses lignes
derrière elle ne lèverait aucune erreur et ne remplirait aucun journal : elle
fausserait seulement, et pour toujours, la base de comparaison sur laquelle repose la
détection d'aubaines. D'où ces tests, que le brief de la tâche 4 ne demandait pas.
"""

import pytest
from sqlmodel import Session, select

from scrappervol.storage.db import session_scope
from scrappervol.storage.models import Route


def test_session_scope_commite_en_sortie_normale(engine):
    with session_scope(engine) as session:
        session.add(Route(label="YUL-CDG"))

    with Session(engine) as verification:
        trajets = verification.exec(select(Route)).all()
    assert [trajet.label for trajet in trajets] == ["YUL-CDG"]


def test_session_scope_annule_tout_si_une_exception_survient(engine):
    with session_scope(engine) as session:
        session.add(Route(label="déjà en base"))

    with (
        pytest.raises(RuntimeError, match="panne au milieu du scan"),
        session_scope(engine) as session,
    ):
        session.add(Route(label="ne doit pas survivre"))
        session.flush()  # la ligne existe dans la transaction avant l'échec
        raise RuntimeError("panne au milieu du scan")

    with Session(engine) as verification:
        trajets = verification.exec(select(Route)).all()
    assert [trajet.label for trajet in trajets] == ["déjà en base"]


def test_session_scope_propage_lexception_au_lieu_de_lavaler(engine):
    with pytest.raises(ValueError, match="remonte jusquici"), session_scope(engine):
        raise ValueError("remonte jusquici")
