"""What a model may read: team stats from before the week it predicts, never that week or later.

Every model reads team stats through here, so the as_of_week rule lives in one place.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import TeamDefenseStats, TeamOffenseStats


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
