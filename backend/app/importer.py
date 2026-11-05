"""Import one CSV plus its sidecar into Postgres.

Run with:  python -m app.importer data/extracted/season-2026/week-06/team_offense_metrics.csv
      or:  python -m app.importer data/extracted/season-2026/week-06    (every CSV in the folder)

The CSV must sit in data/extracted/season-YYYY/week-NN/ next to <table_key>.meta.json.
Values arrive exactly as the source showed them ("42.1%", "+6.6", "Los Angeles Rams");
this module parses, validates and upserts them, then records a data_snapshots row.

A whole file is rejected for: no sidecar, a bad sidecar, a folder week that doesn't
match target_week, wrong columns, or no usable rows. Single rows are rejected for: an
unknown team, a duplicate team, or a value that won't parse or is outside a plausible
range. Rejected rows make the snapshot 'partial'; the rest still load.
"""

import argparse
import csv
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DataSnapshot, Team, TeamDefenseStats, TeamOffenseStats
from app.run_log import logged_run
from app.team_lookup import TeamLookup

SIDECAR_FIELDS = ("source_name", "source_url", "captured_at", "as_of_week", "target_week")
# A source writes a dash where a cell has no value.
EMPTY_MARKERS = {"", "-", "–", "—"}

# Value kinds. RATE: "42.1%" -> 0.421, and a bare number must already be a fraction.
# PCT_POINTS: "+6.6" percentage points -> 0.066. MINUTES: "32.2" or "32:12" -> 32.2.
RATE, PCT_POINTS, NUMBER, MINUTES = "rate", "pct_points", "number", "minutes"


@dataclass(frozen=True)
class Column:
    model: type
    kind: str
    low: float | None = None
    high: float | None = None
    # The CSV column naming the team this value belongs to.
    team_column: str = "team"
    # Flip the sign when the source measures from the other side of the ball.
    negate: bool = False


@dataclass(frozen=True)
class TableSpec:
    # CSV column -> where it goes. The database column has the same name.
    columns: dict[str, Column]
    # Columns the template has but the database doesn't store.
    ignored: tuple[str, ...] = ()
    # Fewer accepted rows than this makes the snapshot 'partial'.
    min_rows: int = 32

    @property
    def team_columns(self) -> set[str]:
        return {c.team_column for c in self.columns.values()}

    @property
    def header(self) -> set[str]:
        return self.team_columns | set(self.columns) | set(self.ignored)


O, D = TeamOffenseStats, TeamDefenseStats


def _rate(model):
    return Column(model, RATE, 0, 1)


TABLES: dict[str, TableSpec] = {
    "team_offense_metrics": TableSpec({
        "epa_per_play": Column(O, NUMBER, -0.6, 0.6),
        "yards_per_play": Column(O, NUMBER, 2, 10),
        "points_per_drive": Column(O, NUMBER, 0, 5),
        "explosive_play_rate": _rate(O),
        "down_conversion_rate": _rate(O),
    }),
    "team_defense_metrics": TableSpec({
        # The charting site shows EPA from the defense's side (positive = good defense).
        # Stored as EPA allowed (positive = bad defense) so it lines up with offense EPA.
        "epa_per_play_allowed": Column(D, NUMBER, -0.6, 0.6, negate=True),
        "yards_per_play_allowed": Column(D, NUMBER, 2, 10),
        "points_per_drive_allowed": Column(D, NUMBER, 0, 5),
        "explosive_play_rate_allowed": _rate(D),
        "down_conversion_rate_allowed": _rate(D),
    }),
    "offensive_tendencies": TableSpec({
        "motion_rate": _rate(O),
        "play_action_rate": _rate(O),
        "air_yards_per_att": Column(O, NUMBER, 3, 15),
        "shotgun_rate": _rate(O),
        "no_huddle_rate": _rate(O),
    }),
    "offensive_pace_volume": TableSpec({
        "plays_per_game": Column(O, NUMBER, 40, 90),
        "adj_plays_per_game": Column(O, NUMBER, 40, 90),
        "plays_per_drive": Column(O, NUMBER, 3, 10),
        "drives_per_game": Column(O, NUMBER, 7, 16),
        "time_of_possession": Column(O, MINUTES, 20, 40),
    }),
    "offensive_pace_tempo": TableSpec(
        {
            "play_clock_used": Column(O, NUMBER, 0, 40),
            "neutral_play_clock": Column(O, NUMBER, 0, 40),
            "neutral_pass_rate": _rate(O),
            "gear_change": Column(O, NUMBER),
        },
        # Tempo leaves out snaps after timeouts, penalties, reviews and the like, so its
        # no-huddle figure differs from no_huddle_rate (offensive_tendencies, all snaps).
        ignored=("no_huddle_rate_tempo",),
    ),
    "personnel_usage": TableSpec(
        {f"pers_{g}": _rate(O) for g in ("11", "12", "13", "21", "22", "2te", "2rb", "3wr")},
        ignored=("plays",),
    ),
    "offensive_line": TableSpec({
        "ol_pressure_rate_allowed": _rate(O),
        "ol_no_blitz_pressure_allowed": _rate(O),
        "time_to_throw": Column(O, NUMBER, 2, 4.5),
        "ol_ybc_per_rb_rush": Column(O, NUMBER, 0, 5),
        "ol_rush_stuff_rate": _rate(O),
    }),
    "defensive_line": TableSpec({
        "dl_pressure_rate": _rate(D),
        "dl_no_blitz_pressure_rate": _rate(D),
        "dl_ybc_per_rb_rush": Column(D, NUMBER, 0, 5),
        "dl_rush_stuff_rate": _rate(D),
    }),
    "defensive_tendencies": TableSpec({
        "blitz_rate": _rate(D),
        "light_box_rate": _rate(D),
        "heavy_box_rate": _rate(D),
        "sub_package_rate": _rate(D),
    }),
    "man_zone_coverage": TableSpec({
        "man_rate": _rate(D),
        "zone_rate": _rate(D),
        "middle_closed_rate": _rate(D),
        "middle_open_rate": _rate(D),
    }),
    "positional_coverage": TableSpec({
        f"ypt_allowed_{p}": Column(D, NUMBER, 0, 20)
        for p in ("wr", "te", "rb", "outside", "slot")
    }),
    "proe_offense_defense": TableSpec({
        "off_proe": Column(O, PCT_POINTS, -0.3, 0.3),
        "def_proe": Column(D, PCT_POINTS, -0.3, 0.3),
    }),
    # Target-week matchups only, so bye teams are absent (up to 6 per week).
    "proe_matchups": TableSpec(
        {
            "off_proe": Column(O, PCT_POINTS, -0.3, 0.3, team_column="offense_team"),
            "def_proe": Column(D, PCT_POINTS, -0.3, 0.3, team_column="defense_team"),
        },
        ignored=("matchup_score",),
        min_rows=26,
    ),
}


class ImportRejected(Exception):
    """The whole file is refused."""


class RowError(Exception):
    """One row is refused; the rest of the file still loads."""


@dataclass
class Sidecar:
    source_name: str
    source_url: str
    captured_at: datetime
    as_of_week: int
    target_week: int


@dataclass
class ImportResult:
    table_key: str
    file_name: str
    status: str
    rows_read: int = 0
    rows_loaded: int = 0
    rejected: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    reason: str | None = None
    snapshot_id: int | None = None
    # Extra tallies for loaders that fill more than one table, e.g. {"closing_lines": 285}.
    counts: dict[str, int] = field(default_factory=dict)


def parse_value(text: str, kind: str) -> float | None:
    """A cell as shown by the source, parsed to a number. Blank or dash -> None."""
    s = text.strip().replace("−", "-")
    if s in EMPTY_MARKERS:
        return None
    is_pct = s.endswith("%")
    s = s.removesuffix("%").strip().removeprefix("+")
    if kind == MINUTES and ":" in s:
        minutes, _, seconds = s.partition(":")
        return int(minutes) + int(seconds) / 60
    number = float(s)
    if kind == RATE:
        if is_pct:
            return number / 100
        if not 0 <= number <= 1:
            raise ValueError("looks like a percent without a % sign")
        return number
    if kind == PCT_POINTS:
        return number / 100
    if is_pct:
        raise ValueError("unexpected % sign")
    return number


def display_tolerance(text: str, kind: str) -> float:
    """How far another source's value can be from this cell and still agree.

    Sources round differently ("16%" vs "16.5%"), so this allows half of this cell's
    display unit, plus 10% slack for the other source's own rounding.
    """
    s = text.strip()
    decimals = len(s.removesuffix("%").partition(".")[2])
    unit = 10.0**-decimals
    if s.endswith("%") or kind == PCT_POINTS:
        unit /= 100
    return 0.55 * unit + 1e-9


def folder_weeks(csv_path: Path) -> tuple[int, int]:
    """(season, week) from .../season-YYYY/week-NN/<file>.csv."""
    season = re.fullmatch(r"season-(\d{4})", csv_path.parent.parent.name)
    week = re.fullmatch(r"week-(\d{2})", csv_path.parent.name)
    if not season or not week:
        raise ImportRejected(
            f"{csv_path} is not inside season-YYYY/week-NN/; can't tell the season and week"
        )
    return int(season.group(1)), int(week.group(1))


def read_sidecar(csv_path: Path, folder_week: int) -> Sidecar:
    sidecar = load_sidecar(csv_path.with_suffix(".meta.json"))
    if sidecar.target_week != folder_week:
        raise ImportRejected(
            f"sidecar target_week is {sidecar.target_week} but the file is in week-{folder_week:02d}/"
        )
    return sidecar


def load_sidecar(path: Path) -> Sidecar:
    """Read and check one <table_key>.meta.json."""
    if not path.exists():
        raise ImportRejected(f"no sidecar: {path.name} is missing")
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ImportRejected(f"sidecar is not valid JSON: {exc}") from exc

    missing = [f for f in SIDECAR_FIELDS if raw.get(f) in (None, "")]
    if missing:
        raise ImportRejected(f"sidecar is missing {', '.join(missing)}")
    try:
        captured_at = datetime.fromisoformat(raw["captured_at"])
        as_of_week, target_week = int(raw["as_of_week"]), int(raw["target_week"])
    except (TypeError, ValueError) as exc:
        raise ImportRejected(f"sidecar has a bad value: {exc}") from exc
    if captured_at.tzinfo is None:
        raise ImportRejected("sidecar captured_at needs a UTC offset, e.g. -04:00")
    if not 0 <= as_of_week < target_week:
        raise ImportRejected(
            f"sidecar as_of_week ({as_of_week}) must be before target_week ({target_week})"
        )
    return Sidecar(
        str(raw["source_name"]), str(raw["source_url"]), captured_at, as_of_week, target_week
    )


def read_rows(csv_path: Path, spec: TableSpec) -> list[tuple[int, dict[str, str]]]:
    """(line number, row) for every non-blank data row, after checking the header."""
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = {name.strip() for name in reader.fieldnames or []}
        if header != spec.header:
            problems = []
            if missing := spec.header - header:
                problems.append(f"missing {', '.join(sorted(missing))}")
            if extra := header - spec.header:
                problems.append(f"unexpected {', '.join(sorted(extra))}")
            raise ImportRejected(f"wrong columns: {'; '.join(problems)}")
        rows = []
        for row in reader:
            row = {k.strip(): (v or "") for k, v in row.items() if k is not None}
            if any(v.strip() for v in row.values()):
                rows.append((reader.line_num, row))
    return rows


def _parse_row(row: dict[str, str], spec: TableSpec, lookup: TeamLookup):
    """{(model, team_id): {column: (value, tolerance)}} for one CSV row, or RowError."""
    team_ids = {}
    for team_column in spec.team_columns:
        text = row[team_column]
        team_ids[team_column] = lookup.team_id(text)
        if team_ids[team_column] is None:
            raise RowError(f"unknown team {text.strip()!r}")

    updates: dict[tuple[type, int], dict[str, tuple[float | None, float]]] = {}
    for name, column in spec.columns.items():
        text = row[name]
        try:
            value = parse_value(text, column.kind)
        except ValueError as exc:
            raise RowError(f"{name}={text.strip()!r} won't parse ({exc})") from exc
        if value is not None and column.negate:
            value = -value
        if value is not None and (
            (column.low is not None and value < column.low)
            or (column.high is not None and value > column.high)
        ):
            raise RowError(
                f"{name}={text.strip()!r} is outside the plausible range "
                f"{column.low} to {column.high}"
            )
        key = (column.model, team_ids[column.team_column])
        updates.setdefault(key, {})[name] = (value, display_tolerance(text, column.kind))
    return updates


def _stale_flags(session, spec, accepted, season, as_of_week) -> list[str]:
    """Flag a table whose values exactly match the previous week's: the page may not have updated."""
    flags = []
    for model in {c.model for c in spec.columns.values()}:
        columns = [n for n, c in spec.columns.items() if c.model is model]
        previous_week = session.scalar(
            select(model.as_of_week)
            .where(model.season == season, model.as_of_week < as_of_week)
            .order_by(model.as_of_week.desc())
            .limit(1)
        )
        if previous_week is None:
            continue
        previous = {
            row.team_id: tuple(getattr(row, c) for c in columns)
            for row in session.scalars(
                select(model).where(model.season == season, model.as_of_week == previous_week)
            )
        }
        compared = [
            (previous[team_id], tuple(values[c][0] for c in columns))
            for (m, team_id), values in accepted.items()
            if m is model and team_id in previous
        ]
        if len(compared) >= 10 and all(old == new and any(v is not None for v in new) for old, new in compared):
            flags.append(
                f"stale: {', '.join(columns)} identical to as_of_week {previous_week} "
                f"for all {len(compared)} teams; the source may not have updated"
            )
    return flags


def _load(session, csv_path, table_key, spec, rows, season, sidecar) -> ImportResult:
    lookup = TeamLookup(session)
    result = ImportResult(table_key, csv_path.name, "loaded", rows_read=len(rows))

    parsed: dict[int, dict] = {}
    for line, row in rows:
        try:
            parsed[line] = _parse_row(row, spec, lookup)
        except RowError as exc:
            result.rejected.append(f"line {line}: {exc}")

    # A team appearing twice can't be resolved by guessing, so every copy is refused.
    lines_by_key: dict[tuple, list[int]] = {}
    for line, updates in parsed.items():
        for key in updates:
            lines_by_key.setdefault(key, []).append(line)
    team_names = dict(session.execute(select(Team.id, Team.name)).all())
    duplicate_lines = set()
    for (model, team_id), lines in lines_by_key.items():
        if len(lines) > 1:
            duplicate_lines.update(lines)
            result.rejected.append(
                f"lines {', '.join(map(str, lines))}: duplicate team {team_names[team_id]}"
            )
    accepted = {
        key: values
        for line, updates in parsed.items()
        if line not in duplicate_lines
        for key, values in updates.items()
    }
    result.rows_loaded = len(parsed) - len(duplicate_lines)
    if result.rows_loaded == 0:
        raise ImportRejected("no usable rows: " + "; ".join(result.rejected))

    result.flags.extend(_stale_flags(session, spec, accepted, season, sidecar.as_of_week))

    for (model, team_id), values in accepted.items():
        row = session.scalar(
            select(model).where(
                model.team_id == team_id,
                model.season == season,
                model.as_of_week == sidecar.as_of_week,
            )
        )
        if row is None:
            row = model(team_id=team_id, season=season, as_of_week=sidecar.as_of_week)
            session.add(row)
        for name, (value, tolerance) in values.items():
            if value is None:
                continue  # a blank cell never erases a value another import filled
            old = getattr(row, name)
            if old is not None and abs(old - value) > tolerance:
                result.flags.append(
                    f"disagreement: {team_names[team_id]} {name} was {old:g}, "
                    f"{table_key} says {value:g}; kept {value:g}"
                )
            setattr(row, name, value)

    for model in {c.model for c in spec.columns.values()}:
        present = {team_id for (m, team_id) in accepted if m is model}
        missing = sorted(name for team_id, name in team_names.items() if team_id not in present)
        side = "offense" if model is TeamOffenseStats else "defense"
        if missing and spec.min_rows == 32:
            result.flags.append(f"missing teams ({side}): {', '.join(missing)}")
        elif missing:
            result.flags.append(f"{side} not in this table (bye or missing): {', '.join(missing)}")

    if result.rejected or result.rows_loaded < spec.min_rows:
        result.status = "partial"
    snapshot = _snapshot(season, sidecar, table_key, csv_path, result)
    session.add(snapshot)
    session.commit()
    result.snapshot_id = snapshot.id
    return result


def _snapshot(season, sidecar, table_key, csv_path, result) -> DataSnapshot:
    notes = result.flags + result.rejected + ([result.reason] if result.reason else [])
    return DataSnapshot(
        season=season,
        as_of_week=sidecar.as_of_week,
        target_week=sidecar.target_week,
        table_key=table_key,
        source_name=sidecar.source_name,
        source_url=sidecar.source_url,
        captured_at=sidecar.captured_at,
        file_name=csv_path.name,
        row_count=result.rows_loaded,
        status=result.status,
        notes="\n".join(notes) or None,
    )


def import_csv(session: Session, csv_path: Path, log_dir: Path | None = None) -> ImportResult:
    """Validate and load one CSV. Never raises for bad data; the result says what happened."""
    csv_path = Path(csv_path)
    table_key = csv_path.stem
    with logged_run("import", log_dir, table_key=table_key, file=str(csv_path)) as log:
        sidecar = season = None
        rows: list = []
        try:
            season, folder_week = folder_weeks(csv_path)
            sidecar = read_sidecar(csv_path, folder_week)
            # Imported here because game_importer builds on this module's helpers.
            from app.game_importer import GAME_TABLES

            if table_key in TABLES:
                spec = TABLES[table_key]
                rows = read_rows(csv_path, spec)
                result = _load(session, csv_path, table_key, spec, rows, season, sidecar)
            elif table_key in GAME_TABLES:
                spec = GAME_TABLES[table_key]
                rows = read_rows(csv_path, spec)
                result = spec.load(session, csv_path, table_key, rows, season, sidecar)
            else:
                raise ImportRejected(f"no importer for table_key {table_key!r} yet")
        except ImportRejected as exc:
            session.rollback()
            result = ImportResult(
                table_key, csv_path.name, "rejected", rows_read=len(rows), reason=str(exc)
            )
            if sidecar is not None:
                snapshot = _snapshot(season, sidecar, table_key, csv_path, result)
                session.add(snapshot)
                session.commit()
                result.snapshot_id = snapshot.id
        log_summary(log, result, season, sidecar)
    return result


def log_summary(log, result: ImportResult, season: int | None, sidecar: Sidecar | None) -> None:
    """The fields every import writes on its run_end log line."""
    log.summary.update(
        table_key=result.table_key,
        status=result.status,
        source=sidecar.source_name if sidecar else None,
        season=season,
        as_of_week=sidecar.as_of_week if sidecar else None,
        target_week=sidecar.target_week if sidecar else None,
        rows_read=result.rows_read,
        rows_loaded=result.rows_loaded,
        rows_rejected=result.rejected,
        flags=result.flags,
        reason=result.reason,
        **({"counts": result.counts} if result.counts else {}),
    )


def main() -> None:
    from app.db import SessionLocal

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", type=Path, help="CSV files or week folders")
    args = parser.parse_args()

    csv_paths = []
    for path in args.paths:
        csv_paths.extend(sorted(path.glob("*.csv")) if path.is_dir() else [path])

    with SessionLocal() as session:
        for csv_path in csv_paths:
            result = import_csv(session, csv_path)
            print(f"{result.table_key}: {result.status}, {result.rows_loaded}/{result.rows_read} rows")
            for line in result.rejected + result.flags + ([result.reason] if result.reason else []):
                print(f"  - {line}")


if __name__ == "__main__":
    main()
