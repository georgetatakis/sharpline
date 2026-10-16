from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Index,
    MetaData,
    String,
    Text,
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
    # Pinned so the column type doesn't change between SQLAlchemy versions.
    type_annotation_map = {float: Double}


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
    # Common feed abbreviations (e.g. LA, LV, WAS). The importer also accepts variants like LAR.
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


class DataSnapshot(Base):
    """One import of one CSV: what was loaded, from where, and for which weeks.

    A model predicting week N may only read data whose as_of_week is below N.
    """

    __tablename__ = "data_snapshots"
    __table_args__ = (
        CheckConstraint(
            "status IN ('loaded', 'partial', 'rejected')", name="status_valid"
        ),
        CheckConstraint("as_of_week >= 0", name="as_of_week_nonnegative"),
        CheckConstraint("target_week > as_of_week", name="target_after_as_of"),
        Index("ix_data_snapshots_season_table_key", "season", "table_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int]
    as_of_week: Mapped[int]
    target_week: Mapped[int]
    table_key: Mapped[str] = mapped_column(String(64))
    source_name: Mapped[str] = mapped_column(String(128))
    # A page URL for chat-pulled CSVs, or the literal "screenshot".
    source_url: Mapped[str] = mapped_column(String(512))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    file_name: Mapped[str] = mapped_column(String(256))
    row_count: Mapped[int]
    status: Mapped[str] = mapped_column(String(8))
    notes: Mapped[str | None] = mapped_column(Text)


# Team stats tables are wide: one row per team + season + as_of_week, and each
# table_key import fills its own columns. Rates are stored as fractions (42.1% -> 0.421).


class TeamOffenseStats(Base):
    __tablename__ = "team_offense_stats"
    __table_args__ = (
        UniqueConstraint(
            "team_id", "season", "as_of_week", name="uq_team_offense_stats_row"
        ),
        CheckConstraint("as_of_week >= 0", name="as_of_week_nonnegative"),
        Index("ix_team_offense_stats_season_as_of_week", "season", "as_of_week"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    season: Mapped[int]
    as_of_week: Mapped[int]

    # team_offense_metrics
    epa_per_play: Mapped[float | None]
    yards_per_play: Mapped[float | None]
    points_per_drive: Mapped[float | None]
    explosive_play_rate: Mapped[float | None]
    down_conversion_rate: Mapped[float | None]
    # offensive_tendencies
    motion_rate: Mapped[float | None]
    play_action_rate: Mapped[float | None]
    air_yards_per_att: Mapped[float | None]
    shotgun_rate: Mapped[float | None]
    no_huddle_rate: Mapped[float | None]
    # offensive_pace_volume
    plays_per_game: Mapped[float | None]
    adj_plays_per_game: Mapped[float | None]
    plays_per_drive: Mapped[float | None]
    drives_per_game: Mapped[float | None]
    time_of_possession: Mapped[float | None]
    # offensive_pace_tempo
    play_clock_used: Mapped[float | None]
    neutral_play_clock: Mapped[float | None]
    neutral_pass_rate: Mapped[float | None]
    gear_change: Mapped[float | None]
    # personnel_usage
    pers_11: Mapped[float | None]
    pers_12: Mapped[float | None]
    pers_13: Mapped[float | None]
    pers_21: Mapped[float | None]
    pers_22: Mapped[float | None]
    pers_2te: Mapped[float | None]
    pers_2rb: Mapped[float | None]
    pers_3wr: Mapped[float | None]
    # offensive_line
    ol_pressure_rate_allowed: Mapped[float | None]
    ol_no_blitz_pressure_allowed: Mapped[float | None]
    time_to_throw: Mapped[float | None]
    ol_ybc_per_rb_rush: Mapped[float | None]
    ol_rush_stuff_rate: Mapped[float | None]
    # proe_matchups / proe_offense_defense
    off_proe: Mapped[float | None]
    # derived from box scores
    rz_td_rate: Mapped[float | None]


class TeamDefenseStats(Base):
    __tablename__ = "team_defense_stats"
    __table_args__ = (
        UniqueConstraint(
            "team_id", "season", "as_of_week", name="uq_team_defense_stats_row"
        ),
        CheckConstraint("as_of_week >= 0", name="as_of_week_nonnegative"),
        Index("ix_team_defense_stats_season_as_of_week", "season", "as_of_week"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    season: Mapped[int]
    as_of_week: Mapped[int]

    # team_defense_metrics
    epa_per_play_allowed: Mapped[float | None]
    yards_per_play_allowed: Mapped[float | None]
    points_per_drive_allowed: Mapped[float | None]
    explosive_play_rate_allowed: Mapped[float | None]
    down_conversion_rate_allowed: Mapped[float | None]
    # defensive_tendencies
    blitz_rate: Mapped[float | None]
    light_box_rate: Mapped[float | None]
    heavy_box_rate: Mapped[float | None]
    sub_package_rate: Mapped[float | None]
    # defensive_line
    dl_pressure_rate: Mapped[float | None]
    dl_no_blitz_pressure_rate: Mapped[float | None]
    dl_ybc_per_rb_rush: Mapped[float | None]
    dl_rush_stuff_rate: Mapped[float | None]
    # man_zone_coverage
    man_rate: Mapped[float | None]
    zone_rate: Mapped[float | None]
    middle_closed_rate: Mapped[float | None]
    middle_open_rate: Mapped[float | None]
    # positional_coverage
    ypt_allowed_wr: Mapped[float | None]
    ypt_allowed_te: Mapped[float | None]
    ypt_allowed_rb: Mapped[float | None]
    ypt_allowed_outside: Mapped[float | None]
    ypt_allowed_slot: Mapped[float | None]
    # proe_matchups / proe_offense_defense
    def_proe: Mapped[float | None]
    # derived from box scores
    rz_td_rate_allowed: Mapped[float | None]
