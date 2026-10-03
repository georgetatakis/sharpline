from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    MetaData,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Explicit constraint names keep Alembic migrations deterministic across databases.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (
        CheckConstraint("conference IN ('AFC', 'NFC')", name="conference_valid"),
        CheckConstraint(
            "division IN ('East', 'North', 'South', 'West')", name="division_valid"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    # nflverse abbreviations (e.g. LA, LV, WAS) so nfl_data_py rows join on this directly.
    abbreviation: Mapped[str] = mapped_column(String(4), unique=True)
    conference: Mapped[str] = mapped_column(String(3))
    division: Mapped[str] = mapped_column(String(5))


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    # Nullable so free agents and retired players can still be stored.
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), index=True)
    position: Mapped[str] = mapped_column(String(8))


class Game(Base):
    __tablename__ = "games"
    __table_args__ = (
        CheckConstraint("home_team_id <> away_team_id", name="distinct_teams"),
        CheckConstraint("week >= 1", name="week_positive"),
        UniqueConstraint(
            "season", "week", "home_team_id", name="uq_games_season_week_home_team"
        ),
        Index("ix_games_season_week", "season", "week"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int]
    week: Mapped[int]
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    kickoff_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
