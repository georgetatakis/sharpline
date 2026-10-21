import csv
import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.importer import import_csv
from app.models import Base, DataSnapshot, Game, Team
from app.seed import TEAMS, seed_teams

NAMES = [name for name, _, _, _ in TEAMS]
# 16 games: team 0 at team 1, team 2 at team 3, ...
PAIRS = [(NAMES[i], NAMES[i + 1]) for i in range(0, 32, 2)]

MATCHUP_HEADER = ["week", "away_team", "home_team", "kickoff_time"]
RESULT_HEADER = ["week", "away_team", "home_team", "away_score", "home_score"]


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed_teams(session)
        yield session


def write_table(tmp_path, table_key, header, rows, as_of_week=5, target_week=6):
    folder = tmp_path / "season-2026" / f"week-{target_week:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{table_key}.csv"
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    (folder / f"{table_key}.meta.json").write_text(json.dumps({
        "source_name": "sports feed", "source_url": "https://example.com/feed",
        "captured_at": "2026-10-13T09:00:00-04:00",
        "as_of_week": as_of_week, "target_week": target_week,
    }))
    return path


def matchups(tmp_path, pairs=PAIRS, week=6, **kwargs):
    rows = [[week, away, home, "2026-10-18T13:00:00-04:00"] for away, home in pairs]
    return write_table(tmp_path, "weekly_matchups", MATCHUP_HEADER, rows, **kwargs)


def results(tmp_path, rows, **kwargs):
    return write_table(tmp_path, "game_results", RESULT_HEADER, rows, **kwargs)


def run(session, path, tmp_path):
    return import_csv(session, path, log_dir=tmp_path / "logs")


def game_between(session, away_abbr, home_abbr, week):
    away = session.scalar(select(Team.id).where(Team.abbreviation == away_abbr))
    home = session.scalar(select(Team.id).where(Team.abbreviation == home_abbr))
    return session.scalar(
        select(Game).where(Game.away_team_id == away, Game.home_team_id == home, Game.week == week)
    )


def count_games(session):
    return session.scalar(select(func.count()).select_from(Game))


# --- Schedule --------------------------------------------------------------

def test_matchups_create_scheduled_games(session, tmp_path):
    result = run(session, matchups(tmp_path), tmp_path)

    assert result.status == "loaded" and result.rows_loaded == 16
    game = game_between(session, "BUF", "MIA", 6)
    assert game.status == "scheduled" and game.home_score is None
    assert game.kickoff_time is not None
    snapshot = session.scalar(select(DataSnapshot))
    assert (snapshot.table_key, snapshot.status, snapshot.row_count) == ("weekly_matchups", "loaded", 16)


def test_reimported_schedule_updates_kickoff_without_duplicating(session, tmp_path):
    run(session, matchups(tmp_path), tmp_path)
    rows = [[6, away, home, "2026-10-18T20:20:00-04:00"] for away, home in PAIRS]
    run(session, write_table(tmp_path, "weekly_matchups", MATCHUP_HEADER, rows), tmp_path)

    assert count_games(session) == 16
    # SQLite drops the offset on read (Postgres keeps it); the wall-clock time is what matters here.
    assert game_between(session, "BUF", "MIA", 6).kickoff_time.replace(tzinfo=None) == datetime(2026, 10, 18, 20, 20)


def test_light_slate_is_partial(session, tmp_path):
    result = run(session, matchups(tmp_path, PAIRS[:12]), tmp_path)

    assert result.status == "partial" and result.rows_loaded == 12


def test_schedule_for_another_week_is_flagged(session, tmp_path):
    result = run(session, matchups(tmp_path, week=7), tmp_path)

    assert any("week 7" in f and "target_week 6" in f for f in result.flags)


@pytest.mark.parametrize(
    "kickoff, message",
    [("2026-10-18T13:00:00", "UTC offset"), ("Sunday 1pm", "ISO"), ("", "ISO")],
)
def test_bad_kickoff_rejects_the_row(session, tmp_path, kickoff, message):
    rows = [[6, away, home, "2026-10-18T13:00:00-04:00"] for away, home in PAIRS]
    rows[0][3] = kickoff

    result = run(session, write_table(tmp_path, "weekly_matchups", MATCHUP_HEADER, rows), tmp_path)

    assert result.status == "partial"
    assert len(result.rejected) == 1 and message in result.rejected[0]


def test_team_listed_twice_in_a_week_rejects_both_rows(session, tmp_path):
    pairs = PAIRS.copy()
    pairs[1] = (NAMES[0], NAMES[3])  # Bills again, now at Jets

    result = run(session, matchups(tmp_path, pairs), tmp_path)

    assert any("Buffalo Bills appears more than once in week 6" in r for r in result.rejected)
    assert game_between(session, "BUF", "MIA", 6) is None
    assert result.rows_loaded == 14


@pytest.mark.parametrize("away, home", [("Nowhere FC", "Rams"), ("Rams", "LAR")])
def test_unknown_or_same_team_rejects_the_row(session, tmp_path, away, home):
    pairs = PAIRS[:15] + [(away, home)]

    result = run(session, matchups(tmp_path, pairs), tmp_path)

    assert len(result.rejected) == 1 and result.rows_loaded == 15


# --- Final results ---------------------------------------------------------

def test_result_finishes_a_scheduled_game(session, tmp_path):
    run(session, matchups(tmp_path, week=5, as_of_week=4, target_week=5), tmp_path)

    result = run(session, results(tmp_path, [[5, "Buffalo Bills", "Miami Dolphins", "24", "17"]]), tmp_path)

    assert result.status == "loaded"
    game = game_between(session, "BUF", "MIA", 5)
    assert (game.status, game.away_score, game.home_score) == ("final", 24, 17)
    assert game.kickoff_time is not None  # schedule kept
    assert count_games(session) == 16


def test_tie_is_stored_as_a_final_with_equal_scores(session, tmp_path):
    run(session, results(tmp_path, [[5, "Rams", "49ers", "27", "27"]]), tmp_path)

    game = game_between(session, "LA", "SF", 5)
    assert (game.status, game.away_score, game.home_score) == ("final", 27, 27)


def test_result_without_a_schedule_creates_the_game(session, tmp_path):
    result = run(session, results(tmp_path, [[3, "KC", "LV", "31", "10"]]), tmp_path)

    assert result.status == "loaded"
    game = game_between(session, "KC", "LV", 3)
    assert game.status == "final" and game.kickoff_time is None


def test_home_away_swapped_against_stored_game_is_rejected(session, tmp_path):
    run(session, results(tmp_path, [[5, "Bills", "Dolphins", "24", "17"]]), tmp_path)

    result = run(session, results(tmp_path, [
        [5, "Dolphins", "Bills", "17", "24"],
        [5, "Chiefs", "Raiders", "20", "13"],
    ]), tmp_path)

    assert result.status == "partial"
    assert any("swapped" in r for r in result.rejected)
    game = game_between(session, "BUF", "MIA", 5)
    assert (game.away_score, game.home_score) == (24, 17)


def test_reimporting_same_scores_is_quiet_and_changed_scores_are_flagged(session, tmp_path):
    run(session, results(tmp_path, [[5, "Bills", "Dolphins", "24", "17"]]), tmp_path)

    same = run(session, results(tmp_path, [[5, "Bills", "Dolphins", "24", "17"]]), tmp_path)
    changed = run(session, results(tmp_path, [[5, "Bills", "Dolphins", "24", "20"]]), tmp_path)

    assert not [f for f in same.flags if f.startswith("disagreement")]
    assert any(f.startswith("disagreement") and "24-17" in f and "24-20" in f for f in changed.flags)
    assert count_games(session) == 1


@pytest.mark.parametrize("away_score", ["", "-3", "24.5", "W"])
def test_bad_score_rejects_the_row(session, tmp_path, away_score):
    result = run(session, results(tmp_path, [
        [5, "Bills", "Dolphins", away_score, "17"],
        [5, "Chiefs", "Raiders", "20", "13"],
    ]), tmp_path)

    assert result.status == "partial" and result.rows_loaded == 1
    assert game_between(session, "BUF", "MIA", 5) is None


def test_result_after_as_of_week_is_rejected(session, tmp_path):
    # A file for target week 6 (stats through week 5) can't hold a final from week 6.
    result = run(session, results(tmp_path, [
        [6, "Bills", "Dolphins", "24", "17"],
        [5, "Chiefs", "Raiders", "20", "13"],
    ]), tmp_path)

    assert any("after as_of_week 5" in r for r in result.rejected)
    assert game_between(session, "BUF", "MIA", 6) is None


def test_source_behind_as_of_week_is_flagged_stale(session, tmp_path):
    result = run(session, results(tmp_path, [[4, "Bills", "Dolphins", "24", "17"]]), tmp_path)

    assert any(f.startswith("stale") and "week 4" in f for f in result.flags)


def test_result_import_writes_snapshot_and_log(session, tmp_path):
    run(session, results(tmp_path, [[5, "Bills", "Dolphins", "24", "17"]]), tmp_path)

    snapshot = session.scalar(select(DataSnapshot))
    assert (snapshot.table_key, snapshot.source_url, snapshot.row_count) == ("game_results", "https://example.com/feed", 1)
    end = json.loads((tmp_path / "logs" / "import.jsonl").read_text().splitlines()[-1])
    assert (end["event"], end["table_key"], end["rows_loaded"]) == ("run_end", "game_results", 1)


def test_file_with_no_usable_rows_is_rejected(session, tmp_path):
    result = run(session, results(tmp_path, [[5, "Nowhere FC", "Dolphins", "24", "17"]]), tmp_path)

    assert result.status == "rejected" and "no usable rows" in result.reason
    assert count_games(session) == 0


# --- Database rules --------------------------------------------------------

def test_final_game_must_have_both_scores(session):
    kc = session.scalar(select(Team.id).where(Team.abbreviation == "KC"))
    lv = session.scalar(select(Team.id).where(Team.abbreviation == "LV"))
    session.add(Game(season=2026, week=1, home_team_id=kc, away_team_id=lv, status="final", home_score=21))

    with pytest.raises(IntegrityError):
        session.commit()


def test_game_status_must_be_known(session):
    kc = session.scalar(select(Team.id).where(Team.abbreviation == "KC"))
    lv = session.scalar(select(Team.id).where(Team.abbreviation == "LV"))
    session.add(Game(season=2026, week=1, home_team_id=kc, away_team_id=lv, status="live",
                     kickoff_time=datetime(2026, 9, 10)))

    with pytest.raises(IntegrityError):
        session.commit()
