import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import game_model
from app.game_model import (
    DEFENSE_STATS,
    HOME_FIELD,
    OFFENSE_STATS,
    PRIOR_GAMES,
    PRIOR_REGRESSION,
    QB_OUT_POINTS,
    GameContext,
    predict_game,
    predict_week,
    set_qb_out,
    team_ratings,
)
from app.models import Base, Game, GameLine, ModelPrediction, Team, TeamDefenseStats, TeamOffenseStats
from app.seed import seed_teams

SEASON, WEEK = 2026, 7
NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
KICKOFF = datetime(2026, 10, 25, 17, tzinfo=UTC)
SOURCES = {f"offense.{s}": {"source": "nflverse"} for s in OFFENSE_STATS} | {
    f"defense.{s}": {"source": "nflverse"} for s in DEFENSE_STATS}

LEAGUE = {"points_per_drive": 2.0, "points_per_drive_allowed": 2.0, "epa_per_play": 0.0,
          "epa_per_play_allowed": 0.0, "rz_td_rate": 0.55, "rz_td_rate_allowed": 0.55,
          "drives_per_game": 11.0, "plays_per_drive": 6.0}


def team(**overrides):
    return {**LEAGUE, **overrides}


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed_teams(session)
        yield session


def team_ids(session):
    return sorted(session.scalars(select(Team.id)))


def add_stats(session, season, as_of_week, offense=None, defense=None, games=None, teams=None):
    """League-average rows for every team, with per-team overrides: {team_id: {stat: value}}."""
    for t in teams or team_ids(session):
        o = {s: LEAGUE[s] for s in OFFENSE_STATS} | (offense or {}).get(t, {})
        d = {s: LEAGUE[s] for s in DEFENSE_STATS} | (defense or {}).get(t, {})
        gp = games if games is not None else as_of_week
        session.add(TeamOffenseStats(team_id=t, season=season, as_of_week=as_of_week, source="nflverse",
                                     games_played=gp, **o))
        session.add(TeamDefenseStats(team_id=t, season=season, as_of_week=as_of_week, source="nflverse",
                                     games_played=gp, **d))
    session.commit()


def add_game(session, home, away, week=WEEK, kickoff=KICKOFF, **fields):
    game = Game(season=SEASON, week=week, home_team_id=home, away_team_id=away, kickoff_time=kickoff, **fields)
    session.add(game)
    session.commit()
    return game


def add_line(session, game, spread=-3.0, total=44.5, captured=NOW - timedelta(hours=1), line_type="current"):
    session.add(GameLine(game_id=game.id, line_type=line_type, spread_home=spread, total=total,
                         moneyline_home=-150, moneyline_away=130, captured_at=captured, source_name="test"))
    session.commit()


def run(session, tmp_path, week=WEEK, now=NOW):
    return predict_week(session, SEASON, week, now=now, n=4000, sources=SOURCES, log_dir=tmp_path)


def rows(session, game):
    return {p.bet_type: p for p in session.scalars(select(ModelPrediction).where(ModelPrediction.game_id == game.id))}


# predict_game: the expected-points math and game context


def test_league_average_teams_split_the_total_and_home_field_is_the_margin():
    pred = predict_game(team(), team(), LEAGUE, GameContext(), n=4000, seed=1)
    assert pred.total == pytest.approx(2 * 2.0 * 11.0)
    assert pred.margin == pytest.approx(HOME_FIELD)
    assert pred.home_win_prob > 0.5

    neutral = predict_game(team(), team(), LEAGUE, GameContext(neutral_site=True), n=4000, seed=1)
    assert neutral.margin == pytest.approx(0)


def test_a_better_offense_and_defense_win_more():
    good = team(points_per_drive=2.6, epa_per_play=0.1, points_per_drive_allowed=1.6, epa_per_play_allowed=-0.08)
    pred = predict_game(good, team(), LEAGUE, GameContext(neutral_site=True), n=4000, seed=1)
    assert pred.home_points > pred.away_points
    # ppd 0.4 x 2.6 + 0.6 x (2.0 + 0.1 x 6) = 2.6 points per drive over 11 drives.
    assert pred.breakdown["home"]["ppd"] == pytest.approx(2.6)
    assert pred.home_win_prob > 0.75


def test_qb_out_rest_and_wind():
    base = predict_game(team(), team(), LEAGUE, GameContext(neutral_site=True), n=4000, seed=1)
    qb = predict_game(team(), team(), LEAGUE, GameContext(neutral_site=True, home_qb_out=True), n=4000, seed=1)
    assert qb.margin == pytest.approx(base.margin - QB_OUT_POINTS)

    rested = predict_game(team(), team(), LEAGUE,
                          GameContext(neutral_site=True, home_rest_days=13, away_rest_days=6), n=4000, seed=1)
    # 7 days of extra rest is capped at 4.
    assert rested.margin == pytest.approx(4 * game_model.REST_POINTS_PER_DAY)
    assert rested.total == pytest.approx(base.total)

    windy = predict_game(team(), team(), LEAGUE, GameContext(neutral_site=True, roof="outdoors", wind=20),
                         n=4000, seed=1)
    dome = predict_game(team(), team(), LEAGUE, GameContext(neutral_site=True, roof="dome", wind=20),
                        n=4000, seed=1)
    assert windy.total == pytest.approx(base.total - game_model.WIND_TOTAL_POINTS)
    assert dome.total == pytest.approx(base.total)


# team_ratings: what it reads


def test_ratings_ignore_the_predicted_week_and_later(session):
    t = team_ids(session)[0]
    add_stats(session, SEASON, WEEK - 1, offense={t: {"points_per_drive": 2.5}})
    # A row built through the week being predicted must never be read.
    add_stats(session, SEASON, WEEK, offense={t: {"points_per_drive": 9.9}})

    ratings = team_ratings(session, SEASON, WEEK, SOURCES)

    assert ratings.as_of_week == WEEK - 1
    assert ratings.teams[t]["points_per_drive"] < 2.5


def test_early_season_blends_with_last_season_regressed(session):
    t = team_ids(session)[0]
    add_stats(session, SEASON - 1, 18, offense={t: {"points_per_drive": 3.0}})
    add_stats(session, SEASON, 2, offense={t: {"points_per_drive": 2.4}}, games=2)

    ratings = team_ratings(session, SEASON, 3, SOURCES)

    others = (2.0 * 31 + 2.4) / 32
    prior_mean = (2.0 * 31 + 3.0) / 32
    regressed = others + (1 - PRIOR_REGRESSION) * (3.0 - prior_mean)
    w = 2 / (2 + PRIOR_GAMES)
    assert ratings.teams[t]["points_per_drive"] == pytest.approx(w * 2.4 + (1 - w) * regressed)


def test_week_one_reads_only_last_season(session):
    t = team_ids(session)[0]
    add_stats(session, SEASON - 1, 18, offense={t: {"points_per_drive": 3.0}})

    ratings = team_ratings(session, SEASON, 1, SOURCES)

    assert ratings.as_of_week == 0
    prior_mean = (2.0 * 31 + 3.0) / 32
    assert ratings.teams[t]["points_per_drive"] == pytest.approx(prior_mean + 0.5 * (3.0 - prior_mean))


def test_each_stat_reads_the_source_the_overlap_test_chose(session):
    a, b = team_ids(session)[:2]
    # nflverse says a is the better offense; the screenshot says b.
    add_stats(session, SEASON, WEEK - 1, offense={a: {"points_per_drive": 3.0}, b: {"points_per_drive": 1.0}})
    for t, ppd in ((a, 1.0), (b, 3.0)):
        session.add(TeamOffenseStats(team_id=t, season=SEASON, as_of_week=WEEK - 1, source="screenshot",
                                     points_per_drive=ppd))
    session.commit()

    by_nflverse = team_ratings(session, SEASON, WEEK, SOURCES)
    by_screenshot = team_ratings(session, SEASON, WEEK,
                                 SOURCES | {"offense.points_per_drive": {"source": "screenshot"}})

    assert by_nflverse.teams[a]["points_per_drive"] > by_nflverse.teams[b]["points_per_drive"]
    assert by_screenshot.teams[a]["points_per_drive"] < by_screenshot.teams[b]["points_per_drive"]
    # EPA still reads nflverse either way.
    assert by_screenshot.teams[a]["epa_per_play"] == by_nflverse.teams[a]["epa_per_play"]


def test_no_stats_at_all_is_an_error(session):
    with pytest.raises(RuntimeError, match="nflverse_build"):
        team_ratings(session, SEASON, WEEK, SOURCES)


# predict_week: locked rows


def test_writes_three_locked_rows_per_game(session, tmp_path):
    a, b, c, d = team_ids(session)[:4]
    add_stats(session, SEASON, WEEK - 1, offense={a: {"points_per_drive": 2.6}})
    priced = add_game(session, a, b)
    add_line(session, priced, spread=-3.0, total=44.5)
    unpriced = add_game(session, c, d)

    out = run(session, tmp_path)

    assert out["predicted"] == 2 and out["snapshot_as_of_week"] == WEEK - 1
    p = rows(session, priced)
    assert set(p) == {"spread", "moneyline", "total"}
    assert p["spread"].market_line == -3.0 and p["total"].market_line == 44.5
    assert p["spread"].predicted_value == pytest.approx(-p["moneyline"].predicted_value)
    assert 0 < p["spread"].predicted_prob < 1 and 0 < p["total"].predicted_prob < 1
    assert p["moneyline"].predicted_prob > 0.5
    assert all(r.snapshot_as_of_week == WEEK - 1 and r.model_version == "game-v0" for r in p.values())
    u = rows(session, unpriced)
    assert u["spread"].predicted_prob is None and u["spread"].market_line is None
    assert u["moneyline"].predicted_prob is not None

    log = [json.loads(line) for line in (tmp_path / "game_model.jsonl").read_text().splitlines()]
    assert [e["event"] for e in log].count("game") == 2 and log[-1]["event"] == "run_end"


def test_locked_rows_are_never_rewritten(session, tmp_path):
    a, b = team_ids(session)[:2]
    add_stats(session, SEASON, WEEK - 1)
    game = add_game(session, a, b)
    run(session, tmp_path)
    first = {k: (r.predicted_value, r.locked_at) for k, r in rows(session, game).items()}

    session.query(TeamOffenseStats).filter_by(team_id=a).update({"points_per_drive": 3.5})
    session.commit()
    out = run(session, tmp_path, now=NOW + timedelta(hours=2))

    assert out["already_locked"] == 1 and out["predicted"] == 0
    assert {k: (r.predicted_value, r.locked_at) for k, r in rows(session, game).items()} == first


def test_games_that_started_or_finished_are_skipped(session, tmp_path):
    a, b, c, d = team_ids(session)[:4]
    add_stats(session, SEASON, WEEK - 1)
    started = add_game(session, a, b, kickoff=NOW - timedelta(minutes=5))
    final = add_game(session, c, d, status="final", home_score=24, away_score=17)

    out = run(session, tmp_path)

    assert out["started"] == 2 and out["predicted"] == 0
    assert not rows(session, started) and not rows(session, final)


def test_lines_captured_after_now_are_not_used(session, tmp_path):
    a, b = team_ids(session)[:2]
    add_stats(session, SEASON, WEEK - 1)
    game = add_game(session, a, b)
    add_line(session, game, spread=-3.0, captured=NOW - timedelta(days=1))
    add_line(session, game, spread=-7.0, captured=NOW + timedelta(hours=1))

    run(session, tmp_path)

    assert rows(session, game)["spread"].market_line == -3.0


def test_rest_days_come_from_each_teams_last_game(session, tmp_path):
    a, b, c = team_ids(session)[:3]
    add_game(session, a, c, week=WEEK - 1, kickoff=KICKOFF - timedelta(days=7))
    add_game(session, b, c, week=WEEK - 2, kickoff=KICKOFF - timedelta(days=14))
    game = add_game(session, a, b)

    assert game_model.rest_days(session, game, a) == 7
    assert game_model.rest_days(session, game, b) == 14


def test_qb_out_flag_moves_the_prediction_and_locks_with_it(session, tmp_path):
    a, b, c, d = team_ids(session)[:4]
    add_stats(session, SEASON, WEEK - 1)
    healthy = add_game(session, a, b)
    hurt = add_game(session, c, d)
    set_qb_out(session, SEASON, WEEK, c)

    run(session, tmp_path)

    margin = lambda g: rows(session, g)["moneyline"].predicted_value
    assert margin(hurt) == pytest.approx(margin(healthy) - QB_OUT_POINTS)
    with pytest.raises(RuntimeError, match="already predicted"):
        set_qb_out(session, SEASON, WEEK, c, out=False)


def test_a_week_with_no_games_is_an_error(session, tmp_path):
    add_stats(session, SEASON, WEEK - 1)
    with pytest.raises(RuntimeError, match="load the schedule"):
        run(session, tmp_path)
