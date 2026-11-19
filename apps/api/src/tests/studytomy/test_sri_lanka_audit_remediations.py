"""Focused regression tests for Sri Lankan National Syllabus audit tasks (T-01 through T-18)."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.db.courses.courses import Course
from src.db.paths import Path, PathCourse, PathEnrollment
from src.db.studytomy.adaptive import StudyCurriculumRevision
from src.db.studytomy.family import (
    StudyEligibilityDecision,
    StudyEligibilityDecisionCreate,
    StudyEligibilityKind,
    StudyEligibilityOutcome,
    StudyLearnerActivationCreate,
    StudyLearnerControlMode,
    StudyLearnerProfile,
    StudyRelationship,
    StudyRelationshipStatus,
)
from src.db.studytomy.governance import (
    StudyOwnerAssignment,
    StudyOwnerFunction,
    StudyOwnerStatus,
    StudyPolicyApproval,
    StudyPolicyKey,
)
from src.services.studytomy.policies import POLICY_REQUIRED_RESPONSIBILITIES
from src.db.studytomy.operations import StudyOutboxPurpose, StudyOutboxRecord
from src.db.studytomy.privacy import (
    StudyConsentChoice,
    StudyConsentEventCreate,
    StudyConsentPurpose,
)
from src.db.user_organizations import UserOrganization
from src.db.users import PublicUser, User, UserUpdatePassword
from src.security.security import security_hash_password
from src.security.studytomy_access import StudytomyCapabilityName
from src.services.paths import paths as paths_service
from src.services.studytomy import (
    adaptive_curriculum,
    adaptive_curriculum_review,
    adaptive_guidance,
    adaptive_teaching,
    authority,
    consents,
    learners,
    orders,
    report_notifications,
    worker,
)
from src.services.users.users import update_user_password
from src.tests.studytomy.test_studytomy_access import (
    _grant_role_capabilities,
    _seed_approved_policy,
    _seed_staff_scope,
)


async def _seed_owner_assignment(db, org_id: int, org_uuid: str, user_id: int, function: StudyOwnerFunction):
    assignment = StudyOwnerAssignment(
        org_id=org_id,
        user_id=user_id,
        responsibility=function.value,
        scope_kind="organization",
        scope_uuid=org_uuid,
        status=StudyOwnerStatus.ACTIVE.value,
        evidence_reference="OWNER-ASSIGNMENT-LK-001",
        assigned_by_user_id=user_id,
        effective_from=datetime.now(UTC) - timedelta(days=1),
    )
    db.add(assignment)
    await db.flush()
    return assignment


async def _seed_family_policies(db, org, approver_id: int):
    specs = {
        StudyPolicyKey.D_01: {
            "markets": ["LK"],
            "learner_age_limits": {"grades": "6-13"},
            "independent_participation": {"verified": True},
        },
        StudyPolicyKey.D_02: {
            "subjects": ["Mathematics", "Science"],
            "learner_levels": ["Grade 10 (G.C.E. O/L)"],
            "languages": ["en"],
            "curriculum_claims": {
                "adaptive_curriculum_keys": ["LK-NIE-G10-MATH"],
                "programme_manifest": {
                    "authorities": [
                        "National Institute of Education (NIE), Ministry of Education — Sri Lanka"
                    ],
                    "syllabus_editions": ["2015"],
                    "effective_cohorts": ["2026"],
                },
            },
        },
        StudyPolicyKey.D_03: {
            "guardian_authority": {"verification": "human"},
            "adult_transition": {"review": "human"},
        },
        StudyPolicyKey.D_05: {
            "prices": {"currency": "LKR"},
            "access_periods": {"term_days": 90},
            "learner_allocation": {"single_named_learner": True},
        },
        StudyPolicyKey.D_12: {
            "information_uses": {
                "learning_terms": ["course_release", "adaptive_progress_report"],
                "ai_assistance": ["adaptive_lesson_guidance"],
            },
            "consent": {
                "notice_versions": {
                    "learning_terms": "v1-lk-learning",
                    "ai_assistance": "v1-lk-ai",
                },
                "notice_texts": {
                    "learning_terms": "Approved learning terms notice.",
                    "ai_assistance": "Approved AI assistance notice.",
                },
            },
            "parent_report_scope": [
                "consent_management",
                "purchase_allocation",
                "adaptive_report_summary",
                "adaptive_report_email",
            ],
        },
        StudyPolicyKey.D_13: {
            "retention": {"learner_evidence_retention_category": "family_evidence_7y"},
            "record_access": {"assigned_reviewer_only": True},
        },
        StudyPolicyKey.D_15: {
            "supported_formats": {"max_bytes": 4_194_304, "media_types": ["application/pdf"]},
        },
        StudyPolicyKey.D_18: {
            "customer_terms": {"version": "v1-lk-learning", "summary": "Approved Sri Lanka terms."},
            "selected_market_requirements": {"jurisdiction": "LK"},
        },
    }
    seeded = {}
    owners: dict[StudyOwnerFunction, StudyOwnerAssignment] = {}
    for key, body in specs.items():
        policy = await _seed_approved_policy(db, org, approver_id, key, specified=body)
        seeded[key] = policy
        for responsibility in POLICY_REQUIRED_RESPONSIBILITIES[key]:
            owner = owners.get(responsibility)
            if owner is None:
                owner = await _seed_owner_assignment(
                    db, org.id, org.org_uuid, approver_id, responsibility
                )
                owners[responsibility] = owner
            db.add(
                StudyPolicyApproval(
                    org_id=org.id,
                    policy_version_id=policy.id,
                    owner_assignment_id=owner.id,
                    responsibility=responsibility.value,
                    approver_user_id=approver_id,
                    evidence_reference=f"APPROVAL-{key.value}-{responsibility.value}",
                    rationale="Test policy approval evidence.",
                    approved_at=datetime.now(UTC) - timedelta(hours=1),
                )
            )
    await db.commit()
    return seeded, owners


@pytest.mark.asyncio
async def test_t01_password_change_advances_session_generation_atomically(db, regular_user):
    user = await db.get(User, regular_user.id)
    user.password = security_hash_password("OldPass123!")
    user.session_generation = 3
    db.add(user)
    await db.commit()

    await update_user_password(
        request=SimpleNamespace(),
        user_id=user.id,
        form=UserUpdatePassword(old_password="OldPass123!", new_password="NewStrongPassword123!"),
        current_user=regular_user,
        db_session=db,
    )
    refreshed = await db.get(User, user.id)
    assert refreshed.session_generation == 4
    assert refreshed.password_changed_at is not None


@pytest.mark.asyncio
async def test_t02_paths_exclude_protected_learners_from_roster_and_webhooks(
    db, org, admin_user, regular_user
):
    course = Course(
        org_id=org.id,
        name="Maths G10",
        description="Desc",
        public=True,
        published=True,
        open_to_contributors=False,
        course_uuid="course_path_t02",
        creation_date="2026-01-01",
        update_date="2026-01-01",
    )
    db.add(course)
    await db.flush()
    path = Path(
        org_id=org.id,
        path_uuid="path_t02",
        name="Grade 10 Path",
        description="Desc",
        published=True,
        public=True,
        certificate_enabled=False,
        creation_date="2026-01-01",
        update_date="2026-01-01",
    )
    db.add(path)
    await db.flush()
    db.add(PathCourse(path_id=path.id, course_id=course.id, position=0, required=True))
    # Make regular_user a protected Studytomy learner
    db.add(
        StudyLearnerProfile(
            org_id=org.id,
            user_id=regular_user.id,
            created_by_user_id=admin_user.id,
            status="active",
            control_mode="dependent",
        )
    )
    await db.commit()

    with patch("src.services.paths.paths.dispatch_webhooks", new_callable=AsyncMock) as webhook_mock, patch(
        "src.services.paths.paths.track", new_callable=AsyncMock
    ):
        await paths_service.enroll_in_path(None, path.path_uuid, regular_user, db)
        webhook_mock.assert_not_awaited()

    # Enroll non-protected admin_user too
    db.add(PathEnrollment(path_id=path.id, user_id=admin_user.id, enrolled_at="2026-01-01"))
    await db.commit()

    with patch("src.services.paths.paths._require_manage", new_callable=AsyncMock):
        roster = await paths_service.get_path_learners(None, path.path_uuid, admin_user, db)

    returned_ids = {entry.user["id"] for entry in roster}
    assert regular_user.id not in returned_ids
    assert admin_user.id in returned_ids


@pytest.mark.asyncio
async def test_t04_t05_t06_t07_t08_t16_dependent_family_activation_consent_and_checkout(
    db, org, admin_user, regular_user
):
    # Create a distinct parent/guardian adult user + a second learner sorted earlier
    parent = User(
        username="parent_lk",
        email="parent@lk.example",
        first_name="Nimal",
        last_name="Perera",
        password=security_hash_password("Secret123!"),
        user_uuid="user_parent_lk",
    )
    other_student = User(
        username="student_a",
        email="a@lk.example",
        first_name="Aasha",
        last_name="Silva",
        password=security_hash_password("Secret123!"),
        user_uuid="user_student_a",
    )
    db.add_all([parent, other_student])
    await db.flush()
    for uid in (parent.id, other_student.id):
        db.add(
            UserOrganization(
                user_id=uid,
                org_id=org.id,
                role_id=4,
                creation_date="2026-01-01",
                update_date="2026-01-01",
            )
        )
    await db.commit()

    policies, owners = await _seed_family_policies(db, org, admin_user.id)
    owner = owners.get(StudyOwnerFunction.FAMILY_SERVICES) or await _seed_owner_assignment(
        db, org.id, org.org_uuid, admin_user.id, StudyOwnerFunction.FAMILY_SERVICES
    )

    # Seed another active independent profile that sorts ahead alphabetically (T-16)
    db.add(
        StudyLearnerProfile(
            learner_uuid="learner_0000_first",
            org_id=org.id,
            user_id=other_student.id,
            created_by_user_id=admin_user.id,
            status="active",
            control_mode="independent",
        )
    )
    # Create pending learner profile for regular_user
    profile = StudyLearnerProfile(
        learner_uuid="learner_zzzz_child",
        org_id=org.id,
        user_id=regular_user.id,
        created_by_user_id=admin_user.id,
        status="pending",
        control_mode="unresolved",
        access_version=1,
    )
    db.add(profile)
    await db.flush()

    await _grant_role_capabilities(db, org.id, admin_user.id, learner_eligibility_review=True)
    await _seed_staff_scope(
        db,
        org.id,
        owner.id,
        StudytomyCapabilityName.LEARNER_ELIGIBILITY_REVIEW,
        "learner",
        profile.learner_uuid,
        admin_user.id,
    )
    await db.commit()

    # T-04: Cannot activate while control_mode is unresolved
    with pytest.raises(HTTPException) as unresolved_err:
        await learners.activate_learner_profile(
            db,
            org,
            admin_user,
            profile.learner_uuid,
            StudyLearnerActivationCreate(expected_access_version=1, reason_code="ACTIVATE_ATTEMPT"),
            "idem-act-unresolved",
        )
    assert unresolved_err.value.status_code == 409

    # Record eligible dependent learning decision
    await learners.record_eligibility_decision(
        db,
        org,
        admin_user,
        profile.learner_uuid,
        StudyEligibilityDecisionCreate(
            eligibility_kind=StudyEligibilityKind.LEARNING,
            outcome=StudyEligibilityOutcome.ELIGIBLE,
            evidence_reference="CASE-LK-001",
            control_mode=StudyLearnerControlMode.DEPENDENT,
        ),
        "idem-elig-dep-1",
    )
    await db.refresh(profile)
    assert profile.control_mode == "dependent"
    assert profile.status == "pending"

    # T-07: Dependent activation requires an accepted guardian relationship first
    with pytest.raises(HTTPException) as missing_guard_err:
        await learners.activate_learner_profile(
            db,
            org,
            admin_user,
            profile.learner_uuid,
            StudyLearnerActivationCreate(
                expected_access_version=profile.access_version,
                reason_code="ACTIVATE_NO_GUARDIAN",
            ),
            "idem-act-no-guardian",
        )
    assert missing_guard_err.value.status_code == 409

    # Add accepted current guardian relationship with consent + purchase scopes
    now = datetime.now(UTC)
    guardian_policy_ids = [
        int(policies[k].id) for k in (StudyPolicyKey.D_03, StudyPolicyKey.D_12, StudyPolicyKey.D_13)
    ]
    rel = StudyRelationship(
        org_id=org.id,
        learner_profile_id=profile.id,
        adult_user_id=parent.id,
        requested_by_user_id=parent.id,
        requested_scopes=["consent_management", "purchase_allocation", "adaptive_report_summary"],
        approved_scopes=["consent_management", "purchase_allocation", "adaptive_report_summary"],
        status=StudyRelationshipStatus.ACCEPTED.value,
        request_evidence_reference="REQ-LK-001",
        request_policy_version_ids=guardian_policy_ids,
        decision_policy_version_ids=guardian_policy_ids,
        valid_from=now - timedelta(hours=1),
        valid_to=now + timedelta(days=30),
        decided_by_user_id=admin_user.id,
        decision_owner_assignment_id=owner.id,
        decision_evidence_reference="VERIFIED-LK-001",
        decision_reason_code="GUARDIAN_VERIFIED",
        decided_at=now,
        authority_version=2,
    )
    db.add(rel)
    await db.commit()

    # Now dependent activation succeeds cleanly (no circular dependency!)
    activated = await learners.activate_learner_profile(
        db,
        org,
        admin_user,
        profile.learner_uuid,
        StudyLearnerActivationCreate(
            expected_access_version=profile.access_version,
            reason_code="DEPENDENT_ACTIVATED",
        ),
        "idem-act-dep-ok",
    )
    assert activated.status.value == "active"

    # T-05: Active profile can receive an append-only renewal decision
    renewed = await learners.record_eligibility_decision(
        db,
        org,
        admin_user,
        profile.learner_uuid,
        StudyEligibilityDecisionCreate(
            eligibility_kind=StudyEligibilityKind.LEARNING,
            outcome=StudyEligibilityOutcome.ELIGIBLE,
            evidence_reference="CASE-LK-002",
        ),
        "idem-elig-renew-1",
    )
    assert renewed.currently_eligible is True

    # T-07: Guardian records learning_terms consent for dependent child
    parent_actor = PublicUser.model_validate(parent, from_attributes=True)
    await db.refresh(profile)
    assert not await consents.has_current_consent_scope(
        db, org, profile, StudyConsentPurpose.LEARNING_TERMS, "course_release"
    )
    await consents.record_guardian_consent_choice(
        db,
        org,
        parent_actor,
        profile.learner_uuid,
        StudyConsentEventCreate(
            purpose=StudyConsentPurpose.LEARNING_TERMS,
            choice=StudyConsentChoice.ACCEPT,
            notice_version="v1-lk-learning",
            scope_codes=["course_release"],
        ),
        "idem-guardian-consent-1",
    )
    assert await consents.has_current_consent_scope(
        db, org, profile, StudyConsentPurpose.LEARNING_TERMS, "course_release"
    )

    # T-08 & T-16: Parent can discover and purchase for dependent child even with limit=1
    choices = await orders.list_purchasable_learners(db, org, purchaser_id=parent.id, limit=1)
    assert [c.learner_uuid for c in choices] == [profile.learner_uuid]

    # Revoking guardian authority immediately invalidates dependent consent & purchase authority
    rel.status = StudyRelationshipStatus.REVOKED.value
    rel.approved_scopes = []
    rel.authority_version += 1
    db.add(rel)
    await db.commit()
    assert not await consents.has_current_consent_scope(
        db, org, profile, StudyConsentPurpose.LEARNING_TERMS, "course_release"
    )
    assert not await orders.can_purchase_for_learner(
        db, org, purchaser_id=parent.id, learner_profile_id=profile.id, now=datetime.now(UTC)
    )


@pytest.mark.asyncio
async def test_t10_t12_t13_programme_metadata_language_and_hint_release_scope(
    db, org, admin_user
):
    # T-10: ProgrammeMetadata validates official Sri Lankan syllabus identity
    meta = adaptive_curriculum.ProgrammeMetadata(
        authority="National Institute of Education (NIE), Ministry of Education — Sri Lanka",
        syllabus_edition="2015",
        effective_cohort="2026",
        stage="gce_ol",
        coverage_status="partial",
        source_language="en",
    )
    assert meta.stage == "gce_ol"

    # T-12: Teaching prompt includes server-supplied instruction_language
    captured: dict = {}

    async def _fake_generate(**kwargs):
        captured.update(kwargs)
        return {
            "message": "Read the worked example carefully.",
            "teaching_action": "explain",
            "check_prompt": None,
            "source_refs": ["current_lesson"],
        }

    ctx = adaptive_guidance.GuidanceContext(
        lesson_title="Linear Equations",
        lesson_prose="Solve 2x + 3 = 11 by subtracting 3 from both sides.",
        concept_title="Linear equations",
        objective="Solve one-variable linear equations",
        mastery_state="unmeasured",
        independent_family_count=0,
        instruction_language="en-LK",
    )
    with patch("src.services.studytomy.adaptive_teaching.generate", side_effect=_fake_generate):
        result = await adaptive_teaching.generate_teaching(
            ctx, action="explain", model_name="test-model"
        )
    assert result.fallback_used is False
    assert '"instruction_language": "en-LK"' in captured["user_prompt"]
    assert "instruction_language" in captured["system_prompt"]

    # T-13: apply_reviewed_hint forwards course_uuid and source_release_uuid
    fake_session = SimpleNamespace(
        learner_profile_id=1,
        curriculum_revision_id=1,
        practice_attempt_id=9,
        concept_id=3,
        activity_uuid="activity_released_1",
        course_uuid="course_released_1",
        source_release_uuid="course_release_1",
    )
    fake_item = SimpleNamespace(
        item_revision_id=55,
        help_count=0,
        response_version=1,
    )
    fake_reviewed = SimpleNamespace(item=SimpleNamespace(hint_ladder=["Check the sign."]))
    mock_result = SimpleNamespace(scalar_one_or_none=lambda: fake_item)
    with patch(
        "src.services.studytomy.adaptive_teaching.load_guidance_context",
        new_callable=AsyncMock,
        return_value=(fake_session, ctx),
    ), patch.object(db, "execute", new_callable=AsyncMock, return_value=mock_result), patch.object(
        db, "flush", new_callable=AsyncMock
    ), patch(
        "src.services.studytomy.adaptive_teaching.require_practice_access",
        new_callable=AsyncMock,
        return_value=fake_reviewed,
    ) as practice_access_mock:
        hint = await adaptive_teaching.apply_reviewed_hint(
            SimpleNamespace(), db, org, admin_user, "tutor_1"
        )
    assert hint.message == "Check the sign."
    assert practice_access_mock.await_args.kwargs["course_uuid"] == "course_released_1"
    assert practice_access_mock.await_args.kwargs["course_release_uuid"] == "course_release_1"


@pytest.mark.asyncio
async def test_t18_worker_bounds_hung_provider_call_to_reconciliation_required(
    db, org, admin_user
):
    now = datetime.now(UTC)
    record = StudyOutboxRecord(
        org_id=org.id,
        recipient_user_id=admin_user.id,
        purpose=StudyOutboxPurpose.PROGRESS_REPORT.value,
        template_key="progress_report_v1",
        resource_kind="progress_report",
        resource_uuid="report_hung_1",
        resource_version=1,
        authority_reference_uuid="grant_hung_1",
        policy_refs=[1],
        dedupe_hash="a" * 64,
        request_hash="b" * 64,
        provider_idempotency_key="prov-hung-key",
        status="queued",
        next_attempt_at=now - timedelta(seconds=5),
        created_by_user_id=admin_user.id,
        created_at=now - timedelta(seconds=5),
        updated_at=now - timedelta(seconds=5),
    )
    db.add(record)
    await db.commit()

    async def _allow(_db, _rec):
        return worker.DeliveryAuthorization(
            purpose_allowed=True,
            consent_current=True,
            relationship_current=True,
            resource_complete=True,
            resource_version=1,
        )

    async def _hang(_db, _rec, _auth, _key):
        await asyncio.sleep(5)
        return worker.ProviderReceipt(accepted=True)

    outcomes = await worker.run_delivery_batch(
        db,
        worker_id="worker-timeout-test",
        revalidate=_allow,
        send=_hang,
        limit=1,
        now=now,
        purpose=StudyOutboxPurpose.PROGRESS_REPORT,
        send_timeout_seconds=0.05,
    )
    assert outcomes["reconciliation_required"] == 1
    refreshed = (
        await db.execute(
            select(StudyOutboxRecord).where(StudyOutboxRecord.id == record.id)
        )
    ).scalar_one()
    assert refreshed.status == "reconciliation_required"
    assert refreshed.last_error_code == "PROVIDER_OUTCOME_UNKNOWN"


@pytest.mark.asyncio
async def test_t06_relationship_decision_validates_bound_ev_evidence_file(
    db, org, admin_user, regular_user
):
    from src.db.studytomy.family import (
        StudyRelationshipDecisionCreate,
        StudyRelationshipDecisionOutcome,
    )
    from src.db.studytomy.operations import StudyEvidenceFile

    parent = User(
        username="parent_t06",
        email="parent_t06@lk.example",
        first_name="Kamala",
        last_name="Fernando",
        password=security_hash_password("Secret123!"),
        user_uuid="user_parent_t06",
    )
    db.add(parent)
    await db.flush()
    db.add(
        UserOrganization(
            user_id=parent.id,
            org_id=org.id,
            role_id=4,
            creation_date="2026-01-01",
            update_date="2026-01-01",
        )
    )
    policies, owners = await _seed_family_policies(db, org, admin_user.id)
    owner = owners.get(StudyOwnerFunction.FAMILY_SERVICES) or await _seed_owner_assignment(
        db, org.id, org.org_uuid, admin_user.id, StudyOwnerFunction.FAMILY_SERVICES
    )
    profile = StudyLearnerProfile(
        learner_uuid="learner_t06_child",
        org_id=org.id,
        user_id=regular_user.id,
        created_by_user_id=admin_user.id,
        status="pending",
        control_mode="dependent",
        access_version=1,
    )
    db.add(profile)
    await db.flush()
    await _grant_role_capabilities(db, org.id, admin_user.id, family_authority_review=True)
    await _seed_staff_scope(
        db,
        org.id,
        owner.id,
        StudytomyCapabilityName.FAMILY_AUTHORITY_REVIEW,
        "learner",
        profile.learner_uuid,
        admin_user.id,
    )
    guardian_policy_ids = [
        int(policies[k].id) for k in (StudyPolicyKey.D_03, StudyPolicyKey.D_12, StudyPolicyKey.D_13)
    ]
    rel = StudyRelationship(
        org_id=org.id,
        learner_profile_id=profile.id,
        adult_user_id=parent.id,
        requested_by_user_id=parent.id,
        requested_scopes=["consent_management", "adaptive_report_summary"],
        status=StudyRelationshipStatus.PENDING.value,
        request_evidence_reference="REQ-T06",
        request_policy_version_ids=guardian_policy_ids,
        authority_version=1,
    )
    db.add(rel)
    await db.commit()
    await db.refresh(rel)

    now = datetime.now(UTC)
    # Unbound ev_ reference is rejected
    with pytest.raises(HTTPException) as bad_ev:
        await authority.decide_relationship_request(
            db,
            org,
            admin_user,
            profile.learner_uuid,
            rel.relationship_uuid,
            StudyRelationshipDecisionCreate(
                expected_version=rel.authority_version,
                outcome=StudyRelationshipDecisionOutcome.ACCEPT,
                approved_scopes=["consent_management"],
                valid_from=now + timedelta(minutes=1),
                valid_to=now + timedelta(days=30),
                reason_code="GUARDIAN_VERIFIED",
                evidence_reference="ev_nonexistent_1234567890",
            ),
            "idem-t06-missing-ev",
        )
    assert bad_ev.value.status_code == 422
    assert "EVIDENCE" in str(bad_ev.value.detail).upper()

    # Bound guardian_authority_evidence file succeeds
    evidence_policy_ids = [
        int(policies[k].id)
        for k in (
            StudyPolicyKey.D_01,
            StudyPolicyKey.D_03,
            StudyPolicyKey.D_12,
            StudyPolicyKey.D_13,
            StudyPolicyKey.D_15,
        )
    ]
    evidence = StudyEvidenceFile(
        evidence_uuid="ev_1234567890abcdef123456",
        org_id=org.id,
        uploaded_by_user_id=admin_user.id,
        purpose="guardian_authority_evidence",
        learner_profile_id=profile.id,
        relationship_uuid=rel.relationship_uuid,
        storage_reference=f"private/studytomy/{org.org_uuid}/guardian/test.pdf",
        media_type="application/pdf",
        size_bytes=1024,
        content_sha256="c" * 64,
        retention_category="family_evidence_7y",
        upload_status="ready",
        policy_version_ids=evidence_policy_ids,
    )
    db.add(evidence)
    await db.commit()
    await db.refresh(evidence)

    decided = await authority.decide_relationship_request(
        db,
        org,
        admin_user,
        profile.learner_uuid,
        rel.relationship_uuid,
        StudyRelationshipDecisionCreate(
            expected_version=rel.authority_version,
            outcome=StudyRelationshipDecisionOutcome.ACCEPT,
            approved_scopes=["consent_management", "adaptive_report_summary"],
            valid_from=now + timedelta(minutes=1),
            valid_to=now + timedelta(days=30),
            reason_code="GUARDIAN_VERIFIED",
            evidence_reference=evidence.evidence_uuid,
        ),
        "idem-t06-valid-ev",
    )
    assert decided.status.value == "accepted"
    await db.refresh(rel)
    assert rel.decision_evidence_reference == evidence.evidence_uuid
    assert owner.id is not None


@pytest.mark.asyncio
async def test_t17_reconciliation_queue_scans_past_inaccessible_rows_with_cursor(
    db, org, admin_user
):
    now = datetime.now(UTC)
    for idx, res_uuid in enumerate(("rep_hidden_1", "rep_hidden_2", "rep_visible_3", "rep_visible_4")):
        db.add(
            StudyOutboxRecord(
                org_id=org.id,
                recipient_user_id=admin_user.id,
                purpose=StudyOutboxPurpose.PROGRESS_REPORT.value,
                template_key="progress_report_v1",
                resource_kind="progress_report",
                resource_uuid=res_uuid,
                resource_version=1,
                authority_reference_uuid=f"rel_{idx}",
                policy_refs=[1],
                dedupe_hash=f"{idx:064x}",
                request_hash=f"{idx + 10:064x}",
                provider_idempotency_key=f"prov-t17-{idx}",
                status="reconciliation_required",
                last_error_code="PROVIDER_OUTCOME_UNKNOWN",
                next_attempt_at=now,
                created_by_user_id=admin_user.id,
                created_at=now + timedelta(seconds=idx),
                updated_at=now + timedelta(seconds=idx),
            )
        )
    await db.commit()

    async def _fake_scope(_db, _org, _actor, record):
        if record.resource_uuid.startswith("rep_hidden_"):
            raise HTTPException(403, "Not assigned")
        return SimpleNamespace(uuid=record.resource_uuid), "learner_visible"

    with patch(
        "src.services.studytomy.report_notifications._outbox_report_scope",
        side_effect=_fake_scope,
    ):
        page1 = await report_notifications.list_outbox_reconciliation(
            db, org, admin_user, limit=1
        )
        assert [item.resource_uuid for item in page1.deliveries] == ["rep_visible_3"]
        assert page1.next_cursor is not None

        page2 = await report_notifications.list_outbox_reconciliation(
            db, org, admin_user, after=page1.next_cursor, limit=10
        )
        assert [item.resource_uuid for item in page2.deliveries] == ["rep_visible_4"]
        assert page2.next_cursor is None
