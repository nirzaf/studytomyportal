"""Keep retained draft source versions immutable except for revocation."""

from alembic import op

revision = "a78f2c9d1e64"
down_revision = "c4f8920d6e1b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("""
            CREATE FUNCTION protect_st_draft_material_version() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                IF (to_jsonb(NEW) - 'revoked_at') IS DISTINCT FROM (to_jsonb(OLD) - 'revoked_at') THEN
                    RAISE EXCEPTION 'Draft source version is immutable except for one-way revocation'
                      USING ERRCODE = '23514';
                END IF;
                IF NEW.revoked_at IS DISTINCT FROM OLD.revoked_at
                   AND (OLD.revoked_at IS NOT NULL OR NEW.revoked_at IS NULL
                        OR NEW.revoked_at < OLD.created_at) THEN
                    RAISE EXCEPTION 'Draft source revocation cannot be reversed or backdated'
                      USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $$
        """)
        op.execute("""
            CREATE TRIGGER st_draft_material_version_guard
            BEFORE UPDATE ON st_draft_material
            FOR EACH ROW EXECUTE FUNCTION protect_st_draft_material_version()
        """)
    elif dialect == "sqlite":
        op.execute("""
            CREATE TRIGGER st_draft_material_version_guard_update
            BEFORE UPDATE ON st_draft_material
            WHEN NEW.id IS NOT OLD.id
              OR NEW.source_uuid IS NOT OLD.source_uuid
              OR NEW.course_id IS NOT OLD.course_id
              OR NEW.activity_id IS NOT OLD.activity_id
              OR NEW.uploaded_by_user_id IS NOT OLD.uploaded_by_user_id
              OR NEW.label IS NOT OLD.label
              OR NEW.sha256 IS NOT OLD.sha256
              OR NEW.original_pdf IS NOT OLD.original_pdf
              OR NEW.pages IS NOT OLD.pages
              OR NEW.created_at IS NOT OLD.created_at
              OR (OLD.revoked_at IS NULL AND NEW.revoked_at IS NOT NULL
                  AND NEW.revoked_at < OLD.created_at)
              OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS NOT OLD.revoked_at)
            BEGIN
                SELECT RAISE(ABORT, 'Draft source version is immutable except for one-way revocation');
            END
        """)


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER st_draft_material_version_guard ON st_draft_material")
        op.execute("DROP FUNCTION protect_st_draft_material_version()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER st_draft_material_version_guard_update")
