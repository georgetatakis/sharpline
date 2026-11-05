"""games: nflverse id, game type, site, QB-out flags, weather; game_lines; game_scoring_plays

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-05
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # batch_alter_table rebuilds the table on SQLite (used by the migration tests).
    with op.batch_alter_table("games") as batch:
        batch.add_column(sa.Column("nflverse_game_id", sa.String(length=32), nullable=True))
        batch.add_column(
            sa.Column("game_type", sa.String(length=3), nullable=False, server_default="REG")
        )
        batch.add_column(
            sa.Column("neutral_site", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(
            sa.Column("home_qb_out", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(
            sa.Column("away_qb_out", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("roof", sa.String(length=8), nullable=True))
        batch.add_column(sa.Column("temp", sa.Double(), nullable=True))
        batch.add_column(sa.Column("wind", sa.Double(), nullable=True))
        batch.create_unique_constraint(
            op.f("uq_games_nflverse_game_id"), ["nflverse_game_id"]
        )
        batch.create_check_constraint(
            op.f("ck_games_game_type_valid"), "game_type IN ('REG', 'WC', 'DIV', 'CON', 'SB')"
        )
        batch.create_check_constraint(
            op.f("ck_games_roof_valid"), "roof IN ('outdoors', 'dome', 'closed', 'open')"
        )

    op.create_table(
        "game_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("line_type", sa.String(length=8), nullable=False),
        sa.Column("spread_home", sa.Double(), nullable=True),
        sa.Column("spread_price_home", sa.Integer(), nullable=True),
        sa.Column("spread_price_away", sa.Integer(), nullable=True),
        sa.Column("total", sa.Double(), nullable=True),
        sa.Column("over_price", sa.Integer(), nullable=True),
        sa.Column("under_price", sa.Integer(), nullable=True),
        sa.Column("moneyline_home", sa.Integer(), nullable=True),
        sa.Column("moneyline_away", sa.Integer(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_name", sa.String(length=128), nullable=False),
        sa.Column("book_label", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "line_type IN ('open', 'current', 'closing')",
            name=op.f("ck_game_lines_line_type_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["game_id"], ["games.id"], name=op.f("fk_game_lines_game_id_games")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_game_lines")),
        sa.UniqueConstraint(
            "game_id", "line_type", "source_name", "captured_at", name="uq_game_lines_row"
        ),
    )

    op.create_table(
        "game_scoring_plays",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("nflverse_play_id", sa.Integer(), nullable=False),
        sa.Column("period", sa.Integer(), nullable=False),
        sa.Column("clock", sa.String(length=5), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("scoring_team_id", sa.Integer(), nullable=False),
        sa.Column("play_type", sa.String(length=9), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("scorer_name", sa.String(length=128), nullable=True),
        sa.Column("scorer_nflverse_id", sa.String(length=16), nullable=True),
        sa.Column("scorer_player_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "play_type IN ('td_pass', 'td_rush', 'td_other', 'fg', 'xp', 'two_point', 'other')",
            name=op.f("ck_game_scoring_plays_play_type_valid"),
        ),
        sa.CheckConstraint("points > 0", name=op.f("ck_game_scoring_plays_points_positive")),
        sa.ForeignKeyConstraint(
            ["game_id"], ["games.id"], name=op.f("fk_game_scoring_plays_game_id_games")
        ),
        sa.ForeignKeyConstraint(
            ["scoring_team_id"], ["teams.id"],
            name=op.f("fk_game_scoring_plays_scoring_team_id_teams"),
        ),
        sa.ForeignKeyConstraint(
            ["scorer_player_id"], ["players.id"],
            name=op.f("fk_game_scoring_plays_scorer_player_id_players"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_game_scoring_plays")),
        sa.UniqueConstraint(
            "game_id", "nflverse_play_id", "play_type", name="uq_game_scoring_plays_row"
        ),
    )


def downgrade() -> None:
    op.drop_table("game_scoring_plays")
    op.drop_table("game_lines")
    with op.batch_alter_table("games") as batch:
        batch.drop_constraint(op.f("ck_games_roof_valid"), type_="check")
        batch.drop_constraint(op.f("ck_games_game_type_valid"), type_="check")
        batch.drop_constraint(op.f("uq_games_nflverse_game_id"), type_="unique")
        batch.drop_column("wind")
        batch.drop_column("temp")
        batch.drop_column("roof")
        batch.drop_column("away_qb_out")
        batch.drop_column("home_qb_out")
        batch.drop_column("neutral_site")
        batch.drop_column("game_type")
        batch.drop_column("nflverse_game_id")
