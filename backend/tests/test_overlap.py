import csv
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.features import feature_source, feature_sources
from app.models import Base, Team, TeamDefenseStats, TeamOffenseStats
from app.overlap import correlation, run
from app.seed import seed_teams

SEASON, WEEK = 2026, 5


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed_teams(session)
        yield session


def add_rows(session, model, source, values_by_stat, teams=None, as_of_week=WEEK):
    """values_by_stat: {stat: f(i)} giving team i's value."""
    team_ids = teams or sorted(session.scalars(select(Team.id)))
    for i, team_id in enumerate(team_ids):
        session.add(model(team_id=team_id, season=SEASON, as_of_week=as_of_week, source=source,
                          plays=300, **{stat: f(i) for stat, f in values_by_stat.items()}))
    session.commit()


def both_sources(session, **stats):
    """stats: {stat: (screenshot f, nflverse f)} on the offense table."""
    add_rows(session, TeamOffenseStats, "screenshot", {s: fs[0] for s, fs in stats.items()})
    add_rows(session, TeamOffenseStats, "nflverse", {s: fs[1] for s, fs in stats.items()})


def stat(out, key):
    return out["stats"][key]


def test_correlation():
    assert correlation([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)
    assert correlation([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    assert correlation([1, 1, 1], [1, 2, 3]) is None


def test_matching_stat_reads_nflverse_even_with_a_level_gap(session, tmp_path):
    # Same ranking of teams, nflverse a steady 0.02 higher: definitions agree, scale differs.
    both_sources(session, epa_per_play=(lambda i: i / 100, lambda i: i / 100 + 0.02))

    out = run(session, SEASON, WEEK, log_dir=tmp_path)

    epa = stat(out, "offense.epa_per_play")
    assert (epa["status"], epa["source"], epa["teams"]) == ("pass", "nflverse", 32)
    assert epa["correlation"] == pytest.approx(1.0)
    assert epa["mean_gap"] == pytest.approx(0.02)


def test_stat_at_or_below_the_threshold_stays_on_the_screenshot(session, tmp_path):
    # nflverse ranks teams quite differently from the screenshot.
    both_sources(session,
                 epa_per_play=(lambda i: i / 100, lambda i: i / 100),
                 explosive_play_rate=(lambda i: i / 100, lambda i: ((i * 7) % 32) / 100))

    out = run(session, SEASON, WEEK, log_dir=tmp_path)

    explosive = stat(out, "offense.explosive_play_rate")
    assert (explosive["status"], explosive["source"]) == ("fail", "screenshot")
    assert explosive["correlation"] <= 0.9


def test_stats_with_one_source_go_to_that_source_and_say_why(session, tmp_path):
    add_rows(session, TeamOffenseStats, "screenshot",
             {"epa_per_play": lambda i: i / 100, "motion_rate": lambda i: 0.5, "pers_11": lambda i: 0.6})
    add_rows(session, TeamOffenseStats, "nflverse",
             {"epa_per_play": lambda i: i / 100, "success_rate": lambda i: 0.44})

    out = run(session, SEASON, WEEK, log_dir=tmp_path)

    assert stat(out, "offense.motion_rate")["reason"] == "not built from nflverse"
    assert "participation" in stat(out, "offense.pers_11")["reason"]
    assert stat(out, "offense.pers_11")["source"] == "screenshot"
    assert (stat(out, "offense.success_rate")["status"], stat(out, "offense.success_rate")["source"]) == (
        "nflverse_only", "nflverse")
    assert stat(out, "offense.gear_change")["status"] == "no_data"


def test_too_few_paired_teams_is_untested(session, tmp_path):
    teams = sorted(session.scalars(select(Team.id)))
    add_rows(session, TeamOffenseStats, "screenshot", {"epa_per_play": lambda i: i / 100})
    add_rows(session, TeamOffenseStats, "nflverse", {"epa_per_play": lambda i: i / 100})
    add_rows(session, TeamDefenseStats, "screenshot", {"def_proe": lambda i: i / 100}, teams=teams[:10])
    add_rows(session, TeamDefenseStats, "nflverse", {"def_proe": lambda i: i / 100}, teams=teams[:10])

    out = run(session, SEASON, WEEK, log_dir=tmp_path)

    proe = stat(out, "defense.def_proe")
    assert (proe["status"], proe["source"], proe["teams"]) == ("untested", "screenshot", 10)


def test_only_the_requested_week_is_compared(session, tmp_path):
    add_rows(session, TeamOffenseStats, "screenshot", {"epa_per_play": lambda i: i / 100})
    add_rows(session, TeamOffenseStats, "nflverse", {"epa_per_play": lambda i: i / 100})
    # A different week where nflverse disagrees completely must not affect week 5.
    add_rows(session, TeamOffenseStats, "nflverse", {"epa_per_play": lambda i: -i / 100}, as_of_week=4)

    out = run(session, SEASON, WEEK, log_dir=tmp_path)

    assert stat(out, "offense.epa_per_play")["correlation"] == pytest.approx(1.0)


def test_nothing_to_compare_is_an_error_not_an_empty_config(session, tmp_path):
    add_rows(session, TeamOffenseStats, "screenshot", {"epa_per_play": lambda i: i / 100})

    with pytest.raises(RuntimeError, match="load the screenshots and build nflverse first"):
        run(session, SEASON, WEEK, write_to=tmp_path / "feature_sources.json", log_dir=tmp_path)
    assert not (tmp_path / "feature_sources.json").exists()


def test_charted_plays_checksum(session, tmp_path):
    both_sources(session, epa_per_play=(lambda i: i / 100, lambda i: i / 100))
    personnel = tmp_path / "personnel_usage.csv"
    with personnel.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["team", "pers_11", "plays"])
        writer.writerows([["KC", "60%", "1,200"], ["BUF", "55%", "1200"]])

    out = run(session, SEASON, WEEK, personnel_csv=personnel, log_dir=tmp_path)

    assert out["charted_plays_checksum"] == {"charted_plays": 2400, "nflverse_plays": 9600, "ratio": 4.0}


def test_written_config_is_what_models_read(session, tmp_path):
    both_sources(session,
                 epa_per_play=(lambda i: i / 100, lambda i: i / 100),
                 explosive_play_rate=(lambda i: i / 100, lambda i: ((i * 7) % 32) / 100))
    path = tmp_path / "config" / "feature_sources.json"

    run(session, SEASON, WEEK, write_to=path, log_dir=tmp_path)

    sources = feature_sources(path)
    assert feature_source("offense", "epa_per_play", sources) == "nflverse"
    assert feature_source("offense", "explosive_play_rate", sources) == "screenshot"
    with pytest.raises(KeyError):
        feature_source("offense", "gear_change", sources)  # neither source has it
    saved = json.loads(path.read_text())
    assert (saved["season"], saved["as_of_week"], saved["threshold"]) == (2026, 5, 0.9)
    log = json.loads((tmp_path / "overlap.jsonl").read_text().splitlines()[-1])
    assert log["event"] == "run_end" and "offense.explosive_play_rate" in log["failed"]


def test_missing_config_says_how_to_make_it(tmp_path):
    with pytest.raises(FileNotFoundError, match="app.overlap --write"):
        feature_sources(tmp_path / "nope.json")
