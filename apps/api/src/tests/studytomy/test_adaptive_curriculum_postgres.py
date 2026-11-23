"""Exercise curriculum integrity with real PostgreSQL sessions."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.db.courses.activities import Activity, ActivitySubTypeEnum, ActivityTypeEnum
from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.studytomy.adaptive import (
    CurriculumDraftRead,
    CurriculumReviewedRead,
    StudyConcept,
    StudyConceptActivity,
    StudyConceptPrerequisite,
    StudyCurriculumRevision,
)

pytestmark = pytest.mark.studytomy_postgres


async def _revision(db, org_id, key="synthetic-curriculum", revision=1):
    row = StudyCurriculumRevision(
        org_id=org_id,
        curriculum_key=key,
        revision=revision,
        subject="Synthetic mathematics",
        level="Test level",
        language="en",
        source_reference="test-fixture",
        rights_reference="test-fixture-rights",
    )
    db.add(row)
    await db.flush()
    return row


def _concept(org_id, revision_id, code):
    return StudyConcept(
        org_id=org_id,
        curriculum_revision_id=revision_id,
        code=code,
        title=code,
        objective="Demonstrate the synthetic concept.",
    )


def _edge(org_id, revision_id, concept_id, prerequisite_id):
    return StudyConceptPrerequisite(
        org_id=org_id,
        curriculum_revision_id=revision_id,
        concept_id=concept_id,
        prerequisite_id=prerequisite_id,
    )


async def test_curriculum_ownership_uniqueness_and_cycle_checks(postgres_test_rows):
    rows = postgres_test_rows
    async with rows.session_factory() as db:
        root = await _revision(db, rows.org_id)
        other = await _revision(db, rows.org_id, revision=2)
        foreign_org = Organization(
            name="Synthetic other tenant",
            slug=f"tenant-{rows.token}",
            email=f"tenant-{rows.token}@example.invalid",
            org_uuid=f"org_{uuid4()}",
        )
        db.add(foreign_org)
        await db.flush()
        a = _concept(rows.org_id, root.id, "a")
        b = _concept(rows.org_id, root.id, "b")
        foreign = _concept(rows.org_id, other.id, "a")
        db.add_all([a, b, foreign])
        await db.flush()
        db.add(_edge(rows.org_id, root.id, a.id, b.id))
        await db.flush()

        invalid_rows = [
            _concept(rows.org_id, root.id, "a"),
            _concept(foreign_org.id, root.id, "wrong-org"),
            _edge(rows.org_id, root.id, a.id, foreign.id),
            _edge(rows.org_id, root.id, b.id, b.id),
            _edge(rows.org_id, root.id, b.id, a.id),
        ]
        for invalid in invalid_rows:
            with pytest.raises(IntegrityError):
                async with db.begin_nested():
                    db.add(invalid)
                    await db.flush()
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                await _revision(db, rows.org_id)
        assert (
            await db.execute(
                select(StudyConceptPrerequisite.concept_id).where(
                    StudyConceptPrerequisite.curriculum_revision_id == root.id,
                )
            )
        ).scalars().all() == [a.id]


async def test_reviewed_definition_and_children_cannot_be_rewritten(postgres_test_rows):
    rows = postgres_test_rows
    async with rows.session_factory() as db:
        root = await _revision(db, rows.org_id)
        concept = _concept(rows.org_id, root.id, "frozen")
        db.add(concept)
        await db.flush()
        root_id, concept_id = root.id, concept.id
        assert CurriculumDraftRead.model_validate(root).status == "draft"
        root.status = "published"
        root.reviewer_id = rows.user_id
        root.approved_at = datetime.now(UTC)
        root.content_digest = "a" * 64
        root.version = 2
        await db.flush()
        assert CurriculumReviewedRead.model_validate(root).content_digest == "a" * 64

        change_root = update(StudyCurriculumRevision).where(
            StudyCurriculumRevision.id == root_id
        )
        change_child = update(StudyConcept).where(StudyConcept.id == concept_id)
        for mutation in (
            change_root.values(subject="rewritten"),
            change_root.values(status="retired", version=3, subject="rewritten"),
            change_root.values(status="draft"),
            change_child.values(title="rewritten"),
            delete(StudyConcept).where(StudyConcept.id == concept_id),
        ):
            with pytest.raises(IntegrityError):
                async with db.begin_nested():
                    await db.execute(
                        mutation.execution_options(synchronize_session=False)
                    )
        await db.execute(change_root.values(status="retired", version=3))
        assert root.status == "retired"


async def test_activity_mapping_requires_current_native_pair_but_retains_snapshot(
    postgres_test_rows,
):
    rows = postgres_test_rows
    async with rows.session_factory() as db:
        root = await _revision(db, rows.org_id)
        concept = _concept(rows.org_id, root.id, "mapped")
        first = Course(
            name="Synthetic source",
            org_id=rows.org_id,
            course_uuid=f"course_{uuid4()}",
            public=False,
            open_to_contributors=False,
        )
        second = Course(
            name="Synthetic peer",
            org_id=rows.org_id,
            course_uuid=f"course_{uuid4()}",
            public=False,
            open_to_contributors=False,
        )
        db.add_all([concept, first, second])
        await db.flush()
        lesson = Activity(
            name="Synthetic lesson",
            activity_uuid=f"activity_{uuid4()}",
            org_id=rows.org_id,
            course_id=first.id,
            activity_type=ActivityTypeEnum.TYPE_DYNAMIC,
            activity_sub_type=ActivitySubTypeEnum.SUBTYPE_DYNAMIC_PAGE,
        )
        db.add(lesson)
        await db.flush()
        fields = {
            "org_id": rows.org_id,
            "curriculum_revision_id": root.id,
            "concept_id": concept.id,
            "course_id": first.id,
            "course_uuid": first.course_uuid,
            "activity_id": lesson.id,
            "activity_uuid": lesson.activity_uuid,
            "activity_version": lesson.current_version,
            "activity_digest": "b" * 64,
            "usage": "teach",
        }
        for changed in (
            {"course_id": second.id, "course_uuid": second.course_uuid},
            {"activity_version": lesson.current_version + 1},
            {"activity_id": lesson.id + 100_000},
        ):
            with pytest.raises(IntegrityError, match="needs a current lesson"):
                async with db.begin_nested():
                    db.add(StudyConceptActivity(**(fields | changed)))
                    await db.flush()

        mapping = StudyConceptActivity(**fields)
        db.add(mapping)
        await db.flush()
        await db.delete(lesson)
        await db.flush()
        await db.refresh(mapping)
        assert mapping.activity_uuid == fields["activity_uuid"]


@pytest.mark.parametrize(
    ("isolation", "rejection"),
    [("READ COMMITTED", "23514"), ("REPEATABLE READ", "40001")],
)
async def test_opposing_prerequisite_writes_serialize_on_postgres(
    postgres_test_rows,
    isolation,
    rejection,
):
    rows = postgres_test_rows
    async with rows.session_factory() as db, db.begin():
        root = await _revision(db, rows.org_id)
        a = _concept(rows.org_id, root.id, "a")
        b = _concept(rows.org_id, root.id, "b")
        db.add_all([a, b])
        await db.flush()
        root_id, a_id, b_id = root.id, a.id, b.id

    try:
        async with rows.session_factory() as first, rows.session_factory() as second:
            await second.execute(text(f"SET TRANSACTION ISOLATION LEVEL {isolation}"))
            second_pid = (
                await second.execute(text("SELECT pg_backend_pid()"))
            ).scalar_one()
            first.add(_edge(rows.org_id, root_id, a_id, b_id))
            await first.flush()
            second.add(_edge(rows.org_id, root_id, b_id, a_id))
            async with asyncio.TaskGroup() as tasks:

                async def rejected_edge():
                    with pytest.raises(DBAPIError) as error:
                        await second.flush()
                    assert error.value.orig.sqlstate == rejection

                tasks.create_task(rejected_edge())
                async with rows.session_factory() as observer, asyncio.timeout(5):
                    while not (
                        await observer.execute(
                            text("SELECT cardinality(pg_blocking_pids(:pid))"),
                            {"pid": second_pid},
                        )
                    ).scalar_one():
                        await asyncio.sleep(0.01)
                await first.commit()
            await second.rollback()
            assert (
                await second.execute(
                    select(StudyConceptPrerequisite.concept_id).where(
                        StudyConceptPrerequisite.curriculum_revision_id == root_id,
                    )
                )
            ).scalars().all() == [a_id]
    finally:
        async with rows.session_factory() as db, db.begin():
            await db.execute(
                delete(StudyConceptPrerequisite).where(
                    StudyConceptPrerequisite.curriculum_revision_id == root_id,
                )
            )
            await db.execute(
                delete(StudyConcept).where(
                    StudyConcept.curriculum_revision_id == root_id
                )
            )
            await db.execute(
                delete(StudyCurriculumRevision).where(
                    StudyCurriculumRevision.id == root_id
                )
            )
