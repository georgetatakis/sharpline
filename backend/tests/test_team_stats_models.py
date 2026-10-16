from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Base, DataSnapshot, Team, TeamDefenseStats, TeamOffenseStats
from app.seed import seed_teams


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed_teams(session)
        yield session


def team_id(session, abbreviation):
    return session.scalar(select(Team.id).where(Team.abbreviation == abbreviation))


def snapshot(**overrides):
    fields = {
        "season": 2026, "as_of_week": 5, "target_week": 6,
        "table_key": "team_offense_metrics", "source_name": "charting site",
        "source_url": "screenshot", "captured_at": datetime(2026, 10, 14, tzinfo=UTC),
        "file_name": "team_offense_metrics.csv", "row_count": 32, "status": "loaded",
    }
    fields.update(overrides)
    return DataSnapshot(**fields)


def test_snapshot_saves_with_optional_notes_empty(session):
    session.add(snapshot())
    session.commit()

    saved = session.scalar(select(DataSnapshot))
    assert saved.status == "loaded" and saved.notes is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "done"},
        {"as_of_week": 6, "target_week": 6},  # must predict a later week
        {"as_of_week": -1, "target_week": 1},
    ],
)
def test_snapshot_rejects_invalid_values(session, overrides):
    session.add(snapshot(**overrides))
    with pytest.raises(IntegrityError):
        session.commit()


@pytest.mark.parametrize("model", [TeamOffenseStats, TeamDefenseStats])
def test_one_row_per_team_season_as_of_week(session, model):
    kc = team_id(session, "KC")
    session.add(model(team_id=kc, season=2026, as_of_week=5))
    session.commit()

    session.add(model(team_id=kc, season=2026, as_of_week=5))
    with pytest.raises(IntegrityError):
        session.commit()


@pytest.mark.parametrize("model", [TeamOffenseStats, TeamDefenseStats])
def test_new_as_of_week_is_a_new_row_not_an_overwrite(session, model):
    kc = team_id(session, "KC")
    session.add_all([
        model(team_id=kc, season=2026, as_of_week=5),
        model(team_id=kc, season=2026, as_of_week=6),
    ])
    session.commit()

    weeks = session.scalars(select(model.as_of_week).order_by(model.as_of_week)).all()
    assert weeks == [5, 6]


def test_metrics_default_to_null_so_imports_fill_their_own_columns(session):
    session.add(TeamOffenseStats(team_id=team_id(session, "KC"), season=2026, as_of_week=5, epa_per_play=0.15))
    session.commit()

    row = session.scalar(select(TeamOffenseStats))
    assert row.epa_per_play == 0.15
    assert row.motion_rate is None and row.pers_11 is None
