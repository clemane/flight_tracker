"""Garde-fous sur `session_scope`.

Une transaction avortée qui laisserait ses lignes derrière elle ne lèverait aucune
erreur et ne remplirait aucun journal : elle fausserait seulement, et pour toujours, la
base de comparaison sur laquelle repose la détection d'aubaines. D'où ces tests, que le
brief de la tâche 4 ne demandait pas.

Portée réelle, à ne pas surestimer : `session_scope` n'annule que ce qui n'a pas encore
été committé. Or chaque fonction de `scrappervol.storage.repo` committe elle-même. Un
scan qui enchaîne `record_observations` puis `upsert_daily_low` dans un même
`session_scope` n'est donc PAS atomique : si l'erreur survient entre les deux, les
observations restent en base et le plus bas du jour reste périmé, durablement et en
silence. Vérifié par mutation à la tâche 5. Ce point doit être tranché explicitement à
la tâche 15 — soit en retirant les commits de `repo.py`, soit en concevant le scan comme
reprenable plutôt que tout-ou-rien. Ne pas se fier à ces tests pour croire l'inverse.
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
