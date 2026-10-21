"""games: status and final scores; kickoff_time nullable

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # batch_alter_table rebuilds the table on SQLite (used by the migration tests).
    with op.batch_alter_table("games") as batch:
        batch.alter_column(
            "kickoff_time", existing_type=sa.DateTime(timezone=True), nullable=True
        )
        batch.add_column(
            sa.Column(
                "status", sa.String(length=9), nullable=False, server_default="scheduled"
            )
        )
        batch.add_column(sa.Column("home_score", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("away_score", sa.Integer(), nullable=True))
        batch.create_check_constraint(
            op.f("ck_games_status_valid"), "status IN ('scheduled', 'final')"
        )
        batch.create_check_constraint(
            op.f("ck_games_scores_nonnegative"), "home_score >= 0 AND away_score >= 0"
        )
        batch.create_check_constraint(
            op.f("ck_games_final_has_scores"),
            "status <> 'final' OR (home_score IS NOT NULL AND away_score IS NOT NULL)",
        )


def downgrade() -> None:
    with op.batch_alter_table("games") as batch:
        batch.drop_constraint(op.f("ck_games_final_has_scores"), type_="check")
        batch.drop_constraint(op.f("ck_games_scores_nonnegative"), type_="check")
        batch.drop_constraint(op.f("ck_games_status_valid"), type_="check")
        batch.drop_column("away_score")
        batch.drop_column("home_score")
        batch.drop_column("status")
        batch.alter_column(
            "kickoff_time", existing_type=sa.DateTime(timezone=True), nullable=False
        )
