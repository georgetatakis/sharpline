"""Build team-week stats, game box scores and player weekly lines from nflverse files.

For one season, reads files George downloaded (each needs its sidecar, like any import):

  play_by_play       required; every team stat starts from it
  pbp_participation  personnel, box counts, pass rushers, pressure, time to throw, coverage
  rosters            players, and receiver positions for yards per target by position
  player_stats       each player's weekly line
  snap_counts        each player's offensive snaps

and writes:

  team_offense_stats / team_defense_stats with source='nflverse': one row per team per
    as_of_week, season to date. The row for as_of_week w uses games of weeks 1..w only,
    so a model predicting week w+1 can read it and nothing later.
  game_team_stats: each team's box score for each game.
  players and player_weekly_stats.

Run with:  python -m app.nflverse_build data/extracted/nflverse [--season 2026]
Load games first with app.nflverse_loader; box scores and player lines attach to them.

A file whose sidecar is missing is refused. Weeks after a file's as_of_week are left
out, so a partly played week (a Thursday game) never enters a season-to-date row.
"""

import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.importer import ImportRejected, ImportResult, Sidecar, _snapshot, load_sidecar, log_summary
from app.models import (
    Game,
    GameTeamStats,
    Player,
    PlayerWeeklyStats,
    TeamDefenseStats,
    TeamOffenseStats,
)
from app.nflverse_loader import scoring_events, sidecar_path
from app.run_log import logged_run
from app.team_lookup import TeamLookup

SOURCE = "nflverse"
TEAM_TABLE_KEY = "nflverse_team_week_stats"
BOX_TABLE_KEY = "nflverse_game_team_stats"
PLAYER_TABLE_KEY = "nflverse_player_weekly_stats"

PBP_COLUMNS = [
    "game_id", "play_id", "season", "week", "posteam", "defteam", "play_type", "pass",
    "rush", "epa", "success", "yards_gained", "shotgun", "no_huddle", "pass_attempt",
    "sack", "air_yards", "passing_yards", "rushing_yards", "complete_pass", "rush_attempt",
    "interception", "fumble_lost", "wp", "half_seconds_remaining", "pass_oe",
    "two_point_attempt", "fixed_drive", "fixed_drive_result", "drive_inside20",
    "drive_time_of_possession", "series", "series_success", "penalty", "penalty_team",
    "penalty_yards", "yardline_100", "receiver_player_id", "rusher_player_id",
    # for points per drive, through app.nflverse_loader.scoring_events
    "touchdown", "pass_touchdown", "rush_touchdown", "td_team", "td_player_name",
    "td_player_id", "field_goal_result", "extra_point_result", "two_point_conv_result",
    "defensive_two_point_conv", "safety", "kicker_player_name", "kicker_player_id",
    "receiver_player_name", "rusher_player_name",
]
PARTICIPATION_COLUMNS = [
    "nflverse_game_id", "play_id", "offense_personnel", "defense_personnel",
    "defenders_in_box", "number_of_pass_rushers", "time_to_throw", "was_pressure",
    "defense_man_zone_type", "defense_coverage_type",
]
PLAYER_STATS_COLUMNS = [
    "player_id", "player_display_name", "position", "position_group", "week", "game_id",
    "team", "targets", "target_share", "carries", "receiving_air_yards", "receiving_yards",
    "rushing_yards", "receptions", "rushing_tds", "receiving_tds", "special_teams_tds",
]
ROSTER_COLUMNS = ["gsis_id", "pfr_id", "full_name", "position", "team"]
SNAP_COLUMNS = ["game_id", "pfr_player_id", "player", "team", "week", "offense_snaps", "offense_pct"]

# Columns that must be numbers, however the CSV left them (an all-blank column reads as text).
NUMERIC_PBP = [
    "play_id", "week", "pass", "rush", "epa", "success", "yards_gained", "shotgun", "no_huddle",
    "pass_attempt", "sack", "air_yards", "passing_yards", "rushing_yards", "complete_pass",
    "rush_attempt", "interception", "fumble_lost", "wp", "half_seconds_remaining", "pass_oe",
    "two_point_attempt", "fixed_drive", "drive_inside20", "series", "series_success", "penalty",
    "penalty_yards", "yardline_100", "touchdown", "pass_touchdown", "rush_touchdown",
    "defensive_two_point_conv", "safety",
]
NUMERIC_PARTICIPATION = ["play_id", "defenders_in_box", "number_of_pass_rushers", "time_to_throw"]


def _numeric(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    frame = frame.copy()
    for column in columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


# Player lines are kept for the positions the prop and TD models price.
PLAYER_POSITIONS = {"QB", "RB", "WR", "TE"}

# Single-high shells close the middle of the field; two-high shells open it.
# Cover 0, Cover 9, combo and prevent count toward neither.
MIDDLE_CLOSED = {"COVER_1", "COVER_3"}
MIDDLE_OPEN = {"COVER_2", "2_MAN", "COVER_4", "COVER_6"}

# Season-to-date sums. Each rate below is one sum over another, so a week's row can be
# built by adding weeks before dividing, never by averaging averages.
SUMS = [
    "games", "plays", "epa_sum", "epa_n", "success_sum", "yards_sum", "explosive",
    "shotgun", "no_huddle", "pass_att", "air_yards_sum", "neutral_n", "neutral_pass",
    "proe_sum", "proe_n", "rush_n", "stuffs", "drives", "drive_points", "rz_drives",
    "rz_td_drives", "top_seconds", "series_n", "series_success",
    "dropbacks", "pressure_n", "pressure", "rushers_n", "blitz", "noblitz_n",
    "noblitz_pressure", "ttt_sum", "ttt_n", "pers_n", "pers_11", "pers_12", "pers_13",
    "pers_21", "pers_22", "pers_2te", "pers_2rb", "pers_3wr", "box_n", "light_box",
    "heavy_box", "dpers_n", "sub_package", "mz_n", "man", "zone", "cov_n", "middle_closed",
    "middle_open", "tgt_wr", "yds_wr", "tgt_te", "yds_te", "tgt_rb", "yds_rb",
]

# column: (numerator, denominator, scale). Shared by offense and defense.
SHARED_RATES = {
    "epa_per_play": ("epa_sum", "epa_n", 1),
    "success_rate": ("success_sum", "epa_n", 1),
    "yards_per_play": ("yards_sum", "plays", 1),
    "points_per_drive": ("drive_points", "drives", 1),
    "explosive_play_rate": ("explosive", "plays", 1),
    "down_conversion_rate": ("series_success", "series_n", 1),
    "proe": ("proe_sum", "proe_n", 0.01),  # nflverse pass_oe is in percentage points
    "rz_td_rate": ("rz_td_drives", "rz_drives", 1),
    "pressure_rate": ("pressure", "pressure_n", 1),
    "no_blitz_pressure_rate": ("noblitz_pressure", "noblitz_n", 1),
    "rush_stuff_rate": ("stuffs", "rush_n", 1),
}
OFFENSE_RATES = {
    "epa_per_play": "epa_per_play", "success_rate": "success_rate",
    "yards_per_play": "yards_per_play", "points_per_drive": "points_per_drive",
    "explosive_play_rate": "explosive_play_rate", "down_conversion_rate": "down_conversion_rate",
    "off_proe": "proe", "rz_td_rate": "rz_td_rate",
    "ol_pressure_rate_allowed": "pressure_rate",
    "ol_no_blitz_pressure_allowed": "no_blitz_pressure_rate",
    "ol_rush_stuff_rate": "rush_stuff_rate",
}
DEFENSE_RATES = {
    "epa_per_play_allowed": "epa_per_play", "success_rate_allowed": "success_rate",
    "yards_per_play_allowed": "yards_per_play", "points_per_drive_allowed": "points_per_drive",
    "explosive_play_rate_allowed": "explosive_play_rate",
    "down_conversion_rate_allowed": "down_conversion_rate",
    "def_proe": "proe", "rz_td_rate_allowed": "rz_td_rate",
    "dl_pressure_rate": "pressure_rate", "dl_no_blitz_pressure_rate": "no_blitz_pressure_rate",
    "dl_rush_stuff_rate": "rush_stuff_rate",
}
OFFENSE_ONLY = {
    "shotgun_rate": ("shotgun", "plays", 1),
    "no_huddle_rate": ("no_huddle", "plays", 1),
    "air_yards_per_att": ("air_yards_sum", "pass_att", 1),
    "plays_per_game": ("plays", "games", 1),
    "plays_per_drive": ("plays", "drives", 1),
    "drives_per_game": ("drives", "games", 1),
    "time_of_possession": ("top_seconds", "games", 1 / 60),  # minutes per game
    "neutral_pass_rate": ("neutral_pass", "neutral_n", 1),
    "time_to_throw": ("ttt_sum", "ttt_n", 1),
    **{p: (p, "pers_n", 1) for p in
       ("pers_11", "pers_12", "pers_13", "pers_21", "pers_22", "pers_2te", "pers_2rb", "pers_3wr")},
}
DEFENSE_ONLY = {
    "blitz_rate": ("blitz", "rushers_n", 1),
    "light_box_rate": ("light_box", "box_n", 1),
    "heavy_box_rate": ("heavy_box", "box_n", 1),
    "sub_package_rate": ("sub_package", "dpers_n", 1),
    "man_rate": ("man", "mz_n", 1),
    "zone_rate": ("zone", "mz_n", 1),
    "middle_closed_rate": ("middle_closed", "cov_n", 1),
    "middle_open_rate": ("middle_open", "cov_n", 1),
    "ypt_allowed_wr": ("yds_wr", "tgt_wr", 1),
    "ypt_allowed_te": ("yds_te", "tgt_te", 1),
    "ypt_allowed_rb": ("yds_rb", "tgt_rb", 1),
}


# --- Play-level pieces -----------------------------------------------------

def _personnel_counts(text) -> dict[str, int]:
    """{"RB": 1, "TE": 2, ...} from "1 RB, 2 TE, 2 WR" or the newer per-position lists."""
    counts: dict[str, int] = {}
    if isinstance(text, str):
        for n, label in re.findall(r"(\d+) ([A-Z]+)", text):
            counts[label] = counts.get(label, 0) + int(n)
    return counts


def _offense_personnel(series: pd.Series) -> pd.DataFrame:
    """Backs (RB + FB), tight ends and receivers per play; NaN where personnel is unknown."""
    parsed = {}
    for text in series.dropna().unique():
        c = _personnel_counts(text)
        parsed[text] = (c.get("RB", 0) + c.get("FB", 0), c.get("TE", 0), c.get("WR", 0))
    rows = [parsed.get(t, (np.nan,) * 3) for t in series]
    return pd.DataFrame(rows, columns=["n_rb", "n_te", "n_wr"], index=series.index)


def _defensive_backs(series: pd.Series) -> pd.Series:
    """Defensive backs per play (DB, CB, S, FS, SS); NaN where personnel is unknown."""
    parsed = {}
    for text in series.dropna().unique():
        c = _personnel_counts(text)
        parsed[text] = sum(c.get(k, 0) for k in ("DB", "CB", "S", "FS", "SS"))
    return pd.Series([parsed.get(t, np.nan) for t in series], index=series.index, dtype=float)


def _flag(series: pd.Series) -> pd.Series:
    """1.0 / 0.0 / NaN from a True/False column, however the CSV spelled it."""
    mapping = {True: 1.0, False: 0.0, "TRUE": 1.0, "FALSE": 0.0, "True": 1.0, "False": 0.0}
    return series.map(mapping).astype(float)


def _clock_seconds(text) -> float:
    if not isinstance(text, str) or ":" not in text:
        return np.nan
    minutes, _, seconds = text.partition(":")
    return int(minutes) * 60 + int(seconds)


def scrimmage_plays(pbp: pd.DataFrame, participation: pd.DataFrame | None,
                    positions: dict[str, str]) -> pd.DataFrame:
    """One row per offensive snap (passes, sacks, scrambles, runs), with play-level flags."""
    plays = pbp[pbp.play_type.isin(["pass", "run"]) & (pbp.two_point_attempt.fillna(0) == 0)
                & pbp.posteam.notna() & pbp.defteam.notna()].copy()
    if participation is not None and len(participation):
        part = participation.rename(columns={"nflverse_game_id": "game_id"})
        plays = plays.merge(part, on=["game_id", "play_id"], how="left")
    for column in PARTICIPATION_COLUMNS[2:]:
        if column not in plays:
            plays[column] = np.nan

    dropback = plays["pass"].fillna(0) == 1
    designed_run = (plays["rush"].fillna(0) == 1) & ~dropback
    yards = plays.yards_gained.fillna(0)
    out = pd.DataFrame({
        "game_id": plays.game_id, "week": plays.week.astype(int),
        "posteam": plays.posteam, "defteam": plays.defteam,
        "plays": 1.0,
        "epa_sum": plays.epa.fillna(0), "epa_n": plays.epa.notna().astype(float),
        "success_sum": plays.success.where(plays.epa.notna(), 0).fillna(0),
        "yards_sum": yards,
        "explosive": ((designed_run & (yards >= 10)) | (dropback & (yards >= 20))).astype(float),
        "shotgun": plays.shotgun.fillna(0), "no_huddle": plays.no_huddle.fillna(0),
    }, index=plays.index)

    attempt = (plays.pass_attempt.fillna(0) == 1) & (plays.sack.fillna(0) == 0)
    out["pass_att"] = attempt.astype(float)
    out["air_yards_sum"] = plays.air_yards.where(attempt, 0).fillna(0)
    # Neutral: win probability 20-80% and outside the last two minutes of each half.
    neutral = plays.wp.between(0.2, 0.8) & (plays.half_seconds_remaining > 120)
    out["neutral_n"] = neutral.astype(float)
    out["neutral_pass"] = (neutral & dropback).astype(float)
    out["proe_sum"] = plays.pass_oe.fillna(0)
    out["proe_n"] = plays.pass_oe.notna().astype(float)
    out["rush_n"] = designed_run.astype(float)
    out["stuffs"] = (designed_run & (yards <= 0)).astype(float)

    pressure = _flag(plays.was_pressure).where(dropback)
    rushers = plays.number_of_pass_rushers.where(dropback)
    out["dropbacks"] = dropback.astype(float)
    out["pressure_n"] = pressure.notna().astype(float)
    out["pressure"] = pressure.fillna(0)
    out["rushers_n"] = rushers.notna().astype(float)
    out["blitz"] = (rushers >= 5).astype(float)
    no_blitz = (rushers <= 4) & pressure.notna()
    out["noblitz_n"] = no_blitz.astype(float)
    out["noblitz_pressure"] = (no_blitz & (pressure == 1)).astype(float)
    ttt = plays.time_to_throw.where(dropback)
    out["ttt_sum"] = ttt.fillna(0)
    out["ttt_n"] = ttt.notna().astype(float)

    personnel = _offense_personnel(plays.offense_personnel)
    known = personnel.n_rb.notna()
    out["pers_n"] = known.astype(float)
    for name, (rb, te) in {"pers_11": (1, 1), "pers_12": (1, 2), "pers_13": (1, 3),
                           "pers_21": (2, 1), "pers_22": (2, 2)}.items():
        out[name] = ((personnel.n_rb == rb) & (personnel.n_te == te)).astype(float)
    out["pers_2te"] = (personnel.n_te >= 2).astype(float)
    out["pers_2rb"] = (personnel.n_rb >= 2).astype(float)
    out["pers_3wr"] = (personnel.n_wr >= 3).astype(float)

    box = plays.defenders_in_box
    out["box_n"] = box.notna().astype(float)
    out["light_box"] = (box <= 6).astype(float)
    out["heavy_box"] = (box >= 8).astype(float)
    dbs = _defensive_backs(plays.defense_personnel)
    out["dpers_n"] = dbs.notna().astype(float)
    out["sub_package"] = (dbs >= 5).astype(float)
    man_zone = plays.defense_man_zone_type.where(dropback)
    out["mz_n"] = man_zone.isin(["MAN_COVERAGE", "ZONE_COVERAGE"]).astype(float)
    out["man"] = (man_zone == "MAN_COVERAGE").astype(float)
    out["zone"] = (man_zone == "ZONE_COVERAGE").astype(float)
    coverage = plays.defense_coverage_type.where(dropback)
    out["cov_n"] = (coverage.notna() & ~coverage.isin(["BLOWN"])).astype(float)
    out["middle_closed"] = coverage.isin(MIDDLE_CLOSED).astype(float)
    out["middle_open"] = coverage.isin(MIDDLE_OPEN).astype(float)

    target_position = plays.receiver_player_id.map(positions).where(attempt)
    for label, group in (("wr", {"WR"}), ("te", {"TE"}), ("rb", {"RB", "FB"})):
        targeted = target_position.isin(group)
        out[f"tgt_{label}"] = targeted.astype(float)
        out[f"yds_{label}"] = yards.where(targeted, 0)

    # Box score pieces (offense side only).
    out["rush_attempts"] = ((plays.rush_attempt.fillna(0) == 1)).astype(float)
    out["rush_yards"] = plays.rushing_yards.where(plays.rush_attempt.fillna(0) == 1, 0).fillna(0)
    out["pass_completions"] = plays.complete_pass.fillna(0)
    out["pass_yards"] = plays.passing_yards.fillna(0)
    out["sacks_taken"] = plays.sack.fillna(0)
    out["turnovers"] = plays.interception.fillna(0) + plays.fumble_lost.fillna(0)
    red_zone = plays.yardline_100 <= 20
    out["redzone_rush_attempts"] = (red_zone & (plays.rush_attempt.fillna(0) == 1)).astype(float)
    out["redzone_targets"] = (red_zone & attempt & plays.receiver_player_id.notna()).astype(float)
    return out


def drive_sums(pbp: pd.DataFrame) -> pd.DataFrame:
    """Per game and offense: drives, points scored on them, red zone trips and TDs, and possession time.

    Kickoffs are left out: their posteam can differ from the drive's offense.
    """
    plays = pbp[pbp.posteam.notna() & pbp.fixed_drive.notna() & (pbp.play_type != "kickoff")].copy()
    points = []
    for play in plays.to_dict("records"):
        # Only the offense's own points count toward its drive.
        points.append(sum(p for _, team, p, _, _ in scoring_events(play) if team == play["posteam"]))
    plays["points"] = points
    drives = plays.groupby(["game_id", "posteam", "fixed_drive"]).agg(
        week=("week", "first"),
        points=("points", "sum"),
        inside20=("drive_inside20", "max"),
        result=("fixed_drive_result", "first"),
        top=("drive_time_of_possession", "first"),
    ).reset_index()
    drives["top_seconds"] = drives.top.map(_clock_seconds)
    drives["rz"] = (drives.inside20.fillna(0) == 1).astype(float)
    drives["rz_td"] = (drives.rz == 1) & (drives.result == "Touchdown")
    return drives.groupby(["game_id", "posteam"]).agg(
        drives=("fixed_drive", "count"), drive_points=("points", "sum"),
        rz_drives=("rz", "sum"), rz_td_drives=("rz_td", "sum"),
        top_seconds=("top_seconds", "sum"),
    ).reset_index().rename(columns={"posteam": "team"})


def series_sums(pbp: pd.DataFrame) -> pd.DataFrame:
    """Per game and offense: series (first-and-10s) and how many earned a new first down or TD."""
    snaps = pbp[pbp.play_type.isin(["pass", "run"]) & pbp.posteam.notna()]
    series = snaps.dropna(subset=["series"]).groupby(["game_id", "posteam", "series"]).series_success.max()
    return series.groupby(["game_id", "posteam"]).agg(["count", "sum"]).reset_index().rename(
        columns={"posteam": "team", "count": "series_n", "sum": "series_success"})


def team_game_sums(pbp: pd.DataFrame, participation: pd.DataFrame | None,
                   positions: dict[str, str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(offense sums, defense sums), one row per game and team, keyed game_id/week/team."""
    pbp = _numeric(pbp, NUMERIC_PBP)
    if participation is not None:
        participation = _numeric(participation, NUMERIC_PARTICIPATION)
    plays = scrimmage_plays(pbp, participation, positions)
    value_columns = [c for c in plays.columns if c not in ("game_id", "week", "posteam", "defteam")]
    weeks = pbp.groupby("game_id").week.first()
    sides = {}
    for side, team_column in (("offense", "posteam"), ("defense", "defteam")):
        sums = plays.groupby(["game_id", team_column])[value_columns].sum().reset_index()
        sums = sums.rename(columns={team_column: "team"})
        sides[side] = sums

    drives = drive_sums(pbp)
    series = series_sums(pbp)
    opponent = plays.groupby(["game_id", "posteam"]).defteam.first().reset_index().rename(
        columns={"posteam": "team", "defteam": "opponent"})

    offense = sides["offense"].merge(drives, on=["game_id", "team"], how="left").merge(
        series, on=["game_id", "team"], how="left")
    # A defense's drives and series are its opponent's.
    against = drives.merge(series, on=["game_id", "team"], how="outer").merge(
        opponent, on=["game_id", "team"]).drop(columns="team").rename(columns={"opponent": "team"})
    defense = sides["defense"].merge(against, on=["game_id", "team"], how="left")
    for frame in (offense, defense):
        frame["week"] = frame.game_id.map(weeks).astype(int)
        frame["games"] = 1.0
        frame.fillna({c: 0 for c in ("drives", "drive_points", "rz_drives", "rz_td_drives",
                                     "top_seconds", "series_n", "series_success")}, inplace=True)
    return offense, defense


def _ratio(numerator: pd.Series, denominator: pd.Series, scale: float) -> pd.Series:
    return (numerator / denominator.where(denominator > 0)) * scale


def season_to_date(sums: pd.DataFrame, rates: dict, last_week: int) -> pd.DataFrame:
    """One row per team per as_of_week 1..last_week, from games of that week and earlier only."""
    weekly = sums.groupby(["team", "week"])[SUMS].sum().astype(float)
    teams = weekly.index.get_level_values("team").unique()
    grid = pd.MultiIndex.from_product([teams, range(1, last_week + 1)], names=["team", "week"])
    # Bye weeks add zeros, so a team's row carries forward unchanged.
    cumulative = weekly.reindex(grid, fill_value=0).groupby(level="team").cumsum()
    out = pd.DataFrame(index=cumulative.index)
    out["games_played"] = cumulative.games.astype(int)
    out["plays"] = cumulative.plays.astype(int)
    for column, (numerator, denominator, scale) in rates.items():
        out[column] = _ratio(cumulative[numerator], cumulative[denominator], scale)
    out = out[out.games_played > 0]
    return out.reset_index().rename(columns={"week": "as_of_week"})


def team_week_stats(offense_sums, defense_sums, last_week):
    """(offense rows, defense rows) ready for team_offense_stats / team_defense_stats."""
    offense_rates = {c: SHARED_RATES[k] for c, k in OFFENSE_RATES.items()} | OFFENSE_ONLY
    defense_rates = {c: SHARED_RATES[k] for c, k in DEFENSE_RATES.items()} | DEFENSE_ONLY
    return (season_to_date(offense_sums, offense_rates, last_week),
            season_to_date(defense_sums, defense_rates, last_week))


def box_scores(offense_sums: pd.DataFrame, pbp: pd.DataFrame) -> pd.DataFrame:
    """game_team_stats rows: one team's box score per game."""
    pbp = _numeric(pbp, NUMERIC_PBP)
    penalties = pbp[(pbp.penalty.fillna(0) == 1) & pbp.penalty_team.notna()].groupby(
        ["game_id", "penalty_team"]).agg(penalties=("penalty", "count"),
                                         penalty_yards=("penalty_yards", "sum")).reset_index()
    penalties = penalties.rename(columns={"penalty_team": "team"})
    box = offense_sums.merge(penalties, on=["game_id", "team"], how="left").fillna(
        {"penalties": 0, "penalty_yards": 0})
    has_participation = box.rushers_n > 0
    return pd.DataFrame({
        "game_id": box.game_id, "team": box.team, "week": box.week,
        "plays": box.plays, "total_yards": box.yards_sum,
        "time_of_possession": box.top_seconds / 60,
        "rush_attempts": box.rush_attempts, "rush_yards": box.rush_yards,
        "pass_attempts": box.pass_att, "pass_completions": box.pass_completions,
        "pass_yards": box.pass_yards, "air_yards": box.air_yards_sum,
        "sacks_taken": box.sacks_taken, "turnovers": box.turnovers,
        "penalties": box.penalties, "penalty_yards": box.penalty_yards,
        "redzone_rush_attempts": box.redzone_rush_attempts,
        "redzone_targets": box.redzone_targets,
        "blitzes_faced": box.blitz.where(has_participation),
        "avg_pocket_time": _ratio(box.ttt_sum, box.ttt_n, 1),
    })


def red_zone_usage(pbp: pd.DataFrame) -> pd.DataFrame:
    """Per game and player: red zone carries, receptions and targets, and goal-line carries."""
    pbp = _numeric(pbp, NUMERIC_PBP)
    snaps = pbp[pbp.play_type.isin(["pass", "run"]) & (pbp.two_point_attempt.fillna(0) == 0)]
    red_zone = snaps.yardline_100 <= 20
    carries = snaps[red_zone & (snaps.rush_attempt.fillna(0) == 1) & snaps.rusher_player_id.notna()]
    targets = snaps[red_zone & (snaps.pass_attempt.fillna(0) == 1) & (snaps.sack.fillna(0) == 0)
                    & snaps.receiver_player_id.notna()]
    by_carrier = carries.groupby(["game_id", "rusher_player_id"]).agg(
        rz_carries=("play_id", "count"),
        goal_line_carries=("yardline_100", lambda y: int((y <= 5).sum())),
    ).rename_axis(["game_id", "player_id"])
    by_target = targets.groupby(["game_id", "receiver_player_id"]).agg(
        red_zone_targets=("play_id", "count"),
        rz_receptions=("complete_pass", "sum"),
    ).rename_axis(["game_id", "player_id"])
    usage = by_carrier.join(by_target, how="outer").fillna(0)
    usage["red_zone_touches"] = usage.rz_carries + usage.rz_receptions
    return usage[["red_zone_touches", "red_zone_targets", "goal_line_carries"]].reset_index()


def player_weeks(player_stats: pd.DataFrame, pbp: pd.DataFrame, snaps: pd.DataFrame | None,
                 pfr_to_gsis: dict[str, str]) -> pd.DataFrame:
    """player_weekly_stats rows keyed by nflverse player id and game id."""
    stats = player_stats[(player_stats.position_group.isin(PLAYER_POSITIONS)
                          | player_stats.position.isin(PLAYER_POSITIONS))
                         & player_stats.player_id.notna()].copy()
    counts = ["targets", "carries", "receiving_air_yards", "receiving_yards", "rushing_yards",
              "receptions", "rushing_tds", "receiving_tds", "special_teams_tds"]
    stats[counts] = stats[counts].fillna(0)
    stats["tds"] = stats.rushing_tds + stats.receiving_tds + stats.special_teams_tds
    rows = stats.merge(red_zone_usage(pbp), on=["game_id", "player_id"], how="left").fillna(
        {"red_zone_touches": 0, "red_zone_targets": 0, "goal_line_carries": 0})
    if snaps is not None and len(snaps):
        snaps = snaps.assign(player_id=snaps.pfr_player_id.map(pfr_to_gsis))
        # Rosters lack some Pro Football Reference ids; then match on name, team and game,
        # but only where that names exactly one player on each side.
        by_name = rows[["game_id", "team", "player_display_name", "player_id"]].drop_duplicates()
        by_name = by_name[~by_name.duplicated(["game_id", "team", "player_display_name"], keep=False)]
        unmatched = snaps[snaps.player_id.isna()].drop(columns="player_id")
        unmatched = unmatched[~unmatched.duplicated(["game_id", "team", "player"], keep=False)]
        named = unmatched.merge(by_name, left_on=["game_id", "team", "player"],
                                right_on=["game_id", "team", "player_display_name"])
        mapped = pd.concat([snaps.dropna(subset=["player_id"]), named])
        mapped = mapped.drop_duplicates(["game_id", "player_id"])
        rows = rows.merge(mapped[["game_id", "player_id", "offense_snaps", "offense_pct"]],
                          on=["game_id", "player_id"], how="left")
    else:
        rows["offense_snaps"] = np.nan
        rows["offense_pct"] = np.nan
    return rows.rename(columns={"receiving_air_yards": "air_yards", "offense_snaps": "snaps",
                                "offense_pct": "snap_share"})


# --- Files -------------------------------------------------------------------

def _file(root: Path, dataset: str, season: int) -> Path:
    return root / dataset / str(season) / f"{dataset}.csv.gz"


def _read(root: Path, dataset: str, season: int, columns: list[str], result: ImportResult,
          required: bool) -> tuple[pd.DataFrame | None, Sidecar | None]:
    """(frame, sidecar) for one file, or (None, None) when an optional file can't be used."""
    path = _file(root, dataset, season)
    try:
        if not path.exists():
            raise ImportRejected(f"{dataset} {season} is missing")
        sidecar = load_sidecar(sidecar_path(path))
        frame = pd.read_csv(path, usecols=lambda c: c in columns, low_memory=False)
        if missing := set(columns) - set(frame.columns):
            raise ImportRejected(f"{dataset} {season} is missing columns {', '.join(sorted(missing))}")
    except ImportRejected as exc:
        if required:
            raise
        result.flags.append(f"not used: {exc}")
        return None, None
    return frame, sidecar


def _rows_of(frame: pd.DataFrame, columns: list[str]) -> list[dict]:
    return frame[columns].astype(object).where(frame[columns].notna(), None).to_dict("records")


def _int(value):
    return None if value is None else int(round(value))


def _upsert_players(session: Session, roster: pd.DataFrame | None, stats: pd.DataFrame,
                    lookup: TeamLookup, current: bool) -> dict[str, int]:
    """{nflverse id: players.id}, adding or updating everyone on the roster or in player_stats.

    A player's team is set from the latest season only (`current`), so rebuilding an old
    season doesn't move players back to old teams.
    """
    existing = {p.nflverse_id: p for p in session.scalars(select(Player).where(Player.nflverse_id.is_not(None)))}
    people = {}
    if roster is not None:
        for r in _rows_of(roster.dropna(subset=["gsis_id"]), ROSTER_COLUMNS):
            people[r["gsis_id"]] = (r["full_name"], r["position"], r["team"], r["pfr_id"])
    for r in _rows_of(stats.dropna(subset=["player_id"]).drop_duplicates("player_id", keep="last"),
                      ["player_id", "player_display_name", "position", "team"]):
        if r["player_id"] not in people:
            people[r["player_id"]] = (r["player_display_name"], r["position"], r["team"], None)
    for nflverse_id, (name, position, team, pfr_id) in people.items():
        player = existing.get(nflverse_id)
        if player is None:
            player = Player(nflverse_id=nflverse_id)
            session.add(player)
            existing[nflverse_id] = player
        new = player.id is None
        if new or current:
            player.name = name or nflverse_id
            player.position = (position or "UNK")[:8]
            player.team_id = lookup.team_id(team) if team else None
        player.pfr_id = pfr_id or player.pfr_id
    session.flush()
    return {k: p.id for k, p in existing.items()}


def _range_flags(rows: pd.DataFrame, side: str) -> list[str]:
    """Flag rates outside 0..1; they would mean a bug, not a real season."""
    flags = []
    for column in rows.columns:
        if column.endswith(("_rate", "_rate_allowed")) or column.startswith("pers_"):
            bad = rows[(rows[column] < 0) | (rows[column] > 1)]
            if len(bad):
                flags.append(f"{side} {column} outside 0 to 1 for {len(bad)} rows")
    return flags


def build_season(session: Session, root: Path, season: int, log_dir: Path | None = None) -> list[ImportResult]:
    """Build every nflverse-derived table for one season. One result per output table."""
    team_result = ImportResult(TEAM_TABLE_KEY, "play_by_play.csv.gz", "loaded")
    box_result = ImportResult(BOX_TABLE_KEY, "play_by_play.csv.gz", "loaded")
    player_result = ImportResult(PLAYER_TABLE_KEY, "player_stats.csv.gz", "loaded")
    results = [team_result, box_result, player_result]
    lookup = TeamLookup(session)
    games = {g.nflverse_game_id: g for g in session.scalars(
        select(Game).where(Game.season == season, Game.nflverse_game_id.is_not(None)))}

    with logged_run("import", log_dir, table_key=TEAM_TABLE_KEY, season=season) as log:
        sidecar = None
        try:
            pbp, sidecar = _read(root, "play_by_play", season, PBP_COLUMNS, team_result, required=True)
            if not games:
                raise ImportRejected(f"no {season} games in the database; run app.nflverse_loader first")
            last_week = sidecar.as_of_week
            if last_week == 0:
                raise ImportRejected("play_by_play sidecar says no week is fully played yet")
            later = pbp[pbp.week > last_week].game_id.nunique()
            if later:
                team_result.flags.append(
                    f"left out {later} game(s) after as_of_week {last_week} (a partly played week)")
            pbp = pbp[pbp.week <= last_week]
            team_result.rows_read = len(pbp)

            participation, part_sidecar = _read(root, "pbp_participation", season,
                                                PARTICIPATION_COLUMNS, team_result, required=False)
            if part_sidecar is not None and part_sidecar.as_of_week < last_week:
                team_result.flags.append(
                    f"not used: pbp_participation {season} runs through week "
                    f"{part_sidecar.as_of_week}, play-by-play through {last_week}")
                participation = None
            if participation is None:
                team_result.flags.append(
                    "personnel, box, pass rush, pressure, time to throw and coverage stats left empty")
            roster, _ = _read(root, "rosters", season, ROSTER_COLUMNS, team_result, required=False)
            positions = {} if roster is None else dict(zip(roster.gsis_id, roster.position))
            if roster is None:
                team_result.flags.append("yards per target by position left empty (no roster)")

            offense_sums, defense_sums = team_game_sums(pbp, participation, positions)
            offense, defense = team_week_stats(offense_sums, defense_sums, last_week)
            team_result.flags.extend(_range_flags(offense, "offense") + _range_flags(defense, "defense"))
            for side, rows in (("offense", offense), ("defense", defense)):
                first_week = rows[rows.as_of_week == rows.as_of_week.min()]
                if len(first_week) < 32:
                    team_result.flags.append(f"{side}: only {len(first_week)} teams after week 1")
            for model, rows in ((TeamOffenseStats, offense), (TeamDefenseStats, defense)):
                session.execute(delete(model).where(model.season == season, model.source == SOURCE))
                columns = [c for c in rows.columns if c not in ("team", "as_of_week")]
                for r in _rows_of(rows, list(rows.columns)):
                    values = {c: r[c] for c in columns}
                    session.add(model(team_id=lookup.team_id(r["team"]), season=season,
                                      as_of_week=r["as_of_week"], source=SOURCE, **values))
            team_result.rows_loaded = len(offense) + len(defense)
            team_result.counts = {"offense_rows": len(offense), "defense_rows": len(defense),
                                  "weeks": last_week}
            session.add(_snapshot(season, sidecar, TEAM_TABLE_KEY, _file(root, "play_by_play", season), team_result))

            # Box scores, one per team per game.
            box = box_scores(offense_sums, pbp)
            unknown = sorted(set(box.game_id) - set(games))
            if unknown:
                box_result.rejected.append(f"games not in the database: {', '.join(unknown)}")
                box = box[box.game_id.isin(games)]
            session.execute(delete(GameTeamStats).where(
                GameTeamStats.game_id.in_([g.id for g in games.values()])))
            int_columns = [c for c in box.columns if c not in
                           ("game_id", "team", "week", "time_of_possession", "avg_pocket_time", "blitzes_faced")]
            for r in _rows_of(box, list(box.columns)):
                session.add(GameTeamStats(
                    game_id=games[r["game_id"]].id, team_id=lookup.team_id(r["team"]),
                    time_of_possession=r["time_of_possession"], avg_pocket_time=r["avg_pocket_time"],
                    blitzes_faced=_int(r["blitzes_faced"]),
                    **{c: _int(r[c]) for c in int_columns}))
            box_result.rows_read = box_result.rows_loaded = len(box)
            if box_result.rejected:
                box_result.status = "partial"
            session.add(_snapshot(season, sidecar, BOX_TABLE_KEY, _file(root, "play_by_play", season), box_result))

            # Player weekly lines.
            stats, stats_sidecar = _read(root, "player_stats", season, PLAYER_STATS_COLUMNS,
                                         player_result, required=False)
            if stats is not None:
                stats = stats[stats.week <= min(stats_sidecar.as_of_week, last_week)]
                snaps, snap_sidecar = _read(root, "snap_counts", season, SNAP_COLUMNS,
                                            player_result, required=False)
                if snaps is not None:
                    snaps = snaps[snaps.week <= snap_sidecar.as_of_week]
                pfr_to_gsis = {} if roster is None else dict(
                    zip(roster.dropna(subset=["pfr_id"]).pfr_id, roster.dropna(subset=["pfr_id"]).gsis_id))
                lines = player_weeks(stats, pbp, snaps, pfr_to_gsis)
                duplicates = lines.duplicated(["player_id", "week"], keep=False)
                if duplicates.any():
                    player_result.rejected.append(
                        f"{int(duplicates.sum())} player lines share a player and week; none of them kept")
                    lines = lines[~duplicates]
                latest_season = max(session.scalars(select(Game.season).distinct()))
                player_ids = _upsert_players(session, roster, stats, lookup, season >= latest_season)
                player_result.rows_read = len(lines)
                unknown = lines[~lines.game_id.isin(games)]
                if len(unknown):
                    player_result.rejected.append(
                        f"{len(unknown)} player lines for games not in the database")
                    lines = lines[lines.game_id.isin(games)]
                if snaps is not None:
                    unmatched = lines[lines.snaps.isna() & ((lines.targets > 0) | (lines.carries > 0))]
                    if len(unmatched):
                        player_result.flags.append(
                            f"{len(unmatched)} player lines with targets or carries have no snap count "
                            f"(not matched by Pro Football Reference id or by name)")
                session.execute(delete(PlayerWeeklyStats).where(PlayerWeeklyStats.season == season))
                count_columns = ["targets", "carries", "red_zone_touches", "red_zone_targets",
                                 "goal_line_carries", "air_yards", "receiving_yards",
                                 "rushing_yards", "receptions", "tds"]
                for r in _rows_of(lines, ["player_id", "week", "game_id", "team", "snaps",
                                          "snap_share", "target_share", *count_columns]):
                    session.add(PlayerWeeklyStats(
                        player_id=player_ids[r["player_id"]], season=season, week=int(r["week"]),
                        game_id=games[r["game_id"]].id, team_id=lookup.team_id(r["team"]),
                        snaps=_int(r["snaps"]), snap_share=r["snap_share"],
                        target_share=r["target_share"], **{c: _int(r[c]) for c in count_columns}))
                player_result.rows_loaded = len(lines)
                player_result.counts = {"players": int(lines.player_id.nunique())}
                if player_result.rejected:
                    player_result.status = "partial"
                session.add(_snapshot(season, stats_sidecar, PLAYER_TABLE_KEY,
                                      _file(root, "player_stats", season), player_result))
            else:
                player_result.status = "rejected"
                player_result.reason = "player_stats file not usable"
            session.commit()
        except ImportRejected as exc:
            session.rollback()
            for result in results:
                result.status, result.reason = "rejected", str(exc)
            if sidecar is not None:
                session.add(_snapshot(season, sidecar, TEAM_TABLE_KEY,
                                      _file(root, "play_by_play", season), team_result))
                session.commit()
        log_summary(log, team_result, season, sidecar)
        log.summary["outputs"] = [
            {"table_key": r.table_key, "status": r.status, "rows_loaded": r.rows_loaded,
             "flags": r.flags, "rejected": r.rejected, "counts": r.counts}
            for r in results
        ]
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", type=Path, help="the data/extracted/nflverse folder")
    parser.add_argument("--season", type=int, action="append", help="only this season")
    args = parser.parse_args()

    from app.db import SessionLocal

    seasons = sorted(int(p.name) for p in (args.root / "play_by_play").iterdir() if p.name.isdigit())
    with SessionLocal() as session:
        for season in seasons:
            if args.season and season not in args.season:
                continue
            for result in build_season(session, args.root, season):
                counts = "".join(f", {k} {v}" for k, v in result.counts.items())
                print(f"{result.table_key} {season}: {result.status}, {result.rows_loaded} rows{counts}")
                for line in result.rejected + result.flags + ([result.reason] if result.reason else []):
                    print(f"  - {line}")


if __name__ == "__main__":
    main()
