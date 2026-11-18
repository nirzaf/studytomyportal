import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.core.events.database import get_db_session
from src.db.courses.assignments import (
    Assignment,
    AssignmentTask,
    AssignmentTaskTypeEnum,
    GradingTypeEnum,
)
from src.db.courses.chapter_activities import ChapterActivity
from src.db.courses.course_chapters import CourseChapter
from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.resource_authors import (
    ResourceAuthor,
    ResourceAuthorshipEnum,
    ResourceAuthorshipStatusEnum,
)
from src.db.studytomy.access import StudyCourseAccessGrant
from src.db.studytomy.commerce import StudyOfferTerms
from src.db.studytomy.education import StudyCourseRelease, StudyCourseReview
from src.db.studytomy.family import StudyLearnerProfile
from src.db.studytomy.governance import (
    StudyBusinessEvent,
    StudyOwnerAssignment,
    StudyOwnerFunction,
    StudyPolicyApproval,
    StudyPolicyKey,
    StudyPolicyStatus,
    StudyPolicyVersion,
)
from src.db.studytomy.operations import StudyEvidenceFile
from src.db.users import PublicUser, User
from src.routers import local_content
from src.routers.studytomy.files import router
from src.security.api_token_utils import get_authenticated_non_api_token_user
from src.security.auth import get_current_user
from src.security.studytomy_access import StudytomyCapabilityName
from src.services.courses.transfer import storage_utils
from src.services.studytomy import content_evidence, offers, publication
from src.services.studytomy.adaptive_retention import (
    ensure_adaptive_course_deletable,
    ensure_adaptive_org_deletable,
    ensure_adaptive_user_deletable,
)
from src.services.studytomy.entitlements import (
    persist_authorized_course_grant,
    resolve_active_course_grants,
)
from src.services.studytomy.idempotency import canonical_request_hash
from src.services.studytomy.offers import _snapshot_resource_titles, _terms_snapshot
from src.services.studytomy.policies import POLICY_REQUIRED_RESPONSIBILITIES
from src.services.studytomy.releases import (
    ReleasePolicyBinding,
    ReleasePolicyVersion,
    capture_native_release_media,
    course_candidate_snapshot,
    load_release_policy_binding,
    retain_reviewed_release_media,
    validate_release_media_coverage,
)
from src.services.utils import upload_content
from src.tests.studytomy.test_studytomy_access import (
    _grant_role_capabilities,
    _seed_active_owner,
    _seed_approved_policy,
    _seed_staff_scope,
)

PDF = b"%PDF-1.4\n%%EOF\n"


@pytest.mark.parametrize("activity", [
    {"activity_sub_type": "SUBTYPE_DYNAMIC_EMBED"},
    {"activity_sub_type": "SUBTYPE_VIDEO_YOUTUBE"},
    {"activity_sub_type": "SUBTYPE_VIDEO_HOSTED", "extra_metadata": {"captions": {"status": "ready"}}},
    {"activity_sub_type": "SUBTYPE_VIDEO_HOSTED", "extra_metadata": {}},
    {"activity_sub_type": "SUBTYPE_DYNAMIC_PAGE", "content": {
        "type": "doc", "content": [{"type": "paragraph", "content": [{"type": "imageBlock"}]}],
    }},
])
def test_release_media_coverage_refuses_unsupported_dependencies(activity):
    with pytest.raises(HTTPException) as refused:
        validate_release_media_coverage({"chapters": [], "activities": [activity]})
    assert refused.value.detail["code"] == "RELEASE_MEDIA_UNSUPPORTED"
    if activity.get("extra_metadata", {}).get("captions"):
        assert "cannot retain the video's captions" in refused.value.detail["message"]
    elif activity["activity_sub_type"] == "SUBTYPE_VIDEO_HOSTED":
        assert "approved accessible alternative" in refused.value.detail["message"]


async def test_release_media_manifest_binds_native_file_bytes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = SimpleNamespace(hosting_config=SimpleNamespace(
        content_delivery=SimpleNamespace(type="filesystem"),
    ))
    monkeypatch.setattr(upload_content, "get_learnhouse_config", lambda: config)
    snapshot = {
        "course": {"course_uuid": "course_fixture", "thumbnail_image": "cover.png"},
        "activities": [{
            "id": 1, "activity_uuid": "activity_fixture",
            "activity_sub_type": "SUBTYPE_DOCUMENT_PDF", "content": {"filename": "lesson.pdf"},
        }],
        "assignments": [{
            "id": 2, "activity_id": 1, "assignment_uuid": "assignment_fixture", "solution_file": "solution.pdf",
        }],
        "assignment_tasks": [{
            "assignment_id": 2, "activity_id": 1, "assignment_task_uuid": "task_fixture", "reference_file": "reference.pdf",
        }],
    }
    course = "courses/course_fixture"
    activity = f"{course}/activities/activity_fixture"
    assignment = f"{activity}/assignments/assignment_fixture"
    files = [
        f"{course}/thumbnails/cover.png", f"{activity}/documentpdf/lesson.pdf",
        f"{assignment}/solution/solution.pdf", f"{assignment}/tasks/task_fixture/reference.pdf",
    ]
    root = tmp_path / "content" / "orgs" / "org_fixture"
    for ref in files:
        file = root / ref
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(PDF)
    original = await capture_native_release_media("org_fixture", snapshot)
    assert set(original) == set(files)
    assert original[files[1]]["media_type"] == "application/pdf"
    assert {item["size_bytes"] for item in original.values()} == {len(PDF)}
    monkeypatch.setattr(storage_utils, "get_content_delivery_type", lambda: "filesystem")
    retained = await retain_reviewed_release_media("org_fixture", original)
    assert set(retained) == set(files)
    assert retained[files[1]]["media_type"] == "application/pdf"
    assert all(ref["storage_reference"].startswith("studytomy/releases/") for ref in retained.values())
    retained_files = {root / ref["storage_reference"] for ref in retained.values()}
    assert all(path.read_bytes() == PDF for path in retained_files)
    changed = root / files[-1]
    changed.write_bytes(PDF.replace(b"1.4", b"1.5"))
    replacement = await capture_native_release_media("org_fixture", snapshot)
    assert replacement[files[-1]]["sha256"] != original[files[-1]]["sha256"]
    assert all(replacement[ref] == original[ref] for ref in files[:-1])
    with pytest.raises(HTTPException) as stale:
        await retain_reviewed_release_media("org_fixture", original)
    assert stale.value.detail["code"] == "REVIEW_MEDIA_CHANGED"
    assert set((root / "studytomy" / "releases").rglob("*.bin")) == retained_files
    assert all(path.read_bytes() == PDF for path in retained_files)
    changed.unlink()
    with pytest.raises(HTTPException) as missing:
        await capture_native_release_media("org_fixture", snapshot)
    assert missing.value.status_code == 404


async def test_release_policy_binding_requires_current_approved_retention(
    db, org, admin_user, regular_user,
):
    with pytest.raises(HTTPException) as unapproved:
        await load_release_policy_binding(db, org, datetime.now(UTC))
    assert unapproved.value.detail["code"] == "POLICY_NOT_APPROVED"
    policies = {}
    assignments = {}
    for key in (StudyPolicyKey.D_08, StudyPolicyKey.D_09, StudyPolicyKey.D_13, StudyPolicyKey.D_16):
        policies[key] = await _seed_approved_policy(db, org, admin_user.id, key)
        for responsibility in POLICY_REQUIRED_RESPONSIBILITIES[key]:
            if responsibility not in assignments:
                assignments[responsibility] = await _seed_active_owner(
                    db, org, regular_user.id, admin_user.id, responsibility,
                )
            db.add(StudyPolicyApproval(
                org_id=org.id, policy_version_id=policies[key].id,
                responsibility=responsibility.value, owner_assignment_id=assignments[responsibility].id,
                approver_user_id=regular_user.id, evidence_reference="SYNTHETIC-RELEASE-REVIEW",
                rationale="Synthetic release review fixture", approved_at=datetime.now(UTC),
            ))
    await db.commit()
    with pytest.raises(HTTPException) as missing:
        await load_release_policy_binding(db, org, datetime.now(UTC))
    assert missing.value.detail["code"] == "RELEASE_RETENTION_UNCONFIGURED"
    retention = policies[StudyPolicyKey.D_13]
    retention.body = {
        **retention.body,
        "decision_items": {
            **retention.body["decision_items"],
            "retention": {
                "state": "specified",
                "value": {"course_release_retention_category": "synthetic_course_release"},
                "rationale": "Synthetic retention fixture",
                "evidence_reference": "SYNTHETIC-RETENTION",
            },
        },
    }
    await db.commit()
    binding = await load_release_policy_binding(db, org, datetime.now(UTC))
    assert binding.retention_category == "synthetic_course_release"
    assert binding.versions.keys() == policies.keys()
    for key, pinned in binding.versions.items():
        assert pinned.policy_id == policies[key].id
        assert pinned.version == policies[key].version
        assert pinned.body_sha256 == canonical_request_hash(policies[key].body)


async def test_private_course_evidence_is_scoped_replayable_and_revision_bound(
    db, org, course, chapter, activity, admin_user, regular_user, monkeypatch, tmp_path,
):
    await _grant_role_capabilities(db, org.id, admin_user.id, content_release_review=True)
    app = FastAPI()
    app.include_router(router)
    app.include_router(local_content.router)

    async def session():
        # SQLite legacy transaction mode needs an explicit transaction before
        # the idempotency service opens its first savepoint.
        try:
            await db.execute(text("BEGIN"))
            yield db
        finally:
            await db.rollback()

    app.dependency_overrides[get_db_session] = session
    app.dependency_overrides[get_authenticated_non_api_token_user] = lambda: admin_user
    app.dependency_overrides[get_current_user] = lambda: admin_user
    org_uuid, course_uuid, actor_id = org.org_uuid, course.course_uuid, admin_user.id
    assigner_id = regular_user.id
    base = f"/orgs/{org_uuid}/courses/{course_uuid}"
    candidate = f"{base}/content-review-candidate"
    managed_draft = f"{base}/managed-draft"
    files = f"{base}/evidence-files"
    monkeypatch.chdir(tmp_path)
    content_root = tmp_path / "content"
    monkeypatch.setattr(local_content, "CONTENT_DIR", content_root)
    config = SimpleNamespace(hosting_config=SimpleNamespace(
        content_delivery=SimpleNamespace(type="filesystem"),
    ))
    monkeypatch.setattr(upload_content, "get_learnhouse_config", lambda: config)
    monkeypatch.setattr(storage_utils, "get_content_delivery_type", lambda: "filesystem")
    evidence_directory = content_root / "orgs" / org_uuid / "studytomy" / "evidence"
    corrupt_upload = True

    async def store(**kwargs):
        await upload_content.upload_content(**kwargs)
        if corrupt_upload:
            (evidence_directory / kwargs["file_and_format"]).write_bytes(
                PDF.replace(b"1.4", b"1.5")
            )

    upload = AsyncMock(side_effect=store)
    monkeypatch.setattr(content_evidence, "upload_content", upload)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.put(managed_draft)).json()["detail"]["code"] == "POLICY_NOT_APPROVED"
        assert (await client.get(candidate)).json()["detail"]["code"] == "POLICY_NOT_APPROVED"
        # Failed HTTP dependencies roll back their session, including identity
        # map state. Retrieve the fixtures explicitly before further writes.
        await db.refresh(org)
        await db.refresh(course)
        await db.refresh(activity)
        policy_ids = {}
        for key in (StudyPolicyKey.D_02, StudyPolicyKey.D_08, StudyPolicyKey.D_09, StudyPolicyKey.D_10, StudyPolicyKey.D_12,
                    StudyPolicyKey.D_13, StudyPolicyKey.D_15, StudyPolicyKey.D_16):
            synthetic = {
                StudyPolicyKey.D_13: {"retention": {
                    "content_evidence_retention_category": "synthetic_content",
                    "course_release_retention_category": "synthetic_release",
                }},
                StudyPolicyKey.D_15: {"supported_formats": {"max_bytes": 4096, "media_types": ["application/pdf"]}},
            }.get(key)
            policy = await _seed_approved_policy(db, org, assigner_id, key, specified=synthetic)
            policy_ids[key] = policy.id
        owner = await _seed_active_owner(db, org, actor_id, assigner_id, StudyOwnerFunction.EDUCATION)
        await _seed_staff_scope(
            db, org.id, owner.id, StudytomyCapabilityName.CONTENT_RELEASE_REVIEW, "course", course_uuid, assigner_id,
        )
        assert (await client.get(candidate)).json()["detail"]["code"] == "POLICY_APPROVAL_EVIDENCE_MISSING"
        await db.refresh(org)
        policy_owners = {}
        for key, policy_id in policy_ids.items():
            for responsibility in POLICY_REQUIRED_RESPONSIBILITIES[key]:
                if responsibility not in policy_owners:
                    policy_owner = await _seed_active_owner(db, org, assigner_id, actor_id, responsibility)
                    policy_owners[responsibility] = policy_owner.id
                db.add(StudyPolicyApproval(
                    org_id=org.id, policy_version_id=policy_id, responsibility=responsibility.value,
                    owner_assignment_id=policy_owners[responsibility], approver_user_id=assigner_id,
                    evidence_reference="SYNTHETIC-APPROVAL-TEST", rationale="Synthetic named approval for testing",
                    approved_at=datetime.now(UTC),
                ))
        await db.commit()
        await db.refresh(course)
        course.public = True
        await db.commit()
        refused = await client.put(managed_draft)
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "MANAGED_COURSE_PRIVATE_DRAFT_REQUIRED"
        await db.refresh(course)
        assert course.studytomy_managed is False
        course.published = False
        course.public = False
        course.thumbnail_image = "review-cover.png"
        thumbnail_ref = f"courses/{course_uuid}/thumbnails/{course.thumbnail_image}"
        thumbnail = content_root / "orgs" / org_uuid / thumbnail_ref
        thumbnail.parent.mkdir(parents=True, exist_ok=True)
        thumbnail.write_bytes(PDF)
        await db.commit()
        for _ in range(2):
            designated = await client.put(managed_draft)
            assert designated.status_code == 204, designated.text
            assert designated.headers["cache-control"] == "private, no-store"
        await db.refresh(course)
        assert course.studytomy_managed is True
        result = await client.get(candidate)
        assert result.status_code == 200
        assert result.headers["cache-control"] == "private, no-store"
        revision = result.json()["revision_sha256"]
        assert result.json()["evidence"] == []
        payload = {"purpose": "content_rights", "revision_sha256": revision}
        headers = {"Idempotency-Key": "content-fixture-request"}
        evidence = {"file": ("rights.pdf", PDF, "application/pdf")}
        corrupted = await client.post(files, data=payload, files=evidence, headers=headers)
        assert corrupted.status_code == 503, corrupted.text
        assert list(evidence_directory.iterdir()) == []
        corrupt_upload = False
        upload.reset_mock()
        saved = await client.post(files, data=payload, files=evidence, headers=headers)
        assert saved.status_code == 201, saved.text
        assert saved.json()["revision_sha256"] == revision
        assert "storage_reference" not in saved.json()
        candidate_with_evidence = await client.get(candidate)
        assert candidate_with_evidence.json() == {
            "revision_sha256": revision,
            "evidence": [saved.json()],
            "next_before_id": None,
            "current_review": None,
            "grounding_review_required": False,
        }

        replay = await client.post(files, data=payload, files=evidence, headers=headers)
        assert replay.json() == saved.json()
        assert upload.await_count == 1
        evidence_id = saved.json()["evidence_uuid"]
        private_file = evidence_directory / f"{evidence_id}.pdf"
        assert private_file.read_bytes() == PDF
        public_path = f"/{private_file.relative_to(tmp_path).as_posix()}"
        assert (await client.get(public_path)).status_code == 403
        download = await client.get(f"{files}/{evidence_id}/content")
        assert download.content == PDF
        assert download.headers["cache-control"] == "private, no-store"
        assert download.headers["content-disposition"].startswith("attachment;")
        verification = f"{candidate}/verify-evidence"
        selection = {
            "revision_sha256": revision,
            "rights_evidence_uuid": evidence_id,
            "accessibility_evidence_uuid": evidence_id,
        }
        wrong_attachment = await client.post(verification, json=selection)
        assert wrong_attachment.status_code == 409
        assert wrong_attachment.json()["detail"]["code"] == "REVIEW_EVIDENCE_MISMATCH"
        accessible = await client.post(
            files, data=payload | {"purpose": "content_accessibility"},
            files=evidence, headers={"Idempotency-Key": "content-accessibility-fixture"},
        )
        assert accessible.status_code == 201, accessible.text
        selection["accessibility_evidence_uuid"] = accessible.json()["evidence_uuid"]
        verified = await client.post(verification, json=selection)
        assert verified.status_code == 200, verified.text
        assert verified.json() == [saved.json(), accessible.json()]
        assert verified.headers["cache-control"] == "private, no-store"
        private_file.write_bytes(PDF.replace(b"1.4", b"1.5"))
        changed_object = await client.post(verification, json=selection)
        assert changed_object.status_code == 503
        private_file.write_bytes(PDF)
        review_url = f"{base}/content-review-decisions"
        approval = selection | {
            "decision": "approved", "accessibility_confirmed": True, "assessment_confirmed": True,
        }
        review_headers = {"Idempotency-Key": "fixture-course-review"}
        db.add(ResourceAuthor(
            resource_uuid=course_uuid, user_id=actor_id,
            authorship=ResourceAuthorshipEnum.CREATOR,
            authorship_status=ResourceAuthorshipStatusEnum.ACTIVE,
        ))
        await db.commit()
        self_review = await client.post(review_url, json=approval, headers=review_headers)
        assert self_review.status_code == 403, self_review.text
        authorship = (await db.execute(select(ResourceAuthor).where(
            ResourceAuthor.resource_uuid == course_uuid,
            ResourceAuthor.authorship == ResourceAuthorshipEnum.CREATOR,
        ))).scalar_one()
        authorship.user_id = assigner_id
        await db.commit()

        from src.services.studytomy import publication

        read_candidate = publication.course_candidate_snapshot

        async def candidate_with_video(*args, **kwargs):
            snapshot = await read_candidate(*args, **kwargs)
            return {**snapshot, "activities": [*snapshot["activities"], {
                "activity_sub_type": "SUBTYPE_VIDEO_HOSTED",
                "extra_metadata": video_metadata,
            }]}

        for video_metadata in ({"captions": {"enabled": True}}, {}):
            with monkeypatch.context() as review_fixture:
                review_fixture.setattr(publication, "course_candidate_snapshot", candidate_with_video)
                blocked_review = await client.post(review_url, json=approval, headers=review_headers)
            assert blocked_review.status_code == 409, blocked_review.text
            assert blocked_review.json()["detail"]["code"] == "RELEASE_MEDIA_UNSUPPORTED"
            assert (await client.get(candidate)).json()["current_review"] is None

        approved = await client.post(review_url, json=approval, headers=review_headers)
        assert approved.status_code == 201, approved.text
        assert approved.json()["revision_sha256"] == revision
        preflight_path = f"{review_url}/{approved.json()['uuid']}/release-preflight"
        preflight = await client.post(preflight_path)
        assert preflight.status_code == 200, preflight.text
        assert preflight.json() == {"review_uuid": approved.json()["uuid"], "revision_sha256": revision}
        release_path = f"{review_url}/{approved.json()['uuid']}/release"
        real_commit = db.commit

        async def lose_release_commit_acknowledgment():
            await real_commit()
            raise ConnectionError("Release commit acknowledgment lost")

        with monkeypatch.context() as context:
            context.setattr(db, "commit", lose_release_commit_acknowledgment)
            with pytest.raises(ConnectionError, match="Release commit acknowledgment lost"):
                await client.post(release_path)
        thumbnail.write_bytes(PDF.replace(b"1.4", b"1.5"))
        released = await client.post(release_path)
        assert released.status_code == 201, released.text
        assert released.json()["revision_sha256"] == revision
        assert (await client.post(release_path)).json() == released.json()
        stale_media = await client.post(preflight_path)
        assert stale_media.status_code == 409
        assert stale_media.json()["detail"]["code"] == "REVIEW_MEDIA_CHANGED"
        assert (await client.post(review_url, json=approval, headers=review_headers)).json() == approved.json()
        assert (await client.get(candidate)).json()["current_review"] == approved.json()
        other = await client.get(f"/orgs/{org_uuid}/courses/course_other/evidence-files/{evidence_id}/content")
        assert other.status_code in {403, 404}
        wrong_purpose = await client.post(
            files, data={"purpose": "eligibility_evidence", "revision_sha256": revision},
            files=evidence, headers=headers,
        )
        assert wrong_purpose.status_code == 422

        async def current_revision():
            response = await client.get(candidate)
            assert response.status_code == 200, response.text
            return response.json()["revision_sha256"]

        for model in (CourseChapter, ChapterActivity):
            link = (await db.execute(select(model))).scalar_one()
            link.update_date = "fixture-timestamp-change"
            await db.commit()
            assert await current_revision() == revision
            await db.refresh(link)
            original_order = link.order
            link.order += 1
            await db.commit()
            assert await current_revision() != revision
            await db.refresh(link)
            link.order = original_order
            await db.commit()
            assert await current_revision() == revision

        await db.refresh(activity)
        await db.refresh(chapter)
        parents = {
            "org_id": activity.org_id, "course_id": activity.course_id,
            "chapter_id": chapter.id, "activity_id": activity.id,
        }
        assignment = Assignment(
            title="Synthetic assessment", description="", grading_type=GradingTypeEnum.NUMERIC,
            assignment_uuid=f"assignment_{uuid4()}", **parents,
        )
        db.add(assignment)
        await db.flush()
        task = AssignmentTask(
            title="Synthetic question", description="", hint="", creation_date="", update_date="",
            assignment_task_uuid=f"task_{uuid4()}", assignment_id=assignment.id,
            assignment_type=AssignmentTaskTypeEnum.NUMBER_ANSWER,
            contents={"correct_value": "37"}, **parents,
        )
        db.add(task)
        await db.commit()
        assessment_revision = await current_revision()
        assert assessment_revision != revision
        await db.refresh(course)
        snapshot = await course_candidate_snapshot(db, course)
        assert canonical_request_hash(snapshot) == assessment_revision
        reviewed_assignment, = snapshot["assignments"]
        reviewed_task, = snapshot["assignment_tasks"]
        assert reviewed_task["assignment_id"] == reviewed_assignment["id"]
        assert [reviewed_task["chapter_id"], reviewed_task["activity_id"], 1] in snapshot["activity_order"]
        await db.refresh(assignment)
        assignment.allow_retries = True
        await db.commit()
        retry_revision = await current_revision()
        assert retry_revision != assessment_revision
        await db.refresh(task)
        task.contents = {"correct_value": "38"}
        await db.commit()
        task_revision = await current_revision()
        assert task_revision != retry_revision
        assert canonical_request_hash(snapshot) == assessment_revision
        assert reviewed_task["contents"]["correct_value"] == "37"
        assert reviewed_assignment["allow_retries"] is False

        await db.refresh(activity)
        activity.content = {"type": "doc", "content": [{"type": "paragraph"}]}
        await db.commit()
        assert await current_revision() != task_revision
        edited_candidate = (await client.get(candidate)).json()
        assert edited_candidate["evidence"] == []
        assert edited_candidate["current_review"] is None
        stale_release = await client.post(preflight_path)
        assert stale_release.status_code == 409
        assert stale_release.json()["detail"]["code"] == "CURRENT_COURSE_REVIEW_REQUIRED"
        assert (await client.post(release_path)).json() == released.json()
        stale_selection = await client.post(verification, json=selection)
        assert stale_selection.status_code == 409
        assert stale_selection.json()["detail"]["code"] == "STALE_VERSION"
        stale = await client.post(files, data=payload, files=evidence, headers=headers)
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "STALE_VERSION"
        assert upload.await_count == 2

    records = (await db.execute(select(StudyEvidenceFile))).scalars().all()
    assert len(records) == 2 and all(record.learner_profile_id is None for record in records)
    events = (await db.execute(select(StudyBusinessEvent).where(
        StudyBusinessEvent.action == "studytomy.content_evidence.uploaded"
    ))).scalars().all()
    assert len(events) == 2
    decision = (await db.execute(select(StudyCourseReview))).scalar_one()
    assert decision.uuid == approved.json()["uuid"]
    assert decision.owner_user_id == assigner_id
    assert decision.reviewer_user_id == actor_id
    assert decision.media_manifest == {
        thumbnail_ref: {"sha256": hashlib.sha256(PDF).hexdigest(), "size_bytes": len(PDF)}
    }
    release = (await db.execute(select(StudyCourseRelease))).scalar_one()
    assert release.uuid == released.json()["uuid"] and release.review_id == decision.id
    assert canonical_request_hash(release.snapshot) == release.snapshot_sha256
    assert release.policy_binding["retention_category"] == "synthetic_release"
    for key, policy_id in (
        ("D-08", release.assessment_policy_id),
        ("D-09", release.progression_policy_id),
        ("D-13", release.retention_policy_id),
        ("D-16", release.rights_policy_id),
    ):
        policy = await db.get(StudyPolicyVersion, policy_id)
        assert release.policy_binding["versions"][key] == {
            "policy_id": policy_id, "policy_uuid": policy.policy_uuid,
            "version": policy.version, "body_sha256": canonical_request_hash(policy.body),
        }
    retained = content_root / "orgs" / org_uuid / release.media_manifest[thumbnail_ref]["storage_reference"]
    assert retained.read_bytes() == PDF
    assert list((content_root / "orgs" / org_uuid / "studytomy" / "releases").rglob("*.bin")) == [retained]
    await db.refresh(course)
    assert course.published is False and course.public is False
    await db.refresh(org)
    terms = StudyOfferTerms.model_validate({"resources": [{
        "kind": "course", "resource_uuid": course_uuid, "release_uuid": release.uuid,
        "release_sha256": "0" * 64, "title_snapshot": "Untrusted title",
    }]})
    bound = await _snapshot_resource_titles(db, org, terms)
    assert "OFFER_COURSE_RELEASE_REQUIRED" not in bound.completion_blockers()
    bound_snapshot, _ = _terms_snapshot(bound, "course")
    assert bound_snapshot["resources"] == [{
        "kind": "course", "resource_uuid": course_uuid,
        "release_uuid": release.uuid, "release_sha256": release.snapshot_sha256,
        "title_snapshot": release.snapshot["course"]["name"],
    }]
    current_at = datetime.now(UTC)
    assert await _snapshot_resource_titles(
        db, org, terms, require_current_release_policies_at=current_at,
    ) == bound
    current_binding = await load_release_policy_binding(db, org, current_at)
    with monkeypatch.context() as patch:
        patch.setattr(offers, "load_release_policy_binding", AsyncMock(
            return_value=current_binding.model_copy(update={"retention_category": "synthetic_updated"}),
        ))
        with pytest.raises(HTTPException) as stale_policy:
            await _snapshot_resource_titles(
                db, org, terms, require_current_release_policies_at=current_at,
            )
        assert stale_policy.value.detail["code"] == "OFFER_COURSE_RELEASE_POLICY_CHANGED"
    terms.resources[0].release_uuid = "unknown_release"
    with pytest.raises(HTTPException) as missing_release:
        await _snapshot_resource_titles(db, org, terms)
    assert missing_release.value.status_code == 404
    terms.resources[0].release_uuid = None
    unbound = await _snapshot_resource_titles(db, org, terms)
    assert "release_sha256" not in _terms_snapshot(unbound, "course")[0]["resources"][0]
    assert "OFFER_COURSE_RELEASE_REQUIRED" in unbound.completion_blockers()
    duplicate_course = bound.model_copy(update={"resources": bound.resources * 2})
    assert "OFFER_COURSE_RESOURCE_DUPLICATE" in duplicate_course.completion_blockers()
    checks = (
        (ensure_adaptive_course_deletable, (decision.org_id, course_uuid)),
        (ensure_adaptive_org_deletable, (decision.org_id,)),
        (ensure_adaptive_user_deletable, (assigner_id,)),
        (ensure_adaptive_user_deletable, (actor_id,)),
    )
    for check, args in checks:
        with pytest.raises(HTTPException) as protected:
            await check(db, *args)
        assert protected.value.status_code == 409
        assert protected.value.detail["code"] == "COURSE_REVIEW_RETENTION_REQUIRED"


async def _seed_postgres_release_sources(
    db,
    org,
    reviewer_id,
    token,
    media_manifest=None,
    snapshot=None,
    *,
    course_uuid="course_synthetic",
    policy_versions=None,
    policy_version_ids=None,
    revision_sha256=None,
):
    owner = User(
        username=f"review-owner-{token}",
        user_uuid=f"user_owner_{token}",
        email=f"owner-{token}@example.invalid",
        first_name="Synthetic",
        last_name="Owner",
    )
    db.add(owner)
    await db.flush()
    evidence_policy_ids = policy_version_ids or [1]
    evidence_revision_sha256 = revision_sha256 or ("a" * 64)
    assignment = StudyOwnerAssignment(
        org_id=org.id, user_id=reviewer_id, assigned_by_user_id=owner.id,
        responsibility=StudyOwnerFunction.EDUCATION.value, scope_uuid=org.org_uuid,
        evidence_reference="SYNTHETIC", effective_from=datetime.now(UTC),
    )
    db.add(assignment)
    await db.flush()
    attachments = [
        StudyEvidenceFile(
            evidence_uuid=f"ev_{uuid4()}", org_id=org.id, purpose=purpose,
            course_uuid=course_uuid, revision_sha256=evidence_revision_sha256,
            storage_reference=f"synthetic/{purpose}", content_sha256="b" * 64,
            media_type="application/pdf", size_bytes=1,
            policy_version_ids=evidence_policy_ids,
            retention_category="synthetic", upload_status="ready", uploaded_by_user_id=owner.id,
        )
        for purpose in ("content_rights", "content_accessibility")
    ]
    db.add_all(attachments)
    await db.flush()
    snapshot = snapshot if snapshot is not None else {"fixture": "original"}
    digest = canonical_request_hash(snapshot)
    decision = StudyCourseReview(
        org_id=org.id, course_uuid=course_uuid, revision_sha256=digest,
        owner_user_id=owner.id, reviewer_user_id=reviewer_id,
        reviewer_assignment_id=assignment.id,
        rights_evidence_id=attachments[0].id, accessibility_evidence_id=attachments[1].id,
        policy_version_ids=evidence_policy_ids, decision="approved",
        accessibility_confirmed=True, assessment_confirmed=True,
        media_manifest=media_manifest,
    )
    db.add(decision)
    await db.flush()
    policies = policy_versions
    if policies is None:
        policies = [
            StudyPolicyVersion(
                org_id=org.id, policy_key=key, scope_uuid=org.org_uuid,
                evidence_reference="SYNTHETIC", effective_from=datetime.now(UTC),
                created_by_user_id=owner.id,
                status=StudyPolicyStatus.APPROVED.value,
            )
            for key in ("D-08", "D-09", "D-13", "D-16")
        ]
        db.add_all(policies)
        await db.flush()
    return decision, snapshot, policies


@pytest.mark.studytomy_postgres
async def test_postgres_grant_first_use_serializes_and_preserves_independent_sources(postgres_session_factory):
    token = uuid4().hex
    now = datetime.now(UTC)
    org = Organization(
        name="Synthetic grants", slug=f"grants-{token}", org_uuid=f"org_{token}",
        email=f"grants-{token}@example.invalid",
    )
    user = User(
        username=f"learner-{token}", user_uuid=f"user_{token}",
        email=f"learner-{token}@example.invalid", first_name="Synthetic", last_name="Learner",
    )
    async with postgres_session_factory() as db:
        db.add_all([org, user])
        await db.flush()
        profile = StudyLearnerProfile(org_id=org.id, user_id=user.id, created_by_user_id=user.id)
        review, snapshot, policies = await _seed_postgres_release_sources(db, org, user.id, token)
        release = StudyCourseRelease(
            org_id=org.id, course_uuid=review.course_uuid, review_id=review.id,
            snapshot_sha256=canonical_request_hash(snapshot), snapshot=snapshot, media_manifest={},
            assessment_policy_id=policies[0].id, progression_policy_id=policies[1].id,
            retention_policy_id=policies[2].id, rights_policy_id=policies[3].id,
            retention_category="synthetic",
        )
        db.add_all([profile, release])
        await db.flush()
        scope = {
            "org_id": org.id, "learner_profile_id": profile.id,
            "course_uuid": release.course_uuid, "release_id": release.id,
        }
        pending = StudyCourseAccessGrant(
            **scope, source_kind="order_line", source_uuid=f"paid-{token}",
            start_basis="after_first_access", duration_days=2, created_at=now,
        )
        expired = StudyCourseAccessGrant(
            **scope, source_kind="provisional", source_uuid=f"provisional-{token}",
            start_basis="fixed_period", starts_at=now-timedelta(days=1),
            ends_at=now, created_at=now-timedelta(days=1),
        )
        await persist_authorized_course_grant(db, pending)
        db.add(expired)
        await db.commit()

    async with postgres_session_factory() as first, postgres_session_factory() as second:
        assert await resolve_active_course_grants(first, **scope, now=now) == []
        grants = await resolve_active_course_grants(first, **scope, now=now, claim_first_use=True)
        assert [grant.id for grant in grants] == [pending.id]
        rival = asyncio.create_task(resolve_active_course_grants(
            second, **scope, now=now+timedelta(minutes=1), claim_first_use=True,
        ))
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(rival), timeout=0.2)
            await first.commit()
            replay = await asyncio.wait_for(rival, timeout=5)
            assert [(row.id, row.starts_at, row.ends_at) for row in replay] == [
                (pending.id, now, now + timedelta(days=2)),
            ]
        finally:
            if not rival.done():
                rival.cancel()
                await asyncio.gather(rival, return_exceptions=True)
        await second.commit()
        candidate = StudyCourseAccessGrant(
            **scope, source_kind="order_line", source_uuid=pending.source_uuid,
            start_basis="after_first_access", duration_days=2,
        )
        duplicate = await persist_authorized_course_grant(second, candidate)
        assert (duplicate.id, duplicate.starts_at, duplicate.ends_at) == (
            pending.id, now, now + timedelta(days=2),
        )
        candidate.duration_days = 3
        with pytest.raises(HTTPException) as conflict:
            await persist_authorized_course_grant(second, candidate)
        assert conflict.value.status_code == 409
        assert conflict.value.detail["code"] == "COURSE_GRANT_SOURCE_CONFLICT"
        assert await resolve_active_course_grants(second, **scope, now=now+timedelta(days=2)) == []
        for statement, message in (
            (update(StudyCourseAccessGrant).values(source_uuid="rewritten"), "provenance is immutable"),
            (update(StudyCourseAccessGrant).values(ends_at=now+timedelta(days=3)), "window cannot be rewritten"),
            (delete(StudyCourseAccessGrant), "history cannot be deleted"),
        ):
            with pytest.raises(DBAPIError, match=message):
                async with second.begin_nested():
                    await second.execute(statement.where(StudyCourseAccessGrant.id == pending.id))
        free = StudyCourseAccessGrant(
            **scope, source_kind="free", source_uuid=f"free-{token}",
            start_basis="fixed_period", starts_at=now+timedelta(minutes=1),
            ends_at=now+timedelta(hours=1), created_at=now+timedelta(minutes=1),
        )
        await persist_authorized_course_grant(second, free)
        assert [grant.id for grant in await resolve_active_course_grants(
            second, **scope, now=now+timedelta(minutes=1),
        )] == [pending.id, free.id]
        await second.execute(
            update(StudyCourseAccessGrant).where(StudyCourseAccessGrant.id == pending.id)
            .values(revoked_at=now+timedelta(minutes=2))
        )
        await second.commit()
        assert [grant.id for grant in await resolve_active_course_grants(
            first, **scope, now=now+timedelta(minutes=3),
        )] == [free.id]
        assert await resolve_active_course_grants(first, **scope, now=now+timedelta(hours=1)) == []
        with pytest.raises(DBAPIError, match="revocation cannot be rewritten"):
            async with second.begin_nested():
                await second.execute(
                    update(StudyCourseAccessGrant).where(StudyCourseAccessGrant.id == pending.id)
                    .values(revoked_at=None)
                )


@pytest.mark.studytomy_postgres
async def test_postgres_parallel_designations_record_one_event(
    postgres_session_factory, monkeypatch,
):
    token = uuid4().hex
    org = Organization(
        name="Synthetic designation", slug=f"designation-{token}",
        org_uuid=f"org_{token}", email=f"designation-{token}@example.invalid",
    )
    reviewer = User(
        username=f"reviewer-{token}", user_uuid=f"user_{token}",
        email=f"reviewer-{token}@example.invalid", first_name="Synthetic", last_name="Reviewer",
    )
    course_uuid = f"course_{token}"
    async with postgres_session_factory() as seed:
        seed.add_all([org, reviewer])
        await seed.flush()
        course = Course(
            org_id=org.id, course_uuid=course_uuid, name="Synthetic draft",
            public=False, published=False, open_to_contributors=False,
        )
        seed.add(course)
        await seed.commit()
    actor = PublicUser.model_validate(reviewer, from_attributes=True)

    async def scope(db, organization, current_user, requested_uuid):
        assert (organization.id, current_user.id, requested_uuid) == (
            org.id, reviewer.id, course_uuid,
        )
        return await db.get(Course, course.id), []

    monkeypatch.setattr(publication, "_course_scope", scope)
    app = FastAPI()
    app.include_router(router)

    async def session():
        async with postgres_session_factory() as db:
            yield db

    app.dependency_overrides[get_db_session] = session
    app.dependency_overrides[get_authenticated_non_api_token_user] = lambda: actor
    url = f"/orgs/{org.org_uuid}/courses/{course_uuid}/managed-draft"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        responses = await asyncio.wait_for(
            asyncio.gather(client.put(url), client.put(url)), timeout=10
        )
    assert [response.status_code for response in responses] == [204, 204]
    async with postgres_session_factory() as check:
        saved_course = await check.get(Course, course.id)
        assert saved_course.studytomy_managed and not saved_course.public and not saved_course.published
        event = (await check.execute(select(StudyBusinessEvent).where(
            StudyBusinessEvent.org_id == org.id,
            StudyBusinessEvent.resource_uuid == course_uuid,
        ))).scalar_one()
        assert event.action == "studytomy.course.managed_designated"
        assert event.actor_user_id == reviewer.id


@pytest.mark.studytomy_postgres
async def test_postgres_parallel_release_requests_retain_one_copy(
    postgres_session_factory, tmp_path, monkeypatch,
):
    token = uuid4().hex
    org = Organization(
        name="Synthetic release organization", slug=f"release-{token}",
        org_uuid=f"org_{token}", email=f"release-{token}@example.invalid",
    )
    reviewer = User(
        username=f"reviewer-{token}", user_uuid=f"user_reviewer_{token}",
        email=f"reviewer-{token}@example.invalid", first_name="Synthetic", last_name="Reviewer",
    )
    reference = "courses/course_synthetic/thumbnails/fixture.png"
    manifest = {reference: {"sha256": hashlib.sha256(PDF).hexdigest(), "size_bytes": len(PDF)}}
    async with postgres_session_factory() as seed:
        seed.add_all([org, reviewer])
        await seed.flush()
        review, snapshot, policies = await _seed_postgres_release_sources(
            seed, org, reviewer.id, token, manifest
        )
        await seed.commit()
    binding = ReleasePolicyBinding(versions={
        StudyPolicyKey(policy.policy_key): ReleasePolicyVersion(
            policy_id=policy.id, policy_uuid=policy.policy_uuid,
            version=policy.version, body_sha256=canonical_request_hash(policy.body),
        )
        for policy in policies
    }, retention_category="synthetic")
    monkeypatch.chdir(tmp_path)
    config = SimpleNamespace(hosting_config=SimpleNamespace(
        content_delivery=SimpleNamespace(type="filesystem"),
    ))
    monkeypatch.setattr(upload_content, "get_learnhouse_config", lambda: config)
    root = tmp_path / "content" / "orgs" / org.org_uuid
    path = root / reference
    path.parent.mkdir(parents=True)
    path.write_bytes(PDF)
    monkeypatch.setattr(publication, "_course_scope", AsyncMock(
        return_value=(SimpleNamespace(course_uuid=review.course_uuid), [])
    ))
    checked = AsyncMock(return_value=(review, snapshot, binding))
    monkeypatch.setattr(publication, "_checked_course_release", checked)
    copy_started = asyncio.Event()
    finish_copy = asyncio.Event()

    async def paused_copy(org_uuid, approved_manifest):
        copy_started.set()
        await finish_copy.wait()
        return await retain_reviewed_release_media(org_uuid, approved_manifest)

    monkeypatch.setattr(publication, "retain_reviewed_release_media", paused_copy)
    actor = PublicUser.model_validate(reviewer)
    args = (org, actor, review.course_uuid, review.uuid)
    async with postgres_session_factory() as first, postgres_session_factory() as second:
        first_pid = (await first.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        second_pid = (await second.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        async with asyncio.timeout(15), asyncio.TaskGroup() as tasks:
            winner = tasks.create_task(publication.record_course_release(first, *args))
            await copy_started.wait()
            retry = tasks.create_task(publication.record_course_release(second, *args))
            async with postgres_session_factory() as observer:
                while first_pid not in (await observer.execute(
                    text("SELECT pg_blocking_pids(:pid)"), {"pid": second_pid}
                )).scalar_one():
                    await asyncio.sleep(0.01)
            finish_copy.set()
    assert winner.result() == retry.result()
    checked.assert_awaited_once()
    assert [file.read_bytes() for file in (root / "studytomy" / "releases").rglob("*.bin")] == [PDF]
    async with postgres_session_factory() as db:
        release = (await db.execute(select(StudyCourseRelease).where(
            StudyCourseRelease.org_id == org.id
        ))).scalar_one()
        event = (await db.execute(select(StudyBusinessEvent).where(
            StudyBusinessEvent.org_id == org.id,
            StudyBusinessEvent.action == "studytomy.course_release.recorded",
        ))).scalar_one()
    assert release.uuid == event.resource_uuid == winner.result().uuid
    assert release.policy_binding == binding.model_dump(mode="json")


@pytest.mark.studytomy_postgres
async def test_postgres_course_review_and_release_history_reject_mutation(postgres_test_rows):
    rows = postgres_test_rows
    async with rows.session_factory() as db:
        org = await db.get(Organization, rows.org_id)
        decision, snapshot, policies = await _seed_postgres_release_sources(db, org, rows.user_id, rows.token)
        digest = decision.revision_sha256
        scope = StudyCourseReview.id == decision.id
        for mutation in (
            update(StudyCourseReview).where(scope).values(decision="changes_requested"),
            delete(StudyCourseReview).where(scope),
        ):
            with pytest.raises(DBAPIError, match="Study course reviews are immutable"):
                async with db.begin_nested():
                    await db.execute(mutation)
        assert (await db.execute(select(StudyCourseReview.decision).where(scope))).scalar_one() == "approved"
        release = StudyCourseRelease(
            org_id=org.id, course_uuid=decision.course_uuid, review_id=decision.id,
            snapshot_sha256=digest, snapshot=snapshot, media_manifest={},
            assessment_policy_id=policies[0].id, progression_policy_id=policies[1].id,
            retention_policy_id=policies[2].id, rights_policy_id=policies[3].id,
            retention_category="synthetic",
        )
        refused_review = StudyCourseReview.model_validate({
            **decision.model_dump(exclude={"id", "uuid"}),
            "decision": "changes_requested",
        })
        db.add(refused_review)
        await db.flush()
        for override in (
            {"course_uuid": "course_other"},
            {"snapshot_sha256": "0" * 64},
            {"review_id": refused_review.id},
            {"assessment_policy_id": policies[1].id},
        ):
            with pytest.raises(DBAPIError, match="Study course release references are invalid"):
                async with db.begin_nested():
                    await db.execute(insert(StudyCourseRelease).values(
                        **(release.model_dump(exclude={"id"}) | override)
                    ))
        db.add(release)
        await db.flush()
        release_scope = StudyCourseRelease.id == release.id
        for mutation in (
            update(StudyCourseRelease).where(release_scope).values(snapshot={"fixture": "changed"}),
            delete(StudyCourseRelease).where(release_scope),
        ):
            with pytest.raises(DBAPIError, match="Study course releases are immutable"):
                async with db.begin_nested():
                    await db.execute(mutation)
        assert (await db.execute(
            select(StudyCourseRelease.snapshot).where(release_scope)
        )).scalar_one() == snapshot
        await db.rollback()


@pytest.mark.studytomy_postgres
async def test_postgres_managed_course_rejects_native_publication(postgres_test_rows):
    rows = postgres_test_rows
    async with rows.session_factory() as db:
        course = Course(
            name="Synthetic private course", org_id=rows.org_id,
            course_uuid=f"course_{rows.token}", public=False, published=False,
            open_to_contributors=False, studytomy_managed=True,
        )
        db.add(course)
        await db.flush()
        scope = Course.id == course.id
        for field in ("public", "published"):
            with pytest.raises(IntegrityError, match="ck_course_studytomy_managed_private"):
                async with db.begin_nested():
                    await db.execute(update(Course).where(scope).values(**{field: True}))
        course.name = "Edited private course"
        await db.flush()
        assert not course.public and not course.published
        await db.rollback()


@pytest.mark.studytomy_postgres
async def test_postgres_rejects_unscoped_or_mixed_course_evidence(postgres_test_rows):
    rows = postgres_test_rows
    async with rows.session_factory() as db:
        for purpose, course_uuid, revision in (
            ("content_rights", None, "a" * 64),
            ("content_accessibility", "course_synthetic", None),
            ("eligibility_evidence", "course_synthetic", "a" * 64),
        ):
            with pytest.raises(IntegrityError, match="ck_st_evidence_file_scope"):
                async with db.begin_nested():
                    db.add(StudyEvidenceFile(
                        evidence_uuid=f"ev_{rows.token}", org_id=rows.org_id, purpose=purpose,
                        course_uuid=course_uuid, revision_sha256=revision, storage_reference="synthetic",
                        content_sha256="b" * 64, media_type="application/pdf", size_bytes=1,
                        retention_category="synthetic", upload_status="ready", uploaded_by_user_id=rows.user_id,
                    ))
                    await db.flush()
        await db.rollback()
