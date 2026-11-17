"""PostgreSQL invariants shared by curriculum migration and fresh bootstrap."""

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

CURRICULUM_CHILDREN = ("st_concept", "st_concept_prerequisite", "st_concept_activity")

_ROOT_FUNCTION = """
CREATE OR REPLACE FUNCTION guard_st_curriculum_revision() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status = 'draft' THEN RETURN NEW; END IF;
    ELSIF TG_OP = 'DELETE' THEN
        IF OLD.status = 'draft' THEN RETURN OLD; END IF;
    ELSIF OLD.status = 'draft' THEN
        IF ROW(NEW.id, NEW.org_id, NEW.uuid, NEW.curriculum_key, NEW.revision,
               NEW.owner_course_uuid, NEW.created_at)
            IS NOT DISTINCT FROM
           ROW(OLD.id, OLD.org_id, OLD.uuid, OLD.curriculum_key, OLD.revision,
               OLD.owner_course_uuid, OLD.created_at)
           AND NEW.status IN ('draft', 'published')
        THEN RETURN NEW; END IF;
    ELSIF OLD.status = 'published' AND NEW.status = 'retired'
        AND NEW.version = OLD.version + 1
        AND (to_jsonb(NEW) - ARRAY['status', 'version', 'updated_at'])
            = (to_jsonb(OLD) - ARRAY['status', 'version', 'updated_at'])
    THEN RETURN NEW;
    END IF;
    RAISE EXCEPTION 'Invalid curriculum revision change' USING ERRCODE = '23514';
END;
$$
"""

_CHILD_FUNCTION = """
CREATE OR REPLACE FUNCTION guard_st_curriculum_child() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    parent_org integer;
    parent_id bigint;
    parent_status text;
BEGIN
    IF TG_OP = 'UPDATE'
        AND ROW(NEW.id, NEW.uuid, NEW.org_id, NEW.curriculum_revision_id, NEW.created_at)
            IS DISTINCT FROM
            ROW(OLD.id, OLD.uuid, OLD.org_id, OLD.curriculum_revision_id, OLD.created_at)
    THEN
        RAISE EXCEPTION 'Curriculum definition cannot change parent or identity' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'DELETE' THEN
        parent_org := OLD.org_id;
        parent_id := OLD.curriculum_revision_id;
    ELSE
        parent_org := NEW.org_id;
        parent_id := NEW.curriculum_revision_id;
    END IF;
    -- Create a new row version as well as acquiring the lock. Concurrent
    -- writers with a repeatable-read snapshot must retry instead of checking
    -- the graph against a snapshot that omits a committed competing edge.
    UPDATE st_curriculum_revision SET updated_at = updated_at
    WHERE org_id = parent_org AND id = parent_id AND status = 'draft'
    RETURNING status INTO parent_status;
    IF parent_status IS DISTINCT FROM 'draft' THEN
        RAISE EXCEPTION 'Curriculum definition needs a draft parent' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;

    IF TG_TABLE_NAME = 'st_concept_prerequisite' THEN
        IF EXISTS (
            WITH RECURSIVE reachable(concept_id) AS (
                SELECT NEW.prerequisite_id
                UNION
                SELECT edge.prerequisite_id
                FROM st_concept_prerequisite edge JOIN reachable r ON edge.concept_id = r.concept_id
                WHERE edge.org_id = parent_org AND edge.curriculum_revision_id = parent_id
                  AND edge.id <> NEW.id
            )
            SELECT 1 FROM reachable WHERE concept_id = NEW.concept_id
        ) THEN
            RAISE EXCEPTION 'Curriculum prerequisites cannot form a cycle' USING ERRCODE = '23514';
        END IF;
    ELSIF TG_TABLE_NAME = 'st_concept_activity' THEN
        PERFORM 1 FROM activity a JOIN course c ON a.course_id = c.id
        WHERE a.id = NEW.activity_id AND a.activity_uuid = NEW.activity_uuid
          AND a.current_version = NEW.activity_version AND a.org_id = parent_org
          AND c.id = NEW.course_id AND c.course_uuid = NEW.course_uuid AND c.org_id = parent_org
        FOR SHARE OF a, c;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Curriculum mapping needs a current lesson in its course'
                USING ERRCODE = '23503';
        END IF;
    END IF;
    RETURN NEW;
END;
$$
"""


def ensure_adaptive_curriculum_guards(connection: Connection) -> list[str]:
    """Install triggers only when the PostgreSQL definition tables exist."""
    root = "st_curriculum_revision"
    if connection.dialect.name != "postgresql" or not inspect(connection).has_table(
        root
    ):
        return []
    triggers = {
        root: "guard_st_curriculum_revision",
        **dict.fromkeys(CURRICULUM_CHILDREN, "guard_st_curriculum_child"),
    }
    existing = set(
        connection.execute(
            text("""
        SELECT relname FROM pg_trigger JOIN pg_class ON pg_class.oid = tgrelid
        WHERE tgname = 'st_curriculum_definition_guard' AND NOT tgisinternal
          AND tgrelid IN (
              to_regclass('st_curriculum_revision'), to_regclass('st_concept'),
              to_regclass('st_concept_prerequisite'), to_regclass('st_concept_activity')
          )
    """)
        ).scalars()
    )
    missing = triggers.keys() - existing
    if not missing:
        return []
    connection.execute(text(_ROOT_FUNCTION))
    connection.execute(text(_CHILD_FUNCTION))
    for table in sorted(missing):
        connection.execute(
            text(f"""
            CREATE TRIGGER st_curriculum_definition_guard
            BEFORE INSERT OR UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION {triggers[table]}()
        """)
        )
    return [f"{table}.st_curriculum_definition_guard" for table in sorted(missing)]
