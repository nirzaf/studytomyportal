import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.roles import Role, RoleTypeEnum
from src.db.studytomy.adaptive import StudyConcept
from src.db.studytomy.adaptive_evidence import (
    StudyConceptMastery,
    StudyLearningEvidence,
)
from src.db.studytomy.family import StudyLearnerProfile
from src.db.studytomy.governance import (
    StudyBusinessEvent,
    StudyOwnerFunction,
    StudyPolicyApproval,
    StudyPolicyKey,
)
from src.db.user_organizations import UserOrganization
from src.db.users import PublicUser, User
from src.security.studytomy_access import StudytomyCapabilityName
from src.services.studytomy import adaptive_evidence as service
from src.services.studytomy.policies import POLICY_REQUIRED_RESPONSIBILITIES
from src.tests.studytomy.test_adaptive_evidence_postgres import (
    _answer,
    _seed,
    _wait_for_postgres_lock,
)
from src.tests.studytomy.test_adaptive_mastery import POLICY
from src.tests.studytomy.test_studytomy_access import (
    _seed_active_owner,
    _seed_approved_policy,
    _seed_staff_scope,
)

pytestmark = pytest.mark.studytomy_postgres


async def _accept(db, rows, **changes):
    attempt = rows.attempt(**changes)
    db.add(attempt)
    await db.flush()
    issued = rows.issue(attempt)
    db.add(issued)
    await db.flush()
    await _answer(db, issued)
    return issued


async def _projection(db, learner_id):
    return (
        await db.execute(
            select(StudyConceptMastery)
            .where(
                StudyConceptMastery.learner_profile_id == learner_id,
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def test_response_replay_and_projection_failure_share_one_transaction(
    postgres_session_factory,
    monkeypatch,
):
    async with postgres_session_factory() as db:
        rows = await _seed(db, POLICY)
        issued = await _accept(db, rows)
        actor = PublicUser.model_validate(await db.get(User, rows.user_id))
        store = service._store_projection

        async def fail_projection(*args, **kwargs):
            raise RuntimeError("synthetic projection failure")

        monkeypatch.setattr(service, "_store_projection", fail_projection)
        with pytest.raises(RuntimeError, match="synthetic projection failure"):
            async with db.begin_nested():
                await service.append_objective_evidence(
                    db, rows.org_id, actor, issued.uuid, POLICY, [1]
                )
        assert await service._watermark(db, rows.org_id, rows.learner_id) == 0
        assert await _projection(db, rows.learner_id) is None
        monkeypatch.setattr(service, "_store_projection", store)

        evidence = await service.append_objective_evidence(
            db, rows.org_id, actor, issued.uuid, POLICY, [1]
        )
        repeated = await service.append_objective_evidence(
            db, rows.org_id, actor, issued.uuid, POLICY, [1]
        )
        assert evidence.id == repeated.id and evidence.evidence_sequence == 1
        assert evidence.outcome == "correct" and evidence.eligible
        projection = await _projection(db, rows.learner_id)
        assert projection.evidence_watermark == 1 and projection.version == 1
        assert (
            projection.score == Decimal(1)
            and projection.state == "insufficient_evidence"
        )

        profile = await db.get(StudyLearnerProfile, rows.learner_id)
        concept = await db.get(StudyConcept, rows.concept_id)
        correction = rows.observation(
            issued,
            result_revision=2,
            supersedes_id=evidence.id,
            correction_reason="synthetic_review",
            outcome="incorrect",
            score=0,
        )
        _, corrected = await service.append_evidence_and_project(
            db, profile, concept, correction, POLICY
        )
        assert evidence.score == 1 and correction.evidence_sequence == 2
        assert corrected.score == Decimal(0) and corrected.version == 2
        duplicate = rows.observation(
            issued,
            result_revision=2,
            supersedes_id=evidence.id,
            correction_reason="synthetic_review",
            outcome="incorrect",
            score=0,
        )
        replayed, projection_after_replay = await service.append_evidence_and_project(
            db,
            profile,
            concept,
            duplicate,
            POLICY,
        )
        assert replayed.id == correction.id and projection_after_replay.version == 2
        duplicate.correction_reason = "changed_reason"
        with pytest.raises(HTTPException) as conflicting:
            await service.append_evidence_and_project(
                db, profile, concept, duplicate, POLICY
            )
        assert conflicting.value.status_code == 409
        retraction = rows.observation(
            issued,
            result_revision=3,
            supersedes_id=correction.id,
            correction_reason="synthetic_retraction",
            outcome="not_scored",
            score=None,
            eligible=False,
            is_retraction=True,
        )
        _, cleared = await service.append_evidence_and_project(
            db, profile, concept, retraction, POLICY
        )
        assert (
            cleared.score is None
            and cleared.state == "not_assessed"
            and cleared.evidence_watermark == 3
        )


async def test_concurrent_projectors_use_one_learner_watermark(
    postgres_session_factory,
):
    async with postgres_session_factory() as db, db.begin():
        rows = await _seed(db)
        changes = {
            "kind": "practice",
            "diagnostic_revision_id": None,
            "blueprint_uuid": None,
        }
        first_item = await _accept(
            db, rows, started_at=datetime.now(UTC) - timedelta(days=2), **changes
        )
        # Use a distinct family for a second simulated authorized observation.
        attempt = rows.attempt(**changes)
        db.add(attempt)
        await db.flush()
        second_item = rows.issue(attempt, index=1)
        db.add(second_item)
        await db.flush()
        await _answer(db, second_item)
    async with (
        postgres_session_factory() as first,
        postgres_session_factory() as second,
    ):
        first_profile = await first.get(StudyLearnerProfile, rows.learner_id)
        first_concept = await first.get(StudyConcept, rows.concept_id)
        _, current = await service.append_evidence_and_project(
            first,
            first_profile,
            first_concept,
            rows.observation(first_item),
            POLICY,
        )
        assert current.evidence_watermark == 1
        other_profile = await second.get(StudyLearnerProfile, rows.learner_id)
        other_concept = await second.get(StudyConcept, rows.concept_id)
        pid = (await second.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        async with asyncio.TaskGroup() as tasks:
            pending = tasks.create_task(
                service.append_evidence_and_project(
                    second,
                    other_profile,
                    other_concept,
                    rows.observation(second_item),
                    POLICY,
                )
            )
            await _wait_for_postgres_lock(postgres_session_factory, pid)
            await first.commit()
        assert pending.result()[1].evidence_watermark == 2
        await second.commit()
    async with postgres_session_factory() as db:
        projection = await _projection(db, rows.learner_id)
        assert projection.eligible_count == 2 and projection.state == "mastered"
        assert projection.version == 2


async def test_staff_check_and_repair_require_both_scopes(postgres_session_factory):
    async with postgres_session_factory() as root, root.begin():
        connection = await root.connection()
        async with AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        ) as db:
            await _exercise_staff_repair(db)
        await root.rollback()


async def _exercise_staff_repair(db):
    rows = await _seed(db, POLICY)
    org = await db.get(Organization, rows.org_id)
    profile = await db.get(StudyLearnerProfile, rows.learner_id)
    concept = await db.get(StudyConcept, rows.concept_id)
    course = Course(
        org_id=org.id,
        course_uuid=rows.course_uuid,
        name="Synthetic course",
        public=False,
        open_to_contributors=False,
    )
    token = uuid4().hex
    reviewer = User(
        username=f"reviewer-{token}",
        email=f"reviewer-{token}@example.invalid",
        user_uuid=f"user_{token}",
        first_name="Synthetic",
        last_name="Reviewer",
    )
    role = Role(
        name="Synthetic reviewer",
        org_id=org.id,
        role_uuid=f"role_{token}",
        role_type=RoleTypeEnum.TYPE_ORGANIZATION,
        rights={"studytomy": {"assessment_moderation": True}},
    )
    db.add_all([course, reviewer, role])
    await db.flush()
    db.add(
        UserOrganization(
            user_id=reviewer.id,
            org_id=org.id,
            role_id=role.id,
            creation_date="",
            update_date="",
        )
    )
    await db.commit()
    actor = PublicUser.model_validate(reviewer)
    owner = await _seed_active_owner(
        db, org, actor.id, actor.id, StudyOwnerFunction.EDUCATION
    )
    cap = StudytomyCapabilityName.ASSESSMENT_MODERATION
    await _seed_staff_scope(
        db, org.id, owner.id, cap, "course", course.course_uuid, actor.id
    )
    with pytest.raises(HTTPException) as denial:
        await service.reconcile_mastery(
            db, org, actor, profile.learner_uuid, concept.uuid, POLICY.policy_uuid
        )
    assert denial.value.status_code == 404
    await _seed_staff_scope(
        db, org.id, owner.id, cap, "learner", profile.learner_uuid, actor.id
    )
    owners = {StudyOwnerFunction.EDUCATION: owner}
    for key in (
        StudyPolicyKey.D_01,
        StudyPolicyKey.D_02,
        StudyPolicyKey.D_08,
        StudyPolicyKey.D_09,
        StudyPolicyKey.D_12,
        StudyPolicyKey.D_13,
    ):
        values = (
            {"mastery_gate": {"adaptive_mastery_policies": [POLICY.model_dump()]}}
            if key == StudyPolicyKey.D_09
            else None
        )
        policy = await _seed_approved_policy(db, org, actor.id, key, specified=values)
        for responsibility in POLICY_REQUIRED_RESPONSIBILITIES[key]:
            if responsibility not in owners:
                owners[responsibility] = await _seed_active_owner(
                    db, org, actor.id, actor.id, responsibility
                )
            db.add(
                StudyPolicyApproval(
                    org_id=org.id,
                    policy_version_id=policy.id,
                    responsibility=responsibility.value,
                    approver_user_id=actor.id,
                    owner_assignment_id=owners[responsibility].id,
                    evidence_reference="synthetic-policy-evidence",
                    rationale="Synthetic policy fixture",
                    approved_at=datetime.now(UTC),
                )
            )
        await db.commit()

    issued = await _accept(db, rows)
    event = rows.observation(issued)
    db.add(event)  # Existing journal with a missing projection.
    await db.flush()
    checked = await service.reconcile_mastery(
        db, org, actor, profile.learner_uuid, concept.uuid, POLICY.policy_uuid
    )
    assert checked.status == "missing" and checked.evidence_watermark == 1
    assert await _projection(db, rows.learner_id) is None
    with pytest.raises(HTTPException) as stale_preview:
        await service.reconcile_mastery(
            db,
            org,
            actor,
            profile.learner_uuid,
            concept.uuid,
            POLICY.policy_uuid,
            repair=True,
            expected_evidence_watermark=0,
            expected_projection_version=0,
        )
    assert stale_preview.value.status_code == 409
    assert await _projection(db, rows.learner_id) is None
    repaired = await service.reconcile_mastery(
        db,
        org,
        actor,
        profile.learner_uuid,
        concept.uuid,
        POLICY.policy_uuid,
        repair=True,
        expected_evidence_watermark=checked.evidence_watermark,
        expected_projection_version=0,
    )
    assert repaired.status == "repaired" and repaired.projection == checked.projection
    assert repaired.projection_version == 1
    again = await service.reconcile_mastery(
        db,
        org,
        actor,
        profile.learner_uuid,
        concept.uuid,
        POLICY.policy_uuid,
        repair=True,
        expected_evidence_watermark=checked.evidence_watermark,
        expected_projection_version=repaired.projection_version,
    )
    assert again.status == "current" and again.projection_version == 1
    assert (
        await db.execute(
            select(func.count())
            .select_from(StudyLearningEvidence)
            .where(
                StudyLearningEvidence.org_id == org.id,
            )
        )
    ).scalar_one() == 1
    audit = (
        await db.execute(
            select(StudyBusinessEvent).where(
                StudyBusinessEvent.org_id == org.id,
                StudyBusinessEvent.action == "studytomy.adaptive.mastery.repaired",
            )
        )
    ).scalar_one()
    assert audit.actor_user_id == actor.id
