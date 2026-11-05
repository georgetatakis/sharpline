import json
from datetime import datetime

import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models import Base, DataSnapshot, Game, GameLine, GameScoringPlay, Team
from app.nflverse_loader import (
    PBP_COLUMNS,
    SCHEDULE_COLUMNS,
    import_nflverse,
    write_sidecars,
)
from app.seed import seed_teams


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed_teams(session)
        yield session


def sidecar(dataset, season, **overrides):
    fields = {
        "source_name": f"nflverse {dataset} (nflreadr 1.5.1)",
        "source_url": f"nflreadr::load_{dataset}({season})",
        "captured_at": "2026-10-23T00:29:50-04:00",
        "as_of_week": 7,
        "target_week": 8,
    }
    fields.update(overrides)
    return fields


def write_file(root, dataset, season, rows, columns, meta=None):
    folder = root / dataset / str(season)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{dataset}.csv.gz"
    df = pd.DataFrame(rows)
    for column in columns - set(df.columns):
        df[column] = None
    df.to_csv(path, index=False)
    if meta is not False:
        (folder / f"{dataset}.meta.json").write_text(json.dumps(meta or sidecar(dataset, season)))
    return path


def game(away="KC", home="LV", week=6, season=2026, **fields):
    row = {
        "game_id": f"{season}_{week:02d}_{away}_{home}", "season": season, "game_type": "REG",
        "week": week, "gameday": "2026-10-18", "gametime": "16:25", "away_team": away,
        "home_team": home, "away_score": 27, "home_score": 20, "location": "Home",
        "roof": "dome", "temp": None, "wind": None, "spread_line": -3.5,
        "home_spread_odds": -110, "away_spread_odds": -110, "total_line": 47.5,
        "over_odds": -108, "under_odds": -112, "home_moneyline": 150, "away_moneyline": -175,
    }
    row.update(fields)
    return row


def schedules(tmp_path, rows, season=2026, **kwargs):
    return write_file(tmp_path, "schedules", season, rows, SCHEDULE_COLUMNS, **kwargs)


def plays(tmp_path, rows, season=2026, meta=None):
    # Play-by-play here runs through week 6, the week of the test game.
    meta = sidecar("play_by_play", season, as_of_week=6, target_week=7) if meta is None else meta
    return write_file(tmp_path, "play_by_play", season, rows, PBP_COLUMNS, meta)


def play(play_id, game_id="2026_06_KC_LV", week=6, **fields):
    row = {"game_id": game_id, "play_id": play_id, "season": 2026, "week": week, "qtr": 1,
           "time": "10:00", "desc": f"play {play_id}", "posteam": "KC", "defteam": "LV",
           "touchdown": 0, "pass_touchdown": 0, "rush_touchdown": 0, "safety": 0,
           "defensive_two_point_conv": 0}
    row.update(fields)
    return row


def td_pass(play_id, team="KC", other="LV", **fields):
    return play(play_id, posteam=team, defteam=other, td_team=team, touchdown=1,
                pass_touchdown=1, td_player_name="T.Kelce", td_player_id="00-0030506", **fields)


def xp(play_id, team="KC", other="LV"):
    return play(play_id, posteam=team, defteam=other, extra_point_result="good",
                kicker_player_name="H.Butker", kicker_player_id="00-0033873")


def fg(play_id, team="KC", other="LV"):
    return play(play_id, posteam=team, defteam=other, field_goal_result="made",
                kicker_player_name="K", kicker_player_id="00-1")


def run(session, path, tmp_path):
    return import_nflverse(session, path, log_dir=tmp_path / "logs")


def only_game(session):
    return session.scalars(select(Game)).one()


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def team_id(session, abbreviation):
    return session.scalar(select(Team.id).where(Team.abbreviation == abbreviation))


# --- Schedules -------------------------------------------------------------

def test_schedule_loads_game_score_weather_and_closing_line(session, tmp_path):
    result = run(session, schedules(tmp_path, [game(roof="outdoors", temp=61, wind=9)]), tmp_path)

    assert result.status == "loaded" and result.rows_loaded == 1
    g = only_game(session)
    assert (g.status, g.away_score, g.home_score) == ("final", 27, 20)
    assert (g.nflverse_game_id, g.game_type, g.neutral_site) == ("2026_06_KC_LV", "REG", False)
    assert (g.roof, g.temp, g.wind) == ("outdoors", 61, 9)
    assert g.kickoff_time.replace(tzinfo=None) == datetime(2026, 10, 18, 16, 25)
    line = session.scalars(select(GameLine)).one()
    assert line.line_type == "closing"
    assert line.captured_at.replace(tzinfo=None) == datetime(2026, 10, 18, 16, 25)
    assert (line.total, line.over_price, line.under_price) == (47.5, -108, -112)
    assert (line.moneyline_home, line.moneyline_away) == (150, -175)


def test_spread_is_stored_the_way_a_sportsbook_prints_it(session, tmp_path):
    # nflverse spread_line -3.5 means the home team is expected to lose by 3.5.
    run(session, schedules(tmp_path, [game(spread_line=-3.5)]), tmp_path)

    assert session.scalars(select(GameLine)).one().spread_home == 3.5


def test_upcoming_game_is_scheduled_with_a_current_line(session, tmp_path):
    rows = [game(week=8, away_score=None, home_score=None)]

    run(session, schedules(tmp_path, rows), tmp_path)

    g = only_game(session)
    assert g.status == "scheduled" and g.home_score is None
    line = session.scalars(select(GameLine)).one()
    assert line.line_type == "current"
    assert line.captured_at.replace(tzinfo=None) == datetime(2026, 10, 23, 0, 29, 50)


def test_game_without_lines_loads_with_no_line(session, tmp_path):
    rows = [game(week=12, away_score=None, home_score=None, spread_line=None, total_line=None,
                 home_moneyline=None, away_moneyline=None, home_spread_odds=None,
                 away_spread_odds=None, over_odds=None, under_odds=None)]

    result = run(session, schedules(tmp_path, rows), tmp_path)

    assert result.status == "loaded" and count(session, GameLine) == 0


def test_tie_neutral_site_and_playoff_game(session, tmp_path):
    rows = [game(away_score=27, home_score=27, location="Neutral", game_type="WC", week=19, season=2025)]

    run(session, schedules(tmp_path, rows, season=2025,
                           meta=sidecar("schedules", 2025, as_of_week=22, target_week=23)), tmp_path)

    g = only_game(session)
    assert (g.status, g.away_score, g.home_score) == ("final", 27, 27)
    assert g.neutral_site and g.game_type == "WC"


def test_old_team_abbreviations_map_to_the_franchise(session, tmp_path):
    run(session, schedules(tmp_path, [game(away="OAK", home="DEN", season=2019)], season=2019,
                           meta=sidecar("schedules", 2019)), tmp_path)

    assert only_game(session).away_team_id == team_id(session, "LV")


def test_reimport_updates_in_place_and_flags_a_changed_score(session, tmp_path):
    run(session, schedules(tmp_path, [game()]), tmp_path)

    same = run(session, schedules(tmp_path, [game()]), tmp_path)
    changed = run(session, schedules(tmp_path, [game(home_score=23)]), tmp_path)

    assert not any(f.startswith("disagreement") for f in same.flags)
    assert any(f.startswith("disagreement") and "27-20" in f and "27-23" in f for f in changed.flags)
    assert (count(session, Game), count(session, GameLine)) == (1, 1)
    assert only_game(session).home_score == 23


def test_game_loaded_from_a_chat_csv_is_matched_not_duplicated(session, tmp_path):
    session.add(Game(season=2026, week=6, away_team_id=team_id(session, "KC"),
                     home_team_id=team_id(session, "LV")))
    session.commit()

    run(session, schedules(tmp_path, [game()]), tmp_path)

    assert count(session, Game) == 1
    assert only_game(session).nflverse_game_id == "2026_06_KC_LV"


def test_final_after_target_week_is_rejected(session, tmp_path):
    result = run(session, schedules(tmp_path, [game(), game("BUF", "MIA", week=9)]), tmp_path)

    assert result.status == "partial"
    assert any("week 9 final is after target_week 8" in r for r in result.rejected)
    assert count(session, Game) == 1


def test_final_already_played_in_the_target_week_loads_and_is_flagged(session, tmp_path):
    result = run(session, schedules(tmp_path, [game(), game("BUF", "MIA", week=8)]), tmp_path)

    assert result.status == "loaded" and count(session, Game) == 2
    assert any("target week 8 is partly played: 1 final(s) already in (2026_08_BUF_MIA)" in f
               for f in result.flags)


@pytest.mark.parametrize(
    "fields, message",
    [
        ({"away_score": 27, "home_score": None}, "only one team has a score"),
        ({"game_type": "PRE"}, "game_type 'PRE'"),
        ({"home_team": "Nowhere"}, "unknown team"),
    ],
)
def test_bad_row_is_rejected_and_the_rest_load(session, tmp_path, fields, message):
    result = run(session, schedules(tmp_path, [game(**fields), game("BUF", "MIA")]), tmp_path)

    assert result.status == "partial" and result.rows_loaded == 1
    assert any(message in r for r in result.rejected)


def test_implausible_line_is_flagged_and_the_game_still_loads(session, tmp_path):
    result = run(session, schedules(tmp_path, [game(total_line=470)]), tmp_path)

    assert result.status == "loaded" and count(session, Game) == 1
    assert count(session, GameLine) == 0
    assert any("line not loaded" in f and "total 470" in f for f in result.flags)


def test_short_season_is_flagged(session, tmp_path):
    result = run(session, schedules(tmp_path, [game()]), tmp_path)

    assert any("2026 has 1 regular-season games loaded, a full season has 272" in f for f in result.flags)


def test_stale_schedule_is_flagged(session, tmp_path):
    result = run(session, schedules(tmp_path, [game(week=5)]), tmp_path)

    assert any(f.startswith("stale") and "week 5" in f for f in result.flags)


def test_file_without_sidecar_is_refused(session, tmp_path):
    result = run(session, schedules(tmp_path, [game()], meta=False), tmp_path)

    assert result.status == "rejected" and "no sidecar" in result.reason
    assert count(session, Game) == 0 and count(session, DataSnapshot) == 0


def test_file_with_another_season_is_refused(session, tmp_path):
    result = run(session, schedules(tmp_path, [game(season=2025)]), tmp_path)

    assert result.status == "rejected" and "holds seasons [2025]" in result.reason
    assert count(session, Game) == 0


def test_file_missing_a_column_is_refused(session, tmp_path):
    path = schedules(tmp_path, [game()])
    pd.read_csv(path).drop(columns="total_line").to_csv(path, index=False)

    result = run(session, path, tmp_path)

    assert result.status == "rejected" and "missing total_line" in result.reason


def test_schedule_import_writes_snapshot_and_log(session, tmp_path):
    run(session, schedules(tmp_path, [game()]), tmp_path)

    snapshot = session.scalars(select(DataSnapshot)).one()
    assert (snapshot.table_key, snapshot.season, snapshot.as_of_week) == ("nflverse_schedules", 2026, 7)
    assert snapshot.source_url == "nflreadr::load_schedules(2026)"
    end = json.loads((tmp_path / "logs" / "import.jsonl").read_text().splitlines()[-1])
    assert (end["event"], end["table_key"], end["counts"]["closing_lines"]) == ("run_end", "nflverse_schedules", 1)


# --- Scoring plays ---------------------------------------------------------

def load_game(session, tmp_path, **fields):
    run(session, schedules(tmp_path, [game(**fields)]), tmp_path)


def test_scoring_plays_load_with_type_scorer_and_points(session, tmp_path):
    load_game(session, tmp_path, away_score=10, home_score=3)
    rows = [play(1), td_pass(2), xp(3), fg(4, "LV", "KC"), fg(5)]

    result = run(session, plays(tmp_path, rows), tmp_path)

    assert result.status == "loaded" and result.rows_loaded == 4
    assert not result.flags
    td = session.scalar(select(GameScoringPlay).where(GameScoringPlay.play_type == "td_pass"))
    assert (td.points, td.scorer_name, td.scorer_nflverse_id) == (6, "T.Kelce", "00-0030506")
    assert td.scoring_team_id == team_id(session, "KC")


def test_return_td_two_point_and_safety_are_classified(session, tmp_path):
    load_game(session, tmp_path, away_score=2, home_score=8)
    rows = [
        play(1, posteam="KC", defteam="LV", td_team="LV", touchdown=1, td_player_name="D",
             td_player_id="00-2"),
        play(2, posteam="LV", defteam="KC", two_point_conv_result="success",
             receiver_player_name="R", receiver_player_id="00-3"),
        play(3, posteam="LV", defteam="KC", safety=1),
    ]

    result = run(session, plays(tmp_path, rows), tmp_path)

    kinds = dict(session.execute(select(GameScoringPlay.play_type, GameScoringPlay.points)).all())
    assert kinds == {"td_other": 6, "two_point": 2, "other": 2}
    assert not result.flags


def test_plays_not_adding_up_to_the_final_are_flagged(session, tmp_path):
    load_game(session, tmp_path, away_score=27, home_score=20)

    result = run(session, plays(tmp_path, [td_pass(1)]), tmp_path)

    assert any("add up to 6-0, final is 27-20" in f for f in result.flags)
    assert only_game(session).away_score == 27


def test_reimport_replaces_a_games_scoring_plays(session, tmp_path):
    load_game(session, tmp_path, away_score=7, home_score=0)
    run(session, plays(tmp_path, [td_pass(1), xp(2)]), tmp_path)

    run(session, plays(tmp_path, [td_pass(1), xp(2)]), tmp_path)

    assert count(session, GameScoringPlay) == 2


def test_play_by_play_without_its_game_is_rejected(session, tmp_path):
    load_game(session, tmp_path, away_score=7, home_score=0)
    rows = [td_pass(1), xp(2), td_pass(1, game_id="2026_06_BUF_MIA", team="BUF", other="MIA")]

    result = run(session, plays(tmp_path, rows), tmp_path)

    assert result.status == "partial"
    assert any("2026_06_BUF_MIA: not in games; load schedules first" in r for r in result.rejected)


def test_play_by_play_for_a_game_without_a_final_is_rejected(session, tmp_path):
    load_game(session, tmp_path, week=7, away_score=None, home_score=None)

    path = plays(tmp_path, [td_pass(1, game_id="2026_07_KC_LV", week=7)],
                 meta=sidecar("play_by_play", 2026, as_of_week=7, target_week=8))

    result = run(session, path, tmp_path)

    assert result.status == "rejected" and "no final score" in result.reason


def test_final_game_missing_from_play_by_play_is_flagged(session, tmp_path):
    run(session, schedules(tmp_path, [game(away_score=7, home_score=0),
                                      game("BUF", "MIA", away_score=3, home_score=0)]), tmp_path)

    result = run(session, plays(tmp_path, [td_pass(1), xp(2)]), tmp_path)

    assert any("no play-by-play: 2026_06_BUF_MIA" in f for f in result.flags)


# --- Sidecars --------------------------------------------------------------

def test_sidecars_are_written_from_download_info_and_never_overwritten(tmp_path):
    (tmp_path / "DOWNLOAD_INFO.txt").write_text(
        "downloaded: 2026-10-23 00:29:50.514265\nnflreadr: 1.5.1\n"
    )
    schedules(tmp_path, [
        game(week=5), game(week=6), game("BUF", "MIA", week=6),
        game(week=7), game("BUF", "MIA", week=7, away_score=None, home_score=None),
    ], meta=False)
    plays(tmp_path, [td_pass(1, game_id="2026_05_KC_LV", week=5), td_pass(1, week=6)], meta=False)

    write_sidecars(tmp_path)

    meta = json.loads((tmp_path / "schedules" / "2026" / "schedules.meta.json").read_text())
    assert meta == {
        "source_name": "nflverse schedules (nflreadr 1.5.1)",
        "source_url": "nflreadr::load_schedules(2026)",
        "captured_at": "2026-10-23T00:29:50-04:00",
        "as_of_week": 6,  # week 7 has one final but isn't over
        "target_week": 7,
    }
    pbp = json.loads((tmp_path / "play_by_play" / "2026" / "play_by_play.meta.json").read_text())
    # Week 6 had two games and play-by-play has only one of them.
    assert (pbp["as_of_week"], pbp["source_url"]) == (5, "nflreadr::load_pbp(2026)")

    messages = write_sidecars(tmp_path)
    assert all("kept existing" in m for m in messages)


def test_a_later_download_line_sets_that_datasets_time(tmp_path):
    (tmp_path / "DOWNLOAD_INFO.txt").write_text(
        "downloaded: 2026-10-23 00:29:50\nnflreadr: 1.5.1\n"
        "play_by_play 2026 downloaded 2026-11-05T10:20:06-0500 with nflreadr 1.5.1\n"
    )
    schedules(tmp_path, [game()], meta=False)
    plays(tmp_path, [td_pass(1)], meta=False)

    write_sidecars(tmp_path)

    pbp = json.loads((tmp_path / "play_by_play" / "2026" / "play_by_play.meta.json").read_text())
    assert pbp["captured_at"] == "2026-11-05T10:20:06-05:00"


def test_sidecar_needs_the_seasons_schedule_to_tell_the_weeks(tmp_path):
    (tmp_path / "DOWNLOAD_INFO.txt").write_text("downloaded: 2026-10-23 00:29:50\nnflreadr: 1.5.1\n")
    plays(tmp_path, [td_pass(1)], meta=False)

    messages = write_sidecars(tmp_path)

    assert any("no sidecar written" in m and "schedules.csv.gz is missing" in m for m in messages)
    assert not (tmp_path / "play_by_play" / "2026" / "play_by_play.meta.json").exists()
