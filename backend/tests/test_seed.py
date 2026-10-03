from collections import Counter

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models import Base, Team
from app.seed import TEAMS, seed_teams


@pytest.fixture
def session():
    # In-memory SQLite: enough to test the seed logic without a running Postgres.
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_team_list_has_32_unique_teams():
    assert len(TEAMS) == 32
    assert len({abbr for _, abbr, _, _ in TEAMS}) == 32
    assert len({name for name, _, _, _ in TEAMS}) == 32


def test_each_division_has_four_teams():
    divisions = Counter((conf, div) for _, _, conf, div in TEAMS)
    assert len(divisions) == 8
    assert set(divisions.values()) == {4}


def test_seed_inserts_all_teams(session):
    inserted = seed_teams(session)

    assert inserted == 32
    assert session.scalar(select(func.count()).select_from(Team)) == 32


def test_seed_is_idempotent(session):
    seed_teams(session)
    inserted_again = seed_teams(session)

    assert inserted_again == 0
    assert session.scalar(select(func.count()).select_from(Team)) == 32


def test_seed_corrects_changed_team_data(session):
    seed_teams(session)
    team = session.scalar(select(Team).where(Team.abbreviation == "WAS"))
    team.name = "Washington Football Team"
    session.commit()

    seed_teams(session)

    team = session.scalar(select(Team).where(Team.abbreviation == "WAS"))
    assert team.name == "Washington Commanders"
