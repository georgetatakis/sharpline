"""baseline: teams, players, games

Revision ID: 0001
Revises:
Create Date: 2026-10-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("abbreviation", sa.String(length=4), nullable=False),
        sa.Column("conference", sa.String(length=3), nullable=False),
        sa.Column("division", sa.String(length=5), nullable=False),
        sa.CheckConstraint(
            "conference IN ('AFC', 'NFC')", name=op.f("ck_teams_conference_valid")
        ),
        sa.CheckConstraint(
            "division IN ('East', 'North', 'South', 'West')",
            name=op.f("ck_teams_division_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_teams")),
        sa.UniqueConstraint("abbreviation", name=op.f("uq_teams_abbreviation")),
        sa.UniqueConstraint("name", name=op.f("uq_teams_name")),
    )
    op.create_table(
        "players",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=True),
        sa.Column("position", sa.String(length=8), nullable=False),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name=op.f("fk_players_team_id_teams")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_players")),
    )
    op.create_index(op.f("ix_players_team_id"), "players", ["team_id"])
    op.create_table(
        "games",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("week", sa.Integer(), nullable=False),
        sa.Column("home_team_id", sa.Integer(), nullable=False),
        sa.Column("away_team_id", sa.Integer(), nullable=False),
        sa.Column("kickoff_time", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "home_team_id <> away_team_id", name=op.f("ck_games_distinct_teams")
        ),
        sa.CheckConstraint("week >= 1", name=op.f("ck_games_week_positive")),
        sa.ForeignKeyConstraint(
            ["away_team_id"], ["teams.id"], name=op.f("fk_games_away_team_id_teams")
        ),
        sa.ForeignKeyConstraint(
            ["home_team_id"], ["teams.id"], name=op.f("fk_games_home_team_id_teams")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_games")),
        sa.UniqueConstraint(
            "season", "week", "home_team_id", name="uq_games_season_week_home_team"
        ),
    )
    op.create_index("ix_games_season_week", "games", ["season", "week"])


def downgrade() -> None:
    op.drop_index("ix_games_season_week", table_name="games")
    op.drop_table("games")
    op.drop_index(op.f("ix_players_team_id"), table_name="players")
    op.drop_table("players")
    op.drop_table("teams")
