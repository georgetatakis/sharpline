"""What a model may read: team stats from before the week it predicts, never that week or
later, and each stat from the one source the overlap test chose for it.

Every model reads team stats through here, so both rules live in one place.
"""

import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import TeamDefenseStats, TeamOffenseStats
from app.overlap import CONFIG_PATH


def team_stats_before(session: Session, model: type[TeamOffenseStats] | type[TeamDefenseStats],
                      season: int, target_week: int, source: str) -> list:
    """Each team's latest row with as_of_week strictly before target_week, from one source.

    Predicting week 7 reads the week 6 rows (or older, if a source skipped a week). Week 1
    reads nothing from its own season.
    """
    latest = (
        select(model.team_id, func.max(model.as_of_week).label("as_of_week"))
        .where(model.season == season, model.source == source, model.as_of_week < target_week)
        .group_by(model.team_id)
        .subquery()
    )
    return list(session.scalars(
        select(model)
        .join(latest, (model.team_id == latest.c.team_id) & (model.as_of_week == latest.c.as_of_week))
        .where(model.season == season, model.source == source)
        .order_by(model.team_id)
    ))


def feature_sources(path: Path | None = None) -> dict[str, dict]:
    """The committed overlap-test decisions, keyed "offense.epa_per_play" and so on."""
    path = path or CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(f"{path} is missing; run python -m app.overlap --write first")
    return json.loads(path.read_text())["stats"]


def feature_source(side: str, stat: str, sources: dict[str, dict] | None = None) -> str:
    """Which source a model reads this stat from: 'nflverse' or 'screenshot'."""
    sources = sources if sources is not None else feature_sources()
    key = f"{side}.{stat}"
    if key not in sources or sources[key]["source"] is None:
        raise KeyError(f"{key} has no source in feature_sources.json")
    return sources[key]["source"]
