"""Load nflverse files (Path C): schedules, final scores, lines and weather into games
and game_lines, and scoring plays from play-by-play into game_scoring_plays.

George downloads the files with nflreadr into
data/extracted/nflverse/<dataset>/<season>/<dataset>.csv.gz; nothing here downloads.
Every file needs <dataset>.meta.json beside it, the same sidecar every import uses.

Run with:  python -m app.nflverse_loader sidecars data/extracted/nflverse
           python -m app.nflverse_loader load data/extracted/nflverse [--season 2026]

`sidecars` writes the missing sidecars from DOWNLOAD_INFO.txt and the weeks each file
covers, and never overwrites one. `load` runs schedules before play-by-play, because
scoring plays attach to games.
"""

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.game_importer import _apply, _finish, _parse_rows
from app.importer import (
    ImportRejected,
    ImportResult,
    RowError,
    Sidecar,
    _snapshot,
    load_sidecar,
    log_summary,
)
from app.models import Game, GameLine, GameScoringPlay
from app.run_log import logged_run
from app.team_lookup import TeamLookup

# Load order matters: scoring plays need their games.
DATASETS = ("schedules", "play_by_play")
TABLE_KEYS = {"schedules": "nflverse_schedules", "play_by_play": "nflverse_scoring_plays"}
# Every file that needs a sidecar; the last four are read by app.nflverse_build (step 13).
NFLREADR_FUNCTIONS = {
    "schedules": "load_schedules",
    "play_by_play": "load_pbp",
    "pbp_participation": "load_participation",
    "player_stats": "load_player_stats",
    "snap_counts": "load_snap_counts",
    "rosters": "load_rosters",
}

# nflverse gives kickoff as a local date and time in US Eastern.
EASTERN = ZoneInfo("America/New_York")
GAME_TYPES = {"REG", "WC", "DIV", "CON", "SB"}
ROOFS = {"outdoors", "dome", "closed", "open"}

SCHEDULE_COLUMNS = {
    "game_id", "season", "game_type", "week", "gameday", "gametime", "away_team",
    "home_team", "away_score", "home_score", "location", "roof", "temp", "wind",
    "spread_line", "home_spread_odds", "away_spread_odds", "total_line", "over_odds",
    "under_odds", "home_moneyline", "away_moneyline",
}
PBP_COLUMNS = {
    "game_id", "play_id", "season", "week", "qtr", "time", "desc", "posteam", "defteam",
    "td_team", "touchdown", "pass_touchdown", "rush_touchdown", "td_player_name",
    "td_player_id", "field_goal_result", "kicker_player_name", "kicker_player_id",
    "extra_point_result", "two_point_conv_result", "defensive_two_point_conv", "safety",
    "receiver_player_name", "receiver_player_id", "rusher_player_name", "rusher_player_id",
}


def regular_season_games(season: int) -> int:
    """Games a full regular season has: 17 weeks from 2021, 16 before."""
    return 272 if season >= 2021 else 256


# --- Files and sidecars ----------------------------------------------------

def file_parts(path: Path) -> tuple[str, int]:
    """(dataset, season) from .../<dataset>/<season>/<dataset>.csv.gz."""
    dataset = path.parent.parent.name
    if path.name != f"{dataset}.csv.gz" or not re.fullmatch(r"\d{4}", path.parent.name):
        raise ImportRejected(
            f"{path} is not <dataset>/<season>/<dataset>.csv.gz; can't tell the dataset and season"
        )
    if dataset not in DATASETS:
        raise ImportRejected(f"no nflverse loader for {dataset!r} yet")
    return dataset, int(path.parent.name)


def sidecar_path(path: Path) -> Path:
    return path.with_name(path.name.removesuffix(".csv.gz") + ".meta.json")


def read_download_info(root: Path, tz: ZoneInfo) -> tuple[datetime, str, dict[str, datetime]]:
    """(default download time, nflreadr version, {dataset: its own download time}).

    DOWNLOAD_INFO.txt has a "downloaded:" and an "nflreadr:" line for the first download,
    plus one "<dataset> <seasons> downloaded <time> with nflreadr <version>" line per
    later download. A time written without an offset is in `tz`.
    """
    path = root / "DOWNLOAD_INFO.txt"
    if not path.exists():
        raise ImportRejected(f"{path} is missing, so the download time is unknown")

    def when(text: str) -> datetime:
        stamp = datetime.fromisoformat(text.strip())
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=tz)

    downloaded = version = None
    per_dataset = {}
    for line in path.read_text().splitlines():
        if m := re.fullmatch(r"downloaded:\s*(.+)", line.strip()):
            downloaded = when(m.group(1))
        elif m := re.fullmatch(r"nflreadr:\s*(\S+)", line.strip()):
            version = m.group(1)
        elif m := re.fullmatch(r"(\w+) \S+ downloaded (\S+) with nflreadr (\S+)", line.strip()):
            per_dataset[m.group(1)] = when(m.group(2))
    if downloaded is None or version is None:
        raise ImportRejected(f"{path.name} needs a 'downloaded:' and an 'nflreadr:' line")
    return downloaded, version, per_dataset


def complete_through(games_per_week: pd.Series, played_per_week: pd.Series) -> int:
    """The last week with every game played, counting up from week 1."""
    week = 0
    for w, scheduled in games_per_week.sort_index().items():
        if played_per_week.get(w, 0) < scheduled:
            break
        week = int(w)
    return week


# The column holding each file's game id; rosters have none.
GAME_ID_COLUMNS = {
    "play_by_play": "game_id",
    "pbp_participation": "nflverse_game_id",
    "player_stats": "game_id",
    "snap_counts": "game_id",
}


def covered_week(dataset: str, path: Path) -> int:
    """A file's as_of_week: the last week it covers in full.

    A Thursday final doesn't make its week covered: the week's other games haven't
    been played. Other files are measured against the season's schedule file; a roster
    is season-level, so it covers what the schedule covers.
    """
    schedule_path = path.parents[2] / "schedules" / path.parent.name / "schedules.csv.gz"
    if dataset == "schedules":
        df = pd.read_csv(path, usecols=["week", "home_score"])
        return complete_through(df.groupby("week").size(), df[df.home_score.notna()].groupby("week").size())
    if not schedule_path.exists():
        raise ImportRejected(f"{schedule_path} is missing; it's needed to tell which weeks {path.name} covers")
    if dataset == "rosters":
        return covered_week("schedules", schedule_path)
    schedule = pd.read_csv(schedule_path, usecols=["game_id", "week"])
    games = pd.read_csv(path, usecols=[GAME_ID_COLUMNS[dataset]]).iloc[:, 0].drop_duplicates()
    played = schedule[schedule.game_id.isin(games)].groupby("week").size()
    return complete_through(schedule.groupby("week").size(), played)


def write_sidecars(root: Path, tz: ZoneInfo = EASTERN) -> list[str]:
    """Write each missing sidecar; one message per file."""
    downloaded, version, per_dataset = read_download_info(root, tz)
    messages = []
    for dataset in NFLREADR_FUNCTIONS:
        for path in sorted((root / dataset).glob(f"*/{dataset}.csv.gz")):
            season = path.parent.name
            target = sidecar_path(path)
            if target.exists():
                messages.append(f"{dataset} {season}: kept existing {target.name}")
                continue
            try:
                as_of_week = covered_week(dataset, path)
            except ImportRejected as exc:
                messages.append(f"{dataset} {season}: no sidecar written, {exc}")
                continue
            sidecar = {
                "source_name": f"nflverse {dataset} (nflreadr {version})",
                "source_url": f"nflreadr::{NFLREADR_FUNCTIONS[dataset]}({season})",
                "captured_at": per_dataset.get(dataset, downloaded).isoformat(timespec="seconds"),
                "as_of_week": as_of_week,
                "target_week": as_of_week + 1,
            }
            target.write_text(json.dumps(sidecar, indent=2) + "\n")
            messages.append(f"{dataset} {season}: wrote {target.name}, through week {as_of_week}")
    return messages


def read_frame(path: Path, columns: set[str], season: int) -> pd.DataFrame:
    df = pd.read_csv(path, usecols=lambda c: c in columns, low_memory=False)
    if missing := columns - set(df.columns):
        raise ImportRejected(f"wrong columns: missing {', '.join(sorted(missing))}")
    if (seasons := {int(s) for s in df.season.unique()}) - {season}:
        raise ImportRejected(f"file in {season}/ holds seasons {sorted(seasons)}")
    # None for every blank cell, so values can be tested with `is None`.
    return df.astype(object).where(df.notna(), None)


def _whole(name: str, value) -> int | None:
    if value is None:
        return None
    if float(value) != int(value):
        raise RowError(f"{name}={value} is not a whole number")
    return int(value)


# --- Schedules: games, final scores, lines, weather ------------------------

@dataclass
class ScheduleRow:
    nflverse_game_id: str
    game_type: str
    kickoff: datetime | None
    final: tuple[int, int] | None  # (away, home)
    neutral_site: bool
    roof: str | None
    temp: float | None
    wind: float | None
    line: dict | None


def _kickoff(row) -> datetime | None:
    if row["gameday"] is None:
        return None
    try:
        local = datetime.fromisoformat(f"{row['gameday']}T{row['gametime'] or '00:00'}")
    except ValueError:
        raise RowError(f"gameday/gametime {row['gameday']} {row['gametime']} is not a date and time") from None
    return local.replace(tzinfo=EASTERN)


def _final(row) -> tuple[int, int] | None:
    away, home = _whole("away_score", row["away_score"]), _whole("home_score", row["home_score"])
    if (away is None) != (home is None):
        raise RowError("only one team has a score")
    if away is None:
        return None
    if away < 0 or home < 0:
        raise RowError(f"negative score {away}-{home}")
    return away, home


def _price(name: str, value) -> int:
    price = _whole(name, value)
    if price is not None and -100 < price < 100:
        raise ValueError(f"{name}={price} is not an American price")
    return price


def _line(row) -> dict | None:
    """The game's line from nflverse, or None. Raises ValueError for an implausible value.

    nflverse's spread_line is the home team's expected margin (+3 = home favored by 3);
    it's stored the way a sportsbook prints it, so spread_home = -spread_line.
    """
    if all(row[c] is None for c in ("spread_line", "total_line", "home_moneyline", "away_moneyline")):
        return None
    spread = None if row["spread_line"] is None else -float(row["spread_line"])
    total = None if row["total_line"] is None else float(row["total_line"])
    if spread is not None and abs(spread) > 30:
        raise ValueError(f"spread {spread} is outside ±30")
    if total is not None and not 25 <= total <= 75:
        raise ValueError(f"total {total} is outside 25 to 75")
    return {
        "spread_home": spread,
        "spread_price_home": _price("home_spread_odds", row["home_spread_odds"]),
        "spread_price_away": _price("away_spread_odds", row["away_spread_odds"]),
        "total": total,
        "over_price": _price("over_odds", row["over_odds"]),
        "under_price": _price("under_odds", row["under_odds"]),
        "moneyline_home": _price("home_moneyline", row["home_moneyline"]),
        "moneyline_away": _price("away_moneyline", row["away_moneyline"]),
    }


def load_schedules(session: Session, path: Path, season: int, sidecar: Sidecar) -> ImportResult:
    table_key = TABLE_KEYS["schedules"]
    df = read_frame(path, SCHEDULE_COLUMNS, season)
    result = ImportResult(table_key, path.name, "loaded", rows_read=len(df))
    counts = {"finals": 0, "closing_lines": 0, "current_lines": 0}

    def parse(row) -> ScheduleRow:
        raw = row["_raw"]
        game_id = raw["game_id"]
        if raw["game_type"] not in GAME_TYPES:
            raise RowError(f"game_type {raw['game_type']!r} is not one of {sorted(GAME_TYPES)}")
        final = _final(raw)
        week = int(row["week"])
        # A final can come from the target week (a Thursday game already played), never later.
        if final is not None and week > sidecar.target_week:
            raise RowError(f"week {week} final is after target_week {sidecar.target_week}")
        roof = raw["roof"]
        if roof is not None and roof not in ROOFS:
            result.flags.append(f"{game_id}: unknown roof {roof!r}, left empty")
            roof = None
        weather = {}
        for name, low, high in (("temp", -30, 120), ("wind", 0, 60)):
            value = raw[name]
            if value is not None and not low <= float(value) <= high:
                result.flags.append(f"{game_id}: {name}={value} is outside {low} to {high}, left empty")
                value = None
            weather[name] = None if value is None else float(value)
        try:
            line = _line(raw)
        except ValueError as exc:
            result.flags.append(f"{game_id}: line not loaded, {exc}")
            line = None
        return ScheduleRow(
            game_id, raw["game_type"], _kickoff(raw), final, raw["location"] == "Neutral",
            roof, weather["temp"], weather["wind"], line,
        )

    rows = [
        (i + 2, {"week": str(raw["week"]), "away_team": str(raw["away_team"]),
                 "home_team": str(raw["home_team"]), "_raw": raw})
        for i, raw in enumerate(df.to_dict("records"))
    ]
    parsed = _parse_rows(session, rows, result, parse)

    def update(game: Game, row: ScheduleRow) -> None:
        if game.status == "final" and row.final and (game.away_score, game.home_score) != row.final:
            result.flags.append(
                f"disagreement: {row.nflverse_game_id} was {game.away_score}-{game.home_score}, "
                f"nflverse says {row.final[0]}-{row.final[1]}; kept nflverse"
            )
        game.nflverse_game_id = row.nflverse_game_id
        game.game_type = row.game_type
        game.kickoff_time = row.kickoff or game.kickoff_time
        game.neutral_site = row.neutral_site
        game.roof, game.temp, game.wind = row.roof, row.temp, row.wind
        if row.final:
            game.away_score, game.home_score = row.final
            game.status = "final"
            counts["finals"] += 1
        if row.line is None:
            return
        # A played game's line is its closing line, priced at kickoff. An upcoming
        # game's line is whatever nflverse had when George downloaded the file.
        if row.final and game.kickoff_time:
            line_type, captured_at = "closing", game.kickoff_time
        else:
            line_type, captured_at = "current", sidecar.captured_at
        line = session.scalar(
            select(GameLine).where(
                GameLine.game_id == game.id,
                GameLine.line_type == line_type,
                GameLine.source_name == sidecar.source_name,
                GameLine.captured_at == captured_at,
            )
        )
        if line is None:
            line = GameLine(
                game_id=game.id, line_type=line_type, source_name=sidecar.source_name,
                captured_at=captured_at, book_label="nflverse",
            )
            session.add(line)
        for name, value in row.line.items():
            setattr(line, name, value)
        counts[f"{line_type}_lines"] += 1

    _apply(session, parsed, season, result, update)

    regular = sum(1 for _, _, _, row in parsed.values() if row.game_type == "REG")
    if regular != regular_season_games(season):
        result.flags.append(
            f"{season} has {regular} regular-season games loaded, a full season has "
            f"{regular_season_games(season)}"
        )
    final_weeks = [week for week, _, _, row in parsed.values() if row.final]
    early = [row.nflverse_game_id for week, _, _, row in parsed.values()
             if row.final and week == sidecar.target_week]
    if early:
        result.flags.append(
            f"target week {sidecar.target_week} is partly played: {len(early)} final(s) already "
            f"in ({', '.join(sorted(early))}); a model predicting week {sidecar.target_week} "
            f"must not read them"
        )
    if final_weeks and max(final_weeks) < sidecar.as_of_week:
        result.flags.append(
            f"stale: latest final is week {max(final_weeks)} but as_of_week is {sidecar.as_of_week}"
        )
    result.counts = counts
    return _finish(session, path, table_key, season, sidecar, result)


# --- Play-by-play: scoring plays -------------------------------------------

def scoring_events(play) -> list[tuple[str, str, int, str | None, str | None]]:
    """(play_type, scoring team, points, scorer name, scorer nflverse id) for one play."""
    events = []
    if play["touchdown"] == 1:
        if play["pass_touchdown"] == 1:
            kind = "td_pass"
        elif play["rush_touchdown"] == 1:
            kind = "td_rush"
        else:
            kind = "td_other"
        events.append((kind, play["td_team"], 6, play["td_player_name"], play["td_player_id"]))
    if play["extra_point_result"] == "good":
        events.append(("xp", play["posteam"], 1, play["kicker_player_name"], play["kicker_player_id"]))
    if play["two_point_conv_result"] == "success":
        name = play["receiver_player_name"] or play["rusher_player_name"]
        player_id = play["receiver_player_id"] or play["rusher_player_id"]
        events.append(("two_point", play["posteam"], 2, name, player_id))
    if play["defensive_two_point_conv"] == 1:
        events.append(("other", play["defteam"], 2, None, None))
    if play["field_goal_result"] == "made":
        events.append(("fg", play["posteam"], 3, play["kicker_player_name"], play["kicker_player_id"]))
    if play["safety"] == 1:
        events.append(("other", play["defteam"], 2, None, None))
    return events


def load_scoring_plays(session: Session, path: Path, season: int, sidecar: Sidecar) -> ImportResult:
    table_key = TABLE_KEYS["play_by_play"]
    df = read_frame(path, PBP_COLUMNS, season)
    result = ImportResult(table_key, path.name, "loaded", rows_read=len(df))
    lookup = TeamLookup(session)
    games = {
        g.nflverse_game_id: g
        for g in session.scalars(
            select(Game).where(Game.season == season, Game.nflverse_game_id.is_not(None))
        )
    }
    loaded_games = []
    for game_key, plays in df.groupby("game_id", sort=True):
        week = int(plays.week.iloc[0])
        game = games.get(game_key)
        if week > sidecar.target_week:
            result.rejected.append(f"{game_key}: week {week} is after target_week {sidecar.target_week}")
            continue
        if game is None:
            result.rejected.append(f"{game_key}: not in games; load schedules first")
            continue
        if game.status != "final":
            result.rejected.append(f"{game_key}: has play-by-play but no final score in games")
            continue
        rows, unknown = [], set()
        for play in plays.sort_values("play_id").to_dict("records"):
            for kind, team, points, name, player_id in scoring_events(play):
                team_id = lookup.team_id(team) if team else None
                if team_id is None:
                    unknown.add(str(team))
                    continue
                rows.append(GameScoringPlay(
                    game_id=game.id, nflverse_play_id=int(play["play_id"]),
                    period=int(play["qtr"]), clock=play["time"], description=play["desc"],
                    scoring_team_id=team_id, play_type=kind, points=points,
                    scorer_name=name, scorer_nflverse_id=player_id,
                ))
        if unknown:
            result.rejected.append(f"{game_key}: unknown scoring team {', '.join(sorted(unknown))}")
            continue

        # The plays should add up to the final; when they don't, the play-by-play has a
        # scoring play wrong or missing, and the final score from schedules still stands.
        home = sum(r.points for r in rows if r.scoring_team_id == game.home_team_id)
        away = sum(r.points for r in rows if r.scoring_team_id == game.away_team_id)
        if (away, home) != (game.away_score, game.home_score):
            result.flags.append(
                f"{game_key}: scoring plays add up to {away}-{home}, final is "
                f"{game.away_score}-{game.home_score}"
            )
        # Facts about a finished game, so a re-import replaces them rather than adding.
        session.execute(delete(GameScoringPlay).where(GameScoringPlay.game_id == game.id))
        session.add_all(rows)
        loaded_games.append(week)
        result.rows_loaded += len(rows)

    if loaded_games:
        in_file = set(df.game_id)
        missing = [
            key for key, g in games.items()
            if g.status == "final" and g.week <= max(loaded_games) and key not in in_file
        ]
        if missing:
            result.flags.append(f"final games with no play-by-play: {', '.join(sorted(missing))}")
        if max(loaded_games) < sidecar.as_of_week:
            result.flags.append(
                f"stale: latest play-by-play is week {max(loaded_games)} but as_of_week is "
                f"{sidecar.as_of_week}"
            )
    result.counts = {"games": len(loaded_games)}
    return _finish(session, path, table_key, season, sidecar, result)


LOADERS = {"schedules": load_schedules, "play_by_play": load_scoring_plays}


def import_nflverse(session: Session, path: Path, log_dir: Path | None = None) -> ImportResult:
    """Validate and load one nflverse file. Never raises for bad data; the result says what happened."""
    path = Path(path)
    table_key = TABLE_KEYS.get(path.parent.parent.name, path.parent.parent.name)
    with logged_run("import", log_dir, table_key=table_key, file=str(path)) as log:
        sidecar = season = None
        try:
            dataset, season = file_parts(path)
            sidecar = load_sidecar(sidecar_path(path))
            result = LOADERS[dataset](session, path, season, sidecar)
        except ImportRejected as exc:
            session.rollback()
            result = ImportResult(table_key, path.name, "rejected", reason=str(exc))
            if sidecar is not None:
                snapshot = _snapshot(season, sidecar, table_key, path, result)
                session.add(snapshot)
                session.commit()
                result.snapshot_id = snapshot.id
        log_summary(log, result, season, sidecar)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("sidecars", "load"):
        command = sub.add_parser(name)
        command.add_argument("root", type=Path, help="the data/extracted/nflverse folder")
    sub.choices["load"].add_argument("--season", type=int, action="append", help="only this season")
    args = parser.parse_args()

    if args.command == "sidecars":
        for message in write_sidecars(args.root):
            print(message)
        return

    from app.db import SessionLocal

    with SessionLocal() as session:
        for dataset in DATASETS:
            for path in sorted((args.root / dataset).glob(f"*/{dataset}.csv.gz")):
                if args.season and int(path.parent.name) not in args.season:
                    continue
                result = import_nflverse(session, path)
                counts = "".join(f", {k} {v}" for k, v in result.counts.items())
                print(f"{dataset} {path.parent.name}: {result.status}, "
                      f"{result.rows_loaded}/{result.rows_read} rows{counts}")
                for line in result.rejected + result.flags + ([result.reason] if result.reason else []):
                    print(f"  - {line}")


if __name__ == "__main__":
    main()
