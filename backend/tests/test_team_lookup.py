import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Base, Team
from app.seed import TEAMS, seed_teams
from app.team_lookup import TeamLookup


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed_teams(session)
        yield session


@pytest.fixture
def lookup(session):
    return TeamLookup(session)


def rams_id(session):
    return session.scalar(select(Team.id).where(Team.abbreviation == "LA"))


@pytest.mark.parametrize(
    "text",
    ["Los Angeles Rams", "Rams", "LA", "LAR", "los angeles rams", "Los Angeles\nRams", "  RAMS "],
)
def test_every_rams_spelling_maps_to_one_team(session, lookup, text):
    assert lookup.team_id(text) == rams_id(session)


def test_all_32_full_names_nicknames_and_abbreviations_match(lookup):
    ids = set()
    for name, abbreviation, _, _ in TEAMS:
        team_id = lookup.team_id(name)
        assert team_id is not None
        assert lookup.team_id(name.split()[-1]) == team_id
        assert lookup.team_id(abbreviation) == team_id
        ids.add(team_id)
    assert len(ids) == 32


@pytest.mark.parametrize("text", ["Los Angeles", "New York", "XYZ", ""])
def test_ambiguous_or_unknown_text_matches_nothing(lookup, text):
    assert lookup.team_id(text) is None
