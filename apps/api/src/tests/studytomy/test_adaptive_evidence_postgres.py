"""Verify adaptive round and evidence invariants on real PostgreSQL."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError

from src.db.organizations import Organization
from src.db.studytomy.adaptive import StudyConcept, StudyCurriculumRevision
from src.db.studytomy.adaptive_diagnostics import StudyDiagnosticRevision
from src.db.studytomy.adaptive_evidence import (
    StudyAttemptItem,
    StudyConceptMastery,
    StudyLearningAttempt,
    StudyLearningEvidence,
)
from src.db.studytomy.adaptive_items import StudyItemRevision
from src.db.studytomy.family import StudyLearnerProfile
from src.db.users import User

pytestmark = pytest.mark.studytomy_postgres
DEFINITION_DIGEST = "b" * 64
ITEM_DIGEST = "c" * 64


@dataclass
class Landscape:
    org_id: int
    user_id: int
    learner_id: int
    curriculum_id: int
    concept_id: int
    diagnostic_id: int
    blueprint_uuid: str
    course_uuid: str
    items: list[StudyItemRevision]

    def attempt(self, **changes):
        values = {
            "org_id": self.org_id,
            "learner_profile_id": self.learner_id,
            "actor_user_id": self.user_id,
            "actor_capacity": "learner",
            "curriculum_revision_id": self.curriculum_id,
            "kind": "diagnostic",
            "diagnostic_revision_id": self.diagnostic_id,
            "blueprint_uuid": self.blueprint_uuid,
            "source_course_uuid": self.course_uuid,
            "manifest_digest": DEFINITION_DIGEST,
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
        }
        return StudyLearningAttempt(**(values | changes))

    def issue(self, attempt, *, index=0, **changes):
        item = self.items[index]
        values = {
            "org_id": self.org_id,
            "learner_profile_id": self.learner_id,
            "curriculum_revision_id": self.curriculum_id,
            "concept_id": self.concept_id,
            "attempt_id": attempt.id,
            "item_revision_id": item.id,
            "item_family_uuid": item.item_family_uuid,
            "ordinal": attempt.next_ordinal,
            "is_diagnostic": attempt.kind == "diagnostic",
        }
        return StudyAttemptItem(**(values | changes))

    def observation(self, issued, **changes):
        values = {
            "org_id": self.org_id,
            "learner_profile_id": self.learner_id,
            "curriculum_revision_id": self.curriculum_id,
            "concept_id": self.concept_id,
            "attempt_item_id": issued.id,
            "logical_source_uuid": issued.uuid,
            "actor_user_id": self.user_id,
            "outcome": "correct",
            "score": 1,
            "independence": "independent",
            "eligible": True,
            "eligibility_reason": "synthetic",
            "observed_at": issued.responded_at,
            "evaluator_version": "adaptive_objective_v1",
        }
        return StudyLearningEvidence(**(values | changes))


async def _seed(db, policy=None):
    token = uuid4().hex
    org = Organization(
        name="Synthetic evidence org",
        slug=f"evidence-{token}",
        email=f"admin-{token}@example.invalid",
        org_uuid=f"org_{token}",
    )
    user = User(
        username=f"evidence-{token}",
        email=f"learner-{token}@example.invalid",
        user_uuid=f"user_{token}",
        first_name="Synthetic",
        last_name="Learner",
    )
    db.add_all([org, user])
    await db.flush()
    learner = StudyLearnerProfile(
        org_id=org.id,
        user_id=user.id,
        created_by_user_id=user.id,
        status="active",
        control_mode="independent",
    )
    curriculum = StudyCurriculumRevision(
        org_id=org.id,
        curriculum_key="fixture",
        owner_course_uuid=f"course_{token}",
        subject="Synthetic",
        level="Test",
        language="en",
        source_reference="test",
        rights_reference="test",
    )
    db.add_all([learner, curriculum])
    await db.flush()
    concept = StudyConcept(
        org_id=org.id,
        curriculum_revision_id=curriculum.id,
        code="a",
        title="a",
        objective="synthetic",
    )
    db.add(concept)
    await db.flush()
    curriculum.status = "published"
    curriculum.reviewer_id = user.id
    curriculum.approved_at = datetime.now(UTC)
    curriculum.content_digest = "a" * 64
    curriculum.version = 2
    items = [
        StudyItemRevision(
            org_id=org.id,
            curriculum_revision_id=curriculum.id,
            concept_id=concept.id,
            item_family_uuid=f"item_family_{uuid4()}",
            revision=1,
            kind="number",
            difficulty=1,
            course_uuid=curriculum.owner_course_uuid,
            source_digest=("d" if index else "e") * 64,
            source_exposure="private_original",
            evaluator_version="adaptive_objective_v1",
            rights_reference="test",
            public_question={"kind": "number", "prompt": "Synthetic question"},
            private_answer={"expected": "12", "tolerance": "0"},
        )
        for index in range(2)
    ]
    db.add_all(items)
    await db.flush()
    for item in items:
        item.status = "published"
        item.version = 2
        item.approved_at = datetime.now(UTC)
        item.reviewer_id = user.id
        item.content_digest = ITEM_DIGEST
    diagnostic = StudyDiagnosticRevision(
        org_id=org.id,
        curriculum_revision_id=curriculum.id,
        blueprint_uuid=f"blueprint_{uuid4()}",
        name="Synthetic diagnostic",
        revision=1,
        selector_version="adaptive_coverage_v1",
        mastery_policy_uuid=policy.policy_uuid if policy else "synthetic-policy",
        manifest={
            "pool": [
                {"uuid": item.uuid, "content_digest": ITEM_DIGEST} for item in items
            ]
        },
    )
    db.add(diagnostic)
    if policy is not None:
        diagnostic.manifest = diagnostic.manifest | {
            "mastery_policy_digest": policy.digest
        }
    await db.flush()
    diagnostic.status = "published"
    diagnostic.reviewer_id = user.id
    diagnostic.approved_at = datetime.now(UTC)
    diagnostic.content_digest = DEFINITION_DIGEST
    diagnostic.version = 2
    await db.flush()
    return Landscape(
        org.id,
        user.id,
        learner.id,
        curriculum.id,
        concept.id,
        diagnostic.id,
        diagnostic.blueprint_uuid,
        curriculum.owner_course_uuid,
        items,
    )


async def _answer(db, issued):
    issued.response_state = "answered"
    issued.response_version += 1
    issued.normalized_response = "12"
    issued.response_digest = "f" * 64
    issued.responded_at = datetime.now(UTC)
    await db.flush()


async def _wait_for_postgres_lock(factory, pid):
    async with factory() as observer, asyncio.timeout(5):
        while not (
            await observer.execute(
                text("SELECT cardinality(pg_blocking_pids(:pid))"),
                {"pid": pid},
            )
        ).scalar_one():
            await asyncio.sleep(0.01)


async def test_outstanding_item_and_accepted_response_are_protected(
    postgres_session_factory,
):
    async with postgres_session_factory() as db:
        rows = await _seed(db)
        attempt = rows.attempt()
        db.add(attempt)
        await db.flush()
        issued = rows.issue(attempt)
        db.add(issued)
        await db.flush()
        attempt.next_ordinal += 1
        attempt.version += 1
        await db.flush()
        with pytest.raises(IntegrityError, match="uq_st_attempt_item_outstanding"):
            async with db.begin_nested():
                db.add(rows.issue(attempt, index=1))
                await db.flush()
        await _answer(db, issued)
        with pytest.raises(
            IntegrityError, match="uq_st_attempt_diagnostic_item_family"
        ):
            async with db.begin_nested():
                db.add(rows.issue(attempt))
                await db.flush()
        source_id = issued.id
        with pytest.raises(
            IntegrityError,
            match="Accepted responses and issued definitions are immutable",
        ):
            async with db.begin_nested():
                await db.execute(
                    update(StudyAttemptItem)
                    .where(StudyAttemptItem.id == source_id)
                    .values(
                        normalized_response="13",
                        response_version=3,
                    )
                    .execution_options(synchronize_session=False)
                )
        assert issued.normalized_response == "12"


async def test_attempt_scope_and_native_round_identity(postgres_session_factory):
    async with postgres_session_factory() as db:
        rows = await _seed(db)
        other = await _seed(db)
        with pytest.raises(IntegrityError, match="fk_st_attempt_learner"):
            async with db.begin_nested():
                db.add(
                    rows.attempt(
                        learner_profile_id=other.learner_id,
                        actor_capacity="system",
                        actor_reference="synthetic-system",
                    )
                )
                await db.flush()
        with pytest.raises(
            IntegrityError, match="requires the current published manifest"
        ):
            async with db.begin_nested():
                db.add(rows.attempt(manifest_digest="6" * 64))
                await db.flush()
        assignment = {
            "kind": "assignment",
            "diagnostic_revision_id": None,
            "blueprint_uuid": None,
            "source_assignment_uuid": "assignment_fixture",
            "source_submission_uuid": "submission_fixture",
            "source_attempt_number": 1,
        }
        original = rows.attempt(source_round_uuid="round_original", **assignment)
        db.add(original)
        await db.flush()
        with pytest.raises(IntegrityError, match="uq_st_attempt_native_round"):
            async with db.begin_nested():
                db.add(rows.attempt(source_round_uuid="round_duplicate", **assignment))
                await db.flush()
        retry = rows.attempt(
            source_round_uuid="round_retry",
            **(assignment | {"source_attempt_number": 2}),
        )
        recreated = rows.attempt(
            source_round_uuid="round_recreated",
            **(assignment | {"source_submission_uuid": "submission_recreated"}),
        )
        db.add_all([retry, recreated])
        await db.flush()
        assert len({original.id, retry.id, recreated.id}) == 3


async def test_append_only_corrections_and_projection_watermark(
    postgres_session_factory,
):
    async with postgres_session_factory() as db:
        rows = await _seed(db)
        attempt = rows.attempt()
        db.add(attempt)
        await db.flush()
        issued = rows.issue(attempt)
        db.add(issued)
        await db.flush()
        with pytest.raises(IntegrityError, match="accepted response"):
            async with db.begin_nested():
                db.add(rows.observation(issued, observed_at=issued.issued_at))
                await db.flush()
        await _answer(db, issued)
        original = rows.observation(issued)
        db.add(original)
        await db.flush()
        assert original.evidence_sequence == 1
        with pytest.raises(IntegrityError, match="uq_st_evidence_revision"):
            async with db.begin_nested():
                db.add(rows.observation(issued))
                await db.flush()
        with pytest.raises(IntegrityError, match="preceding result revision"):
            async with db.begin_nested():
                db.add(
                    rows.observation(
                        issued,
                        result_revision=3,
                        supersedes_id=original.id,
                        correction_reason="review",
                    )
                )
                await db.flush()
        correction = rows.observation(
            issued,
            result_revision=2,
            supersedes_id=original.id,
            correction_reason="review",
            outcome="incorrect",
            score=0,
        )
        db.add(correction)
        await db.flush()
        assert correction.evidence_sequence == 2 and original.score == 1
        from src.services.studytomy.metrics import assessment_evidence_breakdown

        period = {
            "start": correction.observed_at - timedelta(seconds=1),
            "end": correction.observed_at + timedelta(seconds=1),
        }
        assert await assessment_evidence_breakdown(
            db, org_id=rows.org_id, curriculum_revision_id=rows.curriculum_id, **period,
        ) == [{
            "independence": "independent", "evidence_outcome": "incorrect", "observations": 1,
            "mastery_eligible_observations": 1,
        }]
        assert await assessment_evidence_breakdown(
            db, org_id=rows.org_id, curriculum_revision_id=rows.curriculum_id + 1, **period,
        ) == []
        with pytest.raises(IntegrityError, match="append-only"):
            async with db.begin_nested():
                await db.execute(
                    update(StudyLearningEvidence)
                    .where(StudyLearningEvidence.id == original.id)
                    .values(score=0)
                    .execution_options(synchronize_session=False)
                )
        projection = StudyConceptMastery(
            org_id=rows.org_id,
            learner_profile_id=rows.learner_id,
            curriculum_revision_id=rows.curriculum_id,
            concept_id=rows.concept_id,
            mastery_policy_uuid="synthetic-policy",
            mastery_policy_digest="9" * 64,
            reducer_version="adaptive_mastery_v1",
            evidence_watermark=2,
            state="not_assessed",
            eligible_count=0,
            success_count=0,
            assisted_count=0,
            unknown_count=0,
        )
        db.add(projection)
        await db.flush()
        assert projection.score is None
        for watermark in (1, 3):
            with pytest.raises(IntegrityError, match="watermark|move backward"):
                async with db.begin_nested():
                    await db.execute(
                        update(StudyConceptMastery)
                        .where(StudyConceptMastery.id == projection.id)
                        .values(evidence_watermark=watermark, version=2)
                        .execution_options(synchronize_session=False)
                    )


        attempt.next_ordinal += 1
        attempt.version += 1
        await db.flush()
        assisted_item = rows.issue(attempt, index=1)
        db.add(assisted_item)
        await db.flush()
        await _answer(db, assisted_item)
        db.add_all([
            rows.observation(
                assisted_item, independence="assisted", eligible=False,
                eligibility_reason="assisted",
            ),
            rows.observation(
                issued, result_revision=3, supersedes_id=correction.id,
                correction_reason="invalidated", is_retraction=True,
                outcome="not_scored", score=None, eligible=False,
                eligibility_reason="retracted",
            ),
        ])
        await db.flush()
        period["end"] = assisted_item.responded_at + timedelta(seconds=1)
        assert await assessment_evidence_breakdown(
            db, org_id=rows.org_id, curriculum_revision_id=rows.curriculum_id, **period,
        ) == [
            {"independence": "assisted", "evidence_outcome": "correct", "observations": 1,
             "mastery_eligible_observations": 0},
            {"independence": "independent", "evidence_outcome": "retracted", "observations": 1,
             "mastery_eligible_observations": 0},
        ]


async def test_concurrent_diagnostic_start_uses_one_active_round(
    postgres_session_factory,
):
    async with postgres_session_factory() as db, db.begin():
        rows = await _seed(db)
    async with (
        postgres_session_factory() as first,
        postgres_session_factory() as second,
    ):
        first.add(rows.attempt())
        await first.flush()
        pid = (await second.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        second.add(rows.attempt())
        async with asyncio.TaskGroup() as tasks:
            pending = tasks.create_task(_rejected_start(second))
            await _wait_for_postgres_lock(postgres_session_factory, pid)
            await first.commit()
        assert pending.result() == "23505"


async def _rejected_start(db):
    with pytest.raises(
        IntegrityError, match="uq_st_attempt_active_diagnostic"
    ) as error:
        await db.flush()
    return error.value.orig.sqlstate


async def test_concurrent_evidence_across_rounds_gets_one_learner_sequence(
    postgres_session_factory,
):
    async with postgres_session_factory() as db, db.begin():
        rows = await _seed(db)
        accepted = []
        for index in range(2):
            attempt = rows.attempt(
                kind="practice", diagnostic_revision_id=None, blueprint_uuid=None
            )
            db.add(attempt)
            await db.flush()
            issued = rows.issue(attempt, index=index)
            db.add(issued)
            await db.flush()
            await _answer(db, issued)
            accepted.append(issued)
    async with (
        postgres_session_factory() as first,
        postgres_session_factory() as second,
    ):
        first_event = rows.observation(accepted[0])
        first.add(first_event)
        await first.flush()
        pid = (await second.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        second_event = rows.observation(accepted[1])
        second.add(second_event)
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(second.flush())
            await _wait_for_postgres_lock(postgres_session_factory, pid)
            await first.commit()
        await second.commit()
    assert [first_event.evidence_sequence, second_event.evidence_sequence] == [1, 2]
    async with postgres_session_factory() as db:
        sequences = (
            (
                await db.execute(
                    select(StudyLearningEvidence.evidence_sequence)
                    .where(
                        StudyLearningEvidence.org_id == rows.org_id,
                        StudyLearningEvidence.learner_profile_id == rows.learner_id,
                    )
                    .order_by(StudyLearningEvidence.evidence_sequence)
                )
            )
            .scalars()
            .all()
        )
    assert sequences == [1, 2]
