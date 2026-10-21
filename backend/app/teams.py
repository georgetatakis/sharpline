"""Team stats endpoints: one team's offense or defense snapshots, newest first."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Team, TeamDefenseStats, TeamOffenseStats

router = APIRouter(prefix="/teams", tags=["teams"])

# Keys that identify a row rather than describe the team's play.
_KEY_COLUMNS = {"id", "team_id"}


def _team_or_404(db: Session, team_id: int) -> Team:
    team = db.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail=f"No team with id {team_id}")
    return team


def _snapshots(db: Session, team_id: int, model, season: int | None, as_of_week: int | None):
    team = _team_or_404(db, team_id)
    query = select(model).where(model.team_id == team_id)
    if season is not None:
        query = query.where(model.season == season)
    if as_of_week is not None:
        query = query.where(model.as_of_week == as_of_week)
    query = query.order_by(model.season.desc(), model.as_of_week.desc())

    columns = [c.name for c in model.__table__.columns if c.name not in _KEY_COLUMNS]
    return {
        "team": {"id": team.id, "name": team.name, "abbreviation": team.abbreviation},
        # Rates are fractions (0.421 = 42.1%); a null means no import has filled it yet.
        "snapshots": [{c: getattr(row, c) for c in columns} for row in db.scalars(query)],
    }


@router.get("/{team_id}/offense")
def team_offense(
    team_id: int,
    season: int | None = Query(None, ge=2000),
    as_of_week: int | None = Query(None, ge=0, le=22),
    db: Session = Depends(get_db),
):
    """Offense stats snapshots for one team, optionally for one season and as-of week."""
    return _snapshots(db, team_id, TeamOffenseStats, season, as_of_week)


@router.get("/{team_id}/defense")
def team_defense(
    team_id: int,
    season: int | None = Query(None, ge=2000),
    as_of_week: int | None = Query(None, ge=0, le=22),
    db: Session = Depends(get_db),
):
    """Defense stats snapshots for one team, optionally for one season and as-of week."""
    return _snapshots(db, team_id, TeamDefenseStats, season, as_of_week)
