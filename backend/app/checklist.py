"""Data checklist: for one season and target week, what each catalog table has loaded.

A model predicting week W reads data through week W-1, so each table is judged against
that. Screenshot and chat CSVs count when a snapshot targets week W; nflverse counts when
its latest snapshot runs through week W-1 or later; live lines count when every week W
game has one. Whether a screenshot table is still needed comes from the overlap test: a
table whose every stat reads nflverse is optional, kept as an audit copy.
"""

from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.features import feature_sources
from app.importer import TABLES
from app.models import DataSnapshot, Game, GameLine, TeamOffenseStats

router = APIRouter(prefix="/data", tags=["data"])

PHASE_4 = "needed once the prop and TD models start (Phase 4)"

# (table_key, label, path) per group; the order the tab shows them in.
TEAM_TABLES = [
    ("team_offense_metrics", "Offense EPA, yards, points per drive, explosive, conversions"),
    ("team_defense_metrics", "Defense EPA, yards, points per drive, explosive, conversions allowed"),
    ("offensive_tendencies", "Motion, play action, air yards, shotgun, no-huddle"),
    ("offensive_pace_volume", "Plays, drives and time of possession per game"),
    ("offensive_pace_tempo", "Play clock, neutral pass rate, gear change"),
    ("personnel_usage", "Personnel groupings"),
    ("offensive_line", "Pressure allowed, time to throw, yards before contact, stuffs"),
    ("defensive_line", "Pressure, yards before contact, stuffs"),
    ("defensive_tendencies", "Blitz, box counts, sub packages"),
    ("man_zone_coverage", "Man, zone, middle closed and open"),
    ("positional_coverage", "Yards per target allowed by position"),
    ("proe_matchups", "PROE for this week's matchups"),
    ("proe_offense_defense", "Season PROE, all 32 teams"),
]
OPTIONAL_TEAM_TABLES = {"proe_offense_defense"}


@dataclass
class Item:
    table_key: str
    label: str
    path: str
    required: bool
    status: str = "missing"
    detail: str | None = None
    snapshot: dict | None = None
    notes: list[str] = field(default_factory=list)

    def finish(self) -> dict:
        if self.status == "missing" and not self.required:
            self.status = "optional"
        return {
            "table_key": self.table_key, "label": self.label, "path": self.path,
            "required": self.required, "status": self.status,
            "detail": "; ".join(n for n in [self.detail, *self.notes] if n) or None,
            "snapshot": self.snapshot,
        }


def _snapshot_dict(s: DataSnapshot) -> dict:
    return {
        "id": s.id, "source_name": s.source_name, "captured_at": s.captured_at,
        "as_of_week": s.as_of_week, "target_week": s.target_week, "row_count": s.row_count,
        "status": s.status,
    }


def _latest(db: Session, season: int, table_key: str, target_week: int | None = None):
    query = select(DataSnapshot).where(DataSnapshot.season == season, DataSnapshot.table_key == table_key)
    if target_week is not None:
        query = query.where(DataSnapshot.target_week == target_week)
    return db.scalar(query.order_by(DataSnapshot.captured_at.desc(), DataSnapshot.id.desc()).limit(1))


def _from_snapshot(item: Item, snapshot: DataSnapshot | None) -> Item:
    if snapshot is not None:
        item.status = snapshot.status
        item.snapshot = _snapshot_dict(snapshot)
        item.detail = f"{snapshot.row_count} rows, {snapshot.source_name}"
    return item


def screenshot_item(db, season, week, table_key, label, required, path="A") -> Item:
    item = Item(table_key, label, path, required)
    return _from_snapshot(item, _latest(db, season, table_key, target_week=week))


def nflverse_item(db, season, week, table_key, label, required, path="C") -> Item:
    """Loaded when the latest nflverse snapshot runs through week-1; stale when it stops earlier."""
    item = Item(table_key, label, path, required)
    snapshot = _latest(db, season, table_key)
    if snapshot is None or snapshot.status == "rejected":
        return _from_snapshot(item, snapshot)
    _from_snapshot(item, snapshot)
    if snapshot.as_of_week < week - 1:
        item.status = "stale"
        item.detail = (f"runs through week {snapshot.as_of_week}, week {week} needs week {week - 1}; "
                       "download the updated nflverse files")
    else:
        item.detail = f"through week {snapshot.as_of_week}, {snapshot.row_count} rows"
    return item


def either_item(db, season, week, label, nflverse_key, fallback_key, required) -> Item:
    """An nflverse table with a chat-CSV fallback: whichever covers the week counts."""
    key = f"{nflverse_key} / {fallback_key}"
    primary = nflverse_item(db, season, week, nflverse_key, label, required, path="C (B fallback)")
    primary.table_key = key
    if primary.status in ("loaded", "partial"):
        return primary
    fallback = screenshot_item(db, season, week, fallback_key, label, required, path="C (B fallback)")
    if fallback.status in ("loaded", "partial"):
        fallback.table_key = key
        fallback.notes.append(f"from the {fallback_key} fallback; nflverse: {primary.status}")
        return fallback
    return primary


def live_lines_items(db: Session, season: int, week: int) -> list[Item]:
    """This week's spread and total, and moneylines: every game needs a pregame line."""
    games = db.scalars(select(Game.id).where(Game.season == season, Game.week == week)).all()
    spread_total = Item("game_lines (current spread, total)", "This week's spread and total",
                        "C or B", required=True)
    moneyline = Item("game_lines (current moneyline)", "This week's moneylines, both sides",
                     "A (sportsbook) or C", required=True)
    if not games:
        for item in (spread_total, moneyline):
            item.detail = f"no week {week} games loaded yet"
        return [spread_total, moneyline]
    lines = db.scalars(select(GameLine).where(GameLine.game_id.in_(games),
                                              GameLine.line_type.in_(["current", "closing"]))).all()
    priced = {l.game_id for l in lines if l.spread_home is not None and l.total is not None}
    with_moneyline = {l.game_id for l in lines if l.moneyline_home is not None and l.moneyline_away is not None}
    for item, have in ((spread_total, priced), (moneyline, with_moneyline)):
        if len(have) == len(games):
            item.status = "loaded"
        elif have:
            item.status = "partial"
        item.detail = f"{len(have)} of {len(games)} games"
    return [spread_total, moneyline]


def _screenshot_needed(table_key: str, sources: dict | None) -> tuple[bool, str | None]:
    """A screenshot table is needed while any of its stats reads the screenshot."""
    if table_key in OPTIONAL_TEAM_TABLES:
        return False, None
    if sources is None:
        return True, "no feature_sources.json yet, so every screenshot table counts"
    spec = TABLES[table_key]
    on_screenshot = []
    for column, c in spec.columns.items():
        side = "offense" if c.model is TeamOffenseStats else "defense"
        if sources.get(f"{side}.{column}", {}).get("source") != "nflverse":
            on_screenshot.append(column)
    if on_screenshot:
        return True, f"screenshot is the source for {', '.join(on_screenshot)}"
    return False, "every stat reads nflverse (overlap test); kept as an audit copy"


def checklist(db: Session, season: int, week: int) -> dict:
    try:
        sources = feature_sources()
    except FileNotFoundError:
        sources = None

    team_items = []
    for table_key, label in TEAM_TABLES:
        required, why = _screenshot_needed(table_key, sources)
        item = screenshot_item(db, season, week, table_key, label, required)
        if why:
            item.notes.append(why)
        team_items.append(item)
    team_items.insert(0, nflverse_item(db, season, week, "nflverse_team_week_stats",
                                       "Team stats built from nflverse play-by-play", required=True))

    games = [
        either_item(db, season, week, "Schedule for this week", "nflverse_schedules",
                    "weekly_matchups", required=True),
        either_item(db, season, week, "Final scores through last week", "nflverse_schedules",
                    "game_results", required=True),
        nflverse_item(db, season, week, "nflverse_game_team_stats", "Box scores per game", required=False),
        nflverse_item(db, season, week, "nflverse_scoring_plays", "Scoring plays", required=False),
    ]
    games[2].notes.append("not read by the v0 game model")
    games[3].notes.append(PHASE_4)
    # A schedule is for the coming week; the week W games must exist, not just older ones.
    week_games = db.scalar(select(func.count()).select_from(Game).where(Game.season == season, Game.week == week))
    if games[0].status in ("loaded", "partial") and not week_games:
        games[0].status = "missing"
        games[0].detail = f"no week {week} games in the schedule"

    betting = [
        nflverse_item(db, season, week, "nflverse_schedules", "Closing lines through last week", required=True),
        *live_lines_items(db, season, week),
        screenshot_item(db, season, week, "player_prop_lines", "Prop lines for tracked players", False),
        screenshot_item(db, season, week, "anytime_td_odds", "Anytime TD prices", False),
    ]
    betting[0].table_key = "game_lines (closing)"
    for item in betting[3:]:
        item.notes.append(PHASE_4)

    players = [
        nflverse_item(db, season, week, "nflverse_player_weekly_stats",
                      "Player usage, red zone and results", required=False),
        screenshot_item(db, season, week, "injuries", "Injuries", False, path="C"),
    ]
    players[0].notes.append(PHASE_4)

    groups = {
        "Team stats": team_items,
        "Schedule and results": games,
        "Betting lines": betting,
        "Player data": players,
    }
    finished = {name: [i.finish() for i in items] for name, items in groups.items()}
    every = [i for items in finished.values() for i in items]
    blocking = [i["table_key"] for i in every if i["required"] and i["status"] not in ("loaded", "partial")]
    counts: dict[str, int] = {}
    for i in every:
        counts[i["status"]] = counts.get(i["status"], 0) + 1
    return {
        "season": season,
        "week": week,
        # Data a model may read for this week runs through the week before.
        "data_through_week": week - 1,
        "feature_sources": "loaded" if sources is not None else "missing",
        "ready_for_game_model": not blocking,
        "blocking": blocking,
        "counts": counts,
        "groups": [{"name": name, "items": items} for name, items in finished.items()],
    }


@router.get("/checklist")
def data_checklist(
    season: int = Query(..., ge=2000),
    week: int = Query(..., ge=1, le=22),
    db: Session = Depends(get_db),
):
    """Each catalog table as loaded, partial, missing, stale, rejected or optional for one week."""
    return checklist(db, season, week)
