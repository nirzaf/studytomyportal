"""Protect grounded lesson and quiz source evidence from mutation."""

from alembic import op

revision = "c4f8920d6e1b"
down_revision = "0cb1b9a418ed"
branch_labels = None
depends_on = None

_TABLES = ("st_grounded_lesson_evidence", "st_grounded_quiz_evidence")


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("""
            CREATE FUNCTION reject_st_grounded_source_evidence_mutation() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'Grounded source evidence is immutable' USING ERRCODE = '23514';
            END;
            $$
        """)
        for table in _TABLES:
            op.execute(f"""
                CREATE TRIGGER {table}_immutable
                BEFORE UPDATE OR DELETE ON {table}
                FOR EACH ROW EXECUTE FUNCTION reject_st_grounded_source_evidence_mutation()
            """)
    elif dialect == "sqlite":
        for table in _TABLES:
            for action in ("UPDATE", "DELETE"):
                op.execute(f"""
                    CREATE TRIGGER {table}_immutable_{action.lower()}
                    BEFORE {action} ON {table}
                    BEGIN SELECT RAISE(ABORT, 'Grounded source evidence is immutable'); END
                """)


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        for table in _TABLES:
            op.execute(f"DROP TRIGGER {table}_immutable ON {table}")
        op.execute("DROP FUNCTION reject_st_grounded_source_evidence_mutation()")
    elif dialect == "sqlite":
        for table in _TABLES:
            for action in ("update", "delete"):
                op.execute(f"DROP TRIGGER {table}_immutable_{action}")
