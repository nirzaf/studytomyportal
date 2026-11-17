"""Add ungraded practice action evidence.

Revision ID: 61b26e413fa2
Revises: 23c8b90df174
"""

import sqlalchemy as sa
from alembic import op

revision = "61b26e413fa2"
down_revision = "23c8b90df174"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "st_learning_action",
        sa.Column(
            "surface_uuid", sa.String(36),
            sa.ForeignKey("st_learning_surface.surface_uuid", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("request_uuid", sa.String(36), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("answer", sa.String(48)),
        sa.Column("assisted", sa.Boolean()),
        sa.Column("presentation", sa.JSON(), nullable=False),
        sa.Column("evaluation_basis", sa.JSON(none_as_null=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("surface_uuid", "revision", name="uq_st_learning_action_revision"),
        sa.CheckConstraint("revision > 1", name="ck_st_learning_action_revision"),
        sa.CheckConstraint(
            "action IN ('submit_answer', 'request_hint', 'retry_practice', 'next_question')",
            name="ck_st_learning_action_kind",
        ),
        sa.CheckConstraint(
            "(action = 'submit_answer' AND answer IS NOT NULL AND assisted IS NOT NULL) OR "
            "(action <> 'submit_answer' AND answer IS NULL AND assisted IS NULL)",
            name="ck_st_learning_action_answer_evidence",
        ),
    )
    dialect = op.get_bind().dialect.name
    tables = ("st_learning_surface", "st_learning_action")
    if dialect == "postgresql":
        op.execute("""
            CREATE FUNCTION reject_st_practice_update() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'Lesson practice evidence cannot be updated';
            END;
            $$
        """)
        for table in tables:
            op.execute(f"""
                CREATE TRIGGER {table}_immutable_update BEFORE UPDATE ON {table}
                FOR EACH ROW EXECUTE FUNCTION reject_st_practice_update()
            """)
    elif dialect == "sqlite":
        for table in tables:
            op.execute(f"""
                CREATE TRIGGER {table}_immutable_update BEFORE UPDATE ON {table}
                BEGIN SELECT RAISE(ABORT, 'Lesson practice evidence cannot be updated'); END
            """)


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE st_learning_action IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(sa.text("SELECT 1 FROM st_learning_action LIMIT 1")).first() is not None:
        raise RuntimeError("Retained practice actions exist. Roll back application code without dropping evidence.")
    op.drop_table("st_learning_action")
    op.execute("DROP TRIGGER IF EXISTS st_learning_surface_immutable_update" + (
        " ON st_learning_surface" if connection.dialect.name == "postgresql" else ""
    ))
    if connection.dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS reject_st_practice_update()")
