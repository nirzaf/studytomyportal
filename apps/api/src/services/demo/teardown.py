"""Remove the demo organization completely.

Most of what the demo owns is reached by the ``ON DELETE CASCADE`` on the
organization foreign key, which ``test_demo_teardown`` proves table by table.
This module exists for the three things that cascade cannot reach:

* the seeded students, who are ``User`` rows and belong to no organization;
* ``ResourceAuthor``, which references content by a bare uuid string with no
  foreign key at all;
* uploaded files, which are not in the database.

It lives here rather than in ``cli.py`` so it can be tested against a session
instead of only against a live deployment.
"""

import logging

from sqlalchemy import delete, literal, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.courses import Course
from src.db.demo_state import DEMO_STATE_ID, DemoState
from src.db.organizations import Organization
from src.db.podcasts.podcasts import Podcast
from src.db.resource_authors import ResourceAuthor
from src.db.user_organizations import UserOrganization
from src.db.users import User
from src.services.demo import flags

logger = logging.getLogger(__name__)

# Every direct organization reference in these tables is retention-sensitive.
# Keep this list in sync with the scoped FK metadata test below.
STUDYTOMY_ORG_RETENTION_TABLES = frozenset(
    {
        "st_assessment_response_archive",
        "st_attempt_item",
        "st_business_event",
        "st_concept",
        "st_concept_activity",
        "st_concept_mastery",
        "st_concept_prerequisite",
        "st_consent_event",
        "st_course_review",
        "st_course_release",
        "st_curriculum_revision",
        "st_diagnostic_revision",
        "st_eligibility_decision",
        "st_evidence_file",
        "st_idempotency_record",
        "st_item_revision",
        "st_learner_profile",
        "st_learning_attempt",
        "st_learning_evidence",
        "st_learning_surface",
        "st_offer",
        "st_order",
        "st_outbox_record",
        "st_owner_assignment",
        "st_policy_approval",
        "st_policy_version",
        "st_progress_report",
        "st_relationship",
        "st_report_recipient",
        "st_report_review_request",
        "st_sharing_grant",
        "st_service_case",
        "st_staff_scope",
        "st_syllabus_source",
        "st_tutor_session",
        "st_tutor_turn",
    }
)


class DemoTeardownBlockedError(RuntimeError):
    """A demo organization or seeded account still owns retained Study records."""


async def _assert_no_studytomy_records(
    db_session: AsyncSession,
    org_ids: list[int],
    student_ids: list[int],
) -> None:
    """Refuse demo deletion before any mutation if it would break Study history."""
    if not org_ids and not student_ids:
        return

    from src.core.migrations import load_models

    metadata = load_models()
    for table_name in STUDYTOMY_ORG_RETENTION_TABLES:
        table = metadata.tables[table_name]
        if org_ids:
            retained = await db_session.execute(
                select(literal(1))
                .select_from(table)
                .where(table.c.org_id.in_(org_ids))
                .limit(1)
            )
            if retained.first() is not None:
                raise DemoTeardownBlockedError(
                    "Demo teardown blocked: Studytomy records require retention review."
                )

    if not student_ids:
        return

    for table in metadata.tables.values():
        if not table.name.startswith("st_"):
            continue
        user_columns = [
            column
            for column in table.columns
            if any(fk.column.table.name == "user" for fk in column.foreign_keys)
        ]
        if not user_columns:
            continue
        retained = await db_session.execute(
            select(literal(1))
            .select_from(table)
            .where(or_(*(column.in_(student_ids) for column in user_columns)))
            .limit(1)
        )
        if retained.first() is not None:
            raise DemoTeardownBlockedError(
                "Demo teardown blocked: Studytomy records require retention review."
            )


async def teardown_demo(db_session: AsyncSession) -> list[str]:
    """Delete every demo organization and everything belonging to it.

    Returns one human-readable line per organization removed. Does not commit;
    the caller owns the transaction.
    """
    from src.services.courses.transfer.storage_utils import delete_storage_directory

    orgs = (
        await db_session.execute(
            select(Organization).where(Organization.is_demo.is_(True))
        )
    ).scalars().all()

    removed: list[str] = []

    # Preflight every organization and seeded user before touching any row or
    # file. If a later demo org is blocked, earlier orgs must not be half-removed.
    students_by_org: dict[int, list[tuple[int, str]]] = {}
    for org in orgs:
        students = (
            await db_session.execute(
                select(User.id, User.user_uuid)
                .join(UserOrganization, UserOrganization.user_id == User.id)
                .where(
                    UserOrganization.org_id == org.id,
                    User.signup_method == flags.DEMO_SIGNUP_METHOD,
                )
            )
        ).all()
        students_by_org[org.id] = list(students)

    await _assert_no_studytomy_records(
        db_session,
        [org.id for org in orgs],
        [student_id for students in students_by_org.values() for student_id, _ in students],
    )

    for org in orgs:
        # Collect the students before deleting the org: their membership rows
        # are what identifies them, and the cascade removes those.
        students = students_by_org[org.id]
        student_ids = [row[0] for row in students]
        student_uuids = [row[1] for row in students]

        # ResourceAuthor points at content by a bare uuid string with no foreign
        # key, so the org cascade cannot reach it. Rows written for demo
        # students go with those students, but a visitor who authored something
        # here is a real user who survives teardown, and their row would be left
        # pointing at a course that no longer exists.
        authored_uuids = list(
            (
                await db_session.execute(
                    select(Course.course_uuid).where(Course.org_id == org.id)
                )
            ).scalars().all()
        ) + list(
            (
                await db_session.execute(
                    select(Podcast.podcast_uuid).where(Podcast.org_id == org.id)
                )
            ).scalars().all()
        )
        if authored_uuids:
            await db_session.execute(
                delete(ResourceAuthor).where(
                    ResourceAuthor.resource_uuid.in_(authored_uuids)
                )
            )

        await db_session.delete(org)
        await db_session.flush()

        if student_ids:
            await db_session.execute(delete(User).where(User.id.in_(student_ids)))

        # Uploaded files are not in the database: course media and org branding
        # sit under the org, avatars under each student. Without this every
        # teardown leaves the lot behind, and the next provision writes it all
        # again beside the orphans.
        _delete_files(delete_storage_directory, f"content/orgs/{org.org_uuid}")
        for user_uuid in student_uuids:
            _delete_files(delete_storage_directory, f"content/users/{user_uuid}")

        removed.append(
            f"Removed demo org /{org.slug}, {len(student_ids)} students "
            f"and their uploaded files"
        )

    await db_session.execute(delete(DemoState).where(DemoState.id == DEMO_STATE_ID))

    return removed


def _delete_files(delete_storage_directory, path: str) -> None:
    """Storage failures must not abort a teardown that has already deleted rows.

    The database work is committed by the caller; if the bucket is unreachable
    the right outcome is a logged warning and orphaned files, not a half-torn
    org that the next provision then collides with.
    """
    try:
        delete_storage_directory(path)
    except Exception:
        logger.warning("Demo teardown could not remove %s", path, exc_info=True)
