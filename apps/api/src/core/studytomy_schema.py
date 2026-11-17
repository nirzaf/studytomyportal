"""Database guards not represented by the SQLModel table metadata.

Alembic installs these triggers for stamped upgrades. The application also has
an empty/legacy ``create_all`` bootstrap path, which stamps the latest Alembic
head without executing migration bodies. Keep that path's enforcement aligned
with the Studytomy migrations.
"""

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

_PRACTICE_EVIDENCE_TABLES = ("st_learning_surface", "st_learning_action")
_PRACTICE_POSTGRES_GUARDS = tuple(
    (
        table,
        f"{table}_immutable_update",
        """
        CREATE OR REPLACE FUNCTION reject_st_practice_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Lesson practice evidence cannot be updated';
        END;
        $$
        """,
        f"""
        CREATE TRIGGER {table}_immutable_update
        BEFORE UPDATE ON {table}
        FOR EACH ROW EXECUTE FUNCTION reject_st_practice_update()
        """,
    )
    for table in _PRACTICE_EVIDENCE_TABLES
)
_PRACTICE_SQLITE_GUARDS = tuple(
    (
        table,
        f"{table}_immutable_update",
        f"""
        CREATE TRIGGER IF NOT EXISTS {table}_immutable_update
        BEFORE UPDATE ON {table}
        BEGIN SELECT RAISE(ABORT, 'Lesson practice evidence cannot be updated'); END
        """,
    )
    for table in _PRACTICE_EVIDENCE_TABLES
)

_REPORT_HISTORY_TABLES = ("st_progress_report", "st_report_recipient", "st_report_review_request")
_REPORT_HISTORY_POSTGRES_GUARDS = tuple(
    (
        table,
        f"{table}_immutable",
        """
        CREATE OR REPLACE FUNCTION reject_st_report_history_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Study report history is immutable';
        END;
        $$
        """,
        f"""
        CREATE TRIGGER {table}_immutable
        BEFORE UPDATE OR DELETE ON {table}
        FOR EACH ROW EXECUTE FUNCTION reject_st_report_history_mutation()
        """,
    )
    for table in _REPORT_HISTORY_TABLES
)
_REPORT_HISTORY_SQLITE_GUARDS = tuple(
    (
        table,
        f"{table}_immutable_{action.lower()}",
        f"""
        CREATE TRIGGER IF NOT EXISTS {table}_immutable_{action.lower()}
        BEFORE {action} ON {table}
        BEGIN SELECT RAISE(ABORT, 'Study report history is immutable'); END
        """,
    )
    for table in _REPORT_HISTORY_TABLES
    for action in ("UPDATE", "DELETE")
)

_COURSE_RELEASE_REFERENCE_CHECK = """
    NOT EXISTS (
        SELECT 1 FROM st_course_review AS review
        WHERE review.id = NEW.review_id AND review.org_id = NEW.org_id
          AND review.course_uuid = NEW.course_uuid AND review.decision = 'approved'
          AND review.revision_sha256 = NEW.snapshot_sha256
    )
    OR 4 <> (
        SELECT count(*) FROM st_policy_version AS policy
        JOIN organization AS org ON org.id = NEW.org_id
        WHERE policy.org_id = org.id AND policy.scope_kind = 'organization'
          AND policy.scope_uuid = org.org_uuid AND policy.status = 'approved'
          AND (
            (policy.id = NEW.assessment_policy_id AND policy.policy_key = 'D-08')
            OR (policy.id = NEW.progression_policy_id AND policy.policy_key = 'D-09')
            OR (policy.id = NEW.retention_policy_id AND policy.policy_key = 'D-13')
            OR (policy.id = NEW.rights_policy_id AND policy.policy_key = 'D-16')
          )
    )
"""


COURSE_GRANT_GUARD = (
    "st_course_access_grant",
    "st_course_access_grant_protect_history",
    """
    CREATE OR REPLACE FUNCTION protect_st_course_access_grant() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Course access grant history cannot be deleted';
        END IF;
        IF (to_jsonb(NEW) - ARRAY['starts_at', 'ends_at', 'revoked_at'])
            IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['starts_at', 'ends_at', 'revoked_at']) THEN
            RAISE EXCEPTION 'Course access grant provenance is immutable';
        END IF;
        IF ROW(NEW.starts_at, NEW.ends_at) IS DISTINCT FROM ROW(OLD.starts_at, OLD.ends_at)
            AND NOT (
                OLD.start_basis = 'after_first_access'
                AND OLD.starts_at IS NULL AND OLD.revoked_at IS NULL
                AND NEW.revoked_at IS NULL
                AND NEW.starts_at IS NOT NULL AND NEW.ends_at IS NOT NULL
                AND NEW.starts_at >= OLD.created_at
                AND NEW.ends_at = NEW.starts_at + OLD.duration_days * interval '24 hours'
            ) THEN
            RAISE EXCEPTION 'Course access grant window cannot be rewritten';
        END IF;
        IF NEW.revoked_at IS DISTINCT FROM OLD.revoked_at
            AND (OLD.revoked_at IS NOT NULL OR NEW.revoked_at IS NULL
                OR NEW.revoked_at < OLD.created_at) THEN
            RAISE EXCEPTION 'Course access grant revocation cannot be rewritten';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER st_course_access_grant_protect_history
    BEFORE UPDATE OR DELETE ON st_course_access_grant
    FOR EACH ROW EXECUTE FUNCTION protect_st_course_access_grant()
    """,
)

CAPACITY_HOLD_GUARD = (
    "st_capacity_hold",
    "st_capacity_hold_protect_history",
    """
    CREATE OR REPLACE FUNCTION protect_st_capacity_hold() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Offer capacity hold history cannot be deleted';
        END IF;
        IF (to_jsonb(NEW) - 'consumed_at') IS DISTINCT FROM (to_jsonb(OLD) - 'consumed_at')
            OR (OLD.consumed_at IS NOT NULL AND NEW.consumed_at IS DISTINCT FROM OLD.consumed_at)
        THEN
            RAISE EXCEPTION 'Offer capacity hold history is immutable';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER st_capacity_hold_protect_history
    BEFORE UPDATE OR DELETE ON st_capacity_hold
    FOR EACH ROW EXECUTE FUNCTION protect_st_capacity_hold()
    """,
)

ORDER_HISTORY_GUARDS = tuple(
    (
        table,
        f"{table}_protect_history",
        """
        CREATE OR REPLACE FUNCTION protect_st_order_history() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_TABLE_NAME = 'st_order' AND TG_OP = 'INSERT' THEN
                IF NEW.sealed_at IS NOT NULL THEN
                    RAISE EXCEPTION 'Order must be sealed after its allocations are recorded';
                END IF;
                RETURN NEW;
            END IF;
            IF TG_TABLE_NAME = 'st_order' AND TG_OP = 'UPDATE' THEN
                IF OLD.sealed_at IS NULL AND NEW.sealed_at >= OLD.created_at
                    AND (to_jsonb(NEW) - 'sealed_at') = (to_jsonb(OLD) - 'sealed_at')
                    AND EXISTS (SELECT 1 FROM st_order_line WHERE order_id = OLD.id)
                    AND NEW.total_minor = (SELECT sum(total_minor) FROM st_order_line WHERE order_id = OLD.id)
                THEN
                    RETURN NEW;
                END IF;
            END IF;
            IF TG_TABLE_NAME = 'st_order_line' AND TG_OP = 'INSERT' THEN
                PERFORM 1 FROM st_order WHERE id = NEW.order_id FOR UPDATE;
                IF EXISTS (SELECT 1 FROM st_order WHERE id = NEW.order_id AND sealed_at IS NOT NULL) THEN
                    RAISE EXCEPTION 'Sealed order allocations cannot be extended';
                END IF;
                RETURN NEW;
            END IF;
            IF TG_TABLE_NAME = 'st_order_acceptance' AND TG_OP = 'INSERT' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM st_order WHERE id = NEW.order_id AND org_id = NEW.org_id
                    AND purchaser_user_id = NEW.purchaser_user_id
                    AND confirmation_sha256 = NEW.confirmation_sha256
                    AND sealed_at <= NEW.accepted_at
                ) THEN
                    RAISE EXCEPTION 'Order acceptance requires the sealed purchaser draft';
                END IF;
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'Order quote and allocation history is immutable';
        END;
        $$
        """,
        f"""
        CREATE TRIGGER {table}_protect_history
        BEFORE INSERT OR UPDATE OR DELETE ON {table}
        FOR EACH ROW EXECUTE FUNCTION protect_st_order_history()
        """,
    )
    for table in ("st_order", "st_order_line", "st_order_acceptance")
)

COLLECTION_HISTORY_GUARDS = tuple(
    (
        table,
        f"{table}_protect_history",
        """
        CREATE OR REPLACE FUNCTION protect_st_collection_history() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF TG_TABLE_NAME = 'st_manual_collection_evidence' THEN
                    IF NOT EXISTS (
                        SELECT 1 FROM st_payment_attempt WHERE id = NEW.attempt_id AND method = 'manual'
                    ) THEN
                        RAISE EXCEPTION 'Bank evidence requires a manual collection attempt';
                    END IF;
                ELSIF TG_TABLE_NAME = 'st_collection_remedy' THEN
                    PERFORM 1 FROM st_payment_attempt WHERE id = NEW.attempt_id FOR UPDATE;
                    IF EXISTS (SELECT 1 FROM st_money_movement WHERE attempt_id = NEW.attempt_id) THEN
                        RAISE EXCEPTION 'Collection attempt already has a confirmed movement';
                    END IF;
                ELSIF TG_TABLE_NAME = 'st_money_movement' THEN
                    PERFORM 1 FROM st_payment_attempt WHERE id = NEW.attempt_id FOR UPDATE;
                    IF EXISTS (SELECT 1 FROM st_collection_remedy WHERE attempt_id = NEW.attempt_id) THEN
                        RAISE EXCEPTION 'Collection attempt requires remedy review';
                    END IF;
                    IF EXISTS (
                        SELECT 1 FROM st_payment_attempt WHERE id = NEW.attempt_id AND method = 'manual'
                    ) AND NOT EXISTS (
                        SELECT 1 FROM st_manual_collection_evidence AS evidence
                        WHERE evidence.attempt_id = NEW.attempt_id AND evidence.org_id = NEW.org_id
                        AND evidence.order_id = NEW.order_id AND evidence.amount_minor = NEW.amount_minor
                        AND evidence.currency = NEW.currency AND evidence.external_reference = NEW.external_reference
                        AND evidence.evidence_reference = NEW.evidence_reference
                        AND evidence.verified_at <= NEW.confirmed_at AND NEW.confirmed_by_user_id IS NOT NULL
                    ) THEN
                        RAISE EXCEPTION 'Manual collection requires matching verified bank evidence';
                    END IF;
                END IF;
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'Payment evidence history is immutable';
        END;
        $$
        """,
        f"""
        CREATE TRIGGER {table}_protect_history
        BEFORE INSERT OR UPDATE OR DELETE ON {table}
        FOR EACH ROW EXECUTE FUNCTION protect_st_collection_history()
        """,
    )
    for table in (
        "st_payment_attempt", "st_money_movement", "st_manual_collection_evidence",
        "st_collection_remedy", "st_collection_resolution",
    )
)

REFUND_RESERVATION_GUARD = (
    "st_refund_reservation",
    "st_refund_reservation_protect_history",
    """
    CREATE OR REPLACE FUNCTION protect_st_refund_reservation() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION 'Refund reservation history is immutable';
    END;
    $$
    """,
    """
    CREATE TRIGGER st_refund_reservation_protect_history
    BEFORE UPDATE OR DELETE ON st_refund_reservation
    FOR EACH ROW EXECUTE FUNCTION protect_st_refund_reservation()
    """,
)

REFUND_SETTLEMENT_GUARD = (
    "st_refund_settlement",
    "st_refund_settlement_protect_history",
    """
    CREATE OR REPLACE FUNCTION protect_st_refund_settlement() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'Refund settlement history is immutable';
        END IF;
        IF EXISTS (
            SELECT 1 FROM st_refund_reservation
            WHERE org_id = NEW.org_id AND id = NEW.reservation_id AND approved_at > NEW.settled_at
        ) THEN
            RAISE EXCEPTION 'Refund settlement cannot precede reservation approval';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER st_refund_settlement_protect_history
    BEFORE INSERT OR UPDATE OR DELETE ON st_refund_settlement
    FOR EACH ROW EXECUTE FUNCTION protect_st_refund_settlement()
    """,
)

CHARGEBACK_ADJUSTMENT_GUARD = (
    "st_chargeback_adjustment",
    "st_chargeback_adjustment_protect_history",
    """
    CREATE OR REPLACE FUNCTION protect_st_chargeback_adjustment() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION 'Chargeback adjustment history is immutable';
    END;
    $$
    """,
    """
    CREATE TRIGGER st_chargeback_adjustment_protect_history
    BEFORE UPDATE OR DELETE ON st_chargeback_adjustment
    FOR EACH ROW EXECUTE FUNCTION protect_st_chargeback_adjustment()
    """,
)

CHARGEBACK_REVERSAL_GUARD = (
    "st_chargeback_reversal",
    "st_chargeback_reversal_protect_history",
    """
    CREATE OR REPLACE FUNCTION protect_st_chargeback_reversal() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'Chargeback reversal history is immutable';
        END IF;
        IF EXISTS (
            SELECT 1 FROM st_chargeback_adjustment
            WHERE org_id = NEW.org_id AND id = NEW.adjustment_id AND effective_at > NEW.effective_at
        ) THEN
            RAISE EXCEPTION 'Chargeback reversal cannot precede the debit';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER st_chargeback_reversal_protect_history
    BEFORE INSERT OR UPDATE OR DELETE ON st_chargeback_reversal
    FOR EACH ROW EXECUTE FUNCTION protect_st_chargeback_reversal()
    """,
)

RECORD_REQUEST_EVIDENCE_GUARD = (
    "st_evidence_file",
    "trg_st_record_request_evidence",
    """
CREATE OR REPLACE FUNCTION check_st_record_request_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD.record_request_event_uuid IS NOT NULL AND (
            NEW.record_request_event_uuid IS DISTINCT FROM OLD.record_request_event_uuid
            OR NEW.org_id <> OLD.org_id OR NEW.learner_profile_id IS DISTINCT FROM OLD.learner_profile_id
            OR NEW.purpose <> OLD.purpose
        ) THEN
            RAISE EXCEPTION 'Record request evidence scope is immutable';
        END IF;
    END IF;
    IF NEW.record_request_event_uuid IS NOT NULL AND NOT EXISTS (
        SELECT 1
        FROM st_business_event e
        JOIN st_learner_profile p ON p.org_id = e.org_id AND p.learner_uuid = e.subject_uuid
        WHERE e.org_id = NEW.org_id AND e.event_uuid = NEW.record_request_event_uuid
          AND e.action = 'studytomy.record_request.submitted'
          AND e.resource_kind = 'record_request' AND p.id = NEW.learner_profile_id
    ) THEN
        RAISE EXCEPTION 'Record request evidence requires its exact intake and learner';
    END IF;
    RETURN NEW;
END;
$$;
    """,
    """
    CREATE TRIGGER trg_st_record_request_evidence BEFORE INSERT OR UPDATE ON st_evidence_file
    FOR EACH ROW EXECUTE FUNCTION check_st_record_request_evidence()
    """,
)

ASSESSMENT_ARCHIVE_POSTGRES_GUARD = (
    "st_assessment_response_archive",
    "st_assessment_archive_immutable",
    """
    CREATE OR REPLACE FUNCTION reject_st_assessment_archive_mutation() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'UPDATE' OR OLD.retain_until > CURRENT_TIMESTAMP THEN
            RAISE EXCEPTION 'Assessment response archive is protected by its retention period';
        END IF;
        RETURN OLD;
    END;
    $$
    """,
    """
    CREATE TRIGGER st_assessment_archive_immutable
    BEFORE UPDATE OR DELETE ON st_assessment_response_archive
    FOR EACH ROW EXECUTE FUNCTION reject_st_assessment_archive_mutation()
    """,
)

_POSTGRES_GUARDS = (
    ASSESSMENT_ARCHIVE_POSTGRES_GUARD,
    RECORD_REQUEST_EVIDENCE_GUARD,
    CHARGEBACK_REVERSAL_GUARD,
    CHARGEBACK_ADJUSTMENT_GUARD,
    REFUND_SETTLEMENT_GUARD,
    REFUND_RESERVATION_GUARD,
    *COLLECTION_HISTORY_GUARDS,
    *ORDER_HISTORY_GUARDS,
    CAPACITY_HOLD_GUARD,
    COURSE_GRANT_GUARD,
    *_PRACTICE_POSTGRES_GUARDS,
    *_REPORT_HISTORY_POSTGRES_GUARDS,
    (
        "st_course_release",
        "st_course_release_reference_guard",
        f"""
        CREATE OR REPLACE FUNCTION check_st_course_release_references() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF {_COURSE_RELEASE_REFERENCE_CHECK} THEN
                RAISE EXCEPTION 'Study course release references are invalid';
            END IF;
            RETURN NEW;
        END;
        $$
        """,
        """
        CREATE TRIGGER st_course_release_reference_guard
        BEFORE INSERT ON st_course_release
        FOR EACH ROW EXECUTE FUNCTION check_st_course_release_references()
        """,
    ),
    (
        "st_course_release",
        "st_course_release_immutable",
        """
        CREATE OR REPLACE FUNCTION reject_st_course_release_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Study course releases are immutable';
        END;
        $$
        """,
        """
        CREATE TRIGGER st_course_release_immutable
        BEFORE UPDATE OR DELETE ON st_course_release
        FOR EACH ROW EXECUTE FUNCTION reject_st_course_release_mutation()
        """,
    ),
    (
        "st_course_review",
        "st_course_review_immutable",
        """
        CREATE OR REPLACE FUNCTION reject_st_course_review_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Study course reviews are immutable';
        END;
        $$
        """,
        """
        CREATE TRIGGER st_course_review_immutable
        BEFORE UPDATE OR DELETE ON st_course_review
        FOR EACH ROW EXECUTE FUNCTION reject_st_course_review_mutation()
        """,
    ),
    (
        "st_business_event",
        "st_business_event_immutable",
        """
        CREATE OR REPLACE FUNCTION reject_st_business_event_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'st_business_event rows are immutable';
        END;
        $$
        """,
        """
        CREATE TRIGGER st_business_event_immutable
        BEFORE UPDATE OR DELETE ON st_business_event
        FOR EACH ROW EXECUTE FUNCTION reject_st_business_event_mutation()
        """,
    ),
    (
        "st_eligibility_decision",
        "st_eligibility_decision_immutable",
        """
        CREATE OR REPLACE FUNCTION reject_st_eligibility_decision_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'st_eligibility_decision rows are immutable';
        END;
        $$
        """,
        """
        CREATE TRIGGER st_eligibility_decision_immutable
        BEFORE UPDATE OR DELETE ON st_eligibility_decision
        FOR EACH ROW EXECUTE FUNCTION reject_st_eligibility_decision_mutation()
        """,
    ),
    (
        "st_consent_event",
        "st_consent_event_immutable",
        """
        CREATE OR REPLACE FUNCTION reject_st_consent_event_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'st_consent_event rows are immutable';
        END;
        $$
        """,
        """
        CREATE TRIGGER st_consent_event_immutable
        BEFORE UPDATE OR DELETE ON st_consent_event
        FOR EACH ROW EXECUTE FUNCTION reject_st_consent_event_mutation()
        """,
    ),
    (
        "st_offer_version",
        "st_offer_version_immutable",
        """
        CREATE OR REPLACE FUNCTION reject_st_offer_version_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'st_offer_version rows are immutable';
        END;
        $$
        """,
        """
        CREATE TRIGGER st_offer_version_immutable
        BEFORE UPDATE OR DELETE ON st_offer_version
        FOR EACH ROW EXECUTE FUNCTION reject_st_offer_version_mutation()
        """,
    ),
    (
        "st_offer",
        "st_offer_draft_root_protected",
        """
        CREATE OR REPLACE FUNCTION protect_st_offer_draft_root() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'st_offer draft roots are retained';
            END IF;
            IF NEW.offer_uuid IS DISTINCT FROM OLD.offer_uuid
               OR NEW.org_id IS DISTINCT FROM OLD.org_id
               OR NEW.offer_kind IS DISTINCT FROM OLD.offer_kind
               OR NEW.status IS DISTINCT FROM 'draft'
               OR NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id
               OR NEW.created_at IS DISTINCT FROM OLD.created_at
               OR NEW.current_version_no < OLD.current_version_no
               OR NEW.current_version_no > OLD.current_version_no + 1 THEN
                RAISE EXCEPTION 'st_offer roots are draft-only and version-monotonic';
            END IF;
            IF NEW.current_version_no = OLD.current_version_no + 1
               AND NOT EXISTS (
                   SELECT 1 FROM st_offer_version
                   WHERE offer_id = OLD.id AND version_no = NEW.current_version_no
               ) THEN
                RAISE EXCEPTION 'the next st_offer_version must exist before advancing the draft';
            END IF;
            RETURN NEW;
        END;
        $$
        """,
        """
        CREATE TRIGGER st_offer_draft_root_protected
        BEFORE UPDATE OR DELETE ON st_offer
        FOR EACH ROW EXECUTE FUNCTION protect_st_offer_draft_root()
        """,
    ),
)

ASSESSMENT_ARCHIVE_SQLITE_GUARDS = (
    (
        "st_assessment_response_archive",
        "st_assessment_archive_immutable_update",
        """
        CREATE TRIGGER IF NOT EXISTS st_assessment_archive_immutable_update
        BEFORE UPDATE ON st_assessment_response_archive
        BEGIN SELECT RAISE(ABORT, 'Assessment response archive is immutable'); END
        """,
    ),
    (
        "st_assessment_response_archive",
        "st_assessment_archive_retained_delete",
        """
        CREATE TRIGGER IF NOT EXISTS st_assessment_archive_retained_delete
        BEFORE DELETE ON st_assessment_response_archive
        WHEN julianday(OLD.retain_until) > julianday('now')
        BEGIN SELECT RAISE(ABORT, 'Assessment response archive is protected by its retention period'); END
        """,
    ),
)

_SQLITE_GUARDS = (
    *ASSESSMENT_ARCHIVE_SQLITE_GUARDS,
    *_PRACTICE_SQLITE_GUARDS,
    *_REPORT_HISTORY_SQLITE_GUARDS,
    (
        "st_course_release",
        "st_course_release_reference_guard",
        f"""
        CREATE TRIGGER IF NOT EXISTS st_course_release_reference_guard
        BEFORE INSERT ON st_course_release
        WHEN {_COURSE_RELEASE_REFERENCE_CHECK}
        BEGIN SELECT RAISE(ABORT, 'Study course release references are invalid'); END
        """,
    ),
    *(
        (
            "st_course_release",
            f"st_course_release_immutable_{action.lower()}",
            f"""
            CREATE TRIGGER IF NOT EXISTS st_course_release_immutable_{action.lower()}
            BEFORE {action} ON st_course_release
            BEGIN SELECT RAISE(ABORT, 'Study course releases are immutable'); END
            """,
        )
        for action in ("UPDATE", "DELETE")
    ),
    *(
        (
            "st_course_review",
            f"st_course_review_immutable_{action.lower()}",
            f"""
            CREATE TRIGGER IF NOT EXISTS st_course_review_immutable_{action.lower()}
            BEFORE {action} ON st_course_review
            BEGIN SELECT RAISE(ABORT, 'Study course reviews are immutable'); END
            """,
        )
        for action in ("UPDATE", "DELETE")
    ),
    (
        "st_business_event",
        "st_business_event_immutable_update",
        """
        CREATE TRIGGER IF NOT EXISTS st_business_event_immutable_update
        BEFORE UPDATE ON st_business_event
        BEGIN SELECT RAISE(ABORT, 'st_business_event rows are immutable'); END
        """,
    ),
    (
        "st_business_event",
        "st_business_event_immutable_delete",
        """
        CREATE TRIGGER IF NOT EXISTS st_business_event_immutable_delete
        BEFORE DELETE ON st_business_event
        BEGIN SELECT RAISE(ABORT, 'st_business_event rows are immutable'); END
        """,
    ),
    (
        "st_eligibility_decision",
        "st_eligibility_decision_immutable_update",
        """
        CREATE TRIGGER IF NOT EXISTS st_eligibility_decision_immutable_update
        BEFORE UPDATE ON st_eligibility_decision
        BEGIN SELECT RAISE(ABORT, 'st_eligibility_decision rows are immutable'); END
        """,
    ),
    (
        "st_eligibility_decision",
        "st_eligibility_decision_immutable_delete",
        """
        CREATE TRIGGER IF NOT EXISTS st_eligibility_decision_immutable_delete
        BEFORE DELETE ON st_eligibility_decision
        BEGIN SELECT RAISE(ABORT, 'st_eligibility_decision rows are immutable'); END
        """,
    ),
    (
        "st_consent_event",
        "st_consent_event_immutable_update",
        """
        CREATE TRIGGER IF NOT EXISTS st_consent_event_immutable_update
        BEFORE UPDATE ON st_consent_event
        BEGIN SELECT RAISE(ABORT, 'st_consent_event rows are immutable'); END
        """,
    ),
    (
        "st_consent_event",
        "st_consent_event_immutable_delete",
        """
        CREATE TRIGGER IF NOT EXISTS st_consent_event_immutable_delete
        BEFORE DELETE ON st_consent_event
        BEGIN SELECT RAISE(ABORT, 'st_consent_event rows are immutable'); END
        """,
    ),
    (
        "st_offer",
        "st_offer_draft_root_protected_update",
        """
        CREATE TRIGGER IF NOT EXISTS st_offer_draft_root_protected_update
        BEFORE UPDATE ON st_offer
        WHEN NEW.offer_uuid IS NOT OLD.offer_uuid
          OR NEW.org_id IS NOT OLD.org_id
          OR NEW.offer_kind IS NOT OLD.offer_kind
          OR NEW.status <> 'draft'
          OR NEW.created_by_user_id IS NOT OLD.created_by_user_id
          OR NEW.created_at IS NOT OLD.created_at
          OR NEW.current_version_no < OLD.current_version_no
          OR NEW.current_version_no > OLD.current_version_no + 1
          OR (
              NEW.current_version_no = OLD.current_version_no + 1
              AND NOT EXISTS (
                  SELECT 1 FROM st_offer_version
                  WHERE offer_id = OLD.id AND version_no = NEW.current_version_no
              )
          )
        BEGIN SELECT RAISE(ABORT, 'st_offer roots are draft-only and version-monotonic'); END
        """,
    ),
    (
        "st_offer",
        "st_offer_draft_root_protected_delete",
        """
        CREATE TRIGGER IF NOT EXISTS st_offer_draft_root_protected_delete
        BEFORE DELETE ON st_offer
        BEGIN SELECT RAISE(ABORT, 'st_offer draft roots are retained'); END
        """,
    ),
    (
        "st_offer_version",
        "st_offer_version_immutable_update",
        """
        CREATE TRIGGER IF NOT EXISTS st_offer_version_immutable_update
        BEFORE UPDATE ON st_offer_version
        BEGIN SELECT RAISE(ABORT, 'st_offer_version rows are immutable'); END
        """,
    ),
    (
        "st_offer_version",
        "st_offer_version_immutable_delete",
        """
        CREATE TRIGGER IF NOT EXISTS st_offer_version_immutable_delete
        BEFORE DELETE ON st_offer_version
        BEGIN SELECT RAISE(ABORT, 'st_offer_version rows are immutable'); END
        """,
    ),
)


def ensure_studytomy_database_guards(connection: Connection) -> list[str]:
    """Install missing invariant triggers on fresh and legacy create_all schemas."""
    dialect = connection.dialect.name
    installed: list[str] = []

    if dialect == "postgresql":
        inspector = inspect(connection)
        for table, trigger, function_sql, trigger_sql in _POSTGRES_GUARDS:
            if not inspector.has_table(table):
                continue
            exists = connection.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_trigger
                        WHERE tgname = :trigger
                          AND tgrelid = to_regclass(:table)
                          AND NOT tgisinternal
                    )
                    """
                ),
                {"trigger": trigger, "table": table},
            ).scalar_one()
            if exists:
                continue
            connection.execute(text(function_sql))
            connection.execute(text(f'DROP TRIGGER IF EXISTS "{trigger}" ON "{table}"'))
            connection.execute(text(trigger_sql))
            installed.append(trigger)
    elif dialect == "sqlite":
        inspector = inspect(connection)
        for table, trigger, statement in _SQLITE_GUARDS:
            if not inspector.has_table(table):
                continue
            exists = connection.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM sqlite_master "
                    "WHERE type = 'trigger' AND name = :trigger)"
                ),
                {"trigger": trigger},
            ).scalar_one()
            if exists:
                continue
            connection.execute(text(statement))
            installed.append(trigger)

    from src.core.adaptive_curriculum_schema import ensure_adaptive_curriculum_guards
    from src.core.adaptive_diagnostic_schema import ensure_adaptive_diagnostic_guard
    from src.core.adaptive_evidence_schema import ensure_adaptive_evidence_guards
    from src.core.adaptive_item_schema import ensure_adaptive_item_guard

    return (
        installed + ensure_adaptive_curriculum_guards(connection)
        + ensure_adaptive_item_guard(connection) + ensure_adaptive_diagnostic_guard(connection)
        + ensure_adaptive_evidence_guards(connection)
    )
