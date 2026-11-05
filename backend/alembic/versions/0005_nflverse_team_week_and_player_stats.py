"""team stats source column; players nflverse ids; game_team_stats; player_weekly_stats

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-05
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SUCCESS = {"offense": "success_rate", "defense": "success_rate_allowed"}


def upgrade() -> None:
    # batch_alter_table rebuilds the table on SQLite (used by the migration tests).
    for side, success in SUCCESS.items():
        name = f"team_{side}_stats"
        with op.batch_alter_table(name) as batch:
            batch.add_column(
                sa.Column("source", sa.String(length=10), nullable=False,
                          server_default="screenshot")
            )
            batch.add_column(sa.Column("games_played", sa.Integer(), nullable=True))
            batch.add_column(sa.Column("plays", sa.Integer(), nullable=True))
            batch.add_column(sa.Column(success, sa.Double(), nullable=True))
            batch.drop_constraint(f"uq_{name}_row", type_="unique")
            batch.create_unique_constraint(
                f"uq_{name}_row", ["team_id", "season", "as_of_week", "source"]
            )
            batch.create_check_constraint(
                op.f(f"ck_{name}_source_valid"), "source IN ('screenshot', 'nflverse')"
            )

    with op.batch_alter_table("players") as batch:
        batch.add_column(sa.Column("nflverse_id", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("pfr_id", sa.String(length=16), nullable=True))
        batch.create_unique_constraint(op.f("uq_players_nflverse_id"), ["nflverse_id"])

    counts = [
        "plays", "total_yards", "rush_attempts", "rush_yards", "pass_attempts",
        "pass_completions", "pass_yards", "air_yards", "sacks_taken", "turnovers",
        "penalties", "penalty_yards", "redzone_rush_attempts", "redzone_targets",
    ]
    op.create_table(
        "game_team_stats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        *[sa.Column(c, sa.Integer(), nullable=False) for c in counts[:2]],
        sa.Column("time_of_possession", sa.Double(), nullable=True),
        *[sa.Column(c, sa.Integer(), nullable=False) for c in counts[2:]],
        sa.Column("blitzes_faced", sa.Integer(), nullable=True),
        sa.Column("avg_pocket_time", sa.Double(), nullable=True),
        sa.ForeignKeyConstraint(
            ["game_id"], ["games.id"], name=op.f("fk_game_team_stats_game_id_games")
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name=op.f("fk_game_team_stats_team_id_teams")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_game_team_stats")),
        sa.UniqueConstraint("game_id", "team_id", name="uq_game_team_stats_row"),
    )

    op.create_table(
        "player_weekly_stats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("week", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("snaps", sa.Integer(), nullable=True),
        sa.Column("snap_share", sa.Double(), nullable=True),
        sa.Column("targets", sa.Integer(), nullable=False),
        sa.Column("target_share", sa.Double(), nullable=True),
        *[
            sa.Column(c, sa.Integer(), nullable=False)
            for c in (
                "carries", "red_zone_touches", "red_zone_targets", "goal_line_carries",
                "air_yards", "receiving_yards", "rushing_yards", "receptions", "tds",
            )
        ],
        sa.ForeignKeyConstraint(
            ["player_id"], ["players.id"], name=op.f("fk_player_weekly_stats_player_id_players")
        ),
        sa.ForeignKeyConstraint(
            ["game_id"], ["games.id"], name=op.f("fk_player_weekly_stats_game_id_games")
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name=op.f("fk_player_weekly_stats_team_id_teams")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_player_weekly_stats")),
        sa.UniqueConstraint("player_id", "season", "week", name="uq_player_weekly_stats_row"),
    )
    op.create_index(
        "ix_player_weekly_stats_season_week", "player_weekly_stats", ["season", "week"]
    )


def downgrade() -> None:
    op.drop_index("ix_player_weekly_stats_season_week", table_name="player_weekly_stats")
    op.drop_table("player_weekly_stats")
    op.drop_table("game_team_stats")
    with op.batch_alter_table("players") as batch:
        batch.drop_constraint(op.f("uq_players_nflverse_id"), type_="unique")
        batch.drop_column("pfr_id")
        batch.drop_column("nflverse_id")
    for side, success in SUCCESS.items():
        name = f"team_{side}_stats"
        with op.batch_alter_table(name) as batch:
            batch.drop_constraint(op.f(f"ck_{name}_source_valid"), type_="check")
            batch.drop_constraint(f"uq_{name}_row", type_="unique")
            batch.create_unique_constraint(f"uq_{name}_row", ["team_id", "season", "as_of_week"])
            batch.drop_column(success)
            batch.drop_column("plays")
            batch.drop_column("games_played")
            batch.drop_column("source")
