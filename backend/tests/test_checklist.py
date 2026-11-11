from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.checklist as checklist_module
from app.db import get_db
from app.main import app
from app.models import Base, DataSnapshot, Game, GameLine, Team
from app.seed import seed_teams

SEASON, WEEK = 2026, 7
CAPTURED = datetime(2026, 10, 23, 4, 29, tzinfo=timezone.utc)


@pytest.fixture
def session():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed_teams(session)
        yield session


@pytest.fixture
def sources(monkeypatch):
    """Overlap decisions: offense metrics all nflverse; defense explosive stays on the screenshot."""
    decided = {f"offense.{c}": {"source": "nflverse"} for c in (
        "epa_per_play", "yards_per_play", "points_per_drive", "explosive_play_rate",
        "down_conversion_rate", "off_proe")}
    decided |= {f"defense.{c}": {"source": "nflverse"} for c in (
        "epa_per_play_allowed", "yards_per_play_allowed", "points_per_drive_allowed",
        "down_conversion_rate_allowed", "def_proe")}
    decided["defense.explosive_play_rate_allowed"] = {"source": "screenshot"}
    monkeypatch.setattr(checklist_module, "feature_sources", lambda: decided)
    return decided


@pytest.fixture
def client(session, sources):
    app.dependency_overrides[get_db] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def snapshot(session, table_key, as_of_week, target_week, status="loaded", rows=32):
    session.add(DataSnapshot(season=SEASON, as_of_week=as_of_week, target_week=target_week,
                             table_key=table_key, source_name="test", source_url="screenshot",
                             captured_at=CAPTURED, file_name=f"{table_key}.csv", row_count=rows,
                             status=status))
    session.commit()


def week_games(session, week=WEEK, lines=True, moneyline=True):
    teams = sorted(session.scalars(select(Team.id)))
    for i in range(0, 32, 2):
        game = Game(season=SEASON, week=week, away_team_id=teams[i], home_team_id=teams[i + 1])
        session.add(game)
        session.flush()
        if lines:
            session.add(GameLine(game_id=game.id, line_type="current", spread_home=-3.0, total=45.5,
                                 moneyline_home=-150 if moneyline else None,
                                 moneyline_away=130 if moneyline else None,
                                 captured_at=CAPTURED, source_name="test"))
    session.commit()


def get(client, week=WEEK):
    response = client.get("/data/checklist", params={"season": SEASON, "week": week})
    assert response.status_code == 200
    return response.json()


def item(body, table_key):
    return next(i for g in body["groups"] for i in g["items"] if i["table_key"] == table_key)


def test_empty_database_lists_everything_required_as_missing(client):
    body = get(client)

    assert body["ready_for_game_model"] is False
    assert item(body, "nflverse_team_week_stats")["status"] == "missing"
    assert item(body, "team_defense_metrics")["status"] == "missing"
    assert item(body, "player_prop_lines")["status"] == "optional"
    assert body["data_through_week"] == 6


def test_screenshot_table_whose_stats_all_read_nflverse_is_optional(client):
    body = get(client)

    offense = item(body, "team_offense_metrics")
    assert (offense["required"], offense["status"]) == (False, "optional")
    assert "audit copy" in offense["detail"]
    defense = item(body, "team_defense_metrics")
    assert defense["required"] and "explosive_play_rate_allowed" in defense["detail"]
    assert item(body, "proe_matchups")["required"] is False


def test_screenshot_counts_only_for_the_week_it_targets(client, session):
    snapshot(session, "team_defense_metrics", 5, 6)

    assert item(get(client, week=6), "team_defense_metrics")["status"] == "loaded"
    assert item(get(client, week=7), "team_defense_metrics")["status"] == "missing"


def test_partial_and_rejected_snapshots_show_as_such(client, session):
    snapshot(session, "man_zone_coverage", 6, 7, status="partial", rows=30)
    snapshot(session, "defensive_line", 6, 7, status="rejected", rows=0)

    body = get(client)

    assert item(body, "man_zone_coverage")["status"] == "partial"
    assert item(body, "defensive_line")["status"] == "rejected"
    assert "defensive_line" in body["blocking"]
    assert "man_zone_coverage" not in body["blocking"]


def test_nflverse_counts_when_it_runs_through_last_week_and_is_stale_otherwise(client, session):
    snapshot(session, "nflverse_team_week_stats", 6, 7, rows=384)

    assert item(get(client, week=7), "nflverse_team_week_stats")["status"] == "loaded"
    stale = item(get(client, week=8), "nflverse_team_week_stats")
    assert stale["status"] == "stale" and "download the updated nflverse files" in stale["detail"]


def test_schedule_needs_this_weeks_games(client, session):
    snapshot(session, "nflverse_schedules", 6, 7, rows=272)

    before = item(get(client), "nflverse_schedules / weekly_matchups")
    week_games(session)
    after = item(get(client), "nflverse_schedules / weekly_matchups")

    assert before["status"] == "missing" and "no week 7 games" in before["detail"]
    assert after["status"] == "loaded"


def test_chat_csv_fallback_covers_results_when_nflverse_is_behind(client, session):
    snapshot(session, "nflverse_schedules", 5, 6, rows=272)
    snapshot(session, "game_results", 6, 7, rows=14)

    results = item(get(client), "nflverse_schedules / game_results")

    assert results["status"] == "loaded" and "game_results fallback" in results["detail"]


def test_live_lines_need_every_game_priced(client, session):
    week_games(session, moneyline=False)

    body = get(client)

    assert item(body, "game_lines (current spread, total)")["status"] == "loaded"
    moneyline = item(body, "game_lines (current moneyline)")
    assert moneyline["status"] == "missing" and moneyline["detail"] == "0 of 16 games"


def test_ready_when_every_required_table_is_in(client, session, sources):
    for key in ("nflverse_team_week_stats", "nflverse_schedules"):
        snapshot(session, key, 6, 7)
    week_games(session)
    for table_key, _ in checklist_module.TEAM_TABLES:
        snapshot(session, table_key, 6, 7)

    body = get(client)

    assert body["ready_for_game_model"] is True and body["blocking"] == []


def test_without_feature_sources_every_screenshot_table_is_required(session, monkeypatch):
    def missing():
        raise FileNotFoundError

    monkeypatch.setattr(checklist_module, "feature_sources", missing)
    app.dependency_overrides[get_db] = lambda: session
    body = TestClient(app).get("/data/checklist", params={"season": SEASON, "week": WEEK}).json()
    app.dependency_overrides.clear()

    assert body["feature_sources"] == "missing"
    assert item(body, "team_offense_metrics")["required"] is True


@pytest.mark.parametrize("params", [{"season": SEASON}, {"season": SEASON, "week": 0},
                                    {"season": SEASON, "week": 23}, {"week": 7}])
def test_bad_query_is_422(client, params):
    assert client.get("/data/checklist", params=params).status_code == 422
