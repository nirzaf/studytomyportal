"""Install PostgreSQL integrity guards on fresh and upgraded adaptive schemas."""

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

_GUARDS = {
    "st_learning_attempt": """
    DECLARE allowed boolean;
    BEGIN
      IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'active' OR NEW.version <> 1 OR NEW.next_ordinal <> 1 THEN
          RAISE EXCEPTION 'New adaptive rounds must start active and empty' USING ERRCODE = '23514';
        END IF;
        IF NEW.actor_capacity = 'learner' AND NOT EXISTS (
          SELECT 1 FROM st_learner_profile
          WHERE org_id = NEW.org_id AND id = NEW.learner_profile_id AND user_id = NEW.actor_user_id
        ) THEN
          RAISE EXCEPTION 'Adaptive round actor does not own this learner profile' USING ERRCODE = '23514';
        END IF;
        IF NEW.kind = 'diagnostic' THEN
          SELECT d.status = 'published' AND c.status = 'published'
                 AND d.content_digest = NEW.manifest_digest AND c.owner_course_uuid = NEW.source_course_uuid
          INTO allowed
          FROM st_diagnostic_revision d
          JOIN st_curriculum_revision c ON c.id = d.curriculum_revision_id AND c.org_id = d.org_id
          WHERE d.id = NEW.diagnostic_revision_id AND d.org_id = NEW.org_id
          FOR SHARE OF d, c;
          IF allowed IS DISTINCT FROM true THEN
            RAISE EXCEPTION 'Diagnostic round requires the current published manifest'
              USING ERRCODE = '23514';
          END IF;
        END IF;
        RETURN NEW;
      ELSIF TG_OP = 'UPDATE' AND OLD.status = 'active' AND NEW.version = OLD.version + 1
        AND NEW.next_ordinal BETWEEN OLD.next_ordinal AND OLD.next_ordinal + 1
        AND (to_jsonb(NEW) - ARRAY['status','version','next_ordinal','ended_at'])
          = (to_jsonb(OLD) - ARRAY['status','version','next_ordinal','ended_at'])
      THEN
        IF NEW.next_ordinal > OLD.next_ordinal AND NOT EXISTS (
          SELECT 1 FROM st_attempt_item WHERE attempt_id = OLD.id AND ordinal = OLD.next_ordinal
        ) THEN
          RAISE EXCEPTION 'An empty issued ordinal cannot be advanced' USING ERRCODE = '23514';
        END IF;
        IF NEW.status <> 'active' AND EXISTS (
          SELECT 1 FROM st_attempt_item WHERE attempt_id = OLD.id AND response_state = 'issued'
        ) THEN
          RAISE EXCEPTION 'Close outstanding items before closing a round' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
      END IF;
      RAISE EXCEPTION 'Invalid adaptive round change' USING ERRCODE = '23514';
    END;
    """,
    "st_attempt_item": """
    DECLARE
      parent st_learning_attempt%ROWTYPE;
      definition st_item_revision%ROWTYPE;
    BEGIN
      IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Adaptive issued items are retained' USING ERRCODE = '23514';
      END IF;
      SELECT * INTO parent FROM st_learning_attempt
      WHERE org_id = NEW.org_id AND id = NEW.attempt_id FOR UPDATE;
      IF parent.status IS DISTINCT FROM 'active'
        OR NEW.is_diagnostic IS DISTINCT FROM (parent.kind = 'diagnostic') THEN
        RAISE EXCEPTION 'Issued item does not belong to an active round' USING ERRCODE = '23514';
      END IF;
      IF TG_OP = 'INSERT' THEN
        IF NEW.response_state <> 'issued' OR NEW.response_version <> 1
          OR NEW.ordinal <> parent.next_ordinal OR NEW.issued_at < parent.started_at
          OR NEW.issued_at >= parent.expires_at THEN
          RAISE EXCEPTION 'Invalid issued item state or ordinal' USING ERRCODE = '23514';
        END IF;
        SELECT * INTO definition FROM st_item_revision
        WHERE org_id = NEW.org_id AND id = NEW.item_revision_id FOR SHARE;
        IF definition.status IS DISTINCT FROM 'published' THEN
          RAISE EXCEPTION 'Issued items require a published definition' USING ERRCODE = '23514';
        END IF;
        IF NEW.is_diagnostic THEN
          PERFORM 1 FROM st_diagnostic_revision d
          JOIN st_curriculum_revision c ON d.curriculum_revision_id = c.id AND d.org_id = c.org_id
          WHERE d.id = parent.diagnostic_revision_id AND d.org_id = parent.org_id
            AND d.status = 'published' AND c.status = 'published' FOR SHARE OF c, d;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'Diagnostic definitions must still be published at issuance'
                USING ERRCODE = '23514';
          END IF;
        END IF;
        IF NEW.is_diagnostic AND (
          definition.source_exposure <> 'private_original' OR NOT EXISTS (
            SELECT 1 FROM st_diagnostic_revision d,
              json_array_elements(d.manifest->'pool') AS entry
            WHERE d.id = parent.diagnostic_revision_id AND d.org_id = parent.org_id
              AND d.status = 'published' AND entry->>'uuid' = definition.uuid
              AND entry->>'content_digest' = definition.content_digest
          )
        ) THEN
          RAISE EXCEPTION 'Diagnostic item is outside the reviewed pool' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
      ELSIF OLD.response_state = 'issued' AND NEW.response_version = OLD.response_version + 1
        AND NEW.help_count >= OLD.help_count AND NEW.exposure_count >= OLD.exposure_count
        AND (NEW.response_state <> 'answered' OR NEW.responded_at < parent.expires_at)
        AND (to_jsonb(NEW) - ARRAY['response_state','response_version','normalized_response',
                                  'responded_at','response_digest','skip_reason','help_count','exposure_count'])
          = (to_jsonb(OLD) - ARRAY['response_state','response_version','normalized_response',
                                  'responded_at','response_digest','skip_reason','help_count','exposure_count'])
      THEN
        RETURN NEW;
      END IF;
      RAISE EXCEPTION 'Accepted responses and issued definitions are immutable' USING ERRCODE = '23514';
    END;
    """,
    "st_learning_evidence": """
    DECLARE
      source st_attempt_item%ROWTYPE;
      predecessor integer;
      next_sequence bigint;
    BEGIN
      IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'Learning evidence is append-only' USING ERRCODE = '23514';
      END IF;
      -- Serialize one learner's sequence, including across separate attempts.
      UPDATE st_learner_profile SET access_version = access_version
        WHERE org_id = NEW.org_id AND id = NEW.learner_profile_id;
      IF NOT FOUND THEN
        RAISE EXCEPTION 'Evidence learner profile is invalid' USING ERRCODE = '23514';
      END IF;
      SELECT * INTO source FROM st_attempt_item
        WHERE org_id = NEW.org_id AND id = NEW.attempt_item_id AND learner_profile_id = NEW.learner_profile_id;
      IF source.response_state IS NULL OR source.response_state = 'issued'
        OR (source.response_state = 'skipped' AND NEW.outcome <> 'not_scored')
        OR source.responded_at IS DISTINCT FROM NEW.observed_at
        OR (NEW.eligible AND (source.help_count > 0 OR source.exposure_count > 0
          OR NOT EXISTS (SELECT 1 FROM st_learning_attempt
            WHERE id = source.attempt_id AND org_id = NEW.org_id AND actor_capacity = 'learner')))
      THEN
        RAISE EXCEPTION 'Evidence does not match the accepted response and its provenance' USING ERRCODE = '23514';
      END IF;
      IF NEW.result_revision > 1 THEN
        SELECT result_revision INTO predecessor FROM st_learning_evidence
          WHERE id = NEW.supersedes_id AND org_id = NEW.org_id AND learner_profile_id = NEW.learner_profile_id
            AND logical_source_uuid = NEW.logical_source_uuid;
        IF predecessor IS DISTINCT FROM NEW.result_revision - 1 THEN
          RAISE EXCEPTION 'A correction must supersede the preceding result revision' USING ERRCODE = '23514';
        END IF;
      END IF;
      SELECT COALESCE(MAX(evidence_sequence), 0) + 1 INTO next_sequence
        FROM st_learning_evidence WHERE org_id = NEW.org_id AND learner_profile_id = NEW.learner_profile_id;
      IF COALESCE(NEW.evidence_sequence, 0) NOT IN (0, next_sequence) THEN
        RAISE EXCEPTION 'Invalid learner evidence sequence' USING ERRCODE = '23514';
      END IF;
      NEW.evidence_sequence := next_sequence;
      RETURN NEW;
    END;
    """,
    "st_concept_mastery": """
    BEGIN
      IF NEW.evidence_watermark > COALESCE((
        SELECT MAX(evidence_sequence) FROM st_learning_evidence
          WHERE org_id = NEW.org_id AND learner_profile_id = NEW.learner_profile_id
      ), 0) THEN
        RAISE EXCEPTION 'Projection watermark exceeds the evidence journal' USING ERRCODE = '23514';
      END IF;
      IF TG_OP = 'UPDATE' AND (
        NEW.evidence_watermark < OLD.evidence_watermark OR NEW.version <> OLD.version + 1
        OR ROW(NEW.id,NEW.org_id,NEW.uuid,NEW.learner_profile_id,NEW.curriculum_revision_id,NEW.concept_id,
               NEW.mastery_policy_uuid,NEW.mastery_policy_digest,NEW.reducer_version,NEW.created_at)
        IS DISTINCT FROM
        ROW(OLD.id,OLD.org_id,OLD.uuid,OLD.learner_profile_id,OLD.curriculum_revision_id,OLD.concept_id,
            OLD.mastery_policy_uuid,OLD.mastery_policy_digest,OLD.reducer_version,OLD.created_at)
      ) THEN
        RAISE EXCEPTION 'A projection cannot move backward or change identity' USING ERRCODE = '23514';
      END IF;
      RETURN NEW;
    END;
    """,
}


def ensure_adaptive_evidence_guards(connection: Connection) -> list[str]:
    if connection.dialect.name != "postgresql":
        return []
    inspector = inspect(connection)
    installed = []
    for table, body in _GUARDS.items():
        function = f"guard_{table}"
        if (
            not inspector.has_table(table)
            or connection.execute(
                text("""
            SELECT EXISTS (SELECT 1 FROM pg_trigger
              WHERE tgname = :trigger AND tgrelid = to_regclass(:table) AND NOT tgisinternal)
        """),
                {"trigger": function, "table": table},
            ).scalar_one()
        ):
            continue
        connection.execute(
            text(
                f"CREATE OR REPLACE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ {body} $$"
            )
        )
        events = (
            "INSERT OR UPDATE"
            if table == "st_concept_mastery"
            else "INSERT OR UPDATE OR DELETE"
        )
        connection.execute(
            text(f"""
            CREATE TRIGGER {function} BEFORE {events} ON {table}
            FOR EACH ROW EXECUTE FUNCTION {function}()
        """)
        )
        installed.append(f"{table}.{function}")
    return installed
