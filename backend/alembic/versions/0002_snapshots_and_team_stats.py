"""data_snapshots, team_offense_stats, team_defense_stats

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-16
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OFFENSE_METRICS = [
    "epa_per_play", "yards_per_play", "points_per_drive", "explosive_play_rate",
    "down_conversion_rate",
    "motion_rate", "play_action_rate", "air_yards_per_att", "shotgun_rate",
    "no_huddle_rate",
    "plays_per_game", "adj_plays_per_game", "plays_per_drive", "drives_per_game",
    "time_of_possession",
    "play_clock_used", "neutral_play_clock", "neutral_pass_rate", "gear_change",
    "pers_11", "pers_12", "pers_13", "pers_21", "pers_22", "pers_2te", "pers_2rb",
    "pers_3wr",
    "ol_pressure_rate_allowed", "ol_no_blitz_pressure_allowed", "time_to_throw",
    "ol_ybc_per_rb_rush", "ol_rush_stuff_rate",
    "off_proe", "rz_td_rate",
]

DEFENSE_METRICS = [
    "epa_per_play_allowed", "yards_per_play_allowed", "points_per_drive_allowed",
    "explosive_play_rate_allowed", "down_conversion_rate_allowed",
    "blitz_rate", "light_box_rate", "heavy_box_rate", "sub_package_rate",
    "dl_pressure_rate", "dl_no_blitz_pressure_rate", "dl_ybc_per_rb_rush",
    "dl_rush_stuff_rate",
    "man_rate", "zone_rate", "middle_closed_rate", "middle_open_rate",
    "ypt_allowed_wr", "ypt_allowed_te", "ypt_allowed_rb", "ypt_allowed_outside",
    "ypt_allowed_slot",
    "def_proe", "rz_td_rate_allowed",
]


def _team_stats_table(name: str, metrics: list[str]) -> None:
    op.create_table(
        name,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("as_of_week", sa.Integer(), nullable=False),
        *[sa.Column(metric, sa.Double(), nullable=True) for metric in metrics],
        sa.CheckConstraint(
            "as_of_week >= 0", name=op.f(f"ck_{name}_as_of_week_nonnegative")
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name=op.f(f"fk_{name}_team_id_teams")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{name}")),
        sa.UniqueConstraint("team_id", "season", "as_of_week", name=f"uq_{name}_row"),
    )
    op.create_index(f"ix_{name}_season_as_of_week", name, ["season", "as_of_week"])


def upgrade() -> None:
    op.create_table(
        "data_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("as_of_week", sa.Integer(), nullable=False),
        sa.Column("target_week", sa.Integer(), nullable=False),
        sa.Column("table_key", sa.String(length=64), nullable=False),
        sa.Column("source_name", sa.String(length=128), nullable=False),
        sa.Column("source_url", sa.String(length=512), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("file_name", sa.String(length=256), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('loaded', 'partial', 'rejected')",
            name=op.f("ck_data_snapshots_status_valid"),
        ),
        sa.CheckConstraint(
            "as_of_week >= 0", name=op.f("ck_data_snapshots_as_of_week_nonnegative")
        ),
        sa.CheckConstraint(
            "target_week > as_of_week",
            name=op.f("ck_data_snapshots_target_after_as_of"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_snapshots")),
    )
    op.create_index(
        "ix_data_snapshots_season_table_key", "data_snapshots", ["season", "table_key"]
    )
    _team_stats_table("team_offense_stats", OFFENSE_METRICS)
    _team_stats_table("team_defense_stats", DEFENSE_METRICS)


def downgrade() -> None:
    for name in ("team_defense_stats", "team_offense_stats"):
        op.drop_index(f"ix_{name}_season_as_of_week", table_name=name)
        op.drop_table(name)
    op.drop_index("ix_data_snapshots_season_table_key", table_name="data_snapshots")
    op.drop_table("data_snapshots")
