"""Store learner feedback on a protected tutor turn.

Revision ID: ef0b241a1c57
Revises: 950ec32a74bf
"""

import sqlalchemy as sa
from alembic import op

revision = "ef0b241a1c57"
down_revision = "950ec32a74bf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("st_tutor_turn") as batch:
        batch.add_column(sa.Column("feedback_issue", sa.String(16)))
        batch.add_column(sa.Column("feedback_note", sa.String(300)))
        batch.add_column(sa.Column("feedback_at", sa.DateTime(timezone=True)))
        batch.create_check_constraint(
            "ck_st_tutor_turn_feedback",
            "(feedback_issue IS NULL AND feedback_at IS NULL AND feedback_note IS NULL) OR "
            "(feedback_issue IS NOT NULL AND feedback_issue IN ('inaccurate','unclear','unsafe') "
            "AND feedback_at IS NOT NULL AND state = 'completed')",
        )


def downgrade() -> None:
    with op.batch_alter_table("st_tutor_turn") as batch:
        batch.drop_constraint("ck_st_tutor_turn_feedback", type_="check")
        batch.drop_column("feedback_at")
        batch.drop_column("feedback_note")
        batch.drop_column("feedback_issue")
