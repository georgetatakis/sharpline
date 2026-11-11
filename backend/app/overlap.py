"""Overlap test: does each team stat built from nflverse match the screenshot version?

For one season and as_of_week, pairs every team's screenshot row with its nflverse row
and, per stat, measures the correlation across teams and the average gap. A stat whose
correlation is above 0.9 reads nflverse; every other stat stays on the screenshot. The
decision goes into the committed config/feature_sources.json, which models read through
app.features.feature_source, so no stat is ever read from both sources interchangeably.

Run with:  python -m app.overlap --season 2026 --as-of-week 5 \\
               --personnel-csv data/extracted/season-2026/week-06/personnel_usage.csv
           (add --write to save config/feature_sources.json)

Both sources must be loaded first: the Week 6 screenshot CSVs through app.importer, and
nflverse through app.nflverse_loader and app.nflverse_build.
"""

import argparse
import csv
import json
import math
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import TeamDefenseStats, TeamOffenseStats
from app.run_log import logged_run

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "feature_sources.json"
THRESHOLD = 0.9
# Fewer paired teams than this and a correlation means little.
MIN_TEAMS = 20

SIDES = {"offense": TeamOffenseStats, "defense": TeamDefenseStats}
# Columns that identify a row or describe its sample rather than measure the team.
NOT_STATS = {"id", "team_id", "season", "as_of_week", "source", "games_played", "plays"}

# Stats the builder takes from participation data; a season without that file can't test them.
PARTICIPATION_STATS = {
    "time_to_throw", "ol_pressure_rate_allowed", "ol_no_blitz_pressure_allowed",
    "pers_11", "pers_12", "pers_13", "pers_21", "pers_22", "pers_2te", "pers_2rb", "pers_3wr",
    "blitz_rate", "light_box_rate", "heavy_box_rate", "sub_package_rate", "dl_pressure_rate",
    "dl_no_blitz_pressure_rate", "man_rate", "zone_rate", "middle_closed_rate",
    "middle_open_rate",
}


def stat_columns(model) -> list[str]:
    return [c.name for c in model.__table__.columns if c.name not in NOT_STATS]


def correlation(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation, or None when either side doesn't vary."""
    n = len(xs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    syy = sum((y - mean_y) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


def _rows(session: Session, model, season: int, as_of_week: int, source: str) -> dict[int, object]:
    return {
        row.team_id: row
        for row in session.scalars(select(model).where(
            model.season == season, model.as_of_week == as_of_week, model.source == source))
    }


def compare(session: Session, season: int, as_of_week: int) -> list[dict]:
    """One result per stat: the paired teams, correlation, gaps and the source it should read."""
    results = []
    for side, model in SIDES.items():
        screenshot = _rows(session, model, season, as_of_week, "screenshot")
        nflverse = _rows(session, model, season, as_of_week, "nflverse")
        for stat in stat_columns(model):
            pairs = [
                (getattr(screenshot[t], stat), getattr(nflverse[t], stat))
                for t in screenshot.keys() & nflverse.keys()
                if getattr(screenshot[t], stat) is not None and getattr(nflverse[t], stat) is not None
            ]
            has_screenshot = any(getattr(r, stat) is not None for r in screenshot.values())
            has_nflverse = any(getattr(r, stat) is not None for r in nflverse.values())
            result = {"stat": stat, "side": side, "teams": len(pairs), "correlation": None,
                      "mean_abs_gap": None, "mean_gap": None}
            if pairs:
                xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
                result["mean_abs_gap"] = sum(abs(y - x) for x, y in pairs) / len(pairs)
                # nflverse minus screenshot: positive means nflverse reads higher.
                result["mean_gap"] = sum(y - x for x, y in pairs) / len(pairs)
                if len(pairs) >= MIN_TEAMS:
                    result["correlation"] = correlation(xs, ys)
            result.update(_decide(stat, result, has_screenshot, has_nflverse))
            results.append(result)
    return results


def _decide(stat: str, result: dict, has_screenshot: bool, has_nflverse: bool) -> dict:
    """{"source", "status", "reason"} for one stat."""
    r = result["correlation"]
    if not has_screenshot and not has_nflverse:
        return {"source": None, "status": "no_data", "reason": "neither source has it yet"}
    if not has_nflverse:
        reason = ("nflverse participation data for this season isn't published yet"
                  if stat in PARTICIPATION_STATS else "not built from nflverse")
        return {"source": "screenshot", "status": "screenshot_only", "reason": reason}
    if not has_screenshot:
        return {"source": "nflverse", "status": "nflverse_only", "reason": "no screenshot of it"}
    if r is None:
        return {"source": "screenshot", "status": "untested",
                "reason": f"only {result['teams']} paired teams, or a source with no spread"
                if result["teams"] < MIN_TEAMS else "a source has the same value for every team"}
    if r > THRESHOLD:
        return {"source": "nflverse", "status": "pass", "reason": f"correlation {r:.3f} > {THRESHOLD}"}
    return {"source": "screenshot", "status": "fail", "reason": f"correlation {r:.3f} <= {THRESHOLD}"}


def charted_plays_checksum(session: Session, season: int, as_of_week: int,
                           personnel_csv: Path | None) -> dict | None:
    """The screenshot's charted plays (personnel_usage, all teams) next to nflverse's offensive plays."""
    if personnel_csv is None:
        return None
    with personnel_csv.open(newline="", encoding="utf-8-sig") as f:
        charted = sum(int(row["plays"].replace(",", "")) for row in csv.DictReader(f) if row["plays"].strip())
    rows = _rows(session, TeamOffenseStats, season, as_of_week, "nflverse").values()
    nflverse = sum(r.plays or 0 for r in rows)
    return {
        "charted_plays": charted,
        "nflverse_plays": nflverse,
        "ratio": nflverse / charted if charted else None,
    }


def config(results: list[dict], season: int, as_of_week: int, checksum: dict | None) -> dict:
    """The feature_sources config: one entry per stat, plus how it was decided."""
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "season": season,
        "as_of_week": as_of_week,
        "threshold": THRESHOLD,
        "min_teams": MIN_TEAMS,
        "charted_plays_checksum": checksum,
        "stats": {
            f"{r['side']}.{r['stat']}": {
                "source": r["source"], "status": r["status"], "reason": r["reason"],
                "teams": r["teams"],
                "correlation": None if r["correlation"] is None else round(r["correlation"], 4),
                "mean_abs_gap": None if r["mean_abs_gap"] is None else round(r["mean_abs_gap"], 4),
                "mean_gap": None if r["mean_gap"] is None else round(r["mean_gap"], 4),
            }
            for r in results
        },
    }


def run(session: Session, season: int, as_of_week: int, personnel_csv: Path | None = None,
        write_to: Path | None = None, log_dir: Path | None = None) -> dict:
    with logged_run("overlap", log_dir, season=season, as_of_week=as_of_week) as log:
        results = compare(session, season, as_of_week)
        tested = [r for r in results if r["status"] in ("pass", "fail")]
        if not tested:
            raise RuntimeError(
                f"no stat has both sources for {season} as_of_week {as_of_week}; "
                "load the screenshots and build nflverse first")
        checksum = charted_plays_checksum(session, season, as_of_week, personnel_csv)
        out = config(results, season, as_of_week, checksum)
        if write_to is not None:
            write_to.parent.mkdir(parents=True, exist_ok=True)
            write_to.write_text(json.dumps(out, indent=2) + "\n")
        counts = {}
        for r in results:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        log.summary.update(counts=counts, checksum=checksum, written=str(write_to) if write_to else None,
                           failed=[f"{r['side']}.{r['stat']}" for r in results if r["status"] == "fail"])
    return out


def report(out: dict) -> str:
    lines = [f"Overlap test, {out['season']} through week {out['as_of_week']} "
             f"(nflverse if correlation > {out['threshold']})", ""]
    lines.append(f"{'stat':<42}{'teams':>6}{'corr':>8}{'avg gap':>10}{'bias':>9}  decision")
    order = {"pass": 0, "fail": 1, "untested": 2, "screenshot_only": 3, "nflverse_only": 4, "no_data": 5}
    for key, s in sorted(out["stats"].items(), key=lambda kv: (order[kv[1]["status"]], kv[0])):
        fmt = lambda v, w, p: f"{v:>{w}.{p}f}" if v is not None else f"{'-':>{w}}"
        lines.append(f"{key:<42}{s['teams']:>6}{fmt(s['correlation'], 8, 3)}"
                     f"{fmt(s['mean_abs_gap'], 10, 3)}{fmt(s['mean_gap'], 9, 3)}  "
                     f"{s['status']}: {s['source'] or '-'} ({s['reason']})")
    if c := out["charted_plays_checksum"]:
        lines += ["", f"Charted plays: screenshot {c['charted_plays']:,}, nflverse {c['nflverse_plays']:,}"
                      f" ({c['ratio']:.3f}x)"]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--as-of-week", type=int, required=True)
    parser.add_argument("--personnel-csv", type=Path, help="the screenshot personnel_usage.csv, for the plays checksum")
    parser.add_argument("--write", action="store_true", help=f"save {CONFIG_PATH.relative_to(CONFIG_PATH.parents[1])}")
    args = parser.parse_args()

    from app.db import SessionLocal

    with SessionLocal() as session:
        out = run(session, args.season, args.as_of_week, args.personnel_csv,
                  CONFIG_PATH if args.write else None)
    print(report(out))
    if args.write:
        print(f"\nwrote {CONFIG_PATH}")


if __name__ == "__main__":
    main()
