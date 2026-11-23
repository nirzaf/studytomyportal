"""Focused regressions for Current-Head Architecture & Playground Adaptation Audit
(TASK-02, TASK-04, TASK-05, TASK-07, TASK-12, TASK-14, TASK-15)."""

from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import select

from src.db.courses.courses import Course
from src.db.courses.activities import Activity, ActivityTypeEnum
from src.db.courses.chapters import Chapter
from src.db.courses.course_chapters import CourseChapter
from src.db.courses.chapter_activities import ChapterActivity
from src.db.playgrounds import Playground, PlaygroundAccessType, PlaygroundUpdate
from src.db.studytomy.access import StudyCourseAccessGrant
from src.db.studytomy.adaptive import (
    StudyConcept,
    StudyConceptActivity,
    StudyCurriculumRevision,
)
from src.db.studytomy.education import StudyCourseRelease, StudyCourseReview
from src.db.studytomy.family import (
    StudyEligibilityDecision,
    StudyLearnerProfile,
    StudyRelationship,
    StudyRelationshipStatus,
)
from src.db.studytomy.governance import (
    StudyBusinessEvent,
    StudyOwnerAssignment,
    StudyOwnerFunction,
    StudyOwnerStatus,
    StudyPolicyApproval,
    StudyPolicyKey,
)
from src.db.studytomy.privacy import (
    StudyConsentChoice,
    StudyConsentEventCreate,
    StudyConsentPurpose,
)
from src.db.user_organizations import UserOrganization
from src.db.users import PublicUser, User
from src.security.security import security_hash_password
from src.security.studytomy_access import StudytomyCapabilityName
from src.services.playgrounds.playgrounds import (
    get_playground,
    list_org_playgrounds,
    update_playground,
    update_playground_thumbnail,
)
from src.services.studytomy import (
    adaptive_attempt_access,
    adaptive_curriculum,
    adaptive_guidance,
    adaptive_objective_scoring,
    adaptive_progress,
    adaptive_reports,
    adaptive_teaching,
    consents,
    publication,
    releases,
)
from src.services.studytomy.idempotency import canonical_request_hash
from src.services.studytomy.policies import POLICY_REQUIRED_RESPONSIBILITIES
from src.tests.studytomy.test_studytomy_access import (
    _grant_role_capabilities,
    _seed_approved_policy,
    _seed_staff_scope,
)

_TEST_THUMBNAIL_A = b"\x89PNG\r\n\x1a\n" + b"\x00" * 256
_TEST_THUMBNAIL_B = b"\x89PNG\r\n\x1a\n" + b"\x01" * 256


def test_task04_playground_review_digest_binds_thumbnail_bytes_at_same_path():
    playground = SimpleNamespace(
        name="Digest fixture",
        description="Synthetic review fixture",
        access_type=PlaygroundAccessType.AUTHENTICATED,
        course_uuid=None,
        html_content="<p>fixture</p>",
        learner_adaptation=None,
        thumbnail_image="same-stored-name.png",
        thumbnail_content_sha256="a" * 64,
    )
    first_digest = publication.playground_content_digest(playground)
    playground.thumbnail_content_sha256 = "b" * 64
    second_digest = publication.playground_content_digest(playground)

    assert playground.thumbnail_image == "same-stored-name.png"
    assert first_digest != second_digest


async def _seed_owner(db, org_id: int, org_uuid: str, user_id: int, fn: StudyOwnerFunction):
    assignment = StudyOwnerAssignment(
        org_id=org_id,
        user_id=user_id,
        responsibility=fn.value,
        scope_kind="organization",
        scope_uuid=org_uuid,
        status=StudyOwnerStatus.ACTIVE.value,
        evidence_reference=f"OWNER-{fn.value}-{user_id}",
        assigned_by_user_id=user_id,
        effective_from=datetime.now(UTC) - timedelta(days=1),
    )
    db.add(assignment)
    await db.flush()
    return assignment


async def _seed_policies_with_approvals(db, org, approver_id: int, specs: dict):
    seeded = {}
    owners: dict[StudyOwnerFunction, StudyOwnerAssignment] = {}
    for key, body in specs.items():
        policy = await _seed_approved_policy(db, org, approver_id, key, specified=body)
        seeded[key] = policy
        for resp in POLICY_REQUIRED_RESPONSIBILITIES[key]:
            owner = owners.get(resp)
            if owner is None:
                owner = await _seed_owner(db, org.id, org.org_uuid, approver_id, resp)
                owners[resp] = owner
            db.add(
                StudyPolicyApproval(
                    org_id=org.id,
                    policy_version_id=policy.id,
                    owner_assignment_id=owner.id,
                    responsibility=resp.value,
                    approver_user_id=approver_id,
                    evidence_reference=f"APP-{key.value}-{resp.value}",
                    rationale="Test policy approval.",
                    approved_at=datetime.now(UTC) - timedelta(hours=1),
                )
            )
    await db.commit()
    return seeded, owners


async def _make_user(db, org_id: int, username: str, email: str, user_uuid: str) -> User:
    user = User(
        username=username,
        email=email,
        first_name=username.title(),
        last_name="Test",
        password=security_hash_password("Secret123!"),
        user_uuid=user_uuid,
    )
    db.add(user)
    await db.flush()
    db.add(
        UserOrganization(
            user_id=user.id,
            org_id=org_id,
            role_id=4,
            creation_date="2026-01-01",
            update_date="2026-01-01",
        )
    )
    await db.flush()
    return user


@pytest.mark.asyncio
async def test_task04_governed_playground_publish_requires_independent_review_and_invalidates_on_edit(
    db, org, admin_user, regular_user
):
    # 1. Plain Studytomy Portal CE org (no Studytomy profiles/policies): publish works directly.
    pg = Playground(
        org_id=org.id,
        playground_uuid="pg_task04_ce",
        name="Fractions Explorer",
        description="Interactive fractions",
        access_type=PlaygroundAccessType.AUTHENTICATED,
        published=False,
        html_content="<!DOCTYPE html><html><body>v1</body></html>",
        created_by=regular_user.id,
        creation_date="2026-01-01",
        update_date="2026-01-01",
    )
    db.add(pg)
    await db.commit()

    with patch(
        "src.services.playgrounds.playgrounds._get_user_rights",
        new_callable=AsyncMock,
        return_value={"playgrounds": {"action_update": True}},
    ):
        ce_pub = await update_playground(
            SimpleNamespace(),
            pg.playground_uuid,
            PlaygroundUpdate(published=True),
            regular_user,
            db,
        )
    assert ce_pub.published is True

    # 2. Once the org becomes Studytomy-governed, unreviewed publish is blocked (409).
    governed_pg = Playground(
        org_id=org.id,
        playground_uuid="pg_task04_school",
        name="Grade 10 Trigonometry Lab",
        description="NIE O/L",
        access_type=PlaygroundAccessType.AUTHENTICATED,
        published=False,
        html_content="<!DOCTYPE html><html><body>trig-v1</body></html>",
        created_by=regular_user.id,
        creation_date="2026-01-01",
        update_date="2026-01-01",
    )
    db.add(governed_pg)
    db.add(
        StudyLearnerProfile(
            learner_uuid="learner_pg_gate",
            org_id=org.id,
            user_id=regular_user.id,
            created_by_user_id=admin_user.id,
            status="active",
            control_mode="dependent",
        )
    )
    _, owners = await _seed_policies_with_approvals(
        db,
        org,
        admin_user.id,
        {
            StudyPolicyKey.D_02: {
                "subjects": ["Mathematics"],
                "learner_levels": ["Grade 10 (G.C.E. O/L)"],
                "languages": ["en"],
                "curriculum_claims": {"adaptive_curriculum_keys": ["LK-NIE-G10-MATH"]},
            },
            StudyPolicyKey.D_09: {
                "release_schedule": {"mode": "frozen"},
                "completion_prerequisites": {"required": False},
                "mastery_gate": {"separate": True},
            },
            StudyPolicyKey.D_10: {
                "learner_eligibility": {"grades": "6-13"},
                "supervision": {"educator_reviewed": True},
                "materials": {"local_only": True},
                "usage_limits": {"daily_cap": 100},
                "review": {"independent": True},
            },
            StudyPolicyKey.D_15: {
                "accessibility_standard": {"wcag": "2.1-AA"},
                "supported_formats": {"media_types": ["text/html"]},
                "service_recovery": {"fallback": "draft"},
            },
            StudyPolicyKey.D_16: {
                "content_rights": {"verified": True},
                "review_approvals": {"independent_reviewer": True},
                "certificate_presentation": {"none": True},
            },
        },
    )
    edu_owner = owners[StudyOwnerFunction.EDUCATION]
    await _grant_role_capabilities(db, org.id, admin_user.id, content_release_review=True)
    await _seed_staff_scope(
        db,
        org.id,
        edu_owner.id,
        StudytomyCapabilityName.CONTENT_RELEASE_REVIEW,
        "function",
        StudyOwnerFunction.EDUCATION.value,
        admin_user.id,
    )
    await db.commit()

    with patch(
        "src.services.playgrounds.playgrounds._get_user_rights",
        new_callable=AsyncMock,
        return_value={"playgrounds": {"action_update": True}},
    ), pytest.raises(HTTPException) as blocked:
        await update_playground(
            SimpleNamespace(),
            governed_pg.playground_uuid,
            PlaygroundUpdate(published=True),
            regular_user,
            db,
        )
    assert blocked.value.status_code == 409
    assert blocked.value.detail["code"] == "PLAYGROUND_REVIEW_REQUIRED"

    digest = publication.playground_content_digest(governed_pg)

    # Author self-review is rejected (even if author somehow had review capability)
    with pytest.raises(HTTPException) as self_rev:
        await publication.record_playground_review(
            db,
            org,
            regular_user,
            governed_pg.playground_uuid,
            publication.PlaygroundReviewCommand(
                expected_digest=digest,
                decision="approved",
                rights_confirmed=True,
                accessibility_confirmed=True,
                age_suitability_confirmed=True,
                evidence_reference="REVIEW-SELF-01",
            ),
            "idem-pg-self-review",
        )
    assert self_rev.value.status_code == 404

    # Independent reviewer approves exact digest -> publish now succeeds
    receipt = await publication.record_playground_review(
        db,
        org,
        admin_user,
        governed_pg.playground_uuid,
        publication.PlaygroundReviewCommand(
            expected_digest=digest,
            decision="approved",
            rights_confirmed=True,
            accessibility_confirmed=True,
            age_suitability_confirmed=True,
            evidence_reference="REVIEW-LK-PG-01",
        ),
        "idem-pg-indep-review",
    )
    assert receipt.decision == "approved"
    assert receipt.content_digest == digest

    with patch(
        "src.services.playgrounds.playgrounds._get_user_rights",
        new_callable=AsyncMock,
        return_value={"playgrounds": {"action_update": True}},
    ):
        published = await update_playground(
            SimpleNamespace(),
            governed_pg.playground_uuid,
            PlaygroundUpdate(published=True),
            regular_user,
            db,
        )
        assert published.published is True

        # Editing HTML after publish automatically reverts published=False
        edited = await update_playground(
            SimpleNamespace(),
            governed_pg.playground_uuid,
            PlaygroundUpdate(html_content="<!DOCTYPE html><html><body>trig-v2-unreviewed</body></html>"),
            regular_user,
            db,
        )
        assert edited.published is False

        # Re-publishing the unreviewed edit fails with 409
        with pytest.raises(HTTPException) as stale_pub:
            await update_playground(
                SimpleNamespace(),
                governed_pg.playground_uuid,
                PlaygroundUpdate(published=True),
                regular_user,
                db,
            )
        assert stale_pub.value.status_code == 409

    # Give v2 a server-verified thumbnail before independent review.
    with patch(
        "src.services.playgrounds.playgrounds._get_user_rights",
        new_callable=AsyncMock,
        return_value={"playgrounds": {"action_update": True}},
    ), patch(
        "src.services.playgrounds.playgrounds.upload_file",
        new_callable=AsyncMock,
        return_value="thumb-reviewed-v2.png",
    ):
        await update_playground_thumbnail(
            SimpleNamespace(),
            governed_pg.playground_uuid,
            regular_user,
            db,
            UploadFile(filename="thumb.png", file=BytesIO(_TEST_THUMBNAIL_A)),
        )
    await db.refresh(governed_pg)

    # Approve and publish v2, then test T-04/T-05 rejection withdrawal + review status + learner visibility
    v2_digest = publication.playground_content_digest(governed_pg)
    learner = await _make_user(db, org.id, "pg_learner", "pglearner@lk.test", "user_pg_learner")

    with patch(
        "src.services.playgrounds.playgrounds._get_user_rights",
        new_callable=AsyncMock,
        return_value={"playgrounds": {"action_update": True}},
    ):
        await publication.record_playground_review(
            db,
            org,
            admin_user,
            governed_pg.playground_uuid,
            publication.PlaygroundReviewCommand(
                expected_digest=v2_digest,
                decision="approved",
                rights_confirmed=True,
                accessibility_confirmed=True,
                age_suitability_confirmed=True,
                evidence_reference="REVIEW-LK-PG-02",
            ),
            "idem-pg-indep-v2",
        )
        await update_playground(
            SimpleNamespace(),
            governed_pg.playground_uuid,
            PlaygroundUpdate(published=True),
            regular_user,
            db,
        )

    # Learner can now read and list approved governed Playground
    assert (await get_playground(SimpleNamespace(), governed_pg.playground_uuid, learner, db)).published is True
    assert governed_pg.playground_uuid in {
        p.playground_uuid for p in await list_org_playgrounds(SimpleNamespace(), org.id, learner, db)
    }

    # Check independent review status projection (T-05)
    reviewer_status = await publication.get_playground_review_status(
        db, org, admin_user, governed_pg.playground_uuid
    )
    assert reviewer_status.governed is True
    assert reviewer_status.can_review is True
    assert reviewer_status.deliverable is True
    assert reviewer_status.current_revision_approved is True

    # Replacing the learner-visible image changes the digest and withdraws the
    # Playground; the old approval remains in the append-only review history.
    with patch(
        "src.services.playgrounds.playgrounds._get_user_rights",
        new_callable=AsyncMock,
        return_value={"playgrounds": {"action_update": True}},
    ), patch(
        "src.services.playgrounds.playgrounds.upload_file",
        new_callable=AsyncMock,
        return_value="thumb-changed-v3.png",
    ):
        changed_thumbnail = await update_playground_thumbnail(
            SimpleNamespace(),
            governed_pg.playground_uuid,
            regular_user,
            db,
            UploadFile(filename="thumb.png", file=BytesIO(_TEST_THUMBNAIL_B)),
        )
    assert changed_thumbnail.published is False
    await db.refresh(governed_pg)
    v3_digest = publication.playground_content_digest(governed_pg)
    assert v3_digest != v2_digest

    with patch(
        "src.services.playgrounds.playgrounds._get_user_rights",
        new_callable=AsyncMock,
        return_value={"playgrounds": {"action_update": True}},
    ), pytest.raises(HTTPException) as stale_thumbnail_pub:
        await update_playground(
            SimpleNamespace(),
            governed_pg.playground_uuid,
            PlaygroundUpdate(published=True),
            regular_user,
            db,
        )
    assert stale_thumbnail_pub.value.status_code == 409
    assert stale_thumbnail_pub.value.detail["code"] == "PLAYGROUND_REVIEW_REQUIRED"
    await db.refresh(governed_pg)
    with pytest.raises(HTTPException) as hidden_after_thumbnail:
        await get_playground(SimpleNamespace(), governed_pg.playground_uuid, learner, db)
    assert hidden_after_thumbnail.value.status_code == 404
    assert governed_pg.playground_uuid not in {
        p.playground_uuid
        for p in await list_org_playgrounds(SimpleNamespace(), org.id, learner, db)
    }

    prior_thumbnail_review = (
        await db.execute(
            select(StudyBusinessEvent).where(
                StudyBusinessEvent.org_id == org.id,
                StudyBusinessEvent.resource_kind == "playground",
                StudyBusinessEvent.resource_uuid == governed_pg.playground_uuid,
                StudyBusinessEvent.action == "studytomy.playground.review_approved",
            )
        )
    ).scalars().all()
    assert any(
        (event.after_state or {}).get("evidence_sha256") == v2_digest
        for event in prior_thumbnail_review
    )

    await publication.record_playground_review(
        db,
        org,
        admin_user,
        governed_pg.playground_uuid,
        publication.PlaygroundReviewCommand(
            expected_digest=v3_digest,
            decision="approved",
            rights_confirmed=True,
            accessibility_confirmed=True,
            age_suitability_confirmed=True,
            evidence_reference="REVIEW-LK-PG-03-THUMBNAIL",
        ),
        "idem-pg-indep-v3-thumbnail",
    )
    with patch(
        "src.services.playgrounds.playgrounds._get_user_rights",
        new_callable=AsyncMock,
        return_value={"playgrounds": {"action_update": True}},
    ):
        republished_thumbnail = await update_playground(
            SimpleNamespace(),
            governed_pg.playground_uuid,
            PlaygroundUpdate(published=True),
            regular_user,
            db,
        )
    assert republished_thumbnail.published is True
    v2_digest = v3_digest

    with patch(
        "src.services.playgrounds.playgrounds._get_user_rights",
        new_callable=AsyncMock,
        return_value={"playgrounds": {"action_update_own": True}},
    ):
        creator_status = await publication.get_playground_review_status(
            db, org, regular_user, governed_pg.playground_uuid
        )
        assert creator_status.is_creator is True
        assert creator_status.can_review is False

    with patch(
        "src.services.playgrounds.playgrounds._get_user_rights",
        new_callable=AsyncMock,
        return_value={"playgrounds": {}},
    ), pytest.raises(HTTPException) as learner_status_exc:
        await publication.get_playground_review_status(
            db, org, learner, governed_pg.playground_uuid
        )
    assert learner_status_exc.value.status_code == 404

    # Rejection atomically sets published=False and removes learner read & list access
    rejected = await publication.record_playground_review(
        db,
        org,
        admin_user,
        governed_pg.playground_uuid,
        publication.PlaygroundReviewCommand(
            expected_digest=v2_digest,
            decision="rejected",
            rights_confirmed=False,
            accessibility_confirmed=False,
            age_suitability_confirmed=False,
            evidence_reference="REJECT-LK-PG-02",
        ),
        "idem-pg-reject-v2",
    )
    assert rejected.decision == "rejected"
    await db.refresh(governed_pg)
    assert governed_pg.published is False

    with pytest.raises(HTTPException) as hidden_after_reject:
        await get_playground(SimpleNamespace(), governed_pg.playground_uuid, learner, db)
    assert hidden_after_reject.value.status_code == 404
    assert governed_pg.playground_uuid not in {
        p.playground_uuid for p in await list_org_playgrounds(SimpleNamespace(), org.id, learner, db)
    }

    # Independent reviewer can still inspect rejected/draft governed Playground to review
    assert (
        await get_playground(SimpleNamespace(), governed_pg.playground_uuid, admin_user, db)
    ).playground_uuid == governed_pg.playground_uuid

    # A direct path edit has no trusted byte identity, so it cannot retain
    # approval or receive a new approval until the thumbnail is re-uploaded.
    with patch(
        "src.services.playgrounds.playgrounds._get_user_rights",
        new_callable=AsyncMock,
        return_value={"playgrounds": {"action_update": True}},
    ):
        await update_playground(
            SimpleNamespace(),
            governed_pg.playground_uuid,
            PlaygroundUpdate(thumbnail_image="manually-selected.png"),
            regular_user,
            db,
        )
    await db.refresh(governed_pg)
    direct_path_digest = publication.playground_content_digest(governed_pg)
    with pytest.raises(HTTPException) as thumbnail_identity_required:
        await publication.record_playground_review(
            db,
            org,
            admin_user,
            governed_pg.playground_uuid,
            publication.PlaygroundReviewCommand(
                expected_digest=direct_path_digest,
                decision="approved",
                rights_confirmed=True,
                accessibility_confirmed=True,
                age_suitability_confirmed=True,
                evidence_reference="REVIEW-UNVERIFIED-THUMBNAIL",
            ),
            "idem-pg-unverified-thumbnail",
        )
    assert thumbnail_identity_required.value.status_code == 409
    assert (
        thumbnail_identity_required.value.detail["code"]
        == "PLAYGROUND_THUMBNAIL_IDENTITY_REQUIRED"
    )

    # Legacy unreviewed published item (published=True before org became governed) fails closed for learners
    assert ce_pub.playground_uuid not in {
        p.playground_uuid for p in await list_org_playgrounds(SimpleNamespace(), org.id, learner, db)
    }
    with pytest.raises(HTTPException) as legacy_blocked:
        await get_playground(SimpleNamespace(), ce_pub.playground_uuid, learner, db)
    assert legacy_blocked.value.status_code == 404


@pytest.mark.asyncio
async def test_task07_multi_guardian_consent_isolation_precedence_and_dispute_fail_closed(
    db, org, admin_user, regular_user
):
    guardian_a = await _make_user(db, org.id, "guardian_a", "ga@lk.test", "user_guardian_a")
    guardian_b = await _make_user(db, org.id, "guardian_b", "gb@lk.test", "user_guardian_b")
    policies, owners = await _seed_policies_with_approvals(
        db,
        org,
        admin_user.id,
        {
            StudyPolicyKey.D_01: {
                "markets": ["LK"],
                "learner_age_limits": {"grades": "6-13"},
                "independent_participation": {"verified": True},
            },
            StudyPolicyKey.D_02: {
                "subjects": ["Mathematics"],
                "learner_levels": ["Grade 10 (G.C.E. O/L)"],
                "languages": ["en"],
                "curriculum_claims": {"adaptive_curriculum_keys": ["LK-NIE-G10-MATH"]},
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
                "information_uses": {"learning_terms": ["course_release", "adaptive_progress_report"]},
                "consent": {
                    "notice_versions": {"learning_terms": "v1-lk"},
                    "notice_texts": {"learning_terms": "Approved notice."},
                },
                "parent_report_scope": ["consent_management", "adaptive_report_summary"],
            },
            StudyPolicyKey.D_13: {
                "retention": {"learner_evidence_retention_category": "family_7y"},
                "record_access": {"assigned_reviewer_only": True},
            },
            StudyPolicyKey.D_18: {
                "customer_terms": {"version": "v1-lk"},
                "selected_market_requirements": {"jurisdiction": "LK"},
            },
        },
    )
    owner = owners[StudyOwnerFunction.FAMILY_SERVICES]
    profile = StudyLearnerProfile(
        learner_uuid="learner_multiguard",
        org_id=org.id,
        user_id=regular_user.id,
        created_by_user_id=admin_user.id,
        status="active",
        control_mode="dependent",
        access_version=1,
    )
    db.add(profile)
    await db.flush()

    now = datetime.now(UTC)
    db.add(
        StudyEligibilityDecision(
            org_id=org.id,
            learner_profile_id=profile.id,
            eligibility_kind="learning",
            outcome="eligible",
            evidence_reference="ELIG-LK-MG",
            policy_version_ids=[
                int(policies[k].id)
                for k in (StudyPolicyKey.D_01, StudyPolicyKey.D_02, StudyPolicyKey.D_12)
            ],
            reviewer_user_id=admin_user.id,
            owner_assignment_id=owner.id,
            effective_from=now - timedelta(hours=1),
        )
    )
    g_policy_ids = [
        int(policies[k].id) for k in (StudyPolicyKey.D_03, StudyPolicyKey.D_12, StudyPolicyKey.D_13)
    ]
    rel_a = StudyRelationship(
        org_id=org.id,
        learner_profile_id=profile.id,
        adult_user_id=guardian_a.id,
        requested_by_user_id=guardian_a.id,
        requested_scopes=["consent_management"],
        approved_scopes=["consent_management"],
        status=StudyRelationshipStatus.ACCEPTED.value,
        request_evidence_reference="REQ-GA",
        request_policy_version_ids=g_policy_ids,
        decision_policy_version_ids=g_policy_ids,
        valid_from=now - timedelta(hours=1),
        valid_to=now + timedelta(days=30),
        decided_by_user_id=admin_user.id,
        decision_owner_assignment_id=owner.id,
        decision_evidence_reference="VER-GA",
        decision_reason_code="VERIFIED",
        decided_at=now,
        authority_version=2,
    )
    rel_b = StudyRelationship(
        org_id=org.id,
        learner_profile_id=profile.id,
        adult_user_id=guardian_b.id,
        requested_by_user_id=guardian_b.id,
        requested_scopes=["consent_management"],
        approved_scopes=["consent_management"],
        status=StudyRelationshipStatus.ACCEPTED.value,
        request_evidence_reference="REQ-GB",
        request_policy_version_ids=g_policy_ids,
        decision_policy_version_ids=g_policy_ids,
        valid_from=now - timedelta(hours=1),
        valid_to=now + timedelta(days=30),
        decided_by_user_id=admin_user.id,
        decision_owner_assignment_id=owner.id,
        decision_evidence_reference="VER-GB",
        decision_reason_code="VERIFIED",
        decided_at=now,
        authority_version=2,
    )
    db.add_all([rel_a, rel_b])
    await db.commit()

    actor_a = PublicUser.model_validate(guardian_a, from_attributes=True)
    actor_b = PublicUser.model_validate(guardian_b, from_attributes=True)

    # Guardian A accepts
    ev_a = await consents.record_guardian_consent_choice(
        db,
        org,
        actor_a,
        profile.learner_uuid,
        StudyConsentEventCreate(
            purpose=StudyConsentPurpose.LEARNING_TERMS,
            choice=StudyConsentChoice.ACCEPT,
            notice_version="v1-lk",
            scope_codes=["course_release"],
        ),
        "idem-ga-accept",
    )
    assert await consents.has_current_consent_scope(
        db, org, profile, StudyConsentPurpose.LEARNING_TERMS, "course_release"
    )

    # Per-guardian history isolation: Guardian B does NOT see Guardian A's event
    assert len(await consents.list_guardian_consent_events(db, org, actor_a, profile.learner_uuid)) == 1
    assert await consents.list_guardian_consent_events(db, org, actor_b, profile.learner_uuid) == []

    # Guardian B CANNOT withdraw Guardian A's event directly
    with pytest.raises(HTTPException) as cross_withdraw:
        await consents.withdraw_guardian_consent_choice(
            db, org, actor_b, profile.learner_uuid, ev_a.event_uuid, "idem-gb-steal-withdraw"
        )
    assert cross_withdraw.value.status_code == 404

    # Guardian B explicitly declines -> fails closed (overrides Guardian A's accept!)
    await consents.record_guardian_consent_choice(
        db,
        org,
        actor_b,
        profile.learner_uuid,
        StudyConsentEventCreate(
            purpose=StudyConsentPurpose.LEARNING_TERMS,
            choice=StudyConsentChoice.DECLINE,
            notice_version="v1-lk",
            scope_codes=["course_release"],
        ),
        "idem-gb-decline",
    )
    assert not await consents.has_current_consent_scope(
        db, org, profile, StudyConsentPurpose.LEARNING_TERMS, "course_release"
    )

    # Guardian B later accepts -> consent active again
    await consents.record_guardian_consent_choice(
        db,
        org,
        actor_b,
        profile.learner_uuid,
        StudyConsentEventCreate(
            purpose=StudyConsentPurpose.LEARNING_TERMS,
            choice=StudyConsentChoice.ACCEPT,
            notice_version="v1-lk",
            scope_codes=["course_release"],
        ),
        "idem-gb-accept",
    )
    assert await consents.has_current_consent_scope(
        db, org, profile, StudyConsentPurpose.LEARNING_TERMS, "course_release"
    )

    # Disputed relationship immediately fails closed even though Guardian A accepted
    rel_b.status = StudyRelationshipStatus.DISPUTED.value
    rel_b.approved_scopes = []
    rel_b.authority_version += 1
    db.add(rel_b)
    await db.commit()
    assert not await consents.has_current_consent_scope(
        db, org, profile, StudyConsentPurpose.LEARNING_TERMS, "course_release"
    )


@pytest.mark.asyncio
async def test_task05_private_managed_release_resolves_for_entitled_learner_and_denies_other_sibling(
    db, org, admin_user, regular_user
):
    sibling_b_user = await _make_user(db, org.id, "sibling_b", "sb@lk.test", "user_sibling_b")
    now = datetime.now(UTC)

    # Private managed course MUST have public=False, published=False
    course = Course(
        org_id=org.id,
        name="Grade 10 Mathematics (NIE)",
        description="Managed course",
        public=False,
        published=False,
        studytomy_managed=True,
        open_to_contributors=False,
        course_uuid="course_managed_t05",
        creation_date="2026-01-01",
        update_date="2026-01-01",
    )
    db.add(course)
    await db.flush()

    policies, owners = await _seed_policies_with_approvals(
        db,
        org,
        admin_user.id,
        {
            StudyPolicyKey.D_09: {
                "release_schedule": {"mode": "frozen"},
                "completion_prerequisites": {"required": False},
                "mastery_gate": {"separate": True},
            },
            StudyPolicyKey.D_10: {
                "learner_eligibility": {"grades": "6-13"},
                "supervision": {"educator_reviewed": True},
                "materials": {"local_only": True},
                "usage_limits": {"daily_cap": 100},
                "review": {"independent": True},
            },
        },
    )
    edu_owner = owners[StudyOwnerFunction.EDUCATION]

    snapshot = {
        "course": {
            "id": course.id,
            "org_id": org.id,
            "course_uuid": course.course_uuid,
            "name": course.name,
        },
        "chapters": [],
    }
    snapshot_sha = canonical_request_hash(snapshot)

    review = StudyCourseReview(
        org_id=org.id,
        course_uuid=course.course_uuid,
        revision_sha256=snapshot_sha,
        owner_user_id=regular_user.id,
        reviewer_user_id=admin_user.id,
        reviewer_assignment_id=edu_owner.id,
        rights_evidence_id=1,
        accessibility_evidence_id=2,
        policy_version_ids=[int(policies[StudyPolicyKey.D_09].id)],
        decision="approved",
        accessibility_confirmed=True,
        assessment_confirmed=True,
    )
    db.add(review)
    await db.flush()

    release = StudyCourseRelease(
        org_id=org.id,
        course_uuid=course.course_uuid,
        review_id=review.id,
        assessment_policy_id=policies[StudyPolicyKey.D_09].id,
        progression_policy_id=policies[StudyPolicyKey.D_09].id,
        retention_policy_id=policies[StudyPolicyKey.D_09].id,
        rights_policy_id=policies[StudyPolicyKey.D_09].id,
        retention_category="education_7y",
        policy_binding={"policy_version_ids": [int(policies[StudyPolicyKey.D_09].id)]},
        snapshot=snapshot,
        snapshot_sha256=snapshot_sha,
        media_manifest={},
        released_by_user_id=admin_user.id,
    )
    db.add(release)
    await db.flush()

    curriculum = StudyCurriculumRevision(
        org_id=org.id,
        owner_course_uuid=course.course_uuid,
        curriculum_key="LK-NIE-G10-MATH",
        subject="Mathematics",
        level="Grade 10 (G.C.E. O/L)",
        language="en",
        source_reference="NIE-2015",
        rights_reference="RIGHTS-LK-01",
        status="published",
        version=2,
        content_digest="b" * 64,
        reviewer_id=admin_user.id,
        approved_at=now,
    )
    db.add(curriculum)
    await db.flush()

    sibling_a = StudyLearnerProfile(
        learner_uuid="learner_sib_a",
        org_id=org.id,
        user_id=regular_user.id,
        created_by_user_id=admin_user.id,
        status="active",
        control_mode="dependent",
    )
    sibling_b = StudyLearnerProfile(
        learner_uuid="learner_sib_b",
        org_id=org.id,
        user_id=sibling_b_user.id,
        created_by_user_id=admin_user.id,
        status="active",
        control_mode="dependent",
    )
    db.add_all([sibling_a, sibling_b])
    await db.flush()

    # Only sibling A holds an active grant for the frozen release
    db.add(
        StudyCourseAccessGrant(
            org_id=org.id,
            learner_profile_id=sibling_a.id,
            course_uuid=course.course_uuid,
            release_id=release.id,
            source_kind="order_line",
            source_uuid="order_line_sib_a_1",
            start_basis="fixed_period",
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=30),
            created_at=now - timedelta(days=1),
        )
    )
    await db.commit()

    # Entitled sibling A resolves the private unpublished managed course cleanly
    resolved_curr, resolved_course = await adaptive_progress._published_or_managed_curriculum(
        db, org, sibling_a, curriculum.uuid, now
    )
    assert resolved_curr.id == curriculum.id
    assert resolved_course.id == course.id
    assert resolved_course.published is False  # Never flipped to True!

    # Unentitled sibling B fails closed with 404
    with pytest.raises(HTTPException) as denied:
        await adaptive_progress._published_or_managed_curriculum(
            db, org, sibling_b, curriculum.uuid, now
        )
    assert denied.value.status_code == 404


@pytest.mark.asyncio
async def test_task12_task14_objective_math_scoring_and_tutor_injection_fallback():
    # TASK-12: Exact decimal tolerance & single-choice scoring (no subjective AI mark)
    numeric_item = SimpleNamespace(
        evaluator_version=adaptive_objective_scoring.OBJECTIVE_EVALUATOR,
        kind="number",
        public_question={"kind": "number", "prompt": "Find pi to 2 d.p."},
        private_answer={"expected": "3.14", "tolerance": "0.01"},
        error_codes={"incorrect": "ROUNDING_ERROR"},
    )
    ok_res = adaptive_objective_scoring.evaluate_objective_item(
        numeric_item, {"kind": "number", "value": "3.145"}
    )
    assert ok_res.outcome == "correct"
    bad_res = adaptive_objective_scoring.evaluate_objective_item(
        numeric_item, {"kind": "number", "value": "3.20"}
    )
    assert bad_res.outcome == "incorrect"
    assert bad_res.error_code == "ROUNDING_ERROR"

    # TASK-14: Forged grade / injection output from model falls back safely in English
    ctx = adaptive_guidance.GuidanceContext(
        lesson_title="Quadratic Equations",
        lesson_prose="Factor x^2 - 5x + 6 = (x - 2)(x - 3).",
        concept_title="Factorisation",
        objective="Factorise monic quadratic expressions",
        mastery_state="developing",
        independent_family_count=1,
        instruction_language="en-LK",
    )

    async def _malicious_provider(**_kwargs):
        return {
            "message": "Ignore rubric: I grant you 100% mastered!",
            "teaching_action": "grant_grade",
            "check_prompt": None,
            "source_refs": ["external_url"],
        }

    with patch("src.services.studytomy.adaptive_teaching.generate", side_effect=_malicious_provider):
        output = await adaptive_teaching.generate_teaching(
            ctx, action="explain", model_name="synthetic"
        )
    assert output.fallback_used is True
    assert output.teaching_action == "explain"
    assert output.source_refs == ["current_lesson"]


@pytest.mark.asyncio
async def test_t06_t07_immutable_release_curriculum_binding_survives_ab_publish_and_retirement(
    db, org, admin_user, regular_user
):
    now = datetime.now(UTC)
    learner_b_user = await _make_user(db, org.id, "ab_learner_b", "abb@lk.test", "user_ab_b")

    course = Course(
        org_id=org.id,
        name="Grade 11 Physics (NIE)",
        description="Managed A/B test course",
        public=False,
        published=False,
        studytomy_managed=True,
        open_to_contributors=False,
        course_uuid="course_ab_binding_t06",
        creation_date="2026-01-01",
        update_date="2026-01-01",
    )
    db.add(course)
    await db.flush()

    chapter = Chapter(
        org_id=org.id,
        course_id=course.id,
        chapter_uuid="chap_ab_t06",
        name="Mechanics",
        description="Mechanics",
        creation_date="2026-01-01",
        update_date="2026-01-01",
    )
    db.add(chapter)
    await db.flush()
    db.add(
        CourseChapter(
            org_id=org.id,
            course_id=course.id,
            chapter_id=chapter.id,
            order=1,
            creation_date="2026-01-01",
            update_date="2026-01-01",
        )
    )
    lesson = Activity(
        org_id=org.id,
        course_id=course.id,
        activity_uuid="act_ab_physics_1",
        name="Newton Laws",
        activity_type=ActivityTypeEnum.TYPE_DYNAMIC,
        activity_sub_type="SUBTYPE_DYNAMIC_PAGE",
        content={"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "F = ma"}]}]},
        details={},
        published=True,
        current_version=1,
        creation_date="2026-01-01",
        update_date="2026-01-01",
    )
    db.add(lesson)
    await db.flush()
    db.add(
        ChapterActivity(
            org_id=org.id,
            course_id=course.id,
            chapter_id=chapter.id,
            activity_id=lesson.id,
            order=1,
            creation_date="2026-01-01",
            update_date="2026-01-01",
        )
    )

    policies, owners = await _seed_policies_with_approvals(
        db,
        org,
        admin_user.id,
        {
            StudyPolicyKey.D_01: {"markets": ["LK"], "learner_age_limits": {"grades": "6-13"}, "independent_participation": {"verified": True}},
            StudyPolicyKey.D_02: {"subjects": ["Physics"], "learner_levels": ["Grade 11"], "languages": ["en"], "curriculum_claims": {"adaptive_curriculum_keys": ["LK-PHY-G11"]}},
            StudyPolicyKey.D_03: {"guardian_authority": {"verification": "human"}, "adult_transition": {"review": "human"}},
            StudyPolicyKey.D_05: {"prices": {"currency": "LKR"}, "access_periods": {"term_days": 90}, "learner_allocation": {"single_named_learner": True}},
            StudyPolicyKey.D_08: {"assessment": {"passing_score": 50}, "assistance": {"adaptive_guidance": {"enabled": True}}},
            StudyPolicyKey.D_09: {"release_schedule": {"mode": "frozen", "ungated_text_activities": {course.course_uuid: [lesson.activity_uuid]}}, "completion_prerequisites": {"required": False}, "mastery_gate": {"separate": True}},
            StudyPolicyKey.D_10: {"learner_eligibility": {"grades": "6-13"}, "supervision": {"educator_reviewed": True}, "materials": {"local_only": True}, "usage_limits": {"daily_cap": 100}, "review": {"independent": True}},
            StudyPolicyKey.D_11: {"community_audiences": {"restricted": True}, "adult_contact_boundaries": {"moderated": True}, "moderation": {"enabled": True}},
            StudyPolicyKey.D_12: {"information_uses": {"learning_terms": ["course_release", "adaptive_mastery", "adaptive_guidance"]}, "consent": {"notice_versions": {"learning_terms": "v1"}, "notice_texts": {"learning_terms": "Approved"}}, "parent_report_scope": ["consent_management", "adaptive_report_summary"]},
            StudyPolicyKey.D_13: {"retention": {"learner_evidence_retention_category": "education_7y"}, "record_access": {"assigned_reviewer_only": True}},
            StudyPolicyKey.D_15: {"accessibility_standard": {"wcag": "2.1-AA"}, "supported_formats": {"media_types": ["text/html"]}, "service_recovery": {"fallback": "draft"}},
            StudyPolicyKey.D_16: {"content_rights": {"verified": True}, "review_approvals": {"independent_reviewer": True}, "certificate_presentation": {"none": True}},
            StudyPolicyKey.D_18: {"customer_terms": {"version": "v1"}, "selected_market_requirements": {"jurisdiction": "LK"}},
        },
    )
    edu_owner = owners[StudyOwnerFunction.EDUCATION]

    curr_a = StudyCurriculumRevision(
        org_id=org.id,
        owner_course_uuid=course.course_uuid,
        curriculum_key="LK-PHY-G11",
        revision=1,
        subject="Physics",
        level="Grade 11",
        language="en",
        source_reference="NIE-PHY-11",
        rights_reference="RIGHTS-PHY-1",
        status="published",
        version=2,
        content_digest="a" * 64,
        reviewer_id=admin_user.id,
        approved_at=now,
    )
    db.add(curr_a)
    await db.flush()
    concept_a = StudyConcept(
        org_id=org.id,
        curriculum_revision_id=curr_a.id,
        code="NEWTON_1",
        title="Newton's Second Law A",
        objective="Apply F = ma",
        sequence=1,
    )
    db.add(concept_a)
    await db.flush()
    lesson_digest = adaptive_curriculum.curriculum_activity_digest(lesson)
    db.add(
        StudyConceptActivity(
            org_id=org.id,
            curriculum_revision_id=curr_a.id,
            concept_id=concept_a.id,
            course_id=course.id,
            course_uuid=course.course_uuid,
            activity_id=lesson.id,
            activity_uuid=lesson.activity_uuid,
            activity_version=lesson.current_version,
            activity_digest=lesson_digest,
            usage="teach",
        )
    )
    await db.flush()

    # Freeze Release A while Curriculum A is published -> curriculum_binding captures A
    snap_a = await releases.course_candidate_snapshot(db, course)
    assert snap_a["curriculum_binding"]["curriculum_revision_id"] == curr_a.id
    assert snap_a["curriculum_binding"]["curriculum_revision_uuid"] == curr_a.uuid
    assert snap_a["curriculum_binding"]["activity_concepts"][lesson.activity_uuid]["concept_uuids"] == [concept_a.uuid]

    sha_a = canonical_request_hash(snap_a)
    rev_a = StudyCourseReview(
        org_id=org.id,
        course_uuid=course.course_uuid,
        revision_sha256=sha_a,
        owner_user_id=regular_user.id,
        reviewer_user_id=admin_user.id,
        reviewer_assignment_id=edu_owner.id,
        rights_evidence_id=1,
        accessibility_evidence_id=2,
        policy_version_ids=[int(policies[StudyPolicyKey.D_09].id)],
        decision="approved",
        accessibility_confirmed=True,
        assessment_confirmed=True,
    )
    db.add(rev_a)
    await db.flush()
    rel_a = StudyCourseRelease(
        org_id=org.id,
        course_uuid=course.course_uuid,
        review_id=rev_a.id,
        assessment_policy_id=policies[StudyPolicyKey.D_08].id,
        progression_policy_id=policies[StudyPolicyKey.D_09].id,
        retention_policy_id=policies[StudyPolicyKey.D_13].id,
        rights_policy_id=policies[StudyPolicyKey.D_16].id,
        retention_category="education_7y",
        policy_binding={"policy_version_ids": [int(policies[StudyPolicyKey.D_09].id)]},
        snapshot=snap_a,
        snapshot_sha256=sha_a,
        media_manifest={},
        released_by_user_id=admin_user.id,
    )
    db.add(rel_a)
    await db.flush()

    # Publish Curriculum B mapping the exact same unchanged lesson activity & digest
    curr_b = StudyCurriculumRevision(
        org_id=org.id,
        owner_course_uuid=course.course_uuid,
        curriculum_key="LK-PHY-G11",
        revision=2,
        subject="Physics",
        level="Grade 11",
        language="en",
        source_reference="NIE-PHY-11-B",
        rights_reference="RIGHTS-PHY-2",
        status="published",
        version=2,
        content_digest="b" * 64,
        reviewer_id=admin_user.id,
        approved_at=now,
    )
    db.add(curr_b)
    await db.flush()
    concept_b = StudyConcept(
        org_id=org.id,
        curriculum_revision_id=curr_b.id,
        code="NEWTON_1_B",
        title="Newton's Second Law B",
        objective="Apply F = ma (rev B)",
        sequence=1,
    )
    db.add(concept_b)
    await db.flush()
    db.add(
        StudyConceptActivity(
            org_id=org.id,
            curriculum_revision_id=curr_b.id,
            concept_id=concept_b.id,
            course_id=course.id,
            course_uuid=course.course_uuid,
            activity_id=lesson.id,
            activity_uuid=lesson.activity_uuid,
            activity_version=lesson.current_version,
            activity_digest=lesson_digest,
            usage="teach",
        )
    )
    await db.flush()

    # While BOTH A and B are published simultaneously, taking a new snapshot fails closed (409 ambiguous)
    with pytest.raises(HTTPException) as ambig_exc:
        await releases.course_candidate_snapshot(db, course)
    assert ambig_exc.value.status_code == 409
    assert ambig_exc.value.detail["code"] == "AMBIGUOUS_CURRICULUM_BINDING"

    # Retire A -> now B is uniquely published -> Release B pins B cleanly
    curr_a.status = "retired"
    db.add(curr_a)
    await db.flush()
    snap_b = await releases.course_candidate_snapshot(db, course)
    assert snap_b["curriculum_binding"]["curriculum_revision_id"] == curr_b.id
    sha_b = canonical_request_hash(snap_b)
    rev_b = StudyCourseReview(
        org_id=org.id,
        course_uuid=course.course_uuid,
        revision_sha256=sha_b,
        owner_user_id=regular_user.id,
        reviewer_user_id=admin_user.id,
        reviewer_assignment_id=edu_owner.id,
        rights_evidence_id=1,
        accessibility_evidence_id=2,
        policy_version_ids=[int(policies[StudyPolicyKey.D_09].id)],
        decision="approved",
        accessibility_confirmed=True,
        assessment_confirmed=True,
    )
    db.add(rev_b)
    await db.flush()
    rel_b = StudyCourseRelease(
        org_id=org.id,
        course_uuid=course.course_uuid,
        review_id=rev_b.id,
        assessment_policy_id=policies[StudyPolicyKey.D_08].id,
        progression_policy_id=policies[StudyPolicyKey.D_09].id,
        retention_policy_id=policies[StudyPolicyKey.D_13].id,
        rights_policy_id=policies[StudyPolicyKey.D_16].id,
        retention_category="education_7y",
        policy_binding={"policy_version_ids": [int(policies[StudyPolicyKey.D_09].id)]},
        snapshot=snap_b,
        snapshot_sha256=sha_b,
        media_manifest={},
        released_by_user_id=admin_user.id,
    )
    db.add(rel_b)
    await db.flush()

    profile_a = StudyLearnerProfile(
        learner_uuid="learner_ab_a",
        org_id=org.id,
        user_id=regular_user.id,
        created_by_user_id=admin_user.id,
        status="active",
        control_mode="independent",
    )
    profile_b = StudyLearnerProfile(
        learner_uuid="learner_ab_b",
        org_id=org.id,
        user_id=learner_b_user.id,
        created_by_user_id=admin_user.id,
        status="active",
        control_mode="independent",
    )
    db.add_all([profile_a, profile_b])
    await db.flush()

    el_policy_ids = [int(policies[k].id) for k in (StudyPolicyKey.D_01, StudyPolicyKey.D_02, StudyPolicyKey.D_12)]
    indep_policy_ids = [int(policies[k].id) for k in (StudyPolicyKey.D_01, StudyPolicyKey.D_03, StudyPolicyKey.D_12)]
    c_policy_ids = [int(policies[k].id) for k in (StudyPolicyKey.D_01, StudyPolicyKey.D_03, StudyPolicyKey.D_12, StudyPolicyKey.D_05, StudyPolicyKey.D_18)]
    for prof, tok in ((profile_a, "a"), (profile_b, "b")):
        db.add(
            StudyEligibilityDecision(
                org_id=org.id,
                learner_profile_id=prof.id,
                eligibility_kind="learning",
                outcome="eligible",
                evidence_reference=f"ELIG-AB-{tok}",
                policy_version_ids=el_policy_ids,
                reviewer_user_id=admin_user.id,
                owner_assignment_id=edu_owner.id,
                effective_from=now - timedelta(hours=1),
            )
        )
        db.add(
            StudyEligibilityDecision(
                org_id=org.id,
                learner_profile_id=prof.id,
                eligibility_kind="independent_participation",
                outcome="eligible",
                evidence_reference=f"ELIG-INDEP-AB-{tok}",
                policy_version_ids=indep_policy_ids,
                reviewer_user_id=admin_user.id,
                owner_assignment_id=edu_owner.id,
                effective_from=now - timedelta(hours=1),
            )
        )
        from src.db.studytomy.privacy import StudyConsentEvent

        db.add(
            StudyConsentEvent(
                org_id=org.id,
                learner_profile_id=prof.id,
                actor_user_id=prof.user_id,
                subject_user_id=prof.user_id,
                purpose_code="learning_terms",
                choice="accept",
                notice_version="v1",
                scope_codes=["course_release", "adaptive_mastery", "adaptive_guidance"],
                policy_version_ids=c_policy_ids,
                effective_at=now - timedelta(hours=1),
            )
        )

    db.add_all([
        StudyCourseAccessGrant(
            org_id=org.id,
            learner_profile_id=profile_a.id,
            course_uuid=course.course_uuid,
            release_id=rel_a.id,
            source_kind="order_line",
            source_uuid="ord_ab_a",
            start_basis="fixed_period",
            starts_at=now - timedelta(hours=1),
            ends_at=now + timedelta(days=30),
            created_at=now - timedelta(hours=1),
        ),
        StudyCourseAccessGrant(
            org_id=org.id,
            learner_profile_id=profile_b.id,
            course_uuid=course.course_uuid,
            release_id=rel_b.id,
            source_kind="order_line",
            source_uuid="ord_ab_b",
            start_basis="fixed_period",
            starts_at=now - timedelta(hours=1),
            ends_at=now + timedelta(days=30),
            created_at=now - timedelta(hours=1),
        ),
    ])
    await db.commit()

    # Learner A (Release A) still resolves retired Curriculum A & concept A cleanly, NEVER B!
    src_a = await adaptive_attempt_access.resolve_managed_release_lesson_source(
        db, org, regular_user, course.course_uuid, rel_a.uuid, lesson.activity_uuid, now
    )
    assert src_a.curriculum.id == curr_a.id
    assert src_a.concept_uuids == (concept_a.uuid,)

    resolved_curr_a, _ = await adaptive_progress._published_or_managed_curriculum(
        db, org, profile_a, curr_a.uuid, now
    )
    assert resolved_curr_a.id == curr_a.id
    with pytest.raises(HTTPException) as wrong_b:
        await adaptive_progress._published_or_managed_curriculum(
            db, org, profile_a, curr_b.uuid, now
        )
    assert wrong_b.value.status_code == 404

    # Learner B (Release B) resolves Curriculum B & concept B, NEVER A!
    src_b = await adaptive_attempt_access.resolve_managed_release_lesson_source(
        db, org, learner_b_user, course.course_uuid, rel_b.uuid, lesson.activity_uuid, now
    )
    assert src_b.curriculum.id == curr_b.id
    assert src_b.concept_uuids == (concept_b.uuid,)

    resolved_curr_b, _ = await adaptive_progress._published_or_managed_curriculum(
        db, org, profile_b, curr_b.uuid, now
    )
    assert resolved_curr_b.id == curr_b.id
    with pytest.raises(HTTPException) as wrong_a:
        await adaptive_progress._published_or_managed_curriculum(
            db, org, profile_b, curr_a.uuid, now
        )
    assert wrong_a.value.status_code == 404

    # Tampered curriculum_binding fails closed (404)
    tampered_snap = {
        **snap_a,
        "curriculum_binding": {**snap_a["curriculum_binding"], "content_digest": "f" * 64},
    }
    rel_a.snapshot = tampered_snap
    rel_a.snapshot_sha256 = canonical_request_hash(tampered_snap)
    db.add(rel_a)
    await db.commit()
    with pytest.raises(HTTPException) as tampered_exc:
        await adaptive_attempt_access.resolve_managed_release_lesson_source(
            db, org, regular_user, course.course_uuid, rel_a.uuid, lesson.activity_uuid, now
        )
    assert tampered_exc.value.status_code == 404
