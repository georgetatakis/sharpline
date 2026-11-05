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
    false,
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
    # nflverse's gsis id ("00-0033873") and Pro Football Reference's id ("ButkHa00"),
    # which snap counts use.
    nflverse_id: Mapped[str | None] = mapped_column(String(16), unique=True)
    pfr_id: Mapped[str | None] = mapped_column(String(16))


class Game(Base):
    __tablename__ = "games"
    __table_args__ = (
        CheckConstraint("home_team_id <> away_team_id", name="distinct_teams"),
        CheckConstraint("week >= 1", name="week_positive"),
        CheckConstraint("status IN ('scheduled', 'final')", name="status_valid"),
        CheckConstraint(
            "home_score >= 0 AND away_score >= 0", name="scores_nonnegative"
        ),
        CheckConstraint(
            "status <> 'final' OR (home_score IS NOT NULL AND away_score IS NOT NULL)",
            name="final_has_scores",
        ),
        CheckConstraint(
            "game_type IN ('REG', 'WC', 'DIV', 'CON', 'SB')", name="game_type_valid"
        ),
        CheckConstraint(
            "roof IN ('outdoors', 'dome', 'closed', 'open')", name="roof_valid"
        ),
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
    # Nullable so a final result can be loaded even if the schedule never was.
    kickoff_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        String(9), default="scheduled", server_default="scheduled"
    )
    # Only final scores are kept from a game; a tie is a final with equal scores.
    home_score: Mapped[int | None]
    away_score: Mapped[int | None]
    # nflverse's id ("2026_06_KC_LV"); links a game to its play-by-play.
    nflverse_game_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    # REG, or a playoff round: WC, DIV, CON, SB.
    game_type: Mapped[str] = mapped_column(String(3), default="REG", server_default="REG")
    neutral_site: Mapped[bool] = mapped_column(default=False, server_default=false())
    # Manual flags set before kickoff when a team's starting QB is out.
    home_qb_out: Mapped[bool] = mapped_column(default=False, server_default=false())
    away_qb_out: Mapped[bool] = mapped_column(default=False, server_default=false())
    roof: Mapped[str | None] = mapped_column(String(8))
    # Fahrenheit and mph at kickoff; empty for domes and closed roofs.
    temp: Mapped[float | None]
    wind: Mapped[float | None]




class GameTeamStats(Base):
    """One team's box score for one game, built from nflverse play-by-play."""

    __tablename__ = "game_team_stats"
    __table_args__ = (UniqueConstraint("game_id", "team_id", name="uq_game_team_stats_row"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    plays: Mapped[int]
    total_yards: Mapped[int]
    # Minutes.
    time_of_possession: Mapped[float | None]
    rush_attempts: Mapped[int]
    rush_yards: Mapped[int]
    pass_attempts: Mapped[int]
    pass_completions: Mapped[int]
    pass_yards: Mapped[int]
    air_yards: Mapped[int]
    sacks_taken: Mapped[int]
    turnovers: Mapped[int]
    penalties: Mapped[int]
    penalty_yards: Mapped[int]
    redzone_rush_attempts: Mapped[int]
    redzone_targets: Mapped[int]
    # Participation data; empty when that season's file isn't loaded.
    blitzes_faced: Mapped[int | None]
    avg_pocket_time: Mapped[float | None]


class PlayerWeeklyStats(Base):
    """One player's line for one week: usage and results, from nflverse."""

    __tablename__ = "player_weekly_stats"
    __table_args__ = (
        UniqueConstraint("player_id", "season", "week", name="uq_player_weekly_stats_row"),
        Index("ix_player_weekly_stats_season_week", "season", "week"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    season: Mapped[int]
    week: Mapped[int]
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    # Offensive snaps; empty when the player isn't matched in snap counts.
    snaps: Mapped[int | None]
    snap_share: Mapped[float | None]
    targets: Mapped[int]
    target_share: Mapped[float | None]
    carries: Mapped[int]
    # Inside the opponent's 20: carries plus receptions, and targets. Goal line is inside the 5.
    red_zone_touches: Mapped[int]
    red_zone_targets: Mapped[int]
    goal_line_carries: Mapped[int]
    air_yards: Mapped[int]
    receiving_yards: Mapped[int]
    rushing_yards: Mapped[int]
    receptions: Mapped[int]
    # Touchdowns the player scored (rushing, receiving, returns); passing TDs don't count.
    tds: Mapped[int]

class GameLine(Base):
    """One price for one game: the spread, total and moneylines a source showed.

    Raw American prices only; de-vigging happens when a model reads them. spread_home
    is the home team's spread as a sportsbook prints it, so -3.5 means home favored.
    """

    __tablename__ = "game_lines"
    __table_args__ = (
        CheckConstraint(
            "line_type IN ('open', 'current', 'closing')", name="line_type_valid"
        ),
        UniqueConstraint(
            "game_id", "line_type", "source_name", "captured_at", name="uq_game_lines_row"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    line_type: Mapped[str] = mapped_column(String(8))
    spread_home: Mapped[float | None]
    spread_price_home: Mapped[int | None]
    spread_price_away: Mapped[int | None]
    total: Mapped[float | None]
    over_price: Mapped[int | None]
    under_price: Mapped[int | None]
    moneyline_home: Mapped[int | None]
    moneyline_away: Mapped[int | None]
    # A closing line is captured at kickoff.
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_name: Mapped[str] = mapped_column(String(128))
    book_label: Mapped[str | None] = mapped_column(String(64))


class GameScoringPlay(Base):
    """One scoring play of a final game. Settles anytime-TD picks."""

    __tablename__ = "game_scoring_plays"
    __table_args__ = (
        CheckConstraint(
            "play_type IN ('td_pass', 'td_rush', 'td_other', 'fg', 'xp', 'two_point', 'other')",
            name="play_type_valid",
        ),
        CheckConstraint("points > 0", name="points_positive"),
        UniqueConstraint(
            "game_id", "nflverse_play_id", "play_type", name="uq_game_scoring_plays_row"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    nflverse_play_id: Mapped[int]
    period: Mapped[int]
    # Game clock left in the period, MM:SS.
    clock: Mapped[str | None] = mapped_column(String(5))
    description: Mapped[str | None] = mapped_column(Text)
    scoring_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    # td_other is a return or recovery TD; other is a safety or defensive conversion.
    play_type: Mapped[str] = mapped_column(String(9))
    points: Mapped[int]
    scorer_name: Mapped[str | None] = mapped_column(String(128))
    scorer_nflverse_id: Mapped[str | None] = mapped_column(String(16))
    # Filled once players are loaded (step 13).
    scorer_player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id"))

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
            "team_id", "season", "as_of_week", "source", name="uq_team_offense_stats_row"
        ),
        CheckConstraint("as_of_week >= 0", name="as_of_week_nonnegative"),
        CheckConstraint("source IN ('screenshot', 'nflverse')", name="source_valid"),
        Index("ix_team_offense_stats_season_as_of_week", "season", "as_of_week"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    season: Mapped[int]
    as_of_week: Mapped[int]
    # Screenshot rows come from the importer; nflverse rows are built from play-by-play.
    # Which one a model reads is decided per stat by the overlap test (feature_sources).
    source: Mapped[str] = mapped_column(
        String(10), default="screenshot", server_default="screenshot"
    )
    # nflverse only: the sample behind the rates.
    games_played: Mapped[int | None]
    plays: Mapped[int | None]
    success_rate: Mapped[float | None]

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
            "team_id", "season", "as_of_week", "source", name="uq_team_defense_stats_row"
        ),
        CheckConstraint("as_of_week >= 0", name="as_of_week_nonnegative"),
        CheckConstraint("source IN ('screenshot', 'nflverse')", name="source_valid"),
        Index("ix_team_defense_stats_season_as_of_week", "season", "as_of_week"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    season: Mapped[int]
    as_of_week: Mapped[int]
    # Screenshot rows come from the importer; nflverse rows are built from play-by-play.
    # Which one a model reads is decided per stat by the overlap test (feature_sources).
    source: Mapped[str] = mapped_column(
        String(10), default="screenshot", server_default="screenshot"
    )
    # nflverse only: the sample behind the rates.
    games_played: Mapped[int | None]
    plays: Mapped[int | None]
    success_rate_allowed: Mapped[float | None]

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
