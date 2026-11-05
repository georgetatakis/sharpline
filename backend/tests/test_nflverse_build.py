import json

import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.features import team_stats_before
from app.models import (
    Base,
    DataSnapshot,
    Game,
    GameTeamStats,
    Player,
    PlayerWeeklyStats,
    Team,
    TeamDefenseStats,
    TeamOffenseStats,
)
from app.nflverse_build import (
    PARTICIPATION_COLUMNS,
    PBP_COLUMNS,
    PLAYER_STATS_COLUMNS,
    ROSTER_COLUMNS,
    SNAP_COLUMNS,
    build_season,
    team_game_sums,
    team_week_stats,
)
from app.seed import seed_teams

SEASON = 2026


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed_teams(session)
        yield session


def team_id(session, abbreviation):
    return session.scalar(select(Team.id).where(Team.abbreviation == abbreviation))


# --- Fake play-by-play -----------------------------------------------------
# KC plays at LV in week 1, hosts DEN in week 2, and has a bye in week 3.

def snap(game_id, week, play_id, offense, defense, **fields):
    row = {c: None for c in PBP_COLUMNS}
    row.update({
        "game_id": game_id, "play_id": play_id, "season": SEASON, "week": week,
        "posteam": offense, "defteam": defense, "play_type": "run", "pass": 0, "rush": 1,
        "epa": 0.0, "success": 0, "yards_gained": 4, "shotgun": 0, "no_huddle": 0,
        "pass_attempt": 0, "sack": 0, "rush_attempt": 1, "rushing_yards": 4,
        "complete_pass": 0, "interception": 0, "fumble_lost": 0, "wp": 0.5,
        "half_seconds_remaining": 900, "two_point_attempt": 0, "fixed_drive": 1,
        "fixed_drive_result": "Punt", "drive_inside20": 0, "drive_time_of_possession": "2:30",
        "series": 1, "series_success": 0, "penalty": 0, "yardline_100": 60,
        "touchdown": 0, "safety": 0, "defensive_two_point_conv": 0,
    })
    row.update(fields)
    return row


def pass_play(game_id, week, play_id, offense, defense, receiver="00-WR", yards=10, **fields):
    return snap(game_id, week, play_id, offense, defense, play_type="pass", **{"pass": 1}, rush=0,
                rush_attempt=0, rushing_yards=None, pass_attempt=1, complete_pass=1,
                passing_yards=yards, yards_gained=yards, air_yards=8, receiver_player_id=receiver,
                **fields)


def week_one():
    g = "2026_01_KC_LV"
    return [
        # One KC drive: a run, then a red zone touchdown pass and the extra point.
        snap(g, 1, 1, "KC", "LV", epa=0.4, success=1, yards_gained=12, rushing_yards=12,
             series_success=1, rusher_player_id="00-RB", fixed_drive_result="Touchdown",
             drive_inside20=1),
        pass_play(g, 1, 2, "KC", "LV", epa=1.0, success=1, yards=25, shotgun=1, fixed_drive=1,
                  series=2, series_success=1, yardline_100=15, drive_inside20=1,
                  fixed_drive_result="Touchdown", touchdown=1, pass_touchdown=1, td_team="KC",
                  td_player_id="00-WR"),
        snap(g, 1, 3, "KC", "LV", play_type="extra_point", rush=0, rush_attempt=0, fixed_drive=1,
             extra_point_result="good", series=None, fixed_drive_result="Touchdown",
             drive_inside20=1),
        snap(g, 1, 4, "LV", "KC", epa=-0.6, yards_gained=-2, rushing_yards=-2, fixed_drive=2,
             series=3),
    ]


def week_two(kc_epa=0.0):
    g = "2026_02_DEN_KC"
    return [
        snap(g, 2, 1, "KC", "DEN", epa=kc_epa, yards_gained=3, rushing_yards=3, fixed_drive=1),
        snap(g, 2, 2, "DEN", "KC", epa=0.2, success=1, yards_gained=6, rushing_yards=6,
             fixed_drive=2, series=2),
    ]


def participation_rows():
    return [
        {"nflverse_game_id": "2026_01_KC_LV", "play_id": 1, "offense_personnel": "1 RB, 1 TE, 3 WR",
         "defense_personnel": "4 DL, 2 LB, 5 DB", "defenders_in_box": 6},
        {"nflverse_game_id": "2026_01_KC_LV", "play_id": 2, "offense_personnel": "1 RB, 2 TE, 2 WR",
         "defense_personnel": "4 DL, 3 LB, 4 DB", "defenders_in_box": 7,
         "number_of_pass_rushers": 5, "was_pressure": True, "time_to_throw": 2.5,
         "defense_man_zone_type": "MAN_COVERAGE", "defense_coverage_type": "COVER_1"},
        {"nflverse_game_id": "2026_01_KC_LV", "play_id": 4,
         "offense_personnel": "1 C, 2 G, 1 QB, 2 RB, 2 T, 1 TE, 2 WR",
         "defense_personnel": "2 CB, 2 DE, 2 DT, 1 FS, 2 ILB, 1 OLB, 1 SS", "defenders_in_box": 8},
    ]


def frame(rows, columns):
    df = pd.DataFrame(rows)
    for c in columns:
        if c not in df:
            df[c] = None
    return df[columns]


def sums(rows, participation=None, positions=None):
    pbp = frame(rows, PBP_COLUMNS)
    part = None if participation is None else frame(participation, PARTICIPATION_COLUMNS)
    return team_game_sums(pbp, part, positions or {"00-WR": "WR"})


def by_team(rows, team, week):
    return rows[(rows.team == team) & (rows.as_of_week == week)].iloc[0]


# --- Aggregation -----------------------------------------------------------

def test_offense_rates_come_from_season_sums():
    offense, defense = team_week_stats(*sums(week_one() + week_two()), 2)

    kc1 = by_team(offense, "KC", 1)
    assert (kc1.games_played, kc1.plays) == (1, 2)
    assert kc1.epa_per_play == pytest.approx(0.7)
    assert kc1.yards_per_play == pytest.approx(18.5)
    assert kc1.explosive_play_rate == pytest.approx(1.0)  # a 12-yard run and a 25-yard pass
    assert kc1.points_per_drive == pytest.approx(7.0)
    assert kc1.rz_td_rate == pytest.approx(1.0)
    assert kc1.shotgun_rate == pytest.approx(0.5)
    assert kc1.air_yards_per_att == pytest.approx(8.0)
    # Two weeks: three plays summed, not two weekly averages averaged.
    kc2 = by_team(offense, "KC", 2)
    assert kc2.epa_per_play == pytest.approx(1.4 / 3)
    assert kc2.plays_per_game == pytest.approx(1.5)


def test_defense_is_measured_on_the_opponents_plays():
    _, defense = team_week_stats(*sums(week_one()), 1)

    lv = by_team(defense, "LV", 1)
    assert lv.epa_per_play_allowed == pytest.approx(0.7)
    assert lv.points_per_drive_allowed == pytest.approx(7.0)
    kc = by_team(defense, "KC", 1)
    assert kc.epa_per_play_allowed == pytest.approx(-0.6)
    assert kc.dl_rush_stuff_rate == pytest.approx(1.0)


def test_participation_fills_personnel_pressure_and_coverage():
    offense, defense = team_week_stats(*sums(week_one(), participation_rows()), 1)

    kc = by_team(offense, "KC", 1)
    assert (kc.pers_11, kc.pers_12, kc.pers_2te) == (0.5, 0.5, 0.5)
    assert kc.ol_pressure_rate_allowed == pytest.approx(1.0)
    assert kc.time_to_throw == pytest.approx(2.5)
    lv = by_team(defense, "LV", 1)
    assert lv.blitz_rate == 1.0 and lv.man_rate == 1.0 and lv.middle_closed_rate == 1.0
    assert lv.sub_package_rate == pytest.approx(0.5)
    assert lv.light_box_rate == pytest.approx(0.5)
    # The newer per-position personnel lists count backs and DBs the same way.
    assert by_team(offense, "LV", 1).pers_21 == 1.0
    assert by_team(defense, "KC", 1).heavy_box_rate == 1.0
    assert by_team(defense, "KC", 1).sub_package_rate == 0.0


def test_without_participation_those_stats_are_empty_not_zero():
    offense, defense = team_week_stats(*sums(week_one()), 1)

    assert pd.isna(by_team(offense, "KC", 1).pers_11)
    assert pd.isna(by_team(defense, "LV", 1).blitz_rate)


def test_yards_per_target_by_receiver_position():
    rows = week_one() + [pass_play("2026_01_KC_LV", 1, 5, "KC", "LV", receiver="00-TE", yards=6)]

    _, defense = team_week_stats(*sums(rows, positions={"00-WR": "WR", "00-TE": "TE"}), 1)

    lv = by_team(defense, "LV", 1)
    assert (lv.ypt_allowed_wr, lv.ypt_allowed_te) == (25.0, 6.0)
    assert pd.isna(lv.ypt_allowed_rb)


def test_bye_week_carries_the_row_forward():
    rows = week_one() + week_two()
    offense, _ = team_week_stats(*sums(rows), 3)

    week_three = by_team(offense, "KC", 3).drop("as_of_week")
    pd.testing.assert_series_equal(week_three, by_team(offense, "KC", 2).drop("as_of_week"), check_names=False)


# --- Leakage ---------------------------------------------------------------

def test_a_week_n_row_never_reads_week_n_plus_one():
    before, _ = team_week_stats(*sums(week_one() + week_two(kc_epa=0.0)), 2)
    after, _ = team_week_stats(*sums(week_one() + week_two(kc_epa=-3.0)), 2)

    # Week 2 changed; the week 1 row is identical either way.
    pd.testing.assert_series_equal(by_team(before, "KC", 1), by_team(after, "KC", 1))
    assert by_team(before, "KC", 2).epa_per_play != by_team(after, "KC", 2).epa_per_play


# --- Database build --------------------------------------------------------

def write(root, dataset, rows, columns, as_of_week=2):
    folder = root / dataset / str(SEASON)
    folder.mkdir(parents=True, exist_ok=True)
    frame(rows, columns).to_csv(folder / f"{dataset}.csv.gz", index=False)
    (folder / f"{dataset}.meta.json").write_text(json.dumps({
        "source_name": f"nflverse {dataset} (nflreadr 1.5.1)", "source_url": f"nflreadr::x({SEASON})",
        "captured_at": "2026-10-23T00:29:50-04:00", "as_of_week": as_of_week,
        "target_week": as_of_week + 1,
    }))


def add_games(session):
    for nflverse_id, week, away, home in (("2026_01_KC_LV", 1, "KC", "LV"),
                                          ("2026_02_DEN_KC", 2, "DEN", "KC"),
                                          ("2026_03_LV_DEN", 3, "LV", "DEN")):
        session.add(Game(season=SEASON, week=week, nflverse_game_id=nflverse_id,
                         away_team_id=team_id(session, away), home_team_id=team_id(session, home),
                         status="final", away_score=7, home_score=0))
    session.commit()


def player_line(week, game_id, **fields):
    row = {"player_id": "00-WR", "player_display_name": "Wide Out", "position": "WR",
           "position_group": "WR", "week": week, "game_id": game_id, "team": "KC", "targets": 1,
           "target_share": 0.5, "carries": 0, "receiving_air_yards": 8, "receiving_yards": 25,
           "rushing_yards": 0, "receptions": 1, "rushing_tds": 0, "receiving_tds": 1,
           "special_teams_tds": 0}
    row.update(fields)
    return row


@pytest.fixture
def files(tmp_path):
    week_three = [snap("2026_03_LV_DEN", 3, 1, "LV", "DEN", epa=2.0)]
    write(tmp_path, "play_by_play", week_one() + week_two() + week_three, PBP_COLUMNS)
    write(tmp_path, "pbp_participation", participation_rows(), PARTICIPATION_COLUMNS)
    write(tmp_path, "rosters", [{"gsis_id": "00-WR", "pfr_id": "OutW00", "full_name": "Wide Out",
                                 "position": "WR", "team": "KC"}], ROSTER_COLUMNS)
    write(tmp_path, "player_stats", [player_line(1, "2026_01_KC_LV"),
                                     player_line(2, "2026_02_DEN_KC", receiving_tds=0)],
          PLAYER_STATS_COLUMNS)
    write(tmp_path, "snap_counts", [{"game_id": "2026_01_KC_LV", "pfr_player_id": "OutW00",
                                     "player": "Wide Out", "team": "KC", "week": 1,
                                     "offense_snaps": 50, "offense_pct": 0.8}], SNAP_COLUMNS)
    return tmp_path


def build(session, root):
    return build_season(session, root, SEASON, log_dir=root / "logs")


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_build_writes_nflverse_rows_box_scores_and_player_lines(session, files):
    add_games(session)

    team, box, player = build(session, files)

    assert team.status == "loaded" and team.counts["weeks"] == 2
    rows = session.scalars(select(TeamOffenseStats).where(TeamOffenseStats.team_id == team_id(session, "KC"))).all()
    assert [(r.as_of_week, r.source) for r in sorted(rows, key=lambda r: r.as_of_week)] == [(1, "nflverse"), (2, "nflverse")]
    assert any("left out 1 game(s) after as_of_week 2" in f for f in team.flags)

    kc_box = session.scalars(select(GameTeamStats).where(GameTeamStats.team_id == team_id(session, "KC"))).all()
    week_one_box = next(b for b in kc_box if b.plays == 2)
    assert (week_one_box.rush_attempts, week_one_box.pass_attempts, week_one_box.total_yards) == (1, 1, 37)
    assert week_one_box.blitzes_faced == 1 and week_one_box.avg_pocket_time == pytest.approx(2.5)

    assert player.rows_loaded == 2
    wr = session.scalars(select(PlayerWeeklyStats).order_by(PlayerWeeklyStats.week)).all()
    assert (wr[0].snaps, wr[0].snap_share, wr[0].tds, wr[0].red_zone_targets) == (50, 0.8, 1, 1)
    assert wr[1].snaps is None  # no snap count for week 2
    assert session.scalar(select(Player.nflverse_id)) == "00-WR"


def test_rebuild_replaces_nflverse_rows_and_leaves_screenshot_rows(session, files):
    add_games(session)
    session.add(TeamOffenseStats(team_id=team_id(session, "KC"), season=SEASON, as_of_week=1,
                                 source="screenshot", epa_per_play=0.25))
    session.commit()

    build(session, files)
    build(session, files)

    # KC and LV after week 1, KC, LV and DEN after week 2, plus the screenshot row.
    assert count(session, TeamOffenseStats) == 2 + 3 + 1
    screenshot = session.scalar(select(TeamOffenseStats).where(TeamOffenseStats.source == "screenshot"))
    assert screenshot.epa_per_play == 0.25
    assert count(session, PlayerWeeklyStats) == 2 and count(session, Player) == 1


def test_build_writes_snapshots_and_a_log(session, files):
    add_games(session)

    build(session, files)

    keys = set(session.scalars(select(DataSnapshot.table_key)))
    assert keys == {"nflverse_team_week_stats", "nflverse_game_team_stats", "nflverse_player_weekly_stats"}
    end = json.loads((files / "logs" / "import.jsonl").read_text().splitlines()[-1])
    assert end["event"] == "run_end" and len(end["outputs"]) == 3


def test_missing_participation_leaves_those_stats_empty_and_says_so(session, files):
    add_games(session)
    (files / "pbp_participation" / str(SEASON) / "pbp_participation.meta.json").unlink()

    team, _, _ = build(session, files)

    assert any(f.startswith("not used: no sidecar") for f in team.flags)
    lv = session.scalar(select(TeamDefenseStats).where(TeamDefenseStats.team_id == team_id(session, "LV")))
    assert lv.blitz_rate is None and lv.epa_per_play_allowed is not None


def test_play_by_play_without_sidecar_is_refused(session, files):
    add_games(session)
    (files / "play_by_play" / str(SEASON) / "play_by_play.meta.json").unlink()

    team, box, player = build(session, files)

    assert team.status == box.status == player.status == "rejected"
    assert "no sidecar" in team.reason
    assert count(session, TeamOffenseStats) == 0


def test_build_needs_games_loaded_first(session, files):
    team, _, _ = build(session, files)

    assert team.status == "rejected" and "run app.nflverse_loader first" in team.reason


# --- Reading for a prediction ----------------------------------------------

def test_a_model_reads_only_rows_strictly_before_the_week_it_predicts(session, files):
    add_games(session)
    build(session, files)

    for target_week, expected in ((1, set()), (2, {1}), (3, {2}), (9, {2})):
        rows = team_stats_before(session, TeamOffenseStats, SEASON, target_week, "nflverse")
        assert {r.as_of_week for r in rows} == expected
        assert all(r.as_of_week < target_week for r in rows)


def test_reading_one_source_never_returns_the_other(session, files):
    add_games(session)
    build(session, files)
    session.add(TeamOffenseStats(team_id=team_id(session, "KC"), season=SEASON, as_of_week=2,
                                 source="screenshot", epa_per_play=0.25))
    session.commit()

    screenshot = team_stats_before(session, TeamOffenseStats, SEASON, 3, "screenshot")
    nflverse = team_stats_before(session, TeamOffenseStats, SEASON, 3, "nflverse")

    assert [r.epa_per_play for r in screenshot] == [0.25]
    assert len(nflverse) == 3 and all(r.source == "nflverse" for r in nflverse)  # KC, LV, DEN


def test_snap_count_without_a_roster_id_matches_on_name_team_and_game(session, files):
    add_games(session)
    write(files, "snap_counts", [
        {"game_id": "2026_02_DEN_KC", "pfr_player_id": "NotOnRoster", "player": "Wide Out",
         "team": "KC", "week": 2, "offense_snaps": 40, "offense_pct": 0.7},
    ], SNAP_COLUMNS)

    build(session, files)

    week_two = session.scalar(select(PlayerWeeklyStats).where(PlayerWeeklyStats.week == 2))
    assert (week_two.snaps, week_two.snap_share) == (40, 0.7)
