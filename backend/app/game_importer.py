"""Load the schedule (weekly_matchups) and final scores (game_results) into games.

Sharpline is pregame only: the one thing kept from a played game is its final score.
A tie is a final game with equal scores. Called through app.importer.import_csv,
which handles the sidecar, the folder, the header check and logging.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select

from app.importer import ImportRejected, ImportResult, RowError, _snapshot
from app.models import Game, Team
from app.team_lookup import TeamLookup

# A full slate is 16 games; the heaviest bye weeks drop to 13.
MIN_GAMES = 13
MAX_WEEK = 22


@dataclass(frozen=True)
class GameTableSpec:
    header: set[str]
    load: Callable


def _week(text: str) -> int:
    try:
        week = int(text.strip())
    except ValueError:
        raise RowError(f"week={text.strip()!r} is not a whole number") from None
    if not 1 <= week <= MAX_WEEK:
        raise RowError(f"week={week} is outside 1 to {MAX_WEEK}")
    return week


def _score(name: str, text: str) -> int:
    s = text.strip()
    if not s.isdigit():
        raise RowError(f"{name}={s!r} is not a final score")
    return int(s)


def _kickoff(text: str) -> datetime:
    try:
        kickoff = datetime.fromisoformat(text.strip())
    except ValueError:
        raise RowError(f"kickoff_time={text.strip()!r} is not an ISO date-time") from None
    if kickoff.tzinfo is None:
        raise RowError("kickoff_time needs a UTC offset, e.g. 2026-10-18T13:00:00-04:00")
    return kickoff


def _teams(row: dict[str, str], lookup: TeamLookup) -> tuple[int, int]:
    ids = []
    for column in ("away_team", "home_team"):
        team_id = lookup.team_id(row[column])
        if team_id is None:
            raise RowError(f"unknown team {row[column].strip()!r}")
        ids.append(team_id)
    if ids[0] == ids[1]:
        raise RowError(f"{row['home_team'].strip()!r} can't play itself")
    return ids[0], ids[1]


def _find_game(session, season: int, week: int, away_id: int, home_id: int) -> Game | None:
    """The stored game for this pairing, None if there isn't one, or RowError if it conflicts."""
    teams = (away_id, home_id)
    games = session.scalars(
        select(Game).where(
            Game.season == season,
            Game.week == week,
            or_(Game.home_team_id.in_(teams), Game.away_team_id.in_(teams)),
        )
    ).all()
    for game in games:
        if (game.away_team_id, game.home_team_id) == (away_id, home_id):
            return game
        if (game.away_team_id, game.home_team_id) == (home_id, away_id):
            raise RowError("home and away are swapped compared with the stored game")
    if games:
        raise RowError(f"a team in this row already has a different week {week} game")
    return None


def _parse_rows(session, rows, result, parse_extra) -> dict[int, tuple]:
    """{line: (week, away_id, home_id, extra)}, with bad rows and duplicate teams rejected."""
    lookup = TeamLookup(session)
    parsed = {}
    for line, row in rows:
        try:
            week = _week(row["week"])
            away_id, home_id = _teams(row, lookup)
            parsed[line] = (week, away_id, home_id, parse_extra(row))
        except RowError as exc:
            result.rejected.append(f"line {line}: {exc}")

    # A team can play once a week; if it appears twice, neither row can be trusted.
    lines_by_team: dict[tuple[int, int], list[int]] = {}
    for line, (week, away_id, home_id, _) in parsed.items():
        for team_id in (away_id, home_id):
            lines_by_team.setdefault((week, team_id), []).append(line)
    team_names = dict(session.execute(select(Team.id, Team.name)).all())
    duplicate_lines = set()
    for (week, team_id), lines in lines_by_team.items():
        if len(lines) > 1:
            duplicate_lines.update(lines)
            result.rejected.append(
                f"lines {', '.join(map(str, lines))}: {team_names[team_id]} "
                f"appears more than once in week {week}"
            )
    return {line: p for line, p in parsed.items() if line not in duplicate_lines}


def _apply(session, parsed, season, result, update) -> None:
    for line, (week, away_id, home_id, extra) in sorted(parsed.items()):
        try:
            game = _find_game(session, season, week, away_id, home_id)
        except RowError as exc:
            result.rejected.append(f"line {line}: {exc}")
            continue
        if game is None:
            game = Game(season=season, week=week, home_team_id=home_id, away_team_id=away_id)
            session.add(game)
            session.flush()  # so a later row in this file sees the new game
        update(game, extra)
        result.rows_loaded += 1


def _finish(session, csv_path, table_key, season, sidecar, result, min_rows=0) -> ImportResult:
    if result.rows_loaded == 0:
        raise ImportRejected("no usable rows: " + "; ".join(result.rejected))
    if result.rejected or result.rows_loaded < min_rows:
        result.status = "partial"
    snapshot = _snapshot(season, sidecar, table_key, csv_path, result)
    session.add(snapshot)
    session.commit()
    result.snapshot_id = snapshot.id
    return result


def load_matchups(session, csv_path, table_key, rows, season, sidecar) -> ImportResult:
    result = ImportResult(table_key, csv_path.name, "loaded", rows_read=len(rows))
    parsed = _parse_rows(session, rows, result, lambda row: _kickoff(row["kickoff_time"]))

    other_weeks = sorted({week for week, *_ in parsed.values() if week != sidecar.target_week})
    if other_weeks:
        result.flags.append(
            f"schedule rows for week {', '.join(map(str, other_weeks))} in a "
            f"target_week {sidecar.target_week} file"
        )

    def update(game, kickoff):
        game.kickoff_time = kickoff  # flexed games move; the latest schedule wins

    _apply(session, parsed, season, result, update)
    return _finish(session, csv_path, table_key, season, sidecar, result, min_rows=MIN_GAMES)


def load_results(session, csv_path, table_key, rows, season, sidecar) -> ImportResult:
    result = ImportResult(table_key, csv_path.name, "loaded", rows_read=len(rows))

    def scores(row):
        return _score("away_score", row["away_score"]), _score("home_score", row["home_score"])

    parsed = _parse_rows(session, rows, result, scores)

    # A final score can't exist for a game after the snapshot's last week.
    for line, (week, *_rest) in list(parsed.items()):
        if week > sidecar.as_of_week:
            result.rejected.append(
                f"line {line}: week {week} result is after as_of_week {sidecar.as_of_week}"
            )
            del parsed[line]
    weeks = [week for week, *_ in parsed.values()]
    if weeks and max(weeks) < sidecar.as_of_week:
        result.flags.append(
            f"stale: latest result is week {max(weeks)} but as_of_week is "
            f"{sidecar.as_of_week}; the source may be behind"
        )

    def update(game, final):
        away_score, home_score = final
        if game.status == "final" and (game.away_score, game.home_score) != final:
            result.flags.append(
                f"disagreement: week {game.week} game {game.id} was "
                f"{game.away_score}-{game.home_score}, {table_key} says "
                f"{away_score}-{home_score}; kept {away_score}-{home_score}"
            )
        game.away_score, game.home_score, game.status = away_score, home_score, "final"

    _apply(session, parsed, season, result, update)
    return _finish(session, csv_path, table_key, season, sidecar, result)


GAME_TABLES: dict[str, GameTableSpec] = {
    "weekly_matchups": GameTableSpec(
        {"week", "away_team", "home_team", "kickoff_time"}, load_matchups
    ),
    "game_results": GameTableSpec(
        {"week", "away_team", "home_team", "away_score", "home_score"}, load_results
    ),
}
