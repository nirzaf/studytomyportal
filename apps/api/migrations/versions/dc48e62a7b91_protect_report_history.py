"""Protect report, recipient and review history from in-place changes.

Revision ID: dc48e62a7b91
Revises: dfa43c51b792
"""

from alembic import op

revision = "dc48e62a7b91"
down_revision = "dfa43c51b792"
branch_labels = None
depends_on = None

_TABLES = ("st_progress_report", "st_report_recipient", "st_report_review_request")


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("""
            CREATE FUNCTION reject_st_report_history_mutation() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'Study report history is immutable';
            END;
            $$
        """)
        for table in _TABLES:
            op.execute(f"""
                CREATE TRIGGER {table}_immutable
                BEFORE UPDATE OR DELETE ON {table}
                FOR EACH ROW EXECUTE FUNCTION reject_st_report_history_mutation()
            """)
    elif dialect == "sqlite":
        for table in _TABLES:
            for action in ("UPDATE", "DELETE"):
                op.execute(f"""
                    CREATE TRIGGER {table}_immutable_{action.lower()}
                    BEFORE {action} ON {table}
                    BEGIN SELECT RAISE(ABORT, 'Study report history is immutable'); END
                """)


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        for table in _TABLES:
            op.execute(f"DROP TRIGGER {table}_immutable ON {table}")
        op.execute("DROP FUNCTION reject_st_report_history_mutation()")
    elif dialect == "sqlite":
        for table in _TABLES:
            for action in ("update", "delete"):
                op.execute(f"DROP TRIGGER {table}_immutable_{action}")
