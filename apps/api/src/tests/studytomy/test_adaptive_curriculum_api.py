from asyncio import Event, gather, wait_for
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.courses.activities import Activity, ActivitySubTypeEnum, ActivityTypeEnum
from src.db.courses.assignments import (
    Assignment,
    AssignmentTask,
    AssignmentTaskSubmission,
    AssignmentTaskTypeEnum,
    AssignmentUserSubmission,
    AssignmentUserSubmissionStatus,
    GradingTypeEnum,
)
from src.db.courses.chapter_activities import ChapterActivity
from src.db.courses.chapters import Chapter
from src.db.courses.courses import Course
from src.db.organization_config import OrganizationConfig
from src.db.organizations import Organization
from src.db.roles import Role, RoleTypeEnum
from src.db.studytomy.access import StudyCourseAccessGrant
from src.db.studytomy.adaptive import (
    StudyConcept,
    StudyConceptActivity,
    StudyConceptPrerequisite,
    StudyCurriculumRevision,
)
from src.db.studytomy.adaptive_diagnostics import StudyDiagnosticRevision
from src.db.studytomy.adaptive_evidence import (
    StudyAttemptItem,
    StudyConceptMastery,
    StudyLearningAttempt,
    StudyLearningEvidence,
)
from src.db.studytomy.adaptive_guidance import StudyTutorSession, StudyTutorTurn
from src.db.studytomy.adaptive_items import StudyItemRevision
from src.db.studytomy.adaptive_reports import (
    StudyProgressReport,
    StudyReportRecipient,
    StudyReportReviewRequest,
)
from src.db.studytomy.commerce import StudyOffer, StudyOfferVersion
from src.db.studytomy.education import StudyCourseRelease
from src.db.studytomy.family import (
    StudyEligibilityDecision,
    StudyLearnerProfile,
    StudyRelationship,
    StudySharingGrant,
)
from src.db.studytomy.governance import (
    StudyBusinessEvent,
    StudyOwnerFunction,
    StudyPolicyApproval,
    StudyPolicyKey,
    StudyPolicyStatus,
    StudyPolicyVersion,
)
from src.db.studytomy.operations import StudyOutboxPurpose, StudyOutboxRecord
from src.db.studytomy.orders import StudyOrder, StudyOrderAcceptance, StudyOrderLine
from src.db.studytomy.privacy import StudyConsentEvent
from src.db.trail_runs import TrailRun
from src.db.trail_steps import TrailStep
from src.db.trails import Trail
from src.db.user_organizations import UserOrganization
from src.db.users import PublicUser, User
from src.router import v1_router
from src.security.studytomy_access import StudytomyCapabilityName
from src.services.ai.tools.base import make_synthetic_request
from src.services.auth.session import mint_session_tokens
from src.services.courses.activities.assignments import retry_assignment_submission
from src.services.studytomy.adaptive_attempt_access import (
    require_diagnostic_access,
    require_mapped_release_source,
    require_mapped_source,
)
from src.services.studytomy.adaptive_guidance import load_guidance_context
from src.services.studytomy.adaptive_reports import _authorize_report_release
from src.services.studytomy.adaptive_teaching import TeachingResult, apply_reviewed_hint
from src.services.studytomy.assignment_adapter import (
    journal_native_assignment_grade,
    native_round_uuid,
    resolve_native_assignment_snapshot,
)
from src.services.studytomy.entitlements import resolve_active_course_grants
from src.services.studytomy.idempotency import canonical_request_hash
from src.services.studytomy.outbox import enqueue_delivery
from src.services.studytomy.policies import POLICY_REQUIRED_RESPONSIBILITIES
from src.services.studytomy.releases import (
    ReleasePolicyBinding,
    ReleasePolicyVersion,
    course_candidate_snapshot,
)
from src.services.studytomy.report_notifications import (
    revalidate_report_delivery,
    run_report_delivery_batch,
    send_report_delivery,
)
from src.services.studytomy.worker import ProviderOutcomeUnknown
from src.services.studytomy.sharing import withdraw_my_sharing_grant
from src.tests.conftest import ADMIN_RIGHTS, USER_RIGHTS
from src.tests.studytomy.test_content_evidence import _seed_postgres_release_sources
from src.tests.studytomy.test_studytomy_access import (
    _grant_role_capabilities,
    _seed_active_owner,
    _seed_approved_policy,
    _seed_staff_scope,
    _synthetic_policy_body,
)


def _headers(actor, org, method, key="curriculum-fixture"):
    token = mint_session_tokens(actor.email, amr=method, org_id=org.id).access_token
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": key}


async def _course(db, org):
    course = Course(
        org_id=org.id,
        course_uuid=f"course_{uuid4()}",
        name="Synthetic course",
        public=False,
        open_to_contributors=False,
    )
    db.add(course)
    await db.commit()
    return course


async def test_adaptive_curriculum_drafts_require_direct_session_exact_scope_and_native_access(
    db,
    org,
    other_org,
    admin_user,
    regular_user,
):
    await _exercise_authoring(db, org, other_org, admin_user, regular_user)


@pytest.mark.studytomy_postgres
async def test_adaptive_curriculum_drafts_round_trip_on_postgres(
    postgres_session_factory,
):
    async with postgres_session_factory() as db:
        organizations = [
            Organization(
                name="Synthetic org",
                slug=f"ale-{uuid4()}",
                org_uuid=f"org_{uuid4()}",
                email=f"{uuid4()}@example.invalid",
            )
            for _ in range(2)
        ]
        db.add_all(organizations)
        await db.flush()
        org, other_org = organizations
        actors = []
        for name, rights in (("Writer", ADMIN_RIGHTS), ("Reader", USER_RIGHTS)):
            user = User(
                username=f"synthetic-{uuid4()}",
                first_name=name,
                last_name="Fixture",
                email=f"{uuid4()}@example.invalid",
                user_uuid=f"user_{uuid4()}",
            )
            role = Role(
                name=name,
                org_id=org.id,
                role_type=RoleTypeEnum.TYPE_ORGANIZATION,
                role_uuid=f"role_{uuid4()}",
                rights=rights.model_dump(),
            )
            db.add_all([user, role])
            await db.flush()
            db.add(
                UserOrganization(
                    user_id=user.id,
                    org_id=org.id,
                    role_id=role.id,
                    creation_date="",
                    update_date="",
                )
            )
            actors.append(PublicUser.model_validate(user))
        await db.commit()
        await _exercise_authoring(
            db, org, other_org, *actors, session_factory=postgres_session_factory
        )


async def _exercise_authoring(
    db, org, other_org, admin_user, regular_user, *, session_factory=None
):
    app = FastAPI()
    app.include_router(v1_router)
    app.dependency_overrides[get_db_session] = lambda: db

    async def independent_session():
        async with session_factory() as session:
            yield session

    async def concurrent_posts(
        client,
        path,
        requests,
        *,
        second_path=None,
        second_method="POST",
        extra_calls=(),
    ):
        # Release the fixture's locks before independent transactions race.
        await db.commit()
        app.dependency_overrides[get_db_session] = independent_session
        try:
            return await gather(
                *(
                    client.request(
                        second_method if index == 1 else "POST",
                        (second_path or path) if index == 1 else path,
                        json=body,
                        headers=headers,
                    )
                    for index, (body, headers) in enumerate(requests)
                ),
                *(
                    client.request(method, target, json=body, headers=headers)
                    for method, target, body, headers in extra_calls
                ),
            )
        finally:
            app.dependency_overrides[get_db_session] = lambda: db

    owned = await _course(db, org)
    unassigned = await _course(db, org)
    foreign = await _course(db, other_org)
    base = f"/api/v1/studytomy/adaptive/orgs/{org.org_uuid}"
    path = f"{base}/curriculum-revisions"
    configuration = f"{base}/courses/{owned.course_uuid}/configuration"
    body = {
        "owner_course_uuid": owned.course_uuid,
        "curriculum_key": "synthetic",
        "subject": "Maths",
        "level": "Test",
        "language": "en",
        "source_reference": "fixture-source",
        "rights_reference": "fixture-rights",
    }
    direct = _headers(admin_user, org, "password")
    delegated = _headers(admin_user, org, "api_token")
    unprivileged = _headers(regular_user, org, "password")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get(configuration)).status_code == 401
        assert (await client.get(configuration, headers=delegated)).status_code == 403
        assert (await client.get(configuration, headers=direct)).status_code == 404
        assert (await client.post(path, json=body)).status_code == 401
        impersonated = await client.get(f"{base}/availability", headers=delegated)
        assert impersonated.status_code == 403
        assert impersonated.json()["detail"]["code"] == "INTERACTIVE_SESSION_REQUIRED"
        assert (
            await client.post(path, json=body, headers=delegated)
        ).status_code == 403
        assert (
            await client.post(path, json=body, headers=unprivileged)
        ).status_code == 403
        assert (await client.post(path, json=body, headers=direct)).status_code == 403

        await _grant_role_capabilities(
            db,
            org.id,
            admin_user.id,
            adaptive_curriculum_author=True,
            assessment_moderation=True,
            progress_report_review=True,
        )
        owner = await _seed_active_owner(
            db, org, admin_user.id, admin_user.id, StudyOwnerFunction.EDUCATION
        )
        scope = await _seed_staff_scope(
            db,
            org.id,
            owner.id,
            StudytomyCapabilityName.ADAPTIVE_CURRICULUM_AUTHOR,
            "course",
            owned.course_uuid,
            admin_user.id,
        )
        restricted_role = await _grant_role_capabilities(
            db,
            org.id,
            regular_user.id,
            adaptive_curriculum_author=True,
        )
        restricted_role.rights = restricted_role.rights | {
            "courses": restricted_role.rights["courses"]
            | {
                "action_update": False,
                "action_update_own": False,
            },
        }
        db.add(restricted_role)
        await db.commit()
        other_owner = await _seed_active_owner(
            db,
            org,
            regular_user.id,
            admin_user.id,
            StudyOwnerFunction.EDUCATION,
        )
        await _seed_staff_scope(
            db,
            org.id,
            other_owner.id,
            StudytomyCapabilityName.ADAPTIVE_CURRICULUM_AUTHOR,
            "course",
            owned.course_uuid,
            admin_user.id,
        )
        assert (
            await client.post(path, json=body, headers=unprivileged)
        ).status_code == 403
        for other in [unassigned, foreign]:
            denied = await client.post(
                path,
                json=body | {"owner_course_uuid": other.course_uuid},
                headers=direct,
            )
            assert denied.status_code == 404
        assert (
            await client.post(path, json=body | {"status": "published"}, headers=direct)
        ).status_code == 422
        created = await client.post(path, json=body, headers=direct)
        assert created.status_code == 201, created.text
        assert created.headers["Cache-Control"] == "private, no-store"
        draft = created.json()
        assert draft["status"] == "draft"
        assert "rights_reference" not in draft
        author_catalog = await client.get(configuration, headers=direct)
        assert author_catalog.status_code == 200, author_catalog.text
        assert author_catalog.headers["Cache-Control"] == "private, no-store"
        assert author_catalog.json()["can_author"] is True
        assert author_catalog.json()["can_review"] is False
        assert author_catalog.json()["curricula"][0]["uuid"] == draft["uuid"]
        detail_path = f"{path}/{draft['uuid']}"
        empty_preview = await client.get(f"{detail_path}/review", headers=direct)
        assert empty_preview.status_code == 200, empty_preview.text
        assert empty_preview.json()["concepts"] == []
        assert empty_preview.json()["prerequisites"] == []
        assert empty_preview.json()["mappings"] == []
        metadata = {
            field: body[field]
            for field in (
                "subject",
                "level",
                "language",
                "source_reference",
                "rights_reference",
            )
        }
        updated = await client.put(
            detail_path,
            json=metadata | {"subject": "Fractions", "expected_version": 1},
            headers=direct,
        )
        assert updated.status_code == 200
        assert updated.json()["version"] == 2
        assert (await client.post(path, json=body, headers=direct)).json() == draft
        duplicate = await client.post(
            path, json=body | {"subject": "Other"}, headers=direct
        )
        assert duplicate.status_code == 409

        graph_path = f"{detail_path}/concepts"
        a_to_b = {"concept_code": "a", "prerequisite_code": "b"}
        b_to_a = {"concept_code": "b", "prerequisite_code": "a"}
        first_concept = await client.put(
            graph_path,
            json={
                "expected_version": 2,
                "prerequisites": [],
                "concepts": [{
                    "code": "a", "title": "a", "objective": "First objective", "sequence": 0,
                }],
            },
            headers=direct,
        )
        assert first_concept.status_code == 200, first_concept.text
        assert first_concept.json()["curriculum"]["version"] == 3
        assert len(first_concept.json()["concepts"]) == 1
        graph = {
            "expected_version": 3,
            "prerequisites": [a_to_b],
            "concepts": [
                {
                    "code": code,
                    "title": code,
                    "objective": "Synthetic objective",
                    "sequence": sequence,
                }
                for sequence, code in enumerate(("a", "b"))
            ],
        }
        saved = await client.put(graph_path, json=graph, headers=direct)
        assert saved.status_code == 200, saved.text
        assert saved.json()["curriculum"]["version"] == 4
        assert (
            await client.put(graph_path, json=graph, headers=direct)
        ).status_code == 409
        cycle = await client.put(
            graph_path,
            json=graph | {"expected_version": 4, "prerequisites": [a_to_b, b_to_a]},
            headers=direct,
        )
        assert cycle.status_code == 422
        assert cycle.json()["detail"]["code"] == "CURRICULUM_GRAPH_CYCLE"
        assert (
            len(
                (
                    await db.execute(
                        select(StudyConcept).where(
                            StudyConcept.org_id == org.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            == 2
        )
        assert (
            len(
                (
                    await db.execute(
                        select(StudyConceptPrerequisite).where(
                            StudyConceptPrerequisite.org_id == org.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            == 1
        )

        owned.public = True
        owned.published = True
        lesson = Activity(
            org_id=org.id,
            course_id=owned.id,
            activity_uuid=f"activity_{uuid4()}",
            name="Fractions",
            published=True,
            activity_type=ActivityTypeEnum.TYPE_DYNAMIC,
            activity_sub_type=ActivitySubTypeEnum.SUBTYPE_DYNAMIC_PAGE,
            content={
                "type": "doc",
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {"type": "text", "text": "Synthetic released lesson."}
                        ],
                    }
                ],
            },
        )
        next_lesson = Activity(
            org_id=org.id,
            course_id=owned.id,
            activity_uuid=f"activity_{uuid4()}",
            name="Fractions practice",
            published=True,
            activity_type=ActivityTypeEnum.TYPE_DYNAMIC,
            activity_sub_type=ActivitySubTypeEnum.SUBTYPE_DYNAMIC_PAGE,
            content={
                "type": "doc",
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {"type": "text", "text": "Synthetic practice lesson."}
                        ],
                    }
                ],
            },
        )
        unlinked_lesson = Activity(
            org_id=org.id,
            course_id=owned.id,
            activity_uuid=f"activity_{uuid4()}",
            name="Unmapped synthetic lesson",
            published=True,
            activity_type=ActivityTypeEnum.TYPE_DYNAMIC,
            activity_sub_type=ActivitySubTypeEnum.SUBTYPE_DYNAMIC_PAGE,
            content={"type": "doc", "content": []},
        )
        practice_lesson = Activity(
            org_id=org.id, course_id=owned.id, activity_uuid=f"activity_{uuid4()}",
            name="Reviewed practice exercise", published=True,
            activity_type=ActivityTypeEnum.TYPE_DYNAMIC,
            activity_sub_type=ActivitySubTypeEnum.SUBTYPE_DYNAMIC_PAGE,
            content=next_lesson.content,
        )
        db.add_all([lesson, next_lesson, unlinked_lesson, practice_lesson])
        await db.commit()
        concept = saved.json()["concepts"][0]
        prerequisite_concept = saved.json()["concepts"][1]
        native_item, native_task, native_activity = await _exercise_native_items(
            client,
            db,
            base,
            direct,
            unprivileged,
            owned,
            concept["uuid"],
        )
        mappings_path = f"{base}/concepts/{concept['uuid']}/activities"
        mapping = {
            "course_uuid": owned.course_uuid,
            "activity_uuid": lesson.activity_uuid,
            "activity_version": lesson.current_version,
            "usage": "teach",
        }
        practice_mapping = {
            "course_uuid": owned.course_uuid,
            "activity_uuid": native_activity.activity_uuid,
            "activity_version": native_activity.current_version,
            "usage": "practice",
        }
        next_lesson_mapping = {
            "course_uuid": owned.course_uuid,
            "activity_uuid": next_lesson.activity_uuid,
            "activity_version": next_lesson.current_version,
            "usage": "teach",
        }
        released_practice_mapping = {
            **next_lesson_mapping,
            "activity_uuid": practice_lesson.activity_uuid,
            "activity_version": practice_lesson.current_version,
            "usage": "practice",
        }
        for wrong, status in (
            ({"course_uuid": unassigned.course_uuid}, 404),
            ({"activity_uuid": "activity_missing"}, 404),
            ({"activity_version": lesson.current_version + 1}, 409),
        ):
            denied = await client.put(
                mappings_path,
                json={"expected_version": 4, "mappings": [mapping | wrong]},
                headers=direct,
            )
            assert denied.status_code == status
        mapped = await client.put(
            mappings_path,
            json={
                "expected_version": 4,
                "mappings": [mapping, practice_mapping, released_practice_mapping],
            },
            headers=direct,
        )
        assert mapped.status_code == 200, mapped.text
        assert mapped.json()["curriculum"]["version"] == 5
        assert sorted(
            mapped.json()["mappings"], key=lambda m: (m["usage"], m["activity_uuid"])
        ) == sorted(
            [practice_mapping, mapping, released_practice_mapping],
            key=lambda m: (m["usage"], m["activity_uuid"]),
        )
        mapped_prerequisite = await client.put(
            f"{base}/concepts/{prerequisite_concept['uuid']}/activities",
            json={"expected_version": 5, "mappings": [next_lesson_mapping]},
            headers=direct,
        )
        assert mapped_prerequisite.status_code == 200, mapped_prerequisite.text
        assert mapped_prerequisite.json()["curriculum"]["version"] == 6
        remove_mapped_concept = await client.put(
            graph_path,
            json={
                "expected_version": 6,
                "concepts": graph["concepts"][1:],
                "prerequisites": [],
            },
            headers=direct,
        )
        assert remove_mapped_concept.status_code == 409
        assert (
            remove_mapped_concept.json()["detail"]["code"]
            == "CURRICULUM_CONCEPT_MAPPED"
        )
        originals = await _exercise_original_items(
            client,
            db,
            base,
            direct,
            unprivileged,
            concept["uuid"],
            native_item["item_family_uuid"],
        )

        restricted_role.rights = restricted_role.rights | {
            "studytomy": {"content_release_review": True},
        }
        db.add(restricted_role)
        await db.commit()
        await _seed_staff_scope(
            db,
            org.id,
            other_owner.id,
            StudytomyCapabilityName.CONTENT_RELEASE_REVIEW,
            "course",
            owned.course_uuid,
            admin_user.id,
        )
        reviewer_catalog = await client.get(configuration, headers=unprivileged)
        assert reviewer_catalog.status_code == 409
        assert reviewer_catalog.json()["detail"]["code"] == "POLICY_NOT_APPROVED"
        for course in (unassigned, foreign):
            assert (
                await client.get(
                    f"{base}/courses/{course.course_uuid}/configuration", headers=direct
                )
            ).status_code == 404
        await _exercise_publication(
            client,
            db,
            org,
            other_org,
            base,
            detail_path,
            direct,
            unprivileged,
            draft["uuid"],
            lesson,
            next_lesson,
            practice_lesson,
            owner,
            native_item,
            native_task,
            native_activity,
            originals,
            regular_user,
            concurrent_posts if session_factory is not None else None,
        )
        scope.status = "revoked"
        db.add(scope)
        await db.commit()
        assert (await client.post(path, json=body, headers=direct)).status_code == 404
        assert (await client.get(configuration, headers=direct)).status_code == 404


async def _characterize_managed_course_diagnostic_conflict(
    client,
    db,
    org,
    other_org,
    course,
    lesson,
    next_lesson,
    practice_lesson,
    curriculum_uuid,
    staff,
    learner,
    base,
    author_headers,
    reviewer_headers,
):
    """AUD-01/AUD-03: characterize managed private access and exact release sources.

    Every policy, review, release and learner record below is synthetic test data,
    not a real approval. Managed learner access remains private and unpublished.
    """
    token = uuid4().hex
    now = datetime.now(UTC)
    payer, guardian = [
        User(
            username=f"aud01-{token}-{kind}",
            user_uuid=f"user_{token}_{kind}",
            email=f"aud01-{token}-{kind}@example.invalid",
            first_name="Synthetic",
            last_name=kind.title(),
        )
        for kind in ("payer", "guardian")
    ]
    db.add_all([payer, guardian])
    await db.flush()
    assert len({staff.id, learner.id, payer.id, guardian.id}) == 4

    curriculum = (
        await db.execute(
            select(StudyCurriculumRevision).where(
                StudyCurriculumRevision.org_id == org.id,
                StudyCurriculumRevision.uuid == curriculum_uuid,
            )
        )
    ).scalar_one()
    diagnostic = (
        await db.execute(
            select(StudyDiagnosticRevision).where(
                StudyDiagnosticRevision.org_id == org.id,
                StudyDiagnosticRevision.curriculum_revision_id == curriculum.id,
            )
        )
    ).scalar_one()
    assert curriculum.status in {"published", "retired"}
    assert diagnostic.status in {"published", "retired"}
    profile = (
        await db.execute(
            select(StudyLearnerProfile).where(
                StudyLearnerProfile.org_id == org.id,
                StudyLearnerProfile.user_id == learner.id,
            )
        )
    ).scalar_one()

    concept_uuids = tuple(
        (
            await db.execute(
                select(StudyConcept.uuid)
                .join(
                    StudyConceptActivity,
                    StudyConceptActivity.concept_id == StudyConcept.id,
                )
                .where(
                    StudyConcept.org_id == org.id,
                    StudyConcept.curriculum_revision_id == curriculum.id,
                    StudyConceptActivity.activity_uuid == lesson.activity_uuid,
                    StudyConceptActivity.usage == "teach",
                    StudyConceptActivity.archived.is_(False),
                    StudyConcept.archived.is_(False),
                )
            )
        )
        .scalars()
        .all()
    )
    assert concept_uuids

    async def update_d16_reviews(*, item_review=None, diagnostic_review=None):
        current = (
            await db.execute(
                select(StudyPolicyVersion)
                .where(
                    StudyPolicyVersion.org_id == org.id,
                    StudyPolicyVersion.policy_key == StudyPolicyKey.D_16.value,
                    StudyPolicyVersion.status == StudyPolicyStatus.APPROVED.value,
                )
                .order_by(StudyPolicyVersion.version.desc())
                .limit(1)
            )
        ).scalar_one()
        specified = {
            area: decision["value"]
            for area, decision in current.body["decision_items"].items()
            if decision["state"] == "specified"
        }
        review_approvals = dict(specified["review_approvals"])
        if item_review is not None:
            content_rights = dict(specified["content_rights"])
            content_rights["adaptive_original_item_rights"] = [
                *content_rights.get("adaptive_original_item_rights", []),
                item_review["rights_reference"],
            ]
            specified["content_rights"] = content_rights
            review_approvals["adaptive_item_reviews"] = [
                *review_approvals.get("adaptive_item_reviews", []),
                item_review,
            ]
        if diagnostic_review is not None:
            review_approvals["adaptive_diagnostic_reviews"] = [
                *review_approvals.get("adaptive_diagnostic_reviews", []),
                diagnostic_review,
            ]
        specified["review_approvals"] = review_approvals
        current.status = StudyPolicyStatus.SUPERSEDED.value
        await db.flush()
        updated = await _seed_approved_policy(
            db,
            org,
            staff.id,
            StudyPolicyKey.D_16,
            specified=specified,
            version=current.version + 1,
        )
        for responsibility in POLICY_REQUIRED_RESPONSIBILITIES[StudyPolicyKey.D_16]:
            assignment = await _seed_active_owner(
                db,
                org,
                staff.id,
                staff.id,
                responsibility,
            )
            db.add(
                StudyPolicyApproval(
                    org_id=org.id,
                    policy_version_id=updated.id,
                    responsibility=responsibility.value,
                    owner_assignment_id=assignment.id,
                    approver_user_id=staff.id,
                    evidence_reference="synthetic-aud04-review",
                    rationale="Synthetic managed-release test fixture",
                    approved_at=now,
                )
            )
        await db.commit()
        return updated

    # Use a new reviewed item family so the learner's previously exposed
    # native diagnostic item remains excluded by family-level exposure rules.
    item_path = f"{base}/item-revisions"
    managed_item_draft = await client.post(
        item_path,
        json={
            "concept_uuid": concept_uuids[0],
            "difficulty": 2,
            "original_rights_reference": "synthetic-aud04-rights",
            "original_question": {
                "kind": "number",
                "prompt": "Synthetic managed-release measurement.",
            },
            "original_answer": {
                "kind": "number",
                "expected": "42",
                "tolerance": "0.01",
            },
        },
        headers=author_headers | {"Idempotency-Key": f"aud04-item-{uuid4()}"},
    )
    assert managed_item_draft.status_code == 201, managed_item_draft.text
    managed_item = managed_item_draft.json()
    item_preview = await client.get(
        f"{item_path}/{managed_item['uuid']}/review", headers=author_headers
    )
    assert item_preview.status_code == 200, item_preview.text
    item_snapshot = item_preview.json()
    item_evidence = "synthetic-aud04-item-review"
    await update_d16_reviews(
        item_review={
            "revision_uuid": managed_item["uuid"],
            "digest": item_snapshot["content_digest"],
            "rights_reference": item_snapshot["rights_reference"],
            "evidence_reference": item_evidence,
            "allowed_use": "independent_diagnostic",
            "exposure_evidence_reference": "synthetic-aud04-exposure-review",
        }
    )
    managed_item_publish = await client.post(
        f"{base}/definitions/item/{managed_item['uuid']}/publish",
        json={
            "expected_version": managed_item["version"],
            "expected_digest": item_snapshot["content_digest"],
            "review_evidence": item_evidence,
        },
        headers=reviewer_headers | {"Idempotency-Key": f"aud04-item-review-{uuid4()}"},
    )
    assert managed_item_publish.status_code == 200, managed_item_publish.text

    # Use a distinct reviewed diagnostic revision so this managed-release
    # journey does not bypass the native attempt/retake limits exercised above.
    managed_diagnostic_draft = await client.post(
        f"{base}/diagnostic-revisions",
        json={
            "name": "Synthetic managed-release diagnostic",
            "curriculum_uuid": curriculum.uuid,
            "required_concepts": [concept_uuids[0]],
            "item_uuids": [managed_item["uuid"]],
            "selector_version": diagnostic.selector_version,
            "mastery_policy_uuid": diagnostic.mastery_policy_uuid,
            "limits": diagnostic.manifest["limits"],
            "rules": diagnostic.manifest["rules"],
        },
        headers=author_headers | {"Idempotency-Key": f"aud04-diagnostic-{uuid4()}"},
    )
    assert managed_diagnostic_draft.status_code == 201, managed_diagnostic_draft.text
    managed_diagnostic_projection = managed_diagnostic_draft.json()
    diagnostic_evidence = "synthetic-aud04-diagnostic-review"
    await update_d16_reviews(
        diagnostic_review={
            "revision_uuid": managed_diagnostic_projection["uuid"],
            "digest": managed_diagnostic_projection["content_digest"],
            "evidence_reference": diagnostic_evidence,
        }
    )
    managed_diagnostic_response = await client.post(
        f"{base}/definitions/diagnostic/{managed_diagnostic_projection['uuid']}/publish",
        json={
            "expected_version": managed_diagnostic_projection["version"],
            "expected_digest": managed_diagnostic_projection["content_digest"],
            "review_evidence": diagnostic_evidence,
        },
        headers=reviewer_headers | {"Idempotency-Key": f"aud04-publish-{uuid4()}"},
    )
    assert managed_diagnostic_response.status_code == 200, managed_diagnostic_response.text
    managed_diagnostic = (
        await db.execute(
            select(StudyDiagnosticRevision).where(
                StudyDiagnosticRevision.org_id == org.id,
                StudyDiagnosticRevision.uuid == managed_diagnostic_projection["uuid"],
            )
        )
    ).scalar_one()

    concept_uuids = tuple(
        (
            await db.execute(
                select(StudyConcept.uuid)
                .join(
                    StudyConceptActivity,
                    StudyConceptActivity.concept_id == StudyConcept.id,
                )
                .where(
                    StudyConcept.org_id == org.id,
                    StudyConcept.curriculum_revision_id == curriculum.id,
                    StudyConceptActivity.activity_uuid == lesson.activity_uuid,
                    StudyConceptActivity.usage == "teach",
                    StudyConceptActivity.archived.is_(False),
                    StudyConcept.archived.is_(False),
                )
            )
        )
        .scalars()
        .all()
    )
    assert concept_uuids

    # Verify the current native source path with normal authorization first.
    request = make_synthetic_request()
    native_source = await require_mapped_source(
        request,
        db,
        org,
        staff,
        curriculum,
        concept_uuids,
        lesson.activity_uuid,
    )
    assert native_source.id == lesson.id

    # The adaptive authoring fixture mapped the lesson but did not place it in
    # the native course sequence. A releasable lesson must have a chapter link.
    chapter = (
        await db.execute(
            select(Chapter).where(
                Chapter.org_id == org.id,
                Chapter.course_id == course.id,
            )
        )
    ).scalar_one()
    next_lesson = (
        await db.execute(
            select(Activity)
            .join(
                StudyConceptActivity,
                StudyConceptActivity.activity_uuid == Activity.activity_uuid,
            )
            .where(
                Activity.org_id == org.id,
                Activity.course_id == course.id,
                Activity.activity_uuid != lesson.activity_uuid,
                Activity.activity_type == ActivityTypeEnum.TYPE_DYNAMIC,
                Activity.published.is_(True),
                StudyConceptActivity.org_id == org.id,
                StudyConceptActivity.curriculum_revision_id == curriculum.id,
                StudyConceptActivity.course_id == course.id,
                StudyConceptActivity.usage == "teach",
                StudyConceptActivity.archived.is_(False),
            )
            .limit(1)
        )
    ).scalar_one()
    unmapped_lesson = (
        await db.execute(
            select(Activity)
            .where(
                Activity.org_id == org.id,
                Activity.course_id == course.id,
                Activity.activity_uuid.not_in(
                    select(StudyConceptActivity.activity_uuid).where(
                        StudyConceptActivity.org_id == org.id,
                        StudyConceptActivity.curriculum_revision_id == curriculum.id,
                    )
                ),
                Activity.activity_type == ActivityTypeEnum.TYPE_DYNAMIC,
                Activity.published.is_(True),
            )
            .limit(1)
        )
    ).scalar_one()
    db.add(
        ChapterActivity(
            org_id=org.id,
            course_id=course.id,
            chapter_id=chapter.id,
            activity_id=lesson.id,
            order=1,
            creation_date="",
            update_date="",
        )
    )
    db.add_all(
        [
            ChapterActivity(
                org_id=org.id,
                course_id=course.id,
                chapter_id=chapter.id,
                activity_id=activity.id,
                order=index,
                creation_date="",
                update_date="",
            )
            for index, activity in enumerate((next_lesson, unmapped_lesson, practice_lesson), start=2)
        ]
    )
    await db.commit()

    policies = []
    for key in (
        StudyPolicyKey.D_08,
        StudyPolicyKey.D_09,
        StudyPolicyKey.D_13,
        StudyPolicyKey.D_16,
    ):
        policy = (
            await db.execute(
                select(StudyPolicyVersion)
                .where(
                    StudyPolicyVersion.org_id == org.id,
                    StudyPolicyVersion.policy_key == key.value,
                    StudyPolicyVersion.status == StudyPolicyStatus.APPROVED.value,
                )
                .order_by(StudyPolicyVersion.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        assert policy is not None, f"Synthetic {key.value} fixture policy is missing."
        policies.append(policy)

    course.public = False
    course.published = False
    course.studytomy_managed = True
    assignment = (await db.execute(select(Assignment).where(Assignment.course_id == course.id))).scalar_one()
    assignment.published = True
    await db.commit()
    snapshot = await course_candidate_snapshot(db, course)
    review, snapshot, policies = await _seed_postgres_release_sources(
        db,
        org,
        staff.id,
        token,
        snapshot=snapshot,
        course_uuid=course.course_uuid,
        policy_versions=policies,
        policy_version_ids=[int(policy.id) for policy in policies],
        revision_sha256=canonical_request_hash(snapshot),
    )
    binding = ReleasePolicyBinding(
        versions={
            StudyPolicyKey(policy.policy_key): ReleasePolicyVersion(
                policy_id=int(policy.id),
                policy_uuid=policy.policy_uuid,
                version=policy.version,
                body_sha256=canonical_request_hash(policy.body),
            )
            for policy in policies
        },
        retention_category="synthetic",
    )
    release = StudyCourseRelease(
        org_id=org.id,
        course_uuid=course.course_uuid,
        review_id=review.id,
        snapshot_sha256=canonical_request_hash(snapshot),
        snapshot=snapshot,
        media_manifest={},
        assessment_policy_id=int(policies[0].id),
        progression_policy_id=int(policies[1].id),
        retention_policy_id=int(policies[2].id),
        rights_policy_id=int(policies[3].id),
        retention_category="synthetic",
        policy_binding=binding.model_dump(mode="json"),
    )
    db.add(release)
    await db.flush()
    grant = StudyCourseAccessGrant(
        org_id=org.id,
        learner_profile_id=profile.id,
        course_uuid=course.course_uuid,
        release_id=release.id,
        source_kind="free",
        source_uuid=f"aud01-{token}",
        start_basis="fixed_period",
        starts_at=now - timedelta(minutes=1),
        ends_at=now + timedelta(days=1),
        created_at=now - timedelta(minutes=2),
    )
    db.add(grant)
    await db.commit()
    assert review.decision == "approved"
    assert review.revision_sha256 == release.snapshot_sha256
    assert canonical_request_hash(await course_candidate_snapshot(db, course)) == release.snapshot_sha256
    active_grants = await resolve_active_course_grants(
        db,
        org_id=org.id,
        learner_profile_id=profile.id,
        course_uuid=course.course_uuid,
        release_id=release.id,
        now=now,
    )
    assert [row.id for row in active_grants] == [grant.id]

    assert course.public is False and course.published is False

    # AUD-03: a granted learner resolves only the exact reviewed frozen source.
    # The following policy and consent values are synthetic test data; they do
    # not represent owner approval or enable a real learner service.
    d11 = await _seed_approved_policy(
        db,
        org,
        staff.id,
        StudyPolicyKey.D_11,
        specified={
            "community_audiences": {"fixture": True},
            "adult_contact_boundaries": {"fixture": True},
            "moderation": {"fixture": True},
        },
    )
    safeguarding_owner = await _seed_active_owner(
        db,
        org,
        staff.id,
        staff.id,
        StudyOwnerFunction.SAFEGUARDING,
    )
    db.add(
        StudyPolicyApproval(
            org_id=org.id,
            policy_version_id=d11.id,
            responsibility=StudyOwnerFunction.SAFEGUARDING.value,
            owner_assignment_id=safeguarding_owner.id,
            approver_user_id=staff.id,
            evidence_reference="synthetic-aud03-policy",
            rationale="Synthetic managed-release test fixture",
            approved_at=now,
        )
    )
    await db.commit()

    source = await require_mapped_release_source(
        db,
        org,
        learner,
        profile,
        curriculum,
        concept_uuids,
        course.course_uuid,
        release.uuid,
        lesson.activity_uuid,
        now,
    )
    frozen_activity = next(
        row
        for row in release.snapshot["activities"]
        if row["activity_uuid"] == lesson.activity_uuid
    )
    assert source.activity_snapshot == frozen_activity
    assert source.activity_digest == next(
        row.activity_digest
        for row in (
            await db.execute(
                select(StudyConceptActivity).where(
                    StudyConceptActivity.curriculum_revision_id == curriculum.id,
                    StudyConceptActivity.activity_uuid == lesson.activity_uuid,
                    StudyConceptActivity.archived.is_(False),
                )
            )
        ).scalars()
    )
    assert source.authorization.release.id == release.id
    assert source.authorization.profile.id == profile.id
    assert [row.id for row in source.authorization.grants] == [grant.id]
    assert len(source.authorization.current_policy_version_ids) == 4
    assert (
        source.authorization.progression_policy.id
        in source.authorization.current_policy_version_ids
    )
    assert len(source.source_binding_digest) == 64
    from src.services.studytomy import access_policy, adaptive_attempt_access
    from src.services.studytomy.adaptive_progress import _managed_release_mappings

    gated_body = _synthetic_policy_body(StudyPolicyKey.D_09, {
        "release_schedule": {
            "ungated_text_activities": {course.course_uuid: [lesson.activity_uuid]},
            "assessed_pass_delivery_activities": {course.course_uuid: [lesson.activity_uuid]},
        },
        "completion_prerequisites": {"assessed_pass_by_course": {course.course_uuid: [{
            "kind": "assessed_pass", "target_activity_uuid": lesson.activity_uuid,
            "assessment_assignment_uuid": "assignment_missing_synthetic",
        }]}},
    })
    gated_authority = replace(
        source.authorization,
        progression_policy=source.authorization.progression_policy.model_copy(
            update={"body": gated_body.model_dump(mode="json")},
        ),
    )
    source_args = (
        db, org, learner, profile, curriculum, concept_uuids,
        course.course_uuid, release.uuid, lesson.activity_uuid, now,
    )
    with patch.object(adaptive_attempt_access, "resolve_managed_release_authority",
                      AsyncMock(return_value=gated_authority)):
        with pytest.raises(HTTPException) as blocked:
            await require_mapped_release_source(*source_args)
        assert blocked.value.status_code == 404
        with patch.object(access_policy, "managed_assessed_pass_status", AsyncMock(
            return_value="assessment_pass_delivery_pending",
        )) as verified:
            reopened = await require_mapped_release_source(*source_args)
        assert reopened.activity_snapshot == frozen_activity
        verified.assert_awaited_once_with(
            db, gated_body, release, learner.id, lesson.activity_uuid, lock_result=True,
        )

    managed_candidates = await _managed_release_mappings(
        db, org, learner, source, now
    )
    assert any(
        row[0].activity_uuid == next_lesson.activity_uuid
        for row in managed_candidates
    ), [row[0].activity_uuid for row in managed_candidates]
    managed_diagnostic_access = await require_diagnostic_access(
        make_synthetic_request(),
        db,
        org,
        learner,
        profile,
        managed_diagnostic.uuid,
        lesson.activity_uuid,
        now,
        validate_pool=True,
        course_release_uuid=release.uuid,
    )
    assert managed_diagnostic_access.release_source is not None
    assert (
        managed_diagnostic_access.release_source.source_binding_digest
        == source.source_binding_digest
    )

    # Re-consent only inside this synthetic fixture, then exercise the actual
    # private managed-release attempt, evidence, mastery, recommendation, and
    # tutor routes. No production approval or learner activation is implied.
    consent_now = datetime.now(UTC)
    now = consent_now
    consent_policy_rows = (
        await db.execute(
            select(StudyPolicyVersion.policy_key, StudyPolicyVersion.id).where(
                StudyPolicyVersion.org_id == org.id,
                StudyPolicyVersion.policy_key.in_(
                    (
                        StudyPolicyKey.D_01.value,
                        StudyPolicyKey.D_03.value,
                        StudyPolicyKey.D_05.value,
                        StudyPolicyKey.D_12.value,
                        StudyPolicyKey.D_18.value,
                    )
                ),
                StudyPolicyVersion.status == StudyPolicyStatus.APPROVED.value,
            )
        )
    ).all()
    consent_policy_by_key = {key: policy_id for key, policy_id in consent_policy_rows}
    consent_policy_ids = [
        consent_policy_by_key[key]
        for key in (
            StudyPolicyKey.D_01.value,
            StudyPolicyKey.D_03.value,
            StudyPolicyKey.D_12.value,
            StudyPolicyKey.D_05.value,
            StudyPolicyKey.D_18.value,
        )
    ]
    db.add(
        StudyConsentEvent(
            org_id=org.id,
            learner_profile_id=profile.id,
            actor_user_id=learner.id,
            subject_user_id=learner.id,
            purpose_code="learning_terms",
            choice="accept",
            notice_version="fixture-v1",
            scope_codes=[
                "course_release",
                "adaptive_diagnostics",
                "adaptive_practice",
                "adaptive_native_assignments",
                "adaptive_mastery",
                "adaptive_staff_mastery",
                "adaptive_progress_report",
                "adaptive_guidance",
            ],
            policy_version_ids=consent_policy_ids,
            effective_at=consent_now,
        )
    )
    await db.commit()

    learner_headers = _headers(
        learner, org, "password", f"aud04-managed-attempt-{uuid4()}"
    )
    managed_scope = {
        "course_uuid": course.course_uuid,
        "course_release_uuid": release.uuid,
    }
    attempt_options = await client.get(
        f"{base}/me/attempts/options",
        params={"source_activity_uuid": lesson.activity_uuid, **managed_scope},
        headers=learner_headers,
    )
    assert attempt_options.status_code == 200, attempt_options.text
    assert any(
        row["purpose"] == "diagnostic" and row["uuid"] == managed_diagnostic.uuid
        for row in attempt_options.json()
    ), attempt_options.json()
    # The reviewed text lesson and the mapped native practice assignment
    # are distinct sources. A release grant does not make them interchangeable.
    assert all(row["purpose"] != "practice" for row in attempt_options.json())
    practice_source = (
        await db.execute(
            select(StudyConceptActivity.activity_uuid, StudyConcept.uuid)
            .join(StudyConcept, StudyConcept.id == StudyConceptActivity.concept_id)
            .where(
                StudyConceptActivity.curriculum_revision_id == curriculum.id,
                StudyConceptActivity.usage == "practice",
                StudyConceptActivity.activity_uuid != practice_lesson.activity_uuid,
                StudyConceptActivity.archived.is_(False),
            )
        )
    ).one()
    for activity_uuid in (lesson.activity_uuid, practice_source.activity_uuid):
        unavailable_practice = await client.post(
            f"{base}/me/practice-attempts",
            json={
                "source_activity_uuid": activity_uuid,
                "concept_uuid": practice_source.uuid,
                "mode": "supported",
                **managed_scope,
            },
            headers=learner_headers | {"Idempotency-Key": f"managed-practice-denied-{uuid4()}"},
        )
        assert unavailable_practice.status_code == 404, unavailable_practice.text
        assert unavailable_practice.headers["Cache-Control"] == "private, no-store"
    assert await db.scalar(
        select(StudyLearningAttempt.id).where(
            StudyLearningAttempt.source_release_uuid == release.uuid,
            StudyLearningAttempt.kind == "practice",
        )
    ) is None
    wrong_course_options = await client.get(
        f"{base}/me/attempts/options",
        params={"source_activity_uuid": lesson.activity_uuid, **managed_scope, "course_uuid": f"course_{uuid4()}"},
        headers=learner_headers,
    )
    assert wrong_course_options.status_code == 200, wrong_course_options.text
    assert wrong_course_options.json() == []
    for partial_scope in (
        {"course_uuid": course.course_uuid},
        {"course_release_uuid": release.uuid},
    ):
        denied_options = await client.get(
            f"{base}/me/attempts/options",
            params={
                "source_activity_uuid": lesson.activity_uuid,
                **partial_scope,
            },
            headers=learner_headers,
        )
        assert denied_options.status_code == 404, denied_options.text
    denied_cross_lesson = await client.post(
        f"{base}/me/diagnostic-attempts",
        json={
            "diagnostic_revision_uuid": managed_diagnostic.uuid,
            "source_activity_uuid": unmapped_lesson.activity_uuid,
            "course_release_uuid": release.uuid,
        },
        headers=learner_headers | {"Idempotency-Key": f"aud04-unmapped-{uuid4()}"},
    )
    assert denied_cross_lesson.status_code == 404, denied_cross_lesson.text

    start_body = {
        "diagnostic_revision_uuid": managed_diagnostic.uuid,
        "source_activity_uuid": lesson.activity_uuid,
        "course_release_uuid": release.uuid,
    }
    start_key = f"aud04-managed-start-{uuid4()}"
    started = await client.post(
        f"{base}/me/diagnostic-attempts",
        json=start_body,
        headers=learner_headers | {"Idempotency-Key": start_key},
    )
    assert started.status_code == 201, started.text
    started_view = started.json()
    assert started_view["status"] == "active"
    assert started_view["course_release_uuid"] == release.uuid
    replay = await client.post(
        f"{base}/me/diagnostic-attempts",
        json=start_body,
        headers=learner_headers | {"Idempotency-Key": start_key},
    )
    assert replay.status_code == 201 and replay.json() == started_view
    attempt_row = (
        await db.execute(
            select(StudyLearningAttempt).where(
                StudyLearningAttempt.org_id == org.id,
                StudyLearningAttempt.uuid == started_view["uuid"],
            )
        )
    ).scalar_one()
    assert attempt_row.source_release_uuid == release.uuid
    assert attempt_row.source_binding_digest == source.source_binding_digest
    assert "source_binding_digest" not in started.text

    attempt_item = (
        await db.execute(
            select(StudyAttemptItem).where(
                StudyAttemptItem.org_id == org.id,
                StudyAttemptItem.uuid == started_view["current_item"]["uuid"],
            )
        )
    ).scalar_one()
    answer_item = await db.get(StudyItemRevision, attempt_item.item_revision_id)
    assert answer_item.kind == "number"
    learner_response = {"kind": "number", "value": "0"}
    answer_body = {
        "item_uuid": attempt_item.uuid,
        "expected_version": started_view["version"],
        "response": learner_response,
        "declared_external_help": False,
    }
    answer_key = f"aud04-managed-response-{uuid4()}"
    response = await client.post(
        f"{base}/me/attempts/{started_view['uuid']}/responses",
        json=answer_body,
        headers=learner_headers | {"Idempotency-Key": answer_key},
    )
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["status"] == "completed" and receipt["next_item"] is None
    assert (
        await client.post(
            f"{base}/me/attempts/{started_view['uuid']}/responses",
            json=answer_body,
            headers=learner_headers | {"Idempotency-Key": answer_key},
        )
    ).json() == receipt
    summary = await client.get(receipt["summary_ref"], headers=learner_headers)
    assert summary.status_code == 200, summary.text
    assert summary.json()["coverage_complete"] is True
    other_summary = await client.get(receipt["summary_ref"], headers=author_headers)
    assert other_summary.status_code == 404, other_summary.text
    assert other_summary.headers["Cache-Control"] == "private, no-store"
    evidence = (
        await db.execute(
            select(StudyLearningEvidence).where(
                StudyLearningEvidence.org_id == org.id,
                StudyLearningEvidence.logical_source_uuid == attempt_item.uuid,
            )
        )
    ).scalar_one()
    assert evidence.outcome == "incorrect" and evidence.eligible is True
    if db.get_bind().dialect.name == "postgresql":
        from src.services.studytomy.metrics import assessment_evidence_breakdown

        evidence_period = {
            "start": evidence.observed_at,
            "end": evidence.observed_at + timedelta(seconds=1),
            "curriculum_revision_id": evidence.curriculum_revision_id,
            "course_uuid": course.course_uuid,
        }
        assert await assessment_evidence_breakdown(
            db, org_id=org.id, release_uuid=release.uuid, **evidence_period,
        ) == [{
            "independence": "independent", "evidence_outcome": "incorrect", "observations": 1,
            "mastery_eligible_observations": 1,
        }]
        assert await assessment_evidence_breakdown(
            db, org_id=org.id, release_uuid=f"course_release_{uuid4()}", **evidence_period,
        ) == []
        assert await assessment_evidence_breakdown(
            db, org_id=org.id, release_uuid=release.uuid,
            **(evidence_period | {"course_uuid": f"course_{uuid4()}"}),
        ) == []

    progress = await client.get(
        f"{base}/me/lesson-mastery",
        params={"activity_uuid": lesson.activity_uuid, **managed_scope},
        headers=learner_headers,
    )
    assert progress.status_code == 200, progress.text
    assert {row["concept_uuid"] for row in progress.json()["concepts"]} == set(
        concept_uuids
    )
    recommendations = await client.get(
        f"{base}/me/recommendations",
        params={"activity_uuid": lesson.activity_uuid, **managed_scope},
        headers=learner_headers,
    )
    assert recommendations.status_code == 200, recommendations.text
    assert any(
        row.get("activity_uuid") == next_lesson.activity_uuid
        and row.get("course_release_uuid") == release.uuid
        for row in recommendations.json()
    ), recommendations.json()

    practice_body = {
        "source_activity_uuid": practice_lesson.activity_uuid,
        "concept_uuid": practice_source.uuid,
        "mode": "supported",
        **managed_scope,
    }
    managed_practice = await client.post(
        f"{base}/me/practice-attempts", json=practice_body,
        headers=learner_headers | {"Idempotency-Key": f"managed-practice-start-{uuid4()}"},
    )
    assert managed_practice.status_code == 201, managed_practice.text
    practice = managed_practice.json()
    assert practice["course_release_uuid"] == release.uuid
    assert "correct_value" not in managed_practice.text and "Private worked answer" not in managed_practice.text
    practice_answer = await client.post(
        f"{base}/me/attempts/{practice['uuid']}/responses",
        json={"item_uuid": practice["current_item"]["uuid"], "expected_version": practice["version"],
              "response": {"kind": "number", "value": "19.25"}, "declared_external_help": False},
        headers=learner_headers | {"Idempotency-Key": f"managed-practice-answer-{uuid4()}"},
    )
    assert practice_answer.status_code == 200, practice_answer.text
    assert practice_answer.json()["status"] == "completed"
    practice_evidence = (await db.execute(select(StudyLearningEvidence).where(
        StudyLearningEvidence.logical_source_uuid == practice["current_item"]["uuid"],
    ))).scalar_one()
    assert practice_evidence.outcome == "correct" and not practice_evidence.eligible
    assert practice_evidence.independence == "assisted"

    tutor_options = await client.get(
        f"{base}/me/tutor-options",
        params={"activity_uuid": lesson.activity_uuid, **managed_scope},
        headers=learner_headers,
    )
    assert tutor_options.status_code == 200, tutor_options.text
    assert tutor_options.json()["mode"] == "guided"
    for partial_scope in (
        {"course_uuid": course.course_uuid},
        {"course_release_uuid": release.uuid},
    ):
        denied_tutor_options = await client.get(
            f"{base}/me/tutor-options",
            params={"activity_uuid": lesson.activity_uuid, **partial_scope},
            headers=learner_headers,
        )
        assert denied_tutor_options.status_code == 404, denied_tutor_options.text
    tutor_body = {
        "activity_uuid": lesson.activity_uuid,
        **managed_scope,
    }
    foreign_course_uuid = (
        await db.execute(
            select(Course.course_uuid)
            .where(Course.org_id == other_org.id)
            .limit(1)
        )
    ).scalar_one()
    denied_tutor_cross_course = await client.post(
        f"{base}/me/tutor-sessions",
        json=tutor_body | {"course_uuid": foreign_course_uuid},
        headers=learner_headers,
    )
    assert denied_tutor_cross_course.status_code == 404, denied_tutor_cross_course.text
    denied_tutor_cross_lesson = await client.post(
        f"{base}/me/tutor-sessions",
        json=tutor_body | {"activity_uuid": unmapped_lesson.activity_uuid},
        headers=learner_headers,
    )
    assert denied_tutor_cross_lesson.status_code == 404, denied_tutor_cross_lesson.text
    tutor = await client.post(
        f"{base}/me/tutor-sessions", json=tutor_body, headers=learner_headers
    )
    assert tutor.status_code == 201, tutor.text
    tutor_row = (
        await db.execute(
            select(StudyTutorSession).where(
                StudyTutorSession.org_id == org.id,
                StudyTutorSession.uuid == tutor.json()["uuid"],
            )
        )
    ).scalar_one()
    assert tutor_row.source_release_uuid == release.uuid
    assert tutor_row.source_binding_digest == source.source_binding_digest
    stored_tutor, tutor_context = await load_guidance_context(
        Request({"type": "http", "headers": []}),
        db,
        org,
        learner,
        tutor.json()["uuid"],
    )
    assert stored_tutor.uuid == tutor_row.uuid
    assert tutor_context.lesson_title == frozen_activity["name"]
    assert "Synthetic released lesson." in tutor_context.lesson_prose

    with pytest.raises(HTTPException) as native_draft_denied:
        await require_mapped_source(
            request,
            db,
            org,
            learner,
            curriculum,
            concept_uuids,
            lesson.activity_uuid,
        )
    assert native_draft_denied.value.status_code == 404

    sibling = Course(
        org_id=org.id,
        course_uuid=f"course_{uuid4()}",
        name="Synthetic sibling course",
        public=False,
        published=False,
        open_to_contributors=False,
    )
    db.add(sibling)
    await db.flush()

    for unauthorized_org, unauthorized_course, unauthorized_release, unauthorized_actor, unauthorized_activity in (
        (other_org, course.course_uuid, release.uuid, learner, lesson.activity_uuid),
        (org, course.course_uuid, f"course_release_{uuid4()}", learner, lesson.activity_uuid),
        (org, sibling.course_uuid, release.uuid, learner, lesson.activity_uuid),
        (org, course.course_uuid, release.uuid, staff, lesson.activity_uuid),
        (org, course.course_uuid, release.uuid, learner, f"activity_{uuid4()}"),
    ):
        with pytest.raises(HTTPException) as denied_release:
            await require_mapped_release_source(
                db,
                unauthorized_org,
                unauthorized_actor,
                profile,
                curriculum,
                concept_uuids,
                unauthorized_course,
                unauthorized_release,
                unauthorized_activity,
                now,
            )
        assert denied_release.value.status_code == 404

    first_use = StudyCourseAccessGrant(
        org_id=org.id,
        learner_profile_id=profile.id,
        course_uuid=course.course_uuid,
        release_id=release.id,
        source_kind="free",
        source_uuid=f"aud03-first-use-{token}",
        start_basis="after_first_access",
        duration_days=2,
        created_at=now - timedelta(minutes=2),
    )
    db.add(first_use)
    await db.flush()
    with pytest.raises(HTTPException) as unsupported_before_first_use:
        await require_mapped_release_source(
            db,
            org,
            learner,
            profile,
            curriculum,
            concept_uuids,
            course.course_uuid,
            release.uuid,
            f"activity_{uuid4()}",
            now,
            claim_first_use=True,
        )
    assert unsupported_before_first_use.value.status_code == 404
    assert first_use.starts_at is None and first_use.ends_at is None
    claimed = await require_mapped_release_source(
        db,
        org,
        learner,
        profile,
        curriculum,
        concept_uuids,
        course.course_uuid,
        release.uuid,
        lesson.activity_uuid,
        now,
        claim_first_use=True,
    )
    assert first_use.starts_at == now
    assert first_use.ends_at == now + timedelta(days=2)
    assert {row.id for row in claimed.authorization.grants} == {
        grant.id,
        first_use.id,
    }
    first_use.revoked_at = now
    await db.flush()

    expired_grant = StudyCourseAccessGrant(
        org_id=org.id,
        learner_profile_id=profile.id,
        course_uuid=course.course_uuid,
        release_id=release.id,
        source_kind="free",
        source_uuid=f"aud03-expired-{token}",
        start_basis="fixed_period",
        starts_at=now - timedelta(days=2),
        ends_at=now - timedelta(seconds=1),
        created_at=now - timedelta(days=3),
    )
    db.add(expired_grant)
    await db.flush()
    with_expired = await require_mapped_release_source(
        db,
        org,
        learner,
        profile,
        curriculum,
        concept_uuids,
        course.course_uuid,
        release.uuid,
        lesson.activity_uuid,
        now,
    )
    assert {row.id for row in with_expired.authorization.grants} == {grant.id}

    overlapping = StudyCourseAccessGrant(
        org_id=org.id,
        learner_profile_id=profile.id,
        course_uuid=course.course_uuid,
        release_id=release.id,
        source_kind="free",
        source_uuid=f"aud03-overlap-{token}",
        start_basis="fixed_period",
        starts_at=now - timedelta(minutes=1),
        ends_at=now + timedelta(days=1),
        created_at=now - timedelta(minutes=2),
    )
    db.add(overlapping)
    await db.flush()
    both = await require_mapped_release_source(
        db,
        org,
        learner,
        profile,
        curriculum,
        concept_uuids,
        course.course_uuid,
        release.uuid,
        lesson.activity_uuid,
        now,
    )
    assert {row.id for row in both.authorization.grants} == {grant.id, overlapping.id}
    grant.revoked_at = now
    await db.flush()
    remaining = await require_mapped_release_source(
        db,
        org,
        learner,
        profile,
        curriculum,
        concept_uuids,
        course.course_uuid,
        release.uuid,
        lesson.activity_uuid,
        now,
    )
    assert [row.id for row in remaining.authorization.grants] == [overlapping.id]
    overlapping.revoked_at = now
    await db.flush()
    with pytest.raises(HTTPException) as revoked:
        await require_mapped_release_source(
            db,
            org,
            learner,
            profile,
            curriculum,
            concept_uuids,
            course.course_uuid,
            release.uuid,
            lesson.activity_uuid,
            now,
        )
    assert revoked.value.status_code == 404
    await db.commit()

    for field in ("public", "published"):
        with pytest.raises(IntegrityError, match="ck_course_studytomy_managed_private"):
            async with db.begin_nested():
                await db.execute(
                    update(Course)
                    .where(Course.id == course.id)
                    .values(**{field: True})
                )

    with pytest.raises(HTTPException) as denied:
        await require_diagnostic_access(
            request,
            db,
            org,
            learner,
            profile,
            diagnostic.uuid,
            lesson.activity_uuid,
            now,
            validate_pool=False,
        )
    assert denied.value.status_code == 404
    assert denied.value.detail == "Learning source not found."


async def _exercise_native_items(
    client, db, base, author, learner, course, concept_uuid
):
    chapter = Chapter(org_id=course.org_id, course_id=course.id, name="Synthetic")
    activity = Activity(
        org_id=course.org_id,
        course_id=course.id,
        activity_uuid=f"activity_{uuid4()}",
        name="Synthetic assignment",
        published=True,
        activity_type=ActivityTypeEnum.TYPE_ASSIGNMENT,
        activity_sub_type=ActivitySubTypeEnum.SUBTYPE_ASSIGNMENT_ANY,
    )
    db.add_all([chapter, activity])
    await db.flush()
    parents = {
        "org_id": course.org_id,
        "course_id": course.id,
        "chapter_id": chapter.id,
        "activity_id": activity.id,
    }
    db.add(ChapterActivity(order=0, creation_date="", update_date="", **parents))
    assignment = Assignment(
        title="Synthetic assignment",
        description="",
        grading_type=GradingTypeEnum.NUMERIC,
        assignment_uuid=f"assignment_{uuid4()}",
        show_correct_answers=True,
        allow_retries=True,
        max_retries=2,
        **parents,
    )
    db.add(assignment)
    await db.flush()
    task = AssignmentTask(
        title="Synthetic numeric task",
        description="",
        hint="",
        creation_date="",
        update_date="",
        assignment_task_uuid=f"task_{uuid4()}",
        assignment_id=assignment.id,
        **parents,
        assignment_type=AssignmentTaskTypeEnum.NUMBER_ANSWER,
        contents={
            "prompt": "Enter a synthetic measurement",
            "correct_value": "17.25",
            "tolerance": "0.01",
            "explanation": "Private worked answer",
        },
    )
    db.add(task)
    await db.commit()

    source_path = f"{base}/item-sources/{task.assignment_task_uuid}"
    items_path = f"{base}/item-revisions"
    assert (await client.get(source_path, headers=learner)).status_code == 403
    preview = await client.get(source_path, headers=author)
    assert preview.status_code == 200, preview.text
    snapshot = preview.json()
    assert snapshot["source_exposure"] == "possible_native_reveal"
    assert snapshot["question"] == {
        "kind": "number",
        "prompt": "Enter a synthetic measurement",
        "unit": "",
    }
    assert "17.25" not in preview.text and "Private worked answer" not in preview.text
    request = {
        "concept_uuid": concept_uuid,
        "source_task_uuid": task.assignment_task_uuid,
        "expected_source_digest": snapshot["source_digest"],
        "difficulty": 2,
        "hint_ladder": ["Check the units.", "Compare the two measurements."],
    }
    assert (
        await client.post(items_path, json=request, headers=learner)
    ).status_code == 403
    response = await client.post(items_path, json=request, headers=author)
    assert response.status_code == 201, response.text
    original = response.json()
    assert original["independent_diagnostic_eligible"] is False
    assert "private_answer" not in original
    assert (
        await client.post(items_path, json=request, headers=author)
    ).json() == original
    edit = await client.put(
        f"{items_path}/{original['uuid']}",
        json={
            "expected_version": 1,
            "difficulty": 3,
            "error_codes": {"incorrect": "review_required"},
        },
        headers=author,
    )
    assert edit.status_code == 200, edit.text
    assert edit.json()["version"] == 2
    task.contents = task.contents | {"correct_value": "19.25"}
    await db.commit()
    next_preview = await client.get(source_path, headers=author)
    assert next_preview.json()["source_digest"] != snapshot["source_digest"]
    second_key = author | {"Idempotency-Key": "second-native-item-revision"}
    newer = await client.post(
        items_path,
        json=request
        | {
            "expected_source_digest": next_preview.json()["source_digest"],
            "revision": 2,
        },
        headers=second_key,
    )
    assert newer.status_code == 201, newer.text
    assert newer.json()["item_family_uuid"] == original["item_family_uuid"]
    rows = (
        (
            await db.execute(
                select(StudyItemRevision)
                .where(
                    StudyItemRevision.org_id == course.org_id,
                    StudyItemRevision.item_family_uuid == original["item_family_uuid"],
                )
                .order_by(StudyItemRevision.revision)
            )
        )
        .scalars()
        .all()
    )
    assert [row.private_answer["expected"] for row in rows] == ["17.25", "19.25"]
    if db.get_bind().dialect.name == "postgresql":
        item_id = rows[0].id
        with pytest.raises(
            IntegrityError, match="Invalid assessment item revision change"
        ):
            async with db.begin_nested():
                await db.execute(
                    update(StudyItemRevision)
                    .where(StudyItemRevision.id == item_id)
                    .values(
                        private_answer={"expected": "100", "tolerance": "0"},
                        version=3,
                    )
                )
    return newer.json(), task, activity


async def _exercise_original_items(
    client, db, base, author, learner, concept_uuid, native_family
):
    path = f"{base}/item-revisions"
    payload = {
        "concept_uuid": concept_uuid,
        "difficulty": 1,
        "original_rights_reference": "synthetic-original-rights",
        "original_question": {
            "kind": "number",
            "prompt": "Private original measurement.",
        },
        "original_answer": {"kind": "number", "expected": "37.42", "tolerance": "0.01"},
    }
    assert (await client.post(path, json=payload, headers=learner)).status_code == 403
    savepoint = await db.begin_nested()
    wrong_family = await client.post(
        path,
        json=payload
        | {
            "item_family_uuid": native_family,
            "revision": 3,
        },
        headers=author | {"Idempotency-Key": "reject-mixed-item-family"},
    )
    assert wrong_family.status_code == 409
    assert wrong_family.json()["detail"]["code"] == "ITEM_FAMILY_CONFLICT"
    await savepoint.rollback()

    denied_key = await client.post(
        path,
        json=payload
        | {
            "original_answer": {
                "kind": "number",
                "expected": "37.42",
                "tolerance": "-0.01",
            },
        },
        headers=author,
    )
    assert denied_key.status_code == 422
    items = []
    for index in (1, 2):
        response = await client.post(
            path,
            json=payload
            | {
                "original_question": {
                    "kind": "number",
                    "prompt": f"Private original measurement {index}.",
                },
            },
            headers=author | {"Idempotency-Key": f"private-original-{index}"},
        )
        assert response.status_code == 201, response.text
        assert "37.42" not in response.text and "private_answer" not in response.text
        row = response.json()
        assert (
            row["source_task_uuid"] is None
            and row["source_exposure"] == "private_original"
        )
        assert row["independent_diagnostic_eligible"] is False
        preview = await client.get(f"{path}/{row['uuid']}/review", headers=author)
        assert preview.status_code == 200 and "37.42" not in preview.text
        items.append(preview.json())
    return items


async def _exercise_publication(
    client,
    db,
    org,
    other_org,
    base,
    detail_path,
    author_headers,
    reviewer_headers,
    revision_uuid,
    lesson,
    next_lesson,
    practice_lesson,
    education_owner,
    native_item,
    native_task,
    native_activity,
    originals,
    learner,
    concurrent_posts,
):
    preview = await client.get(f"{detail_path}/review", headers=author_headers)
    assert preview.status_code == 200, preview.text
    snapshot = preview.json()
    assert {mapping["activity_uuid"] for mapping in snapshot["mappings"]} == {
        lesson.activity_uuid,
        next_lesson.activity_uuid,
        practice_lesson.activity_uuid,
        native_activity.activity_uuid,
    }
    owning_course = snapshot["curriculum"]["owner_course_uuid"]
    catalogue_path = f"{base}/courses/{owning_course}/reviewed-curricula"
    assert (await client.get(catalogue_path, headers=author_headers)).json() == {
        "revisions": [], "next_cursor": None,
    }
    endpoint = f"{base}/definitions/curriculum/{revision_uuid}"
    publication = {
        "expected_version": snapshot["curriculum"]["version"],
        "expected_digest": snapshot["content_digest"],
        "review_evidence": "synthetic-curriculum-review-evidence",
    }
    item_endpoint = f"{base}/definitions/item/{native_item['uuid']}"
    item_preview = await client.get(
        f"{base}/item-revisions/{native_item['uuid']}/review",
        headers=author_headers,
    )
    assert item_preview.status_code == 200, item_preview.text
    assert (
        "19.25" not in item_preview.text and "private_answer" not in item_preview.text
    )
    item_snapshot = item_preview.json()
    grading_preview = await client.get(
        f"{base}/item-revisions/{native_item['uuid']}/grading-review",
        headers=reviewer_headers,
    )
    assert grading_preview.status_code == 409
    assert grading_preview.json()["detail"]["code"] == "POLICY_NOT_APPROVED"
    item_review = {
        "expected_version": native_item["version"],
        "expected_digest": item_snapshot["content_digest"],
        "review_evidence": "synthetic-item-review",
    }
    assert (
        await client.post(
            f"{endpoint}/publish", json=publication, headers=author_headers
        )
    ).status_code == 403
    assert (
        await client.post(
            f"{endpoint}/publish", json=publication, headers=reviewer_headers
        )
    ).status_code == 409

    mapping = (
        await db.execute(
            select(StudyConceptActivity).where(
                StudyConceptActivity.org_id == org.id,
                StudyConceptActivity.activity_uuid == native_activity.activity_uuid,
                StudyConceptActivity.usage == "practice",
            )
        )
    ).scalar_one()
    assignment = await db.get(Assignment, native_task.assignment_id)
    fixture_values = {
        StudyPolicyKey.D_10: {
            "learner_eligibility": "synthetic",
            "supervision": "synthetic",
            "materials": "synthetic",
            "usage_limits": {"adaptive_guidance": {"max_turns_per_day": 10}},
            "review": "synthetic",
        },
        StudyPolicyKey.D_08: {
            "assistance": {
                "adaptive_guidance": {"enabled": True},
                "adaptive_diagnostic": {
                    "allow_help": False,
                    "reveal_modes": ["after_close"],
                },
                "adaptive_practice": {
                    "mode": "supported",
                    "expires_after_minutes": 60,
                    "mastery_policy_uuid": "synthetic-mastery-v1",
                },
            },
            "attempts": {
                "adaptive_native_assignments": [
                    {
                        "assignment_uuid": assignment.assignment_uuid,
                        "mapping_uuid": mapping.uuid,
                        "item_uuid": native_item["uuid"],
                        "item_digest": item_review["expected_digest"],
                        "assistance": "supported",
                        "mastery_policy_uuid": "synthetic-mastery-v1",
                    }
                ],
                "adaptive_diagnostic": {
                    "max_attempts": 2,
                    "minimum_retake_wait_minutes": 1440,
                },
            },
        },
        StudyPolicyKey.D_09: {
            "release_schedule": {
                "ungated_text_activities": {
                    owning_course: [lesson.activity_uuid, next_lesson.activity_uuid, practice_lesson.activity_uuid],
                },
            },
            "mastery_gate": {
                "adaptive_curriculum_policies": {revision_uuid: "synthetic-mastery-v1"},
                "adaptive_recommendation_support": "return_to_lesson",
                "adaptive_mastery_policies": [
                    {
                        "policy_uuid": "synthetic-mastery-v1",
                        "reducer_version": "adaptive_mastery_v1",
                        "window_size": 8,
                        "minimum_families": 5,
                        "success_threshold_percent": 80,
                        "minimum_sessions": 2,
                        "separation_minutes": 1440,
                        "review_after_days": 30,
                    }
                ],
            },
        },
        StudyPolicyKey.D_02: {
            "subjects": ["Fractions"],
            "learner_levels": ["Test"],
            "languages": ["en"],
            "curriculum_claims": {"adaptive_curriculum_keys": ["synthetic"]},
        },
        StudyPolicyKey.D_16: {
            "content_rights": {
                "adaptive_curriculum_rights": [snapshot["rights_reference"]],
                "adaptive_original_item_rights": ["synthetic-original-rights"],
            },
            "review_approvals": {
                "adaptive_curriculum_reviews": [
                    {
                        "revision_uuid": revision_uuid,
                        "digest": snapshot["content_digest"],
                        "rights_reference": snapshot["rights_reference"],
                        "evidence_reference": publication["review_evidence"],
                    }
                ],
                "adaptive_item_reviews": [
                    {
                        "revision_uuid": native_item["uuid"],
                        "digest": item_review["expected_digest"],
                        "rights_reference": item_snapshot["rights_reference"],
                        "evidence_reference": item_review["review_evidence"],
                        "allowed_use": "practice_only",
                    }
                ]
                + [
                    {
                        "revision_uuid": item["item"]["uuid"],
                        "digest": item["content_digest"],
                        "rights_reference": item["rights_reference"],
                        "evidence_reference": f"synthetic-original-review-{index}",
                        "allowed_use": "independent_diagnostic",
                        **(
                            {"exposure_evidence_reference": "synthetic-exposure-review"}
                            if index == 1
                            else {}
                        ),
                    }
                    for index, item in enumerate(originals, start=1)
                ],
            },
        },
    }
    owners = {StudyOwnerFunction.EDUCATION: education_owner}
    for key in (
        StudyPolicyKey.D_02,
        StudyPolicyKey.D_08,
        StudyPolicyKey.D_09,
        StudyPolicyKey.D_10,
        StudyPolicyKey.D_15,
        StudyPolicyKey.D_16,
    ):
        owner_id = education_owner.user_id
        policy = await _seed_approved_policy(
            db, org, owner_id, key, specified=fixture_values.get(key)
        )
        for responsibility in POLICY_REQUIRED_RESPONSIBILITIES[key]:
            if responsibility not in owners:
                owners[responsibility] = await _seed_active_owner(
                    db, org, owner_id, owner_id, responsibility
                )
            db.add(
                StudyPolicyApproval(
                    org_id=org.id,
                    policy_version_id=policy.id,
                    responsibility=responsibility.value,
                    owner_assignment_id=owners[responsibility].id,
                    approver_user_id=owner_id,
                    evidence_reference="synthetic-policy-evidence",
                    rationale="Synthetic policy fixture",
                    approved_at=datetime.now(UTC),
                )
            )
        await db.commit()

    grading_preview = await client.get(
        f"{base}/item-revisions/{native_item['uuid']}/grading-review",
        headers=reviewer_headers,
    )
    assert grading_preview.status_code == 200, grading_preview.text
    assert grading_preview.json()["content_digest"] == item_snapshot["content_digest"]
    assert grading_preview.json()["item_version"] == native_item["version"]
    assert "19.25" in grading_preview.text
    unauthorized_grading_preview = await client.get(
        f"{base}/item-revisions/{native_item['uuid']}/grading-review",
        headers=author_headers,
    )
    assert unauthorized_grading_preview.status_code == 403
    assert unauthorized_grading_preview.json()["detail"]["code"] == "CAPABILITY_DENIED"

    course_uuid = snapshot["curriculum"]["owner_course_uuid"]
    catalog = await client.get(
        f"{base}/courses/{course_uuid}/configuration", headers=reviewer_headers
    )
    assert catalog.status_code == 200, catalog.text
    reader = catalog.json()
    assert reader["can_review"] and not reader["can_author"]
    assert reader["activities"] == []
    assert {item["uuid"] for item in reader["items"]} >= {native_item["uuid"]}
    assert "private_answer" not in catalog.text and "19.25" not in catalog.text
    reviewer_preview = await client.get(
        f"{detail_path}/review", headers=reviewer_headers
    )
    assert reviewer_preview.json() == snapshot
    for changes, code in (
        ({"expected_digest": "f" * 64}, "STALE_VERSION"),
        ({"review_evidence": "different-evidence"}, "POLICY_NOT_APPROVED"),
    ):
        savepoint = await db.begin_nested()
        blocked = await client.post(
            f"{endpoint}/publish", json=publication | changes, headers=reviewer_headers
        )
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["detail"]["code"] == code
        await savepoint.rollback()

    savepoint = await db.begin_nested()
    lesson.name = "Edited lesson content"
    await db.flush()
    stale_source = await client.post(
        f"{endpoint}/publish", json=publication, headers=reviewer_headers
    )
    assert stale_source.status_code == 409
    assert stale_source.json()["detail"]["code"] == "STALE_VERSION"
    await savepoint.rollback()
    await db.refresh(lesson)

    published = await client.post(
        f"{endpoint}/publish", json=publication, headers=reviewer_headers
    )
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"
    assert published.json()["content_digest"] == snapshot["content_digest"]
    objectives_path = f"{base}/curriculum-revisions/{revision_uuid}/objectives"
    objectives = await client.get(objectives_path, headers=author_headers)
    assert objectives.status_code == 200, objectives.text
    assert objectives.json() == {
        "curriculum": published.json(),
        "concepts": snapshot["concepts"],
        "prerequisites": snapshot["prerequisites"],
    }
    catalogue = await client.get(catalogue_path, headers=author_headers)
    assert catalogue.status_code == 200
    assert catalogue.json() == {"revisions": [published.json()], "next_cursor": None}
    assert (await client.get(catalogue_path, headers=author_headers, params={"after": revision_uuid})).json() == {
        "revisions": [], "next_cursor": None,
    }
    assert (
        await client.post(
            f"{endpoint}/publish", json=publication, headers=reviewer_headers
        )
    ).json() == published.json()
    assert (
        await client.post(
            f"{item_endpoint}/publish",
            json=item_review,
            headers=author_headers,
        )
    ).status_code == 403
    for changes, code in (
        ({"expected_digest": "f" * 64}, "STALE_VERSION"),
        ({"review_evidence": "incorrect-evidence"}, "POLICY_NOT_APPROVED"),
    ):
        savepoint = await db.begin_nested()
        denied = await client.post(
            f"{item_endpoint}/publish",
            json=item_review | changes,
            headers=reviewer_headers,
        )
        assert denied.status_code == 409 and denied.json()["detail"]["code"] == code, (
            denied.text
        )
        await savepoint.rollback()
    savepoint = await db.begin_nested()
    native_task.contents = native_task.contents | {"correct_value": "90"}
    await db.flush()
    stale_item = await client.post(
        f"{item_endpoint}/publish", json=item_review, headers=reviewer_headers
    )
    assert (
        stale_item.status_code == 409
        and stale_item.json()["detail"]["code"] == "STALE_VERSION"
    )
    await savepoint.rollback()
    item = await client.post(
        f"{item_endpoint}/publish", json=item_review, headers=reviewer_headers
    )
    assert item.status_code == 200, item.text
    assert (
        item.json()["status"] == "published"
        and item.json()["allowed_use"] == "practice_only"
    )
    assert item.json()["independent_diagnostic_eligible"] is False
    captured = await resolve_native_assignment_snapshot(
        db, org, native_task, datetime.now(UTC)
    )
    assert captured is not None and captured.mapping_id == mapping.id
    assert captured.assistance == "supported" and len(captured.manifest_digest) == 64
    async with db.begin_nested() as savepoint:
        updated = native_task.contents | {"correct_value": "999.00"}
        await db.execute(
            update(AssignmentTask)
            .where(AssignmentTask.id == native_task.id)
            .values(contents=updated)
        )
        assert (
            await resolve_native_assignment_snapshot(
                db, org, native_task, datetime.now(UTC)
            )
            is None
        )
        await savepoint.rollback()
    await db.refresh(native_task)
    assert (
        await resolve_native_assignment_snapshot(
            db,
            org,
            native_task,
            datetime.now(UTC),
            question_uuid="nonexistent-question",
        )
        is None
    )
    submission_uuid = f"assignmentusersubmission_{uuid4()}"
    first_round = native_round_uuid(org.id, submission_uuid, 1)
    assert first_round == native_round_uuid(org.id, submission_uuid, 1)
    assert first_round != native_round_uuid(org.id, submission_uuid, 2)
    assert first_round != native_round_uuid(
        org.id, f"assignmentusersubmission_{uuid4()}", 1
    )
    assert (
        await client.post(
            f"{item_endpoint}/publish", json=item_review, headers=reviewer_headers
        )
    ).json() == item.json()
    for index, original in enumerate(originals, start=1):
        candidate = original["item"]
        original_path = f"{base}/definitions/item/{candidate['uuid']}/publish"
        savepoint = await db.begin_nested() if index == 2 else None
        decision = await client.post(
            original_path,
            json={
                "expected_version": candidate["version"],
                "expected_digest": original["content_digest"],
                "review_evidence": f"synthetic-original-review-{index}",
            },
            headers=reviewer_headers
            | {"Idempotency-Key": f"review-private-original-{index}"},
        )
        if savepoint is not None:
            assert decision.status_code == 409
            assert decision.json()["detail"]["code"] == "POLICY_NOT_APPROVED"
            await savepoint.rollback()
        else:
            assert decision.status_code == 200, decision.text
            assert decision.json()["allowed_use"] == "independent_diagnostic"
            assert decision.json()["independent_diagnostic_eligible"] is True
            assert "37.42" not in decision.text
    reconciliation_baseline = await _exercise_blueprint(
        client,
        db,
        org,
        base,
        revision_uuid,
        originals[0]["item"],
        native_item,
        author_headers,
        reviewer_headers,
        education_owner,
        fixture_values[StudyPolicyKey.D_16],
        lesson.activity_uuid,
        learner,
        concurrent_posts,
    )
    await _exercise_practice(
        client,
        db,
        org,
        base,
        reviewer_headers,
        author_headers,
        native_activity.activity_uuid,
        originals[0]["item"]["concept_uuid"],
        lesson.activity_uuid,
        native_task,
        concurrent_posts,
    )
    await _exercise_forward_only_mastery_reconciliation(
        client,
        db,
        org,
        base,
        education_owner,
        author_headers,
        reconciliation_baseline,
    )
    course = (
        await db.execute(
            select(Course).where(
                Course.org_id == org.id,
                Course.course_uuid == owning_course,
            )
        )
    ).scalar_one()
    staff = await db.get(User, education_owner.user_id)
    await _characterize_managed_course_diagnostic_conflict(
        client,
        db,
        org,
        other_org,
        course,
        lesson,
        next_lesson,
        practice_lesson,
        revision_uuid,
        staff,
        learner,
        base,
        author_headers,
        reviewer_headers,
    )
    retired_item = await client.post(
        f"{item_endpoint}/retire",
        json={"expected_version": item.json()["version"], "reason": "SUPERSEDED"},
        headers=reviewer_headers,
    )
    assert retired_item.status_code == 200, retired_item.text
    assert retired_item.json()["status"] == "retired"
    assert retired_item.json()["content_digest"] == item.json()["content_digest"]
    history_query = (
        select(StudyLearningEvidence.uuid)
        .join(
            StudyAttemptItem,
            StudyAttemptItem.id == StudyLearningEvidence.attempt_item_id,
        )
        .join(
            StudyLearningAttempt,
            StudyLearningAttempt.id == StudyAttemptItem.attempt_id,
        )
        .where(
            StudyLearningEvidence.org_id == org.id,
            StudyLearningAttempt.source_assignment_uuid == assignment.assignment_uuid,
        )
    )
    retained_before = set((await db.execute(history_query)).scalars())
    assert retained_before
    deleted = await client.delete(
        f"/api/v1/assignments/{assignment.assignment_uuid}/tasks/{native_task.assignment_task_uuid}",
        headers=author_headers,
    )
    assert deleted.status_code == 200, deleted.text
    assert set((await db.execute(history_query)).scalars()) == retained_before
    retirement = await client.post(
        f"{endpoint}/retire",
        json={"expected_version": published.json()["version"], "reason": "SUPERSEDED"},
        headers=reviewer_headers,
    )
    assert retirement.status_code == 200, retirement.text
    assert retirement.json()["status"] == "retired"
    assert retirement.json()["content_digest"] == published.json()["content_digest"]
    historical = await client.get(objectives_path, headers=author_headers)
    assert historical.status_code == 200, historical.text
    assert historical.json() == {**objectives.json(), "curriculum": retirement.json()}
    assert (await client.get(catalogue_path, headers=reviewer_headers)).json() == {
        "revisions": [retirement.json()], "next_cursor": None,
    }
    audit = (
        (
            await db.execute(
                select(StudyBusinessEvent.action)
                .where(
                    StudyBusinessEvent.resource_uuid == revision_uuid,
                )
                .order_by(StudyBusinessEvent.id)
            )
        )
        .scalars()
        .all()
    )
    assert audit == [
        "studytomy.adaptive.curriculum.created",
        "studytomy.adaptive.curriculum.published",
        "studytomy.adaptive.curriculum.retired",
    ]


async def _exercise_practice(
    client,
    db,
    org,
    base,
    learner,
    unrelated,
    activity_uuid,
    concept_uuid,
    unmapped_uuid,
    native_task,
    concurrent_posts,
):
    native_assignment = await db.get(Assignment, native_task.assignment_id)
    tasks_path = f"/api/v1/assignments/{native_assignment.assignment_uuid}/tasks"
    preview = await client.get(tasks_path, headers=unrelated)
    assert preview.status_code == 200, preview.text
    rounds = select(StudyLearningAttempt).where(
        StudyLearningAttempt.org_id == org.id,
        StudyLearningAttempt.source_assignment_uuid
        == native_assignment.assignment_uuid,
    )
    assert (await db.execute(rounds)).scalar_one_or_none() is None
    exposed = await client.get(tasks_path, headers=learner)
    assert exposed.status_code == 200, exposed.text
    for secret in ("correct_value", "19.25"):
        assert secret not in exposed.text
    round_row = (await db.execute(rounds)).scalar_one()
    issued = (
        await db.execute(
            select(StudyAttemptItem).where(
                StudyAttemptItem.attempt_id == round_row.id,
            )
        )
    ).scalar_one()
    assert round_row.actor_capacity == "learner" and issued.response_state == "issued"
    native = (
        await db.execute(
            select(AssignmentUserSubmission).where(
                AssignmentUserSubmission.assignmentusersubmission_uuid
                == round_row.source_submission_uuid
            )
        )
    ).scalar_one()
    assert native.submission_status.value == "PENDING" and native.attempt_number == 1
    assert (await client.get(tasks_path, headers=learner)).json() == exposed.json()
    assert (await db.execute(rounds)).scalar_one().id == round_row.id
    answer_path = f"{tasks_path}/{native_task.assignment_task_uuid}/submissions"
    answer = {"task_submission": {"answer": "19.25"}}
    forged = await client.put(answer_path, json=answer | {"grade": 99}, headers=learner)
    assert forged.status_code == 403, forged.text
    answer_query = select(AssignmentTaskSubmission).where(
        AssignmentTaskSubmission.assignment_task_id == native_task.id,
        AssignmentTaskSubmission.user_id == native.user_id,
    )
    assert (await db.execute(answer_query)).scalar_one_or_none() is None
    saved = await client.put(answer_path, json=answer, headers=learner)
    assert saved.status_code == 200, saved.text
    saved_native = (await db.execute(answer_query)).scalar_one()
    assert saved_native.task_submission == answer["task_submission"]
    assert saved_native.grade == 0 and not saved_native.manually_graded
    source = f"/api/v1/assignments/{native_assignment.assignment_uuid}"
    protected_paths = (
        f"{source}/tasks/{native_task.assignment_task_uuid}",
        source,
        f"/api/v1/activities/{activity_uuid}",
        f"/api/v1/chapters/{native_task.chapter_id}",
        f"/api/v1/courses/{round_row.source_course_uuid}",
    )
    for protected_path in protected_paths:
        blocked = await client.delete(protected_path, headers=unrelated)
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["detail"]["code"] == "ADAPTIVE_SOURCE_IN_USE"
    assert (
        await db.get(AssignmentTaskSubmission, saved_native.id)
    ).task_submission == {"answer": "19.25"}
    async with db.begin_nested() as savepoint:
        native.submission_status = AssignmentUserSubmissionStatus.GRADED
        await db.flush()
        blocked_retry = await client.post(
            f"/api/v1/assignments/{native_assignment.assignment_uuid}/submissions/me/retry",
            headers=learner,
        )
        assert blocked_retry.status_code == 409
        assert blocked_retry.json()["detail"]["code"] == "ADAPTIVE_ROUND_NOT_FROZEN"
        assert (
            await db.get(AssignmentTaskSubmission, saved_native.id)
        ).task_submission == {"answer": "19.25"}
        await savepoint.rollback()
    await db.refresh(native)
    hand_in = await client.post(
        f"/api/v1/assignments/{native_assignment.assignment_uuid}/submissions",
        headers=learner,
    )
    assert hand_in.status_code == 200, hand_in.text
    await db.refresh(round_row)
    await db.refresh(issued)
    assert round_row.status == "completed"
    assert issued.response_state == "answered" and issued.normalized_response == "19.25"
    assert issued.exposure_count == 1
    grade_path = f"/api/v1/assignments/{native_assignment.assignment_uuid}/submissions/{native.user_id}/grade"
    evidence_rows = (
        select(StudyLearningEvidence)
        .where(
            StudyLearningEvidence.org_id == org.id,
            StudyLearningEvidence.logical_source_uuid == issued.uuid,
        )
        .order_by(StudyLearningEvidence.result_revision)
    )

    async def fail_after_journaling(*args, **kwargs):
        await journal_native_assignment_grade(*args, **kwargs)
        raise RuntimeError("synthetic journal failure")

    async with db.begin_nested() as savepoint:
        with (
            patch(
                "src.services.studytomy.assignment_adapter.journal_native_assignment_grade",
                side_effect=fail_after_journaling,
            ),
            pytest.raises(RuntimeError, match="synthetic journal failure"),
        ):
            await client.post(grade_path, headers=unrelated)
        await savepoint.rollback()
    await db.refresh(native)
    assert native.submission_status.value == "SUBMITTED"
    assert (await db.execute(evidence_rows)).scalar_one_or_none() is None

    projection_query = (
        select(StudyConceptMastery)
        .where(
            StudyConceptMastery.org_id == org.id,
            StudyConceptMastery.learner_profile_id == round_row.learner_profile_id,
            StudyConceptMastery.concept_id == issued.concept_id,
        )
        .execution_options(populate_existing=True)
    )
    initial_projection = (await db.execute(projection_query)).scalar_one()
    independent_before = initial_projection.eligible_count
    unknown_before = initial_projection.unknown_count
    graded = await client.post(grade_path, headers=unrelated)
    assert graded.status_code == 200, graded.text
    evidence = (await db.execute(evidence_rows)).scalar_one()
    assert (
        evidence.outcome == "correct"
        and evidence.independence == "unknown"
        and not evidence.eligible
    )
    projection = (await db.execute(projection_query)).scalar_one()
    assert projection.eligible_count == independent_before
    assert projection.unknown_count == unknown_before + 1
    assert projection.evidence_watermark == evidence.evidence_sequence
    assert (await client.post(grade_path, headers=unrelated)).status_code == 200
    assert (await db.execute(evidence_rows)).scalar_one().id == evidence.id
    saved_native.manually_graded = True
    await db.commit()
    corrected = await client.post(grade_path, headers=unrelated)
    assert corrected.status_code == 200, corrected.text
    original, revision = (await db.execute(evidence_rows)).scalars().all()
    assert revision.supersedes_id == original.id and revision.outcome == "not_scored"
    assert revision.eligibility_reason == "native_manual_override"
    retry_path = (
        f"/api/v1/assignments/{native_assignment.assignment_uuid}/submissions/me/retry"
    )
    if concurrent_posts is None:
        retried = await client.post(retry_path, headers=learner)
    else:
        candidates_read, retry_done = Event(), Event()
        execute = AsyncSession.execute

        async def pause_bulk_after_snapshot(session, statement, *args, **kwargs):
            result = await execute(session, statement, *args, **kwargs)
            columns = getattr(statement, "column_descriptions", ())
            if (
                columns
                and columns[0].get("entity") is AssignmentUserSubmission
                and statement._for_update_arg is None
                and "submission_status =" in str(statement)
                and not candidates_read.is_set()
            ):
                candidates_read.set()
                await wait_for(retry_done.wait(), timeout=15)
            return result

        async def retry_after_snapshot(*args, **kwargs):
            await wait_for(candidates_read.wait(), timeout=15)
            try:
                return await retry_assignment_submission(*args, **kwargs)
            finally:
                retry_done.set()

        with (
            patch.object(AsyncSession, "execute", pause_bulk_after_snapshot),
            patch(
                "src.routers.courses.assignments.retry_assignment_submission",
                side_effect=retry_after_snapshot,
            ),
        ):
            retried, repeated_grade, edited_task = await concurrent_posts(
                client,
                retry_path,
                ((None, learner), (None, unrelated)),
                second_path=grade_path,
                extra_calls=(
                    (
                        "PUT",
                        f"{tasks_path}/{native_task.assignment_task_uuid}",
                        {"max_grade_value": native_task.max_grade_value + 1},
                        unrelated,
                    ),
                ),
            )
        assert edited_task.status_code == 200, edited_task.text
        assert repeated_grade.status_code in (200, 400), repeated_grade.text
        if repeated_grade.status_code == 400:
            assert (
                repeated_grade.json()["detail"]
                == "This learner has not handed in an attempt yet; nothing to grade."
            )
    assert retried.status_code == 200, retried.text
    assert retried.json()["submission"]["attempt_number"] == 2
    await db.refresh(native)
    assert native.attempt_number == 2
    assert native.submission_status == AssignmentUserSubmissionStatus.PENDING
    assert (await db.execute(evidence_rows)).scalars().all() == [original, revision]
    await db.refresh(issued)
    assert issued.normalized_response == "19.25"
    assert (await client.get(tasks_path, headers=learner)).status_code == 200
    next_round = (
        await db.execute(rounds.where(StudyLearningAttempt.source_attempt_number == 2))
    ).scalar_one()
    assert next_round.source_submission_uuid == round_row.source_submission_uuid
    assert next_round.source_round_uuid != round_row.source_round_uuid
    next_item = (
        await db.execute(
            select(StudyAttemptItem).where(
                StudyAttemptItem.attempt_id == next_round.id,
            )
        )
    ).scalar_one()
    assert next_item.response_state == "issued"
    second_saved = AssignmentTaskSubmission(
        **saved_native.model_dump(exclude={"id", "assignment_task_submission_uuid"}),
        assignment_task_submission_uuid=f"tasksubmission_{uuid4()}",
    )
    db.add(second_saved)
    await db.commit()
    assert (
        await client.post(
            f"/api/v1/assignments/{native_assignment.assignment_uuid}/submissions",
            headers=learner,
        )
    ).status_code == 200
    assert (await client.post(grade_path, headers=unrelated)).status_code == 200
    second_results = (
        select(StudyLearningEvidence)
        .where(
            StudyLearningEvidence.org_id == org.id,
            StudyLearningEvidence.logical_source_uuid == next_item.uuid,
        )
        .order_by(StudyLearningEvidence.result_revision)
    )
    second_result = (await db.execute(second_results)).scalar_one()
    rejected = await client.delete(
        f"/api/v1/assignments/{native_assignment.assignment_uuid}/submissions/{native.user_id}",
        headers=unrelated,
    )
    assert rejected.status_code == 200, rejected.text
    retained = (await db.execute(evidence_rows)).scalars().all()
    assert retained == [original, revision]
    second_evidence = (await db.execute(second_results)).scalars().all()
    assert len(second_evidence) == 2
    assert (
        second_evidence[-1].supersedes_id == second_result.id
        and second_evidence[-1].is_retraction
    )
    assert second_evidence[-1].correction_reason == "native_submission_rejected"
    projection = (await db.execute(projection_query)).scalar_one()
    assert projection.eligible_count == independent_before
    assert projection.unknown_count == unknown_before + 1
    assert projection.evidence_watermark == second_evidence[-1].evidence_sequence
    await db.refresh(second_saved)
    assert second_saved.task_submission == {"answer": "19.25"}
    await db.delete(second_saved)
    await db.commit()
    await db.refresh(issued)
    assert issued.normalized_response == "19.25"
    path = f"{base}/me/practice-attempts"
    body = {
        "source_activity_uuid": activity_uuid,
        "concept_uuid": concept_uuid,
        "mode": "supported",
    }
    keyed = learner | {"Idempotency-Key": "fixture-practice"}
    async with db.begin_nested() as savepoint:
        denied = await client.post(
            path, json=body | {"source_activity_uuid": unmapped_uuid}, headers=keyed
        )
        assert denied.status_code == 404, denied.text
        await savepoint.rollback()
    async with db.begin_nested() as savepoint:
        await db.execute(
            update(AssignmentTask)
            .where(AssignmentTask.id == native_task.id)
            .values(contents=native_task.contents | {"correct_value": "999.00"})
        )
        changed = await client.post(path, json=body, headers=keyed)
        assert changed.status_code == 409, changed.text
        await savepoint.rollback()
    started = await client.post(path, json=body, headers=keyed)
    assert started.status_code == 201, started.text
    options = await client.get(
        f"{base}/me/attempts/options",
        params={"source_activity_uuid": activity_uuid},
        headers=learner,
    )
    assert options.status_code == 200, options.text
    assert any(
        option["purpose"] == "practice" and option["uuid"] == concept_uuid
        for option in options.json()
    )
    view = started.json()
    assert view["purpose"] == "practice" and view["diagnostic_revision_uuid"] is None
    guided_options = await client.get(
        f"{base}/me/tutor-options",
        params={"activity_uuid": activity_uuid},
        headers=learner,
    )
    assert guided_options.json()["actions"] == ["hint"]
    assert guided_options.json()["practice_attempt_uuid"] == view["uuid"]
    tutor = await client.post(
        f"{base}/me/tutor-sessions",
        json={"activity_uuid": activity_uuid, "practice_attempt_uuid": view["uuid"]},
        headers=learner,
    )
    assert tutor.status_code == 201, tutor.text
    assert tutor.json()["activity_uuid"] == activity_uuid
    profile = await db.get(StudyLearnerProfile, round_row.learner_profile_id)
    actor = PublicUser.model_validate(
        await db.get(User, profile.user_id), from_attributes=True
    )
    request = Request({"type": "http", "headers": []})
    for level, expected in enumerate(
        ("Check the units.", "Compare the two measurements."), 1
    ):
        hint = await apply_reviewed_hint(request, db, org, actor, tutor.json()["uuid"])
        assert (
            hint.hint_level == level and hint.message == expected and hint.fallback_used
        )
        await db.commit()
    exhausted = await apply_reviewed_hint(request, db, org, actor, tutor.json()["uuid"])
    assert (
        exhausted.teaching_action == "return_to_lesson" and exhausted.hint_level is None
    )
    practice_item = (
        await db.execute(
            select(StudyAttemptItem).where(
                StudyAttemptItem.uuid == view["current_item"]["uuid"],
            )
        )
    ).scalar_one()
    assert practice_item.help_count == 2 and practice_item.exposure_count == 0
    assert (
        practice_item.response_state == "issued" and practice_item.response_version == 3
    )
    assert "19.25" not in started.text and "Private worked answer" not in started.text
    attempt = f"{base}/me/attempts/{view['uuid']}"
    assert (await client.get(attempt, headers=unrelated)).status_code == 404
    assert (await client.get(attempt, headers=learner)).json() == view
    assert (await client.post(path, json=body, headers=keyed)).json() == view

    answer = {
        "item_uuid": view["current_item"]["uuid"],
        "expected_version": view["version"],
        "response": {"kind": "number", "value": "19.25"},
        "declared_external_help": False,
    }
    answer_key = learner | {"Idempotency-Key": "fixture-practice-answer"}
    response = await client.post(
        f"{attempt}/responses", json=answer, headers=answer_key
    )
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["status"] == "completed" and receipt["next_item"] is None
    assert (
        await client.post(f"{attempt}/responses", json=answer, headers=answer_key)
    ).json() == receipt
    summary = await client.get(receipt["summary_ref"], headers=learner)
    assert summary.status_code == 200, summary.text
    assert summary.json()["outcome"] == "practice_completed"
    assert (
        summary.json()["scored_count"] is None
        and summary.json()["coverage_complete"] is False
    )
    observation = (
        await db.execute(
            select(StudyLearningEvidence).where(
                StudyLearningEvidence.org_id == org.id,
                StudyLearningEvidence.logical_source_uuid == answer["item_uuid"],
            )
        )
    ).scalar_one()
    assert observation.outcome == "correct" and not observation.eligible
    assert observation.independence == "assisted"

    next_round = await client.post(
        path, json=body, headers=learner | {"Idempotency-Key": "fixture-practice-next"}
    )
    assert next_round.status_code == 201, next_round.text
    new_view = next_round.json()
    assert new_view["uuid"] != view["uuid"]
    abandon = await client.post(
        f"{base}/me/attempts/{new_view['uuid']}/abandon",
        json={"expected_version": new_view["version"], "reason": "restart_later"},
        headers=learner | {"Idempotency-Key": "fixture-practice-end"},
    )
    assert abandon.status_code == 200, abandon.text
    assert (
        abandon.json()["status"] == "abandoned"
        and abandon.json()["current_item"] is None
    )
    assert (
        await db.execute(
            select(StudyLearningEvidence).where(
                StudyLearningEvidence.org_id == org.id,
                StudyLearningEvidence.logical_source_uuid
                == new_view["current_item"]["uuid"],
            )
        )
    ).scalar_one_or_none() is None
    expiring = await client.post(
        path,
        json=body,
        headers=learner | {"Idempotency-Key": "fixture-practice-expiry"},
    )
    assert expiring.status_code == 201, expiring.text
    expiring_view = expiring.json()
    expiring_path = f"{base}/me/attempts/{expiring_view['uuid']}"
    current_path = f"{base}/me/attempts/current"
    params = {"source_activity_uuid": activity_uuid, "purpose": "practice"}
    assert (
        await client.get(current_path, params=params, headers=learner)
    ).json() == expiring_view
    with patch("src.services.studytomy.adaptive_attempts.datetime") as clock:
        clock.now.return_value = datetime.fromisoformat(
            expiring_view["expires_at"]
        ) + timedelta(seconds=1)
        expired = await client.get(expiring_path, headers=learner)
        assert expired.status_code == 200 and expired.json()["status"] == "expired"
        assert expired.json()["current_item"] is None
        assert (
            await client.get(current_path, params=params, headers=learner)
        ).json() is None
        async with db.begin_nested() as savepoint:
            late_answer = await client.post(
                f"{expiring_path}/responses",
                json=answer
                | {
                    "item_uuid": expiring_view["current_item"]["uuid"],
                    "expected_version": expiring_view["version"],
                },
                headers=learner | {"Idempotency-Key": "fixture-expired-answer"},
            )
            assert late_answer.status_code == 409, late_answer.text
            await savepoint.rollback()
    consent = (
        await db.execute(
            select(StudyConsentEvent).where(
                StudyConsentEvent.org_id == org.id,
                StudyConsentEvent.purpose_code == "learning_terms",
            )
        )
    ).scalar_one()
    db.add(
        StudyConsentEvent(
            org_id=org.id,
            learner_profile_id=consent.learner_profile_id,
            actor_user_id=consent.actor_user_id,
            subject_user_id=consent.subject_user_id,
            purpose_code=consent.purpose_code,
            choice="withdraw",
            withdraws_event_uuid=consent.event_uuid,
            notice_version=consent.notice_version,
            scope_codes=["adaptive_practice"],
            policy_version_ids=consent.policy_version_ids,
            effective_at=datetime.now(UTC),
        )
    )
    await db.commit()
    revoked = await client.get(attempt, headers=learner)
    assert (
        revoked.status_code == 403
        and revoked.json()["detail"]["code"] == "POLICY_NOT_APPROVED"
    )


async def _exercise_blueprint(
    client,
    db,
    org,
    base,
    curriculum_uuid,
    original,
    native,
    author,
    reviewer,
    owner,
    d16,
    activity_uuid,
    learner,
    concurrent_posts,
):
    path = f"{base}/diagnostic-revisions"
    payload = {
        "name": "Synthetic diagnostic",
        "curriculum_uuid": curriculum_uuid,
        "required_concepts": [original["concept_uuid"]],
        "item_uuids": [original["uuid"]],
        "selector_version": "adaptive_coverage_v1",
        "mastery_policy_uuid": "synthetic-mastery-v1",
        "limits": {
            "total_items": 2,
            "items_per_concept": 2,
            "minimum_scored_per_concept": 1,
            "expires_after_minutes": 60,
        },
        "rules": {
            "allow_help": False,
            "reveal": "after_close",
            "max_attempts": 2,
            "retake_wait_minutes": 1440,
        },
    }
    keys = author | {"Idempotency-Key": "synthetic-blueprint"}
    assert (await client.post(path, json=payload, headers=reviewer)).status_code == 403
    savepoint = await db.begin_nested()
    unsupported_rules = await client.post(
        path,
        json=payload
        | {
            "rules": payload["rules"] | {"max_attempts": 3},
        },
        headers=keys,
    )
    assert unsupported_rules.status_code == 409
    assert unsupported_rules.json()["detail"]["code"] == "POLICY_NOT_APPROVED"
    await savepoint.rollback()
    for changes, expected in (
        ({"item_uuids": [native["uuid"]]}, "DIAGNOSTIC_ITEM_INVALID"),
        (
            {"limits": payload["limits"] | {"minimum_scored_per_concept": 2}},
            "DIAGNOSTIC_COVERAGE",
        ),
    ):
        savepoint = await db.begin_nested()
        rejected = await client.post(path, json=payload | changes, headers=keys)
        assert (
            rejected.status_code == 422
            and rejected.json()["detail"]["code"] == expected
        ), rejected.text
        await savepoint.rollback()
    draft = await client.post(path, json=payload, headers=keys)
    assert draft.status_code == 201, draft.text
    blueprint = draft.json()
    assert blueprint["status"] == "draft"
    assert "private_answer" not in draft.text
    assert blueprint["manifest"]["pool"][0]["uuid"] == original["uuid"]
    assert (
        await client.get(f"{path}/{blueprint['uuid']}", headers=reviewer)
    ).json() == blueprint
    if db.get_bind().dialect.name == "postgresql":
        with pytest.raises(IntegrityError, match="Invalid diagnostic revision change"):
            async with db.begin_nested():
                await db.execute(
                    update(StudyDiagnosticRevision)
                    .where(
                        StudyDiagnosticRevision.uuid == blueprint["uuid"],
                    )
                    .values(manifest={}, version=2)
                )
    endpoint = f"{base}/definitions/diagnostic/{blueprint['uuid']}"
    command = {
        "expected_version": 1,
        "expected_digest": blueprint["content_digest"],
        "review_evidence": "synthetic-blueprint-review",
    }
    savepoint = await db.begin_nested()
    denied = await client.post(f"{endpoint}/publish", json=command, headers=reviewer)
    assert (
        denied.status_code == 409
        and denied.json()["detail"]["code"] == "POLICY_NOT_APPROVED"
    )
    await savepoint.rollback()

    old = (
        await db.execute(
            select(StudyPolicyVersion).where(
                StudyPolicyVersion.org_id == org.id,
                StudyPolicyVersion.policy_key == StudyPolicyKey.D_16.value,
            )
        )
    ).scalar_one()
    old.status = StudyPolicyStatus.SUPERSEDED.value
    await db.flush()
    updated_d16 = d16 | {
        "review_approvals": d16["review_approvals"]
        | {
            "adaptive_diagnostic_reviews": [
                {
                    "revision_uuid": blueprint["uuid"],
                    "digest": blueprint["content_digest"],
                    "evidence_reference": command["review_evidence"],
                }
            ],
        }
    }
    policy = await _seed_approved_policy(
        db,
        org,
        owner.user_id,
        StudyPolicyKey.D_16,
        specified=updated_d16,
        version=2,
    )
    db.add(
        StudyPolicyApproval(
            org_id=org.id,
            policy_version_id=policy.id,
            responsibility=StudyOwnerFunction.EDUCATION.value,
            owner_assignment_id=owner.id,
            approver_user_id=owner.user_id,
            evidence_reference="synthetic-blueprint-approval",
            rationale="Synthetic policy fixture",
            approved_at=datetime.now(UTC),
        )
    )
    await db.commit()
    savepoint = await db.begin_nested()
    await db.execute(
        update(StudyItemRevision)
        .where(
            StudyItemRevision.uuid == original["uuid"],
        )
        .values(status="retired", version=StudyItemRevision.version + 1)
    )
    stale_pool = await client.post(
        f"{endpoint}/publish", json=command, headers=reviewer
    )
    assert (
        stale_pool.status_code == 422
        and stale_pool.json()["detail"]["code"] == "DIAGNOSTIC_ITEM_INVALID"
    )
    await savepoint.rollback()
    published = await client.post(f"{endpoint}/publish", json=command, headers=reviewer)
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"
    assert published.json()["manifest"] == blueprint["manifest"]
    assert (
        await client.post(f"{endpoint}/publish", json=command, headers=reviewer)
    ).json() == published.json()
    reconciliation_baseline = await _exercise_learner_attempt(
        client,
        db,
        org,
        base,
        blueprint["uuid"],
        activity_uuid,
        learner,
        owner,
        reviewer,
        author,
        concurrent_posts,
    )
    retired = await client.post(
        f"{endpoint}/retire",
        json={
            "expected_version": 2,
            "reason": "SUPERSEDED",
        },
        headers=reviewer,
    )
    assert retired.status_code == 200, retired.text
    assert retired.json()["status"] == "retired"
    assert retired.json()["content_digest"] == blueprint["content_digest"]
    return reconciliation_baseline


async def _exercise_forward_only_mastery_reconciliation(
    client,
    db,
    org,
    base,
    owner,
    staff_headers,
    baseline,
):
    # The earlier diagnostic created the concept-A projection. A supported
    # repair creates a not-assessed projection for concept B at that same
    # learner-wide evidence watermark. The later native assignment response
    # advances the journal and refreshes only concept A, leaving B naturally
    # stale without modifying a projection backwards.
    await _seed_staff_scope(
        db,
        org.id,
        owner.id,
        StudytomyCapabilityName.ASSESSMENT_MODERATION,
        "course",
        baseline["course_uuid"],
        owner.user_id,
    )
    path = (
        f"{base}/staff/learners/{baseline['learner_uuid']}/concepts/"
        f"{baseline['concept_uuid']}/mastery-reconciliation"
    )
    params = {"policy_uuid": baseline["policy_uuid"]}
    mismatch = await client.get(path, params=params, headers=staff_headers)
    assert mismatch.status_code == 200, mismatch.text
    assert mismatch.json()["status"] == "mismatch"
    assert mismatch.json()["evidence_watermark"] > baseline["evidence_watermark"]
    assert mismatch.json()["projection_version"] == baseline["projection_version"]
    assert mismatch.json()["projection"] == baseline["projection"]

    repair_path = f"{path}/repair"
    repair_body = {
        "expected_evidence_watermark": mismatch.json()["evidence_watermark"],
        "expected_projection_version": mismatch.json()["projection_version"],
    }
    repair_key = f"mastery-repair-forward-{uuid4()}"
    repair_headers = staff_headers | {"Idempotency-Key": repair_key}
    repaired = await client.post(
        repair_path, params=params, json=repair_body, headers=repair_headers
    )
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()["status"] == "repaired"
    assert repaired.json()["evidence_watermark"] == mismatch.json()["evidence_watermark"]
    assert repaired.json()["projection_version"] == baseline["projection_version"] + 1
    assert repaired.json()["projection"] == mismatch.json()["projection"]
    assert (
        await client.post(
            repair_path, params=params, json=repair_body, headers=repair_headers
        )
    ).json() == repaired.json()
    stale_repair = await client.post(
        repair_path,
        params=params,
        json=repair_body,
        headers=staff_headers | {"Idempotency-Key": f"mastery-repair-stale-{uuid4()}"},
    )
    assert stale_repair.status_code == 409
    assert stale_repair.json()["detail"]["code"] == "STALE_VERSION"


async def _exercise_learner_attempt(
    client,
    db,
    org,
    base,
    diagnostic_uuid,
    activity_uuid,
    learner,
    owner,
    headers,
    unrelated,
    concurrent_posts,
):
    path = f"{base}/me/diagnostic-attempts"
    body = {
        "source_activity_uuid": activity_uuid,
        "diagnostic_revision_uuid": diagnostic_uuid,
    }
    start_headers = headers | {"Idempotency-Key": "fixture-attempt"}
    disabled = await client.post(path, json=body, headers=start_headers)
    assert (
        disabled.status_code == 403
        and disabled.json()["detail"]["code"] == "ADAPTIVE_DISABLED"
    )
    config = (
        await db.execute(
            select(OrganizationConfig).where(
                OrganizationConfig.org_id == org.id,
            )
        )
    ).scalar_one_or_none()
    if config is None:
        config = OrganizationConfig(org_id=org.id)
    config.config = config.config | {
        "admin_toggles": {"studytomy_adaptive_enabled": True}
    }
    profile = StudyLearnerProfile(
        org_id=org.id,
        user_id=learner.id,
        created_by_user_id=owner.user_id,
        status="active",
        control_mode="independent",
    )
    db.add_all([config, profile])
    await db.commit()
    pending = await client.post(path, json=body, headers=start_headers)
    assert (
        pending.status_code == 403
        and pending.json()["detail"]["code"] == "POLICY_NOT_APPROVED"
    )

    now = datetime.now(UTC)
    values = {
        StudyPolicyKey.D_01: {"independent_participation": "fixture"},
        StudyPolicyKey.D_03: {"adult_transition": "fixture"},
        StudyPolicyKey.D_05: {
            "prices": "fixture",
            "access_periods": "fixture",
            "learner_allocation": "fixture",
        },
        StudyPolicyKey.D_12: {
            "parent_report_scope": ["adaptive_report_summary", "adaptive_report_email"],
            "information_uses": {
                "progress_report_notifications": ["email"],
                "ai_assistance": ["adaptive_guidance"],
                "learning_terms": [
                    "course_release",
                    "adaptive_diagnostics",
                    "adaptive_practice",
                    "adaptive_native_assignments",
                    "adaptive_mastery",
                    "adaptive_staff_mastery",
                    "adaptive_progress_report",
                    "adaptive_guidance",
                ],
            },
            "consent": {
                "notice_versions": {
                    "learning_terms": "fixture-v1",
                    "ai_assistance": "fixture-ai-v1",
                },
                "notice_texts": {
                    "learning_terms": "Synthetic test notice",
                    "ai_assistance": "Synthetic AI notice",
                },
            },
        },
        StudyPolicyKey.D_13: {
            "record_access": {
                "progress_report_notifications": "authenticated_link_only"
            },
            "retention": {
                "adaptive_guidance": {"result_hours": 2, "feedback_notes": True}
            },
        },
        StudyPolicyKey.D_18: {
            "customer_terms": {"progress_report_notifications": True},
            "selected_market_requirements": "fixture",
        },
    }
    for key, specified in values.items():
        policy = await _seed_approved_policy(
            db, org, owner.user_id, key, specified=specified
        )
        for function in POLICY_REQUIRED_RESPONSIBILITIES[key]:
            assignment = (
                owner
                if function == StudyOwnerFunction.EDUCATION
                else await _seed_active_owner(
                    db,
                    org,
                    owner.user_id,
                    owner.user_id,
                    function,
                )
            )
            db.add(
                StudyPolicyApproval(
                    org_id=org.id,
                    policy_version_id=policy.id,
                    responsibility=function.value,
                    owner_assignment_id=assignment.id,
                    approver_user_id=assignment.user_id,
                    evidence_reference="synthetic-learner-policy-review",
                    rationale="Synthetic policy fixture",
                    approved_at=now,
                )
            )
    await db.flush()
    policies = {
        row.policy_key: row.id
        for row in (
            await db.execute(
                select(StudyPolicyVersion).where(
                    StudyPolicyVersion.org_id == org.id,
                    StudyPolicyVersion.status == StudyPolicyStatus.APPROVED.value,
                )
            )
        ).scalars()
    }
    for kind, keys in (
        ("learning", ("D-01", "D-02", "D-12")),
        ("independent_participation", ("D-01", "D-03", "D-12")),
    ):
        db.add(
            StudyEligibilityDecision(
                org_id=org.id,
                learner_profile_id=profile.id,
                eligibility_kind=kind,
                outcome="eligible",
                policy_version_ids=[policies[key] for key in keys],
                evidence_reference="fixture-eligibility",
                effective_from=now,
                reviewer_user_id=owner.user_id,
                owner_assignment_id=owner.id,
            )
        )
    db.add(
        StudyConsentEvent(
            org_id=org.id,
            learner_profile_id=profile.id,
            actor_user_id=learner.id,
            subject_user_id=learner.id,
            purpose_code="learning_terms",
            choice="accept",
            notice_version="fixture-v1",
            scope_codes=[
                "course_release",
                "adaptive_diagnostics",
                "adaptive_practice",
                "adaptive_native_assignments",
                "adaptive_mastery",
                "adaptive_staff_mastery",
                "adaptive_progress_report",
                "adaptive_guidance",
            ],
            policy_version_ids=[
                policies[key] for key in ("D-01", "D-03", "D-12", "D-05", "D-18")
            ],
            effective_at=now,
        )
    )
    await db.commit()

    assert (
        await client.post(
            path,
            json=body | {"source_activity_uuid": f"activity_{uuid4()}"},
            headers=start_headers,
        )
    ).status_code == 404
    tutor_path = f"{base}/me/tutor-sessions"
    tutor_body = {"activity_uuid": activity_uuid}
    denied_tutor = await client.post(tutor_path, json=tutor_body, headers=headers)
    assert denied_tutor.status_code == 403, denied_tutor.text
    db.add(
        StudyConsentEvent(
            org_id=org.id,
            learner_profile_id=profile.id,
            actor_user_id=learner.id,
            subject_user_id=learner.id,
            purpose_code="ai_assistance",
            choice="accept",
            notice_version="fixture-ai-v1",
            scope_codes=["adaptive_guidance"],
            policy_version_ids=[
                policies[key] for key in ("D-01", "D-03", "D-12", "D-10")
            ],
            effective_at=now,
        )
    )
    await db.commit()
    for extra, status in (
        ({"concept_uuid": f"concept_{uuid4()}"}, 404),
        ({"practice_attempt_uuid": f"attempt_{uuid4()}"}, 404),
        ({"chat_uuid": str(uuid4())}, 422),
    ):
        wrong = await client.post(tutor_path, json=tutor_body | extra, headers=headers)
        assert wrong.status_code == status, wrong.text
    tutor_options = f"{base}/me/tutor-options"
    tutor_params = {"activity_uuid": activity_uuid}
    eligible = await client.get(tutor_options, params=tutor_params, headers=headers)
    assert eligible.status_code == 200, eligible.text
    assert eligible.json()["mode"] == "guided"
    assert eligible.json()["actions"] == ["explain", "check_understanding"]
    assert eligible.headers["Cache-Control"] == "private, no-store"
    ordinary = await client.get(tutor_options, params=tutor_params, headers=unrelated)
    assert ordinary.status_code == 200 and ordinary.json()["mode"] == "legacy"
    tutor = await client.post(tutor_path, json=tutor_body, headers=headers)
    assert tutor.status_code == 201, tutor.text
    assert tutor.headers["Cache-Control"] == "private, no-store"
    assert set(tutor.json()) == {
        "uuid",
        "activity_uuid",
        "mode",
        "step",
        "version",
        "closed_at",
    }
    empty_request = Request({"type": "http", "headers": []})
    public_learner = PublicUser.model_validate(learner, from_attributes=True)
    stored, context = await load_guidance_context(
        empty_request, db, org, public_learner, tutor.json()["uuid"]
    )
    assert stored.step == 0 and stored.version == 1
    assert context.lesson_title == "Fractions"
    assert context.independent_family_count == 0
    turns = f"{tutor_path}/{tutor.json()['uuid']}/turns"
    requested = {
        "operation_uuid": str(uuid4()),
        "expected_version": 1,
        "action": "explain",
        "message": "Synthetic learner message",
    }
    if concurrent_posts is None:
        first = await client.post(turns, json=requested, headers=headers)
    else:
        one, two = await concurrent_posts(
            client, turns, ((requested, headers), (requested, headers))
        )
        assert one.status_code == two.status_code == 200
        assert one.json() == two.json()
        first = one
    assert first.status_code == 200, first.text
    assert first.json()["state"] == "completed" and first.json()["session_version"] == 2
    assert first.json()["result"]["fallback_used"]
    assert "Synthetic learner message" not in first.text
    result_path = f"{turns}/{requested['operation_uuid']}"
    assert (await client.get(result_path, headers=headers)).json() == first.json()
    assert (await client.get(result_path, headers=unrelated)).status_code == 404
    feedback_path = turns.removesuffix("/turns") + "/feedback"
    feedback_body = {
        "turn_uuid": requested["operation_uuid"],
        "issue_code": "unclear",
        "note": "Synthetic review note",
    }
    assert (
        await client.post(feedback_path, json=feedback_body, headers=unrelated)
    ).status_code == 404
    assert (
        await client.post(
            feedback_path, json=feedback_body | {"issue_code": "other"}, headers=headers
        )
    ).status_code == 422
    if concurrent_posts is None:
        feedback = await client.post(feedback_path, json=feedback_body, headers=headers)
    else:
        feedback, duplicate = await concurrent_posts(
            client, feedback_path, ((feedback_body, headers), (feedback_body, headers))
        )
        assert feedback.json() == duplicate.json()
    assert feedback.status_code == 200, feedback.text
    assert feedback.headers["Cache-Control"] == "private, no-store"
    assert set(feedback.json()) == {"turn_uuid", "issue_code", "received_at"}
    assert (
        await client.post(feedback_path, json=feedback_body, headers=headers)
    ).json() == feedback.json()
    assert (
        await client.post(
            feedback_path,
            json=feedback_body | {"issue_code": "unsafe"},
            headers=headers,
        )
    ).status_code == 409
    assert (
        "Synthetic review note"
        not in (await client.get(result_path, headers=headers)).text
    )
    assert (
        await client.post(
            turns, json=requested | {"action": "check_understanding"}, headers=headers
        )
    ).status_code == 409
    uncertain_body = requested | {"operation_uuid": str(uuid4()), "expected_version": 2}
    with (
        patch(
            "src.services.studytomy.adaptive_turns._turn_policy",
            new=AsyncMock(return_value=(10, timedelta(hours=2), "synthetic-provider")),
        ),
        patch(
            "src.services.studytomy.adaptive_turns.reserve_ai_credit",
            new=AsyncMock(return_value=1),
        ) as reservation,
        patch(
            "src.services.studytomy.adaptive_turns.generate_teaching",
            new=AsyncMock(side_effect=TimeoutError),
        ) as provider,
    ):
        response = await client.post(turns, json=uncertain_body, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "reconciliation_required"
        assert (
            response.json()["result"] is None
            and response.json()["session_version"] == 2
        )
        assert (
            await client.post(turns, json=uncertain_body, headers=headers)
        ).json() == response.json()
        reservation.assert_awaited_once()
        provider.assert_awaited_once()
    quota_unknown = uncertain_body | {"operation_uuid": str(uuid4())}
    with (
        patch(
            "src.services.studytomy.adaptive_turns._turn_policy",
            new=AsyncMock(return_value=(10, timedelta(hours=2), "synthetic-provider")),
        ),
        patch(
            "src.services.studytomy.adaptive_turns.reserve_ai_credit",
            new=AsyncMock(side_effect=HTTPException(503)),
        ) as reservation,
        patch(
            "src.services.studytomy.adaptive_turns.generate_teaching", new=AsyncMock()
        ) as provider,
    ):
        uncertain_quota = await client.post(turns, json=quota_unknown, headers=headers)
        assert uncertain_quota.json()["state"] == "reconciliation_required"
        assert (
            await client.post(turns, json=quota_unknown, headers=headers)
        ).json() == uncertain_quota.json()
        reservation.assert_awaited_once()
        provider.assert_not_awaited()
    late_body = uncertain_body | {"operation_uuid": str(uuid4())}

    async def complete_after_deadline(*args, **kwargs):
        await db.execute(
            update(StudyTutorTurn)
            .where(StudyTutorTurn.operation_uuid == late_body["operation_uuid"])
            .values(result_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await db.commit()
        return TeachingResult(
            message="Synthetic late output",
            teaching_action="explain",
            fallback_used=False,
        )

    with (
        patch(
            "src.services.studytomy.adaptive_turns._turn_policy",
            new=AsyncMock(return_value=(10, timedelta(hours=2), "synthetic-provider")),
        ),
        patch(
            "src.services.studytomy.adaptive_turns.reserve_ai_credit",
            new=AsyncMock(return_value=1),
        ),
        patch(
            "src.services.studytomy.adaptive_turns.generate_teaching",
            side_effect=complete_after_deadline,
        ) as provider,
    ):
        late = await client.post(turns, json=late_body, headers=headers)
        assert late.status_code == 200, late.text
        assert late.json()["state"] == "reconciliation_required"
        assert late.json()["result"] is None and late.json()["session_version"] == 2
        assert (
            await client.post(turns, json=late_body, headers=headers)
        ).json() == late.json()
        provider.assert_awaited_once()
    late_row = (
        await db.execute(
            select(StudyTutorTurn).where(
                StudyTutorTurn.operation_uuid == late_body["operation_uuid"]
            )
        )
    ).scalar_one()
    assert late_row.result is None and late_row.state == "reconciliation_required"
    await db.execute(
        update(StudyTutorTurn)
        .where(StudyTutorTurn.operation_uuid == requested["operation_uuid"])
        .values(result_expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await db.commit()
    assert (
        await client.post(feedback_path, json=feedback_body, headers=headers)
    ).status_code == 404
    expired_content = (
        await db.execute(
            select(StudyTutorTurn.result, StudyTutorTurn.feedback_note).where(
                StudyTutorTurn.operation_uuid == requested["operation_uuid"]
            )
        )
    ).one()
    assert expired_content == (None, None)
    expired = await client.get(result_path, headers=headers)
    assert expired.status_code == 200 and expired.json()["state"] == "completed"
    assert expired.json()["result"] is None and expired.json()["session_version"] == 2
    tutor_close = f"{tutor_path}/{tutor.json()['uuid']}/close"
    closing = {"expected_version": 2}
    other_close = await client.post(tutor_close, json=closing, headers=unrelated)
    assert other_close.status_code == 404, other_close.text
    if concurrent_posts is None:
        closed = await client.post(tutor_close, json=closing, headers=headers)
    else:
        closes = await concurrent_posts(
            client, tutor_close, ((closing, headers), (closing, headers))
        )
        assert sorted(result.status_code for result in closes) == [200, 409]
        closed = next(result for result in closes if result.status_code == 200)
    assert closed.status_code == 200, closed.text
    assert closed.json()["closed_at"] and closed.json()["version"] == 3
    with pytest.raises(HTTPException) as closed_context:
        await load_guidance_context(
            empty_request, db, org, public_learner, tutor.json()["uuid"]
        )
    assert closed_context.value.status_code == 404
    assert (
        await client.post(tutor_close, json=closing, headers=headers)
    ).status_code == 409

    recommendations = f"{base}/me/recommendations"
    query = {"activity_uuid": activity_uuid}
    progress = await client.get(
        f"{base}/me/lesson-mastery", params=query, headers=headers
    )
    assert progress.status_code == 200, progress.text
    assert [concept["state"] for concept in progress.json()["concepts"]] == [
        "not_assessed",
    ]
    assert progress.json()["concepts"][0]["score"] is None
    assert (
        await client.get(f"{base}/me/lesson-mastery", params=query, headers=unrelated)
    ).status_code == 404
    suggested = await client.get(recommendations, params=query, headers=headers)
    assert suggested.status_code == 200, suggested.text
    assert suggested.headers["Cache-Control"] == "private, no-store"
    suggestion = next(
        row
        for row in suggested.json()
        if row["reason"] == "not_yet_assessed"
    )
    assert suggestion["reason"] == "not_yet_assessed"
    assert suggestion["activity_uuid"] != activity_uuid
    assert (
        await client.get(recommendations, params=query, headers=unrelated)
    ).status_code == 404
    candidate = (
        await db.execute(
            select(Activity).where(
                Activity.activity_uuid == suggestion["activity_uuid"]
            )
        )
    ).scalar_one()
    assert suggestion["activity_title"] == candidate.name
    assert suggestion["course_uuid"].startswith("course_")
    candidate.published = False
    await db.commit()
    filtered = await client.get(recommendations, params=query, headers=headers)
    assert filtered.status_code == 200, filtered.text
    assert all(
        row.get("activity_uuid") != candidate.activity_uuid
        for row in filtered.json()
    )
    candidate.published = True
    await db.commit()

    if concurrent_posts is None:
        started = await client.post(path, json=body, headers=start_headers)
    else:
        results = await concurrent_posts(
            client,
            path,
            (
                (body, start_headers),
                (body, start_headers),
                (body, headers | {"Idempotency-Key": "parallel-start"}),
            ),
        )
        # Either key may win. It must be possible to replay the winner, and all
        # successful or conflicting responses must refer to just one round.
        started = next(result for result in results if result.status_code == 201)
        assert tuple(result.status_code for result in results) in {
            (201, 201, 409),
            (409, 409, 201),
        }, [r.text for r in results]
        for result in results:
            actual = result.json()
            if result.status_code == 201:
                assert actual == started.json()
            else:
                assert actual["detail"]["attempt_uuid"] == started.json()["uuid"]
        if results[0].status_code == 409:
            start_headers = headers | {"Idempotency-Key": "parallel-start"}
    assert started.status_code == 201, started.text
    restricted = await client.post(tutor_path, json=tutor_body, headers=headers)
    assert restricted.status_code == 409, restricted.text
    assert restricted.json()["detail"]["code"] == "ASSESSMENT_IN_PROGRESS"
    restricted_options = await client.get(
        tutor_options, params=tutor_params, headers=headers
    )
    assert restricted_options.status_code == 200
    assert restricted_options.json()["mode"] == "unavailable"
    assert restricted_options.json()["reason"] == "assessment_in_progress"
    assert restricted_options.json()["actions"] == []
    assert started.headers["Cache-Control"] == "private, no-store"
    assert not any(
        private in started.text
        for private in ("private_answer", "manifest_digest", "37.42")
    )
    attempt = started.json()
    attempt_path = f"{base}/me/attempts/{attempt['uuid']}"
    options_path = f"{base}/me/attempts/options"
    options_params = {"source_activity_uuid": activity_uuid}
    options = await client.get(options_path, params=options_params, headers=headers)
    assert options.status_code == 200, options.text
    assert {
        "purpose": "diagnostic",
        "uuid": diagnostic_uuid,
        "label": "Synthetic diagnostic",
    } in options.json()
    assert set(options.json()[0]) == {"purpose", "uuid", "label"}
    assert (
        await client.get(options_path, params=options_params, headers=unrelated)
    ).json() == []
    assert attempt["status"] == "active"
    current_path = f"{base}/me/attempts/current"
    params = {"source_activity_uuid": activity_uuid, "purpose": "diagnostic"}
    resumed = await client.get(current_path, params=params, headers=headers)
    assert (
        resumed.json() == attempt
        and resumed.headers["Cache-Control"] == "private, no-store"
    )
    assert (
        await client.get(current_path, params=params, headers=unrelated)
    ).status_code == 404
    wrong_source = await client.get(
        current_path,
        params=params | {"source_activity_uuid": f"activity_{uuid4()}"},
        headers=headers,
    )
    assert wrong_source.status_code == 200 and wrong_source.json() is None
    assert (await client.get(attempt_path, headers=unrelated)).status_code == 404
    assert (await client.get(attempt_path, headers=headers)).json() == attempt
    assert (await client.post(path, json=body, headers=start_headers)).json() == attempt
    assert (
        await client.get(f"{attempt_path}/summary", headers=headers)
    ).status_code == 409
    savepoint = await db.begin_nested()
    duplicate = await client.post(
        path, json=body, headers=headers | {"Idempotency-Key": "second-start"}
    )
    assert (
        duplicate.status_code == 409
        and duplicate.json()["detail"]["attempt_uuid"] == attempt["uuid"]
    )
    await savepoint.rollback()
    response_path = f"{attempt_path}/responses"
    answer = {
        "item_uuid": attempt["current_item"]["uuid"],
        "expected_version": attempt["version"],
        "response": {"kind": "number", "value": "37.42"},
        "declared_external_help": False,
    }
    answer_headers = headers | {"Idempotency-Key": "fixture-answer"}
    for wrong, status in (
        ({"item_uuid": f"issued_{uuid4()}"}, 404),
        ({"expected_version": attempt["version"] + 1}, 409),
    ):
        savepoint = await db.begin_nested()
        rejected = await client.post(
            response_path, json=answer | wrong, headers=answer_headers
        )
        assert rejected.status_code == status, rejected.text
        await savepoint.rollback()
    savepoint = await db.begin_nested()
    invalid = await client.post(
        response_path,
        json=answer | {"response": {"kind": "number", "value": "NaN"}},
        headers=answer_headers,
    )
    assert invalid.status_code == 422, invalid.text
    await savepoint.rollback()
    if concurrent_posts is None:
        accepted = await client.post(response_path, json=answer, headers=answer_headers)
    else:
        alternate = headers | {"Idempotency-Key": "parallel-answer"}
        results = await concurrent_posts(
            client,
            response_path,
            (
                (answer, answer_headers),
                (answer, answer_headers),
                (answer, alternate),
            ),
        )
        accepted = next(result for result in results if result.status_code == 200)
        assert tuple(result.status_code for result in results) in {
            (200, 200, 409),
            (409, 409, 200),
        }, [r.text for r in results]
        assert all(
            result.json() == accepted.json()
            for result in results
            if result.status_code == 200
        )
        if results[0].status_code == 409:
            answer_headers = alternate
    assert accepted.status_code == 200, accepted.text
    assert (
        accepted.json()["status"] == "completed"
        and accepted.json()["next_item"] is None
    )
    assert not any(
        private in accepted.text for private in ("correct", "37.42", "score")
    )
    assert (
        await client.post(response_path, json=answer, headers=answer_headers)
    ).json() == accepted.json()
    different = answer | {"response": {"kind": "number", "value": "37.41"}}
    for key in (answer_headers["Idempotency-Key"], "new-answer"):
        savepoint = await db.begin_nested()
        conflict = await client.post(
            response_path, json=different, headers=headers | {"Idempotency-Key": key}
        )
        assert conflict.status_code == 409, conflict.text
        await savepoint.rollback()
    summary_ref = accepted.json()["summary_ref"]
    assert summary_ref == f"{attempt_path}/summary"
    summary = await client.get(summary_ref, headers=headers)
    assert summary.status_code == 200, summary.text
    assert (
        summary.json()["outcome"] == "coverage_met"
        and summary.json()["correct_count"] == 1
    )
    mastery_path = f"{base}/me/mastery"
    mastery_params = {
        "curriculum_revision_uuid": attempt["curriculum_uuid"],
        "limit": 1,
    }
    page = await client.get(mastery_path, params=mastery_params, headers=headers)
    assert page.status_code == 200, page.text
    assert page.headers["Cache-Control"] == "private, no-store"
    assert page.json()["evidence_watermark"] == 1
    assert page.json()["concepts"][0]["independent_family_count"] == 1
    assert "private_answer" not in page.text and "37.42" not in page.text
    filtered = await client.get(
        mastery_path,
        params=mastery_params | {"learner_uuid": str(uuid4())},
        headers=headers,
    )
    assert filtered.json()["concepts"] == page.json()["concepts"]
    assert (
        await client.get(mastery_path, params=mastery_params, headers=unrelated)
    ).status_code == 404
    staff_path = f"{base}/staff/learners/{profile.learner_uuid}/mastery"
    assert (
        await client.get(staff_path, params=mastery_params, headers=unrelated)
    ).status_code == 404
    curriculum = (
        await db.execute(
            select(StudyCurriculumRevision).where(
                StudyCurriculumRevision.uuid == attempt["curriculum_uuid"]
            )
        )
    ).scalar_one()
    course_scope = await _seed_staff_scope(
        db,
        org.id,
        owner.id,
        StudytomyCapabilityName.ASSESSMENT_MODERATION,
        "course",
        curriculum.owner_course_uuid,
        owner.user_id,
    )
    assert (
        await client.get(staff_path, params=mastery_params, headers=unrelated)
    ).status_code == 404
    await _seed_staff_scope(
        db,
        org.id,
        owner.id,
        StudytomyCapabilityName.ASSESSMENT_MODERATION,
        "learner",
        profile.learner_uuid,
        owner.user_id,
    )
    staff = await client.get(staff_path, params=mastery_params, headers=unrelated)
    assert staff.status_code == 200, staff.text
    assert staff.headers["Cache-Control"] == "private, no-store"
    assert staff.json()["concepts"] == page.json()["concepts"]
    assert staff.json()["evidence_watermark"] == 1
    assert "private_answer" not in staff.text and "37.42" not in staff.text
    assert (
        await client.get(staff_path, params=mastery_params, headers=headers)
    ).status_code == 404
    mastery_row = (
        await db.execute(
            select(StudyConceptMastery).where(
                StudyConceptMastery.org_id == org.id,
                StudyConceptMastery.learner_profile_id == profile.id,
            )
        )
    ).scalar_one()
    concept = await db.get(StudyConcept, mastery_row.concept_id)
    reconciliation_path = (
        f"{base}/staff/learners/{profile.learner_uuid}/concepts/{concept.uuid}/mastery-reconciliation"
    )
    reconciliation_params = {"policy_uuid": mastery_row.mastery_policy_uuid}
    preview = await client.get(
        reconciliation_path,
        params=reconciliation_params,
        headers=unrelated,
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["status"] == "current"
    assert preview.json()["evidence_watermark"] == 1
    unassessed_concept = (
        await db.execute(
            select(StudyConcept)
            .where(
                StudyConcept.org_id == org.id,
                StudyConcept.curriculum_revision_id == curriculum.id,
                StudyConcept.id != mastery_row.concept_id,
                StudyConcept.archived.is_(False),
            )
            .order_by(StudyConcept.sequence, StudyConcept.id)
            .limit(1)
        )
    ).scalar_one()
    unassessed_path = (
        f"{base}/staff/learners/{profile.learner_uuid}/concepts/"
        f"{unassessed_concept.uuid}/mastery-reconciliation"
    )
    unassessed_params = {"policy_uuid": mastery_row.mastery_policy_uuid}
    missing_projection = await client.get(
        unassessed_path, params=unassessed_params, headers=unrelated
    )
    assert missing_projection.status_code == 200, missing_projection.text
    assert missing_projection.json()["status"] == "missing"
    assert missing_projection.json()["evidence_watermark"] == 1
    assert missing_projection.json()["projection_version"] is None
    assert missing_projection.json()["projection"]["state"] == "not_assessed"
    missing_repair_body = {
        "expected_evidence_watermark": missing_projection.json()["evidence_watermark"],
        "expected_projection_version": 0,
    }
    missing_repair = await client.post(
        f"{unassessed_path}/repair",
        params=unassessed_params,
        json=missing_repair_body,
        headers=unrelated | {"Idempotency-Key": f"mastery-seed-projection-{uuid4()}"},
    )
    assert missing_repair.status_code == 200, missing_repair.text
    assert missing_repair.json()["status"] == "repaired"
    assert missing_repair.json()["evidence_watermark"] == 1
    assert missing_repair.json()["projection_version"] == 1
    assert missing_repair.json()["projection"]["state"] == "not_assessed"
    reconciliation_baseline = {
        "course_uuid": curriculum.owner_course_uuid,
        "learner_uuid": profile.learner_uuid,
        "concept_uuid": unassessed_concept.uuid,
        "policy_uuid": mastery_row.mastery_policy_uuid,
        "evidence_watermark": missing_repair.json()["evidence_watermark"],
        "projection_version": missing_repair.json()["projection_version"],
        "projection": missing_repair.json()["projection"],
    }
    consent = (
        await db.execute(
            select(StudyConsentEvent).where(
                StudyConsentEvent.learner_profile_id == profile.id,
                StudyConsentEvent.purpose_code == "learning_terms",
            )
        )
    ).scalar_one()
    async with db.begin_nested() as savepoint:
        db.add(
            StudyConsentEvent(
                org_id=org.id,
                learner_profile_id=profile.id,
                actor_user_id=learner.id,
                subject_user_id=learner.id,
                purpose_code="learning_terms",
                choice="withdraw",
                withdraws_event_uuid=consent.event_uuid,
                notice_version=consent.notice_version,
                scope_codes=["adaptive_staff_mastery"],
                policy_version_ids=consent.policy_version_ids,
                effective_at=datetime.now(UTC),
            )
        )
        await db.flush()
        assert (
            await client.get(staff_path, params=mastery_params, headers=unrelated)
        ).status_code == 404
        assert (
            await client.get(mastery_path, params=mastery_params, headers=headers)
        ).status_code == 200
        await savepoint.rollback()
    report_path = f"{base}/staff/learners/{profile.learner_uuid}/reports"
    # The report consumes reviewed historical completion independently of the
    # live lesson provider. Reuse the existing approved fixture decisions.
    report_course_uuid = curriculum.owner_course_uuid
    completion_review, retained, _ = await _seed_postgres_release_sources(
        db, org, owner.user_id, uuid4().hex,
        snapshot={"course": {"course_uuid": report_course_uuid, "name": "Synthetic retained course"}},
        course_uuid=report_course_uuid, policy_versions=[],
    )
    completion_release = StudyCourseRelease(
        org_id=org.id, course_uuid=report_course_uuid, review_id=completion_review.id,
        snapshot_sha256=completion_review.revision_sha256, snapshot=retained, media_manifest={},
        assessment_policy_id=policies[StudyPolicyKey.D_08.value],
        progression_policy_id=policies[StudyPolicyKey.D_09.value],
        retention_policy_id=policies[StudyPolicyKey.D_13.value],
        rights_policy_id=policies[StudyPolicyKey.D_16.value],
        retention_category="synthetic",
        policy_binding={"versions": {"D-09": {"policy_id": policies[StudyPolicyKey.D_09.value]}}},
    )
    db.add(completion_release)
    await db.flush()
    completion_event = StudyBusinessEvent(
        org_id=org.id, actor_user_id=learner.id, resource_kind="course_release",
        resource_uuid=completion_release.uuid, action="studytomy.course.completion_recorded",
        policy_refs=[completion_release.progression_policy_id],
        after_state={"source_snapshot_digest": completion_release.snapshot_sha256},
        created_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    db.add(completion_event)
    await db.commit()
    end = datetime.now(UTC)
    report_request = {
        "operation_uuid": str(uuid4()),
        "curriculum_revision_uuid": curriculum.uuid,
        "period_from": (end - timedelta(days=1)).isoformat(),
        "period_to": end.isoformat(),
    }
    rejected = await client.post(report_path, json=report_request, headers=unrelated)
    assert rejected.status_code == 404, rejected.text
    for kind, resource in (
        ("course", curriculum.owner_course_uuid),
        ("learner", profile.learner_uuid),
    ):
        await _seed_staff_scope(
            db,
            org.id,
            owner.id,
            StudytomyCapabilityName.PROGRESS_REPORT_REVIEW,
            kind,
            resource,
            owner.user_id,
        )
    if concurrent_posts is not None:
        created, repeat = await concurrent_posts(
            client,
            report_path,
            ((report_request, unrelated), (report_request, unrelated)),
        )
        assert created.json() == repeat.json()
    else:
        created = await client.post(report_path, json=report_request, headers=unrelated)
    assert created.status_code == 201, created.text
    assert created.headers["Cache-Control"] == "private, no-store"
    report = created.json()
    assert report["status"] == "draft" and report["evidence_watermark"] == 1
    assert report["summary"]["concepts"][0]["independent_correct"] == 1
    assert report["summary"]["concepts"][1]["missing_independent_evidence"] is True
    assert report["summary"]["native_completions"] == 0
    assert report["summary"]["reviewed_release_completions"] == 1
    assert completion_event.event_uuid not in created.text
    assert "37.42" not in created.text and "sources" not in report
    assert (
        await client.post(report_path, json=report_request, headers=headers)
    ).status_code == 404
    assert (
        await client.post(report_path, json=report_request, headers=unrelated)
    ).json() == report
    outside = await client.post(
        report_path,
        json=report_request
        | {
            "operation_uuid": str(uuid4()),
            "period_from": (end - timedelta(days=2)).isoformat(),
            "period_to": (end - timedelta(days=1)).isoformat(),
        },
        headers=unrelated,
    )
    assert outside.status_code == 201, outside.text
    assert "reviewed_release_completions" not in outside.json()["summary"]
    assert (
        outside.json()["summary"]["concepts"][0]["missing_independent_evidence"] is True
    )
    review_path = f"{base}/staff/reports/{report['uuid']}"
    comment = {
        "expected_version": 1,
        "commentary": "Review the measurements next lesson.",
    }
    assert (
        await client.put(review_path, json=comment, headers=headers)
    ).status_code == 404
    assert (
        await client.put(review_path, json=comment | {"summary": {}}, headers=unrelated)
    ).status_code == 422
    edited = await client.put(review_path, json=comment, headers=unrelated)
    assert edited.status_code == 200, edited.text
    assert edited.json()["version"] == 2 and edited.json()["status"] == "draft"
    assert edited.json()["summary"] == report["summary"]
    assert edited.json()["digest"] != report["digest"]
    invalid = {"expected_version": 2, "expected_digest": report["digest"]}
    assert (
        await client.post(f"{review_path}/approve", json=invalid, headers=unrelated)
    ).status_code == 409
    accept_report = invalid | {"expected_digest": edited.json()["digest"]}
    if concurrent_posts is None:
        approval = await client.post(
            f"{review_path}/approve", json=accept_report, headers=unrelated
        )
    else:
        approvals = await concurrent_posts(
            client,
            f"{review_path}/approve",
            ((accept_report, unrelated), (accept_report, unrelated)),
        )
        assert sorted(item.status_code for item in approvals) == [200, 409]
        approval = next(item for item in approvals if item.status_code == 200)
    assert approval.status_code == 200, approval.text
    assert approval.json()["version"] == 3 and approval.json()["status"] == "approved"
    assert approval.json()["digest"] == edited.json()["digest"]
    assert approval.json()["approved_at"]
    revised = await client.put(
        review_path,
        json=comment | {"expected_version": 3, "commentary": None},
        headers=unrelated,
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["status"] == "draft" and revised.json()["approved_at"] is None
    assert revised.json()["version"] == 4 and revised.json()["commentary"] is None
    assert (
        await client.post(
            f"{review_path}/approve", json=accept_report, headers=unrelated
        )
    ).status_code == 409
    approved_again = await client.post(
        f"{review_path}/approve",
        json={
            "expected_version": 4,
            "expected_digest": revised.json()["digest"],
        },
        headers=unrelated,
    )
    assert approved_again.status_code == 200, approved_again.text
    recipient_user = User(
        username=f"recipient-{uuid4()}",
        first_name="Synthetic",
        last_name="Recipient",
        email=f"{uuid4()}@example.invalid",
        user_uuid=f"user_{uuid4()}",
    )
    db.add(recipient_user)
    await db.flush()
    role_id = (
        await db.execute(
            select(UserOrganization.role_id).where(
                UserOrganization.org_id == org.id,
                UserOrganization.user_id == learner.id,
            )
        )
    ).scalar_one()
    db.add(
        UserOrganization(
            user_id=recipient_user.id,
            org_id=org.id,
            role_id=role_id,
            creation_date="",
            update_date="",
        )
    )
    await db.commit()
    pending_guardian = User(
        username=f"guardian-{uuid4()}",
        first_name="Synthetic",
        last_name="Guardian",
        email=f"{uuid4()}@example.invalid",
        user_uuid=f"user_{uuid4()}",
    )
    db.add(pending_guardian)
    await db.flush()
    pending_relationship = StudyRelationship(
        org_id=org.id,
        learner_profile_id=profile.id,
        adult_user_id=pending_guardian.id,
        requested_by_user_id=pending_guardian.id,
        requested_scopes=["adaptive_report_summary"],
        request_evidence_reference="synthetic-pending-request",
        request_policy_version_ids=[policies[key] for key in ("D-01", "D-03", "D-12")],
    )
    db.add_all(
        [
            UserOrganization(
                user_id=pending_guardian.id,
                org_id=org.id,
                role_id=role_id,
                creation_date="",
                update_date="",
            ),
            pending_relationship,
        ]
    )
    await db.commit()
    pending_guardian_headers = _headers(pending_guardian, org, "password")
    payer = User(
        username=f"payer-{uuid4()}",
        first_name="Synthetic",
        last_name="Purchaser",
        email=f"{uuid4()}@example.invalid",
        user_uuid=f"user_{uuid4()}",
    )
    db.add(payer)
    await db.flush()
    order = StudyOrder(
        org_id=org.id, purchaser_user_id=payer.id, currency="AUD",
        total_minor=100, confirmation_sha256="a" * 64,
    )
    offer = StudyOffer(
        org_id=org.id, offer_kind="course", created_by_user_id=payer.id,
        offer_uuid=str(uuid4()),
    )
    db.add_all([
        order,
        offer,
        UserOrganization(
            user_id=payer.id, org_id=org.id, role_id=role_id,
            creation_date="", update_date="",
        ),
    ])
    await db.flush()
    db.add(StudyOfferVersion(
        offer_id=offer.id, version_no=1, terms={}, content_sha256="b" * 64,
        created_by_user_id=payer.id,
    ))
    await db.flush()
    db.add(StudyOrderLine(
        org_id=org.id, order_id=order.id, offer_id=offer.id, offer_version_no=1,
        learner_profile_id=profile.id, total_minor=100, terms_snapshot={},
    ))
    await db.flush()
    order.sealed_at = datetime.now(UTC)
    await db.flush()
    db.add(StudyOrderAcceptance(
        org_id=org.id, order_id=order.id, purchaser_user_id=payer.id,
        confirmation_sha256=order.confirmation_sha256, policy_binding={},
        acknowledged_overlap=False,
    ))
    await db.commit()
    payer_headers = _headers(payer, org, "password")
    reader_headers = _headers(recipient_user, org, "password")
    read_path = f"{base}/reports/{report['uuid']}"
    list_path = f"{base}/me/reports"
    assert (await client.get(read_path, headers=reader_headers)).status_code == 404
    assert (await client.get(list_path, headers=reader_headers)).json()["items"] == []
    grant_values = {
        "org_id": org.id,
        "learner_profile_id": profile.id,
        "recipient_user_id": recipient_user.id,
        "created_by_user_id": learner.id,
        "scope_codes": ["adaptive_report_summary", "adaptive_report_email"],
        "policy_version_ids": [policies[key] for key in ("D-01", "D-03", "D-12")],
        "valid_from": end - timedelta(days=2),
    }
    valid_grant = StudySharingGrant(**grant_values, valid_to=end + timedelta(days=2))
    expired_grant = StudySharingGrant(**grant_values, valid_to=end - timedelta(days=1))
    db.add_all([valid_grant, expired_grant])
    await db.commit()
    publish_path = f"{review_path}/publish"
    recipients_path = f"{review_path}/recipients"
    options = await client.get(
        recipients_path, params={"expected_version": 5}, headers=unrelated
    )
    assert options.status_code == 200, options.text
    assert options.headers["Cache-Control"] == "private, no-store"
    assert options.json() == {
        "items": [
            {
                "grant_uuid": valid_grant.grant_uuid,
                "first_name": recipient_user.first_name,
            }
        ],
        "next_offset": None,
    }
    assert (
        await client.get(
            recipients_path, params={"expected_version": 5}, headers=headers
        )
    ).status_code == 404
    assert (
        await client.get(
            recipients_path, params={"expected_version": 4}, headers=unrelated
        )
    ).status_code == 409
    preview_path = f"{review_path}/recipient-preview"
    selection = {"expected_version": 5, "recipient_grant_uuid": valid_grant.grant_uuid}
    preview = await client.post(preview_path, json=selection, headers=unrelated)
    assert preview.status_code == 200, preview.text
    assert preview.headers["Cache-Control"] == "private, no-store"
    assert preview.json()["commentary"] is None
    assert (
        await client.post(preview_path, json=selection, headers=headers)
    ).status_code == 404
    expired_preview = await client.post(
        preview_path,
        json=selection | {"recipient_grant_uuid": expired_grant.grant_uuid},
        headers=unrelated,
    )
    assert expired_preview.status_code == 409
    assert expired_preview.json()["detail"]["code"] == "REPORT_RECIPIENT_NOT_AUTHORIZED"
    stale_preview = await client.post(
        preview_path,
        json=selection | {"expected_version": 4},
        headers=unrelated,
    )
    assert stale_preview.status_code == 409
    assert stale_preview.json()["detail"]["code"] == "STALE_VERSION"
    command = {"expected_version": 5, "recipient_grant_uuids": [valid_grant.grant_uuid]}
    mixed = command | {
        "recipient_grant_uuids": [valid_grant.grant_uuid, expired_grant.grant_uuid]
    }
    denied_publish = await client.post(publish_path, json=mixed, headers=unrelated)
    assert denied_publish.status_code == 409, denied_publish.text
    assert denied_publish.json()["detail"]["code"] == "REPORT_RECIPIENT_NOT_AUTHORIZED"
    relationship_publish = await client.post(
        publish_path,
        json=command | {
            "recipient_grant_uuids": [valid_grant.grant_uuid, pending_relationship.relationship_uuid],
        },
        headers=unrelated,
    )
    assert relationship_publish.status_code == 409, relationship_publish.text
    assert relationship_publish.json()["detail"]["code"] == "REPORT_RECIPIENT_NOT_AUTHORIZED"
    recipient_query = select(StudyReportRecipient).where(
        StudyReportRecipient.org_id == org.id
    )
    assert (await db.execute(recipient_query)).scalars().all() == []
    assert (
        await client.post(publish_path, json=command, headers=headers)
    ).status_code == 404
    released = await client.post(publish_path, json=command, headers=unrelated)
    assert released.status_code == 200, released.text
    assert released.json()["version"] == 6 and released.json()["recipient_count"] == 1
    assert released.json()["digest"] == approved_again.json()["digest"]
    recipient = (await db.execute(recipient_query)).scalar_one()
    published_row = await db.get(StudyProgressReport, recipient.report_id)
    assert published_row.status == "published" and published_row.version == 6
    assert (
        recipient.sharing_grant_id == valid_grant.id
        and recipient.grant_version == valid_grant.authority_version
    )
    assert (
        await client.post(publish_path, json=command, headers=unrelated)
    ).status_code == 409
    for reader in (reader_headers, headers, unrelated):
        view = await client.get(read_path, headers=reader)
        assert view.status_code == 200, view.text
        assert view.headers["Cache-Control"] == "private, no-store"
        assert view.json()["version"] == 6
        assert view.json()["learner_name"] == learner.first_name
        assert view.json()["can_request_review"] is (reader != unrelated)
        if reader == reader_headers:
            assert view.json() == preview.json() | {"version": 6}
        assert "sources" not in view.json() and "37.42" not in view.text
        assert view.json()["summary"]["reviewed_release_completions"] == 1
        assert completion_event.event_uuid not in view.text and completion_release.uuid not in view.text
    listing = await client.get(list_path, headers=reader_headers)
    assert listing.status_code == 200, listing.text
    assert [item["uuid"] for item in listing.json()["items"]] == [report["uuid"]]
    assert "learner_name" not in listing.json()["items"][0]
    notify_path = f"{review_path}/notify"
    stale_notice = await client.post(
        notify_path, params={"expected_version": 5}, headers=unrelated
    )
    assert stale_notice.status_code == 409
    assert stale_notice.json()["detail"]["code"] == "STALE_VERSION"
    unavailable_email = await client.post(notify_path, headers=unrelated)
    assert unavailable_email.status_code == 200, unavailable_email.text
    assert unavailable_email.json()["deliveries"] == [
        {"status": "suppressed", "recipient_count": 1}
    ]
    recipient_user.email_verified = True
    await db.commit()
    queued = await client.post(
        notify_path, params={"expected_version": 6}, headers=unrelated
    )
    assert queued.status_code == 200, queued.text
    assert queued.json()["deliveries"] == [{"status": "queued", "recipient_count": 1}]
    assert (await client.post(notify_path, headers=unrelated)).json() == queued.json()
    delivery = (
        await db.execute(
            select(StudyOutboxRecord).where(
                StudyOutboxRecord.org_id == org.id,
                StudyOutboxRecord.resource_uuid == report["uuid"],
            )
        )
    ).scalar_one()
    with patch(
        "src.services.studytomy.report_notifications.send_report_delivery",
        new=AsyncMock(side_effect=ProviderOutcomeUnknown),
    ):
        unknown_tally = await run_report_delivery_batch(db, worker_id="report-unknown")
    assert unknown_tally["reconciliation_required"] == 1
    await db.refresh(delivery)
    reconciliation_path = f"{base}/staff/report-deliveries/reconciliation"
    preview = await client.get(reconciliation_path, headers=unrelated)
    assert preview.status_code == 200, preview.text
    assert len(preview.json()["deliveries"]) == 1
    reconciliation_item = preview.json()["deliveries"][0]
    assert reconciliation_item["uuid"] == delivery.record_uuid
    assert reconciliation_item["version"] == delivery.version
    assert (await client.get(reconciliation_path, headers=headers)).json()["deliveries"] == []
    decision_path = f"{reconciliation_path.rsplit('/', 1)[0]}/{delivery.record_uuid}/reconciliation"
    unknown_decision = {
        "expected_version": delivery.version,
        "disposition": "unknown",
        "evidence_reference": "incident:mail-42",
    }
    unknown_key = f"outbox-unknown-{uuid4()}"
    unknown_result = await client.post(
        decision_path,
        json=unknown_decision,
        headers=unrelated | {"Idempotency-Key": unknown_key},
    )
    assert unknown_result.status_code == 200, unknown_result.text
    assert unknown_result.json()["status"] == "reconciliation_required"
    assert (
        await client.post(
            decision_path,
            json=unknown_decision,
            headers=unrelated | {"Idempotency-Key": unknown_key},
        )
    ).json() == unknown_result.json()
    stale_reconciliation = await client.post(
        decision_path,
        json=unknown_decision,
        headers=unrelated | {"Idempotency-Key": f"outbox-stale-{uuid4()}"},
    )
    assert stale_reconciliation.status_code == 409
    assert stale_reconciliation.json()["detail"]["code"] == "STALE_VERSION"
    with patch(
        "src.services.studytomy.report_notifications.send_report_delivery",
        new=AsyncMock(side_effect=ProviderOutcomeUnknown),
    ):
        retry_result = await client.post(
            decision_path,
            json={
                "expected_version": unknown_result.json()["version"],
                "disposition": "known_unsent",
                "evidence_reference": "provider:lookup-43",
            },
            headers=unrelated | {"Idempotency-Key": f"outbox-unsent-{uuid4()}"},
        )
        assert retry_result.status_code == 200, retry_result.text
        assert retry_result.json()["status"] == "retry"
        unknown_again = await run_report_delivery_batch(db, worker_id="report-unknown-again")
        assert unknown_again["reconciliation_required"] == 1
    await db.refresh(delivery)
    accepted_expected_version = delivery.version
    accepted = await client.post(
        decision_path,
        json={
            "expected_version": delivery.version,
            "disposition": "accepted",
            "evidence_reference": "provider:receipt-44",
            "provider_message_reference": "mock-provider:accepted-1",
        },
        headers=unrelated | {"Idempotency-Key": f"outbox-accepted-{uuid4()}"},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "sent"
    assert accepted.json()["version"] == accepted_expected_version + 1
    await db.refresh(delivery)
    assert delivery.provider_message_reference == "mock-provider:accepted-1"
    followup_delivery, _ = await enqueue_delivery(
        db,
        org_id=org.id,
        recipient_user_id=recipient_user.id,
        purpose=StudyOutboxPurpose.PROGRESS_REPORT,
        resource_kind="progress_report",
        resource_uuid=report["uuid"],
        resource_version=6,
        authority_reference_uuid=valid_grant.grant_uuid,
        policy_refs=delivery.policy_refs,
        created_by_user_id=owner.user_id,
        dedupe_key=f"synthetic-followup:{report['uuid']}:{uuid4()}",
    )
    assert followup_delivery.status == "queued"
    await db.commit()
    current_auth = await revalidate_report_delivery(db, delivery)
    assert current_auth.allowed
    with (
        patch(
            "src.services.studytomy.report_notifications.send_email",
            return_value={"id": "mock-email"},
        ) as send,
        patch(
            "src.services.studytomy.report_notifications.get_org_signup_base_url",
            new=AsyncMock(return_value="https://school.example"),
        ),
    ):
        receipt = await send_report_delivery(db, delivery, current_auth, "fixture-key")
        assert receipt.accepted and receipt.message_reference == "mock-email"
        args = send.call_args.args
        assert f"/account/reports?report={report['uuid']}" in args[2]
        assert "37.42" not in args[2] and learner.first_name not in args[2]
        assert send.call_args.kwargs == {"single_attempt": True}
        send.return_value = {"id": None, "to": recipient_user.email}
        smtp_receipt = await send_report_delivery(db, delivery, current_auth, "fixture-key")
        assert smtp_receipt.accepted and smtp_receipt.message_reference is None
        for malformed in ({}, {"id": ""}, {"id": 42}, {"id": None, "to": "other@example.invalid"}):
            send.return_value = malformed
            uncertain = await send_report_delivery(db, delivery, current_auth, "fixture-key")
            assert not uncertain.accepted and uncertain.message_reference is None
    review_request_path = f"{read_path}/review-requests"
    payer_detail = await client.get(read_path, headers=payer_headers)
    assert payer_detail.status_code == 404
    assert payer_detail.headers["Cache-Control"] == "private, no-store"
    payer_list = await client.get(list_path, headers=payer_headers)
    assert payer_list.status_code == 200 and payer_list.json()["items"] == []
    assert (
        await client.post(
            review_request_path,
            headers=payer_headers,
            json={"expected_version": 6, "reason": "privacy_concern"},
        )
    ).status_code == 404
    assert (
        await client.get(read_path, headers=pending_guardian_headers)
    ).status_code == 404
    assert (await client.get(list_path, headers=pending_guardian_headers)).json()[
        "items"
    ] == []
    assert (
        await client.post(
            review_request_path,
            headers=pending_guardian_headers,
            json={"expected_version": 6, "reason": "privacy_concern"},
        )
    ).status_code == 404
    challenge = {"expected_version": 6, "reason": "evidence_error"}
    # Moving to dependent control invalidates prior independent disclosures.
    profile.control_mode = "dependent"
    await db.commit()
    dependent_draft = await client.post(
        report_path,
        json=report_request | {"operation_uuid": str(uuid4())},
        headers=unrelated,
    )
    assert dependent_draft.status_code == 404
    assert dependent_draft.headers["Cache-Control"] == "private, no-store"
    for actor_headers in (headers, reader_headers, pending_guardian_headers):
        detail = await client.get(read_path, headers=actor_headers)
        assert detail.status_code == 404
        assert detail.headers["Cache-Control"] == "private, no-store"
        index = await client.get(list_path, headers=actor_headers)
        assert index.status_code == 200 and index.json()["items"] == []
        review_attempt = await client.post(
            review_request_path, json=challenge, headers=actor_headers
        )
        assert review_attempt.status_code == 404
    profile.control_mode = "independent"
    await db.commit()
    assert (
        await client.post(review_request_path, json=challenge, headers=unrelated)
    ).status_code == 404
    assert (
        await client.post(
            review_request_path,
            json=challenge | {"notes": "unrequested text"},
            headers=headers,
        )
    ).status_code == 422
    assert (
        await client.post(
            review_request_path,
            json=challenge | {"expected_version": 5},
            headers=headers,
        )
    ).status_code == 409
    if concurrent_posts is None:
        received = await client.post(
            review_request_path, json=challenge, headers=reader_headers
        )
        repeated = await client.post(
            review_request_path, json=challenge, headers=reader_headers
        )
    else:
        received, repeated = await concurrent_posts(
            client,
            review_request_path,
            ((challenge, reader_headers), (challenge, reader_headers)),
        )
    assert received.status_code == repeated.status_code == 200, received.text
    assert received.json() == repeated.json()
    assert received.headers["Cache-Control"] == "private, no-store"
    assert received.json()["status"] == "received"
    assert received.json()["report_version"] == 6
    subject_request = await client.post(
        review_request_path, json=challenge, headers=headers
    )
    assert subject_request.status_code == 200, subject_request.text
    assert subject_request.json()["uuid"] != received.json()["uuid"]
    records = (
        (
            await db.execute(
                select(StudyReportReviewRequest).where(
                    StudyReportReviewRequest.report_id == published_row.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert {record.uuid for record in records} == {
        received.json()["uuid"],
        subject_request.json()["uuid"],
    }
    if concurrent_posts is not None:
        targets = (
            (StudyProgressReport, published_row.id, {"commentary": "Changed"}),
            (StudyReportRecipient, recipient.id, {"grant_version": 2}),
            (StudyReportReviewRequest, records[0].id, {"reason": "privacy_concern"}),
        )
        for model, row_id, values in targets:
            condition = model.id == row_id
            for statement in (
                update(model).where(condition).values(**values),
                delete(model).where(condition),
            ):
                with pytest.raises(
                    DBAPIError, match="Study report history is immutable"
                ):
                    async with db.begin_nested():
                        await db.execute(statement)
    assert published_row.status == "published"
    revoked = await client.request(
        "DELETE",
        f"/api/v1/studytomy/orgs/{org.org_uuid}/my-sharing-grants/{valid_grant.grant_uuid}",
        json={"expected_version": 1},
        headers=headers | {"Idempotency-Key": f"withdraw-grant-{uuid4()}"},
    )
    assert revoked.status_code == 200, revoked.text
    await db.refresh(valid_grant)
    assert valid_grant.status == "withdrawn" and valid_grant.authority_version == 2
    assert (
        await client.post(review_request_path, json=challenge, headers=reader_headers)
    ).status_code == 404
    assert (await client.get(read_path, headers=reader_headers)).status_code == 404
    assert (await client.get(list_path, headers=reader_headers)).json()["items"] == []
    assert (await client.get(read_path, headers=headers)).status_code == 200
    with patch(
        "src.services.studytomy.report_notifications.send_email"
    ) as blocked_sender:
        tally = await run_report_delivery_batch(db, worker_id="report-fixture")
    assert tally["suppressed"] == 1 and tally["sent"] == 0
    blocked_sender.assert_not_called()
    current = await client.get(review_path, headers=unrelated)
    assert current.status_code == 200, current.text
    assert current.json()["review_state"] == "current"
    async with db.begin_nested() as evidence_change:
        activity = (
            await db.execute(
                select(Activity).where(Activity.activity_uuid == activity_uuid)
            )
        ).scalar_one()
        dates = {
            "creation_date": report_request["period_from"],
            "update_date": report_request["period_from"],
        }
        trail = Trail(org_id=org.id, user_id=learner.id, **dates)
        db.add(trail)
        await db.flush()
        run = TrailRun(
            trail_id=trail.id,
            course_id=activity.course_id,
            org_id=org.id,
            user_id=learner.id,
            **dates,
        )
        db.add(run)
        await db.flush()
        db.add(
            TrailStep(
                trail_id=trail.id,
                trailrun_id=run.id,
                activity_id=activity.id,
                course_id=activity.course_id,
                org_id=org.id,
                user_id=learner.id,
                complete=True,
                teacher_verified=False,
                grade="",
                **dates,
            )
        )
        await db.flush()
        stale = await client.get(review_path, headers=unrelated)
        assert stale.status_code == 200, stale.text
        assert stale.json()["review_state"] == "correction_pending"
        assert stale.json()["report"] == current.json()["report"]
        assert (await client.get(read_path, headers=headers)).status_code == 404
        await evidence_change.rollback()
    await db.refresh(published_row)
    pending, _ = await enqueue_delivery(
        db,
        org_id=org.id,
        recipient_user_id=recipient_user.id,
        purpose=StudyOutboxPurpose.PROGRESS_REPORT,
        resource_kind="progress_report",
        resource_uuid=report["uuid"],
        resource_version=6,
        authority_reference_uuid=valid_grant.grant_uuid,
        policy_refs=published_row.policy_version_ids,
        dedupe_key=f"withdraw-{uuid4()}",
    )
    await db.commit()
    withdraw_path = f"{review_path}/withdraw"
    withdrawal = {"expected_version": 6, "reason": "report_error"}
    assert (
        await client.post(withdraw_path, json=withdrawal, headers=headers)
    ).status_code == 404
    if concurrent_posts is None:
        withdrawn = await client.post(withdraw_path, json=withdrawal, headers=unrelated)
    else:
        withdrawals = await concurrent_posts(
            client, withdraw_path, ((withdrawal, unrelated), (withdrawal, unrelated))
        )
        assert sorted(response.status_code for response in withdrawals) == [200, 409]
        withdrawn = next(
            response for response in withdrawals if response.status_code == 200
        )
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json() == {
        "uuid": report["uuid"],
        "version": 7,
        "status": "withdrawn",
        "notices_suppressed": 1,
    }
    await db.refresh(pending)
    await db.refresh(published_row)
    assert pending.status == "suppressed" and published_row.status == "published"
    staff_follow_up = await client.get(review_path, headers=unrelated)
    assert staff_follow_up.status_code == 200, staff_follow_up.text
    assert staff_follow_up.headers["Cache-Control"] == "private, no-store"
    assert staff_follow_up.json()["report"]["version"] == 7
    assert staff_follow_up.json()["review_state"] == "withdrawn"
    report_query = {"curriculum_revision_uuid": curriculum.uuid, "limit": 1}
    first_report = await client.get(report_path, params=report_query, headers=unrelated)
    assert first_report.status_code == 200, first_report.text
    assert first_report.headers["Cache-Control"] == "private, no-store"
    first_item = first_report.json()["items"][0]
    assert (first_item["uuid"], first_item["version"], first_item["status"]) == (
        report["uuid"],
        7,
        "withdrawn",
    )
    assert set(first_item) == {"uuid", "version", "status", "period_from", "period_to"}
    cursor = first_report.json()["next_cursor"]
    assert cursor
    second_report = await client.get(
        report_path, params=report_query | {"cursor": cursor}, headers=unrelated
    )
    assert second_report.status_code == 200, second_report.text
    assert second_report.json()["items"][0]["uuid"] != report["uuid"]
    assert (
        await client.get(report_path, params=report_query, headers=headers)
    ).status_code == 404
    assert staff_follow_up.json()["review_requests"] == [
        {"report_version": 6, "reason": "evidence_error", "count": 2}
    ]
    for restricted in (headers, reader_headers):
        assert (await client.get(review_path, headers=restricted)).status_code == 404
    assert (await client.get(read_path, headers=headers)).status_code == 404
    assert (await client.get(list_path, headers=headers)).json()["items"] == []
    assert (
        await client.post(withdraw_path, json=withdrawal, headers=unrelated)
    ).status_code == 409
    corrected = await client.post(
        f"{review_path}/supersede",
        json={"expected_version": 7, "reason": "evidence_corrected"},
        headers=unrelated,
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["version"] == 8
    assert corrected.json()["status"] == "draft"
    assert corrected.json()["digest"] != published_row.digest
    assert corrected.json()["commentary"] is None
    course_delete = await client.delete(
        f"/api/v1/courses/{curriculum.owner_course_uuid}", headers=unrelated
    )
    assert course_delete.status_code == 409, course_delete.text
    assert (
        course_delete.json()["detail"]["code"] == "ADAPTIVE_RETENTION_REVIEW_REQUIRED"
    )
    account_delete = await client.delete(
        f"/api/v1/users/user_id/{learner.id}", headers=headers
    )
    assert account_delete.status_code == 409, account_delete.text
    assert (
        account_delete.json()["detail"]["code"] == "ADAPTIVE_RETENTION_REVIEW_REQUIRED"
    )
    await db.refresh(published_row)
    assert published_row.status == "published" and published_row.version == 6
    assert (await client.get(read_path, headers=headers)).status_code == 404
    assert (await client.get(list_path, headers=headers)).json()["items"] == []
    if concurrent_posts is not None:
        replacement_approval = await client.post(
            f"{review_path}/approve",
            json={"expected_version": 8, "expected_digest": corrected.json()["digest"]},
            headers=unrelated,
        )
        assert replacement_approval.status_code == 200, replacement_approval.text
        race_grant = StudySharingGrant(**grant_values, valid_to=end + timedelta(days=3))
        db.add(race_grant)
        await db.commit()
        race_grant_uuid = race_grant.grant_uuid
        publication_locked, revocation_started = Event(), Event()

        async def hold_publication_lock(*args, **kwargs):
            locked = await _authorize_report_release(*args, **kwargs)
            publication_locked.set()
            await wait_for(revocation_started.wait(), timeout=15)
            return locked

        async def revoke_during_publication(*args, **kwargs):
            await wait_for(publication_locked.wait(), timeout=15)
            revocation_started.set()
            return await withdraw_my_sharing_grant(*args, **kwargs)

        with (
            patch(
                "src.services.studytomy.adaptive_reports._authorize_report_release",
                side_effect=hold_publication_lock,
            ),
            patch(
                "src.routers.studytomy.family.withdraw_my_sharing_grant",
                side_effect=revoke_during_publication,
            ),
        ):
            publish_result, revoke_result = await concurrent_posts(
                client,
                publish_path,
                (
                    (
                        {
                            "expected_version": 9,
                            "recipient_grant_uuids": [race_grant_uuid],
                        },
                        unrelated,
                    ),
                    (
                        {"expected_version": 1},
                        headers | {"Idempotency-Key": f"race-{uuid4()}"},
                    ),
                ),
                second_path=f"/api/v1/studytomy/orgs/{org.org_uuid}/my-sharing-grants/{race_grant_uuid}",
                second_method="DELETE",
            )
        assert revoke_result.status_code == 200, revoke_result.text
        assert revoke_result.json()["authority_version"] == 2
        assert publish_result.status_code == 200, publish_result.text
        assert publish_result.json()["recipient_count"] == 1
        assert publish_result.json()["version"] == 10
        suppressed = await client.post(notify_path, headers=unrelated)
        assert suppressed.status_code == 200, suppressed.text
        assert suppressed.json()["deliveries"] == [
            {"status": "suppressed", "recipient_count": 1}
        ]
        assert (await client.get(read_path, headers=reader_headers)).status_code == 404
        assert (await client.get(list_path, headers=reader_headers)).json()[
            "items"
        ] == []
    course_scope.status = "revoked"
    await db.commit()
    denied = await client.get(staff_path, params=mastery_params, headers=unrelated)
    unknown = await client.get(
        f"{base}/staff/learners/{uuid4()}/mastery",
        params=mastery_params,
        headers=unrelated,
    )
    assert denied.status_code == unknown.status_code == 404
    assert denied.json() == unknown.json()
    unknown_curriculum = await client.get(
        staff_path,
        params=mastery_params | {"curriculum_revision_uuid": f"curriculum_{uuid4()}"},
        headers=unrelated,
    )
    assert unknown_curriculum.status_code == 404
    assert unknown_curriculum.json() == denied.json()
    assert page.json()["next_offset"] == 1
    next_page = await client.get(
        mastery_path, params=mastery_params | {"offset": 1}, headers=headers
    )
    assert next_page.status_code == 200, next_page.text
    assert next_page.json()["concepts"][0]["state"] == "not_assessed"
    assert next_page.json()["concepts"][0]["score"] is None
    assert (
        len(
            (
                await db.execute(
                    select(StudyLearningEvidence).where(
                        StudyLearningEvidence.org_id == org.id,
                        StudyLearningEvidence.learner_profile_id == profile.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        == 1
    )
    return reconciliation_baseline
