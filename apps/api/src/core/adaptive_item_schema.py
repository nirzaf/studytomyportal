"""Protect exact source snapshots in PostgreSQL bootstrap and migration."""

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

_GUARD = """
CREATE OR REPLACE FUNCTION guard_st_item_revision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status = 'draft' THEN RETURN NEW; END IF;
    ELSIF TG_OP = 'UPDATE' THEN
        IF OLD.status = 'draft' AND NEW.status IN ('draft', 'published')
            AND NEW.version = OLD.version + 1
            AND (to_jsonb(NEW) - ARRAY['status', 'version', 'updated_at', 'difficulty',
                 'hint_ladder', 'error_codes', 'reviewer_id', 'approved_at', 'content_digest', 'policy_version_ids'])
                = (to_jsonb(OLD) - ARRAY['status', 'version', 'updated_at', 'difficulty',
                 'hint_ladder', 'error_codes', 'reviewer_id', 'approved_at', 'content_digest', 'policy_version_ids'])
        THEN RETURN NEW; END IF;
        IF OLD.status = 'published' AND NEW.status = 'retired' AND NEW.version = OLD.version + 1
            AND (to_jsonb(NEW) - ARRAY['status', 'version', 'updated_at'])
                = (to_jsonb(OLD) - ARRAY['status', 'version', 'updated_at'])
        THEN RETURN NEW; END IF;
    END IF;
    RAISE EXCEPTION 'Invalid assessment item revision change' USING ERRCODE = '23514';
END;
$$
"""


def ensure_adaptive_item_guard(connection: Connection) -> list[str]:
    if connection.dialect.name != "postgresql" or not inspect(connection).has_table(
        "st_item_revision"
    ):
        return []
    if connection.execute(
        text("""
        SELECT EXISTS (SELECT 1 FROM pg_trigger
            WHERE tgname = 'st_item_revision_guard' AND tgrelid = to_regclass('st_item_revision')
              AND NOT tgisinternal)
    """)
    ).scalar_one():
        return []
    connection.execute(text(_GUARD))
    connection.execute(
        text("""
        CREATE TRIGGER st_item_revision_guard BEFORE INSERT OR UPDATE OR DELETE ON st_item_revision
        FOR EACH ROW EXECUTE FUNCTION guard_st_item_revision()
    """)
    )
    return ["st_item_revision.st_item_revision_guard"]
