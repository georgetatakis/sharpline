import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.main import app
from app.models import Base, Team, TeamDefenseStats, TeamOffenseStats
from app.seed import seed_teams


@pytest.fixture
def session():
    # One shared in-memory SQLite connection, so the API sees what the test wrote.
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed_teams(session)
        yield session


@pytest.fixture
def client(session):
    app.dependency_overrides[get_db] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def team_id(session, abbreviation):
    return session.scalar(select(Team.id).where(Team.abbreviation == abbreviation))


@pytest.fixture
def kc(session):
    kc = team_id(session, "KC")
    session.add_all([
        TeamOffenseStats(team_id=kc, season=2025, as_of_week=17, epa_per_play=0.12),
        TeamOffenseStats(team_id=kc, season=2026, as_of_week=5, epa_per_play=0.15, motion_rate=0.556),
        TeamOffenseStats(team_id=kc, season=2026, as_of_week=6, epa_per_play=0.17),
        TeamDefenseStats(team_id=kc, season=2026, as_of_week=5, epa_per_play_allowed=-0.04, man_rate=0.208),
        # Another team's rows must never leak into KC's response.
        TeamOffenseStats(team_id=team_id(session, "BUF"), season=2026, as_of_week=5, epa_per_play=0.21),
    ])
    session.commit()
    return kc


def test_offense_returns_all_snapshots_newest_first(client, kc):
    response = client.get(f"/teams/{kc}/offense")

    assert response.status_code == 200
    body = response.json()
    assert body["team"] == {"id": kc, "name": "Kansas City Chiefs", "abbreviation": "KC"}
    assert [(s["season"], s["as_of_week"]) for s in body["snapshots"]] == [(2026, 6), (2026, 5), (2025, 17)]


def test_offense_filters_by_season_and_as_of_week(client, kc):
    body = client.get(f"/teams/{kc}/offense", params={"season": 2026, "as_of_week": 5}).json()

    assert len(body["snapshots"]) == 1
    snapshot = body["snapshots"][0]
    assert snapshot["epa_per_play"] == pytest.approx(0.15)
    assert snapshot["motion_rate"] == pytest.approx(0.556)
    assert snapshot["pers_11"] is None  # not imported yet
    assert "id" not in snapshot and "team_id" not in snapshot


def test_offense_filters_by_season_only(client, kc):
    body = client.get(f"/teams/{kc}/offense", params={"season": 2025}).json()

    assert [s["as_of_week"] for s in body["snapshots"]] == [17]


def test_defense_endpoint_returns_defense_columns(client, kc):
    body = client.get(f"/teams/{kc}/defense", params={"season": 2026}).json()

    assert len(body["snapshots"]) == 1
    snapshot = body["snapshots"][0]
    assert snapshot["epa_per_play_allowed"] == pytest.approx(-0.04)
    assert snapshot["man_rate"] == pytest.approx(0.208)
    assert "epa_per_play" not in snapshot


def test_team_without_data_returns_empty_list(client, session, kc):
    body = client.get(f"/teams/{team_id(session, 'SEA')}/defense").json()

    assert body["team"]["abbreviation"] == "SEA"
    assert body["snapshots"] == []


@pytest.mark.parametrize("side", ["offense", "defense"])
def test_unknown_team_is_404(client, side):
    response = client.get(f"/teams/9999/{side}")

    assert response.status_code == 404


@pytest.mark.parametrize("params", [{"as_of_week": -1}, {"as_of_week": 30}, {"season": 1999}, {"season": "abc"}])
def test_invalid_filters_are_422(client, kc, params):
    response = client.get(f"/teams/{kc}/offense", params=params)

    assert response.status_code == 422
