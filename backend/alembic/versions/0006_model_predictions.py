"""model_predictions: locked pregame predictions, one row per game, model and bet type

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-10
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_predictions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("model_type", sa.String(length=4), nullable=False),
        sa.Column("model_version", sa.String(length=32), nullable=False),
        sa.Column("bet_type", sa.String(length=9), nullable=False),
        sa.Column("predicted_value", sa.Double(), nullable=True),
        sa.Column("predicted_prob", sa.Double(), nullable=True),
        sa.Column("market_line", sa.Double(), nullable=True),
        sa.Column("market_prob", sa.Double(), nullable=True),
        sa.Column("edge", sa.Double(), nullable=True),
        sa.Column("shrinkage", sa.Double(), nullable=True),
        sa.Column("pick_price", sa.Integer(), nullable=True),
        sa.Column("closing_price", sa.Integer(), nullable=True),
        sa.Column("clv", sa.Double(), nullable=True),
        sa.Column("snapshot_as_of_week", sa.Integer(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("model_type IN ('game', 'prop', 'td')",
                           name=op.f("ck_model_predictions_model_type_valid")),
        sa.CheckConstraint("bet_type IN ('spread', 'moneyline', 'total')",
                           name=op.f("ck_model_predictions_bet_type_valid")),
        sa.CheckConstraint("predicted_prob >= 0 AND predicted_prob <= 1",
                           name=op.f("ck_model_predictions_prob_range")),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"],
                                name=op.f("fk_model_predictions_game_id_games")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_predictions")),
        sa.UniqueConstraint("game_id", "model_type", "model_version", "bet_type",
                            name="uq_model_predictions_row"),
    )


def downgrade() -> None:
    op.drop_table("model_predictions")
