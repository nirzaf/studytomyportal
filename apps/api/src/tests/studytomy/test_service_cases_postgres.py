"""PostgreSQL constraints for confidential service case metadata."""

import asyncio
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from src.core.events.database import get_db_session
from src.db.organizations import Organization
from src.db.roles import Role, RoleTypeEnum
from src.db.studytomy.cases import StudyGuestSupportChallenge, StudyServiceCase, StudyServiceCaseExclusion
from src.db.studytomy.governance import (
    POLICY_REQUIRED_DECISION_AREAS, StudyBusinessEvent, StudyOwnerAssignment,
    StudyPolicyApproval, StudyPolicyKey, StudyPolicyVersion, StudyStaffScope,
)
from src.db.studytomy.operations import StudyEvidenceFile
from src.db.user_organizations import UserOrganization
from src.db.users import PublicUser, User
from src.router import v1_router
from src.security import auth
from src.services.studytomy import cases, evidence_files
from src.services.studytomy.guest_support import (
    purge_expired_guest_support_challenges, redeem_guest_support_challenge,
)
from src.services.studytomy.metrics import current_support_backlog, support_resolution_activity
from src.services.studytomy.policies import POLICY_REQUIRED_RESPONSIBILITIES
from src.services.studytomy.retention import CaseEvidenceHoldCommand, CaseEvidenceHoldReleaseCommand
from src.services.studytomy.safeguarding_retention import (
    place_safeguarding_hold, release_safeguarding_hold, safeguarding_retention_inventory,
)


@pytest.fixture(autouse=True)
def enable_synthetic_reporting(monkeypatch):
    monkeypatch.setenv("LEARNHOUSE_STUDYTOMY_SAFEGUARDING_REPORTS_ENABLED", "true")


@pytest.mark.studytomy_postgres
async def test_support_backlog_excludes_closed_cases_and_other_scopes(postgres_session_factory):
    tag = uuid4().hex
    async with postgres_session_factory() as db:
        orgs = [
            Organization(
                name="Synthetic support", slug=f"backlog-{tag}-{index}",
                org_uuid=f"org_{uuid4()}", email=f"backlog-{tag}-{index}@example.invalid",
            )
            for index in range(3)
        ]
        reporter, owner = [
            User(
                username=f"support-{tag}-{role}", user_uuid=f"user_{uuid4()}",
                email=f"support-{tag}-{role}@example.invalid",
                first_name="Synthetic", last_name=role,
            )
            for role in ("reporter", "owner")
        ]
        db.add_all([*orgs, reporter, owner])
        await db.flush()
        first, other, empty = orgs
        now = datetime.now(UTC)
        oldest_open = now - timedelta(hours=2)
        db.add_all([
            StudyServiceCase(
                org_id=org_id, reporter_user_id=reporter.id, category=category,
                status=status, created_at=created_at,
                owner_user_id=None if status == "acknowledged" else owner.id,
            )
            for org_id, category, status, created_at in (
                (first.id, "support", "acknowledged", oldest_open),
                (first.id, "support", "under_review", now),
                (first.id, "support", "resolved", now - timedelta(days=3)),
                (first.id, "support", "closed", now - timedelta(days=4)),
                (first.id, "safeguarding", "acknowledged", now - timedelta(days=5)),
                (other.id, "support", "acknowledged", now - timedelta(days=6)),
            )
        ])
        await db.flush()
        aggregate = await current_support_backlog(db, org_id=first.id)
        assert aggregate["unresolved_cases"] == 2
        assert aggregate["oldest_unresolved_age_seconds"] == int(
            (aggregate["observed_at"] - oldest_open).total_seconds()
        )
        empty_aggregate = await current_support_backlog(db, org_id=empty.id)
        assert empty_aggregate["unresolved_cases"] == 0
        assert empty_aggregate["oldest_unresolved_age_seconds"] is None
        assert await support_resolution_activity(
            db, org_id=first.id, start=now - timedelta(days=7), end=now + timedelta(seconds=1),
        ) == {
            "first_resolved_cases": 0, "first_resolution_elapsed_seconds": 0,
            "reopened_transitions": 0, "reopened_cases": 0,
        }


@pytest.mark.studytomy_postgres
async def test_guest_support_challenge_has_tenant_unique_token_and_bounded_consumption(postgres_session_factory):
    tag = uuid4().hex
    now = datetime.now(UTC)
    async with postgres_session_factory() as db:
        organizations = [
            Organization(name="Synthetic support", slug=f"guest-{tag}-{index}",
                         org_uuid=f"org_{uuid4()}", email=f"guest-{tag}-{index}@example.invalid")
            for index in range(2)
        ]
        db.add_all(organizations)
        await db.flush()
        values = {
            "org_id": organizations[0].id, "token_digest": f"{tag}{tag}",
            "contact_ciphertext": "synthetic fixture ciphertext", "topic": "Orders",
            "created_at": now, "expires_at": now + timedelta(minutes=10),
        }
        db.add_all([
            StudyGuestSupportChallenge(**values),
            StudyGuestSupportChallenge(**(values | {"org_id": organizations[1].id})),
        ])
        await db.flush()
        for changes, constraint in (
            ({}, "uq_st_guest_support_challenge_org_token"),
            ({"token_digest": "raw"}, "ck_st_guest_support_challenge_digest"),
            ({"token_digest": "a" * 64, "expires_at": now}, "ck_st_guest_support_challenge_window"),
            (
                {"token_digest": "b" * 64, "consumed_at": values["expires_at"]},
                "ck_st_guest_support_challenge_window",
            ),
        ):
            with pytest.raises(IntegrityError, match=constraint):
                async with db.begin_nested():
                    db.add(StudyGuestSupportChallenge(**(values | changes)))
                    await db.flush()
        await db.rollback()


@pytest.mark.studytomy_postgres
async def test_guest_support_purge_is_bounded_tenant_scoped_and_skips_locked_rows(postgres_session_factory):
    now = datetime.now(UTC)
    async with postgres_session_factory() as db:
        organizations = [
            Organization(name="Synthetic purge", slug=f"guest-purge-{uuid4().hex}",
                         org_uuid=f"org_{uuid4()}", email=f"guest-purge-{uuid4().hex}@example.invalid")
            for _ in range(2)
        ]
        db.add_all(organizations)
        await db.flush()
        org_id, other_org_id = (org.id for org in organizations)
        rows = [
            StudyGuestSupportChallenge(
                org_id=scope, token_digest=uuid4().hex + uuid4().hex,
                contact_ciphertext="synthetic fixture ciphertext", topic="Orders",
                created_at=now - timedelta(minutes=15), expires_at=expiry,
            )
            for scope, expiry in (
                (org_id, now - timedelta(minutes=1)), (org_id, now),
                (org_id, now + timedelta(minutes=1)), (other_org_id, now),
            )
        ]
        db.add_all(rows)
        await db.flush()
        earliest_id, boundary_id, live_id, other_id = (row.id for row in rows)
        await db.commit()

    async with postgres_session_factory() as first, postgres_session_factory() as second:
        assert await purge_expired_guest_support_challenges(first, org_id=org_id, limit=1, now=now) == 1
        assert await purge_expired_guest_support_challenges(first, org_id=org_id, limit=10, now=now) == 2
        assert await purge_expired_guest_support_challenges(
            first, org_id=org_id, limit=1, now=now, execute=True,
        ) == 1
        assert await purge_expired_guest_support_challenges(
            second, org_id=org_id, limit=10, now=now, execute=True,
        ) == 1
        await second.commit()
        await first.rollback()
        assert await first.get(StudyGuestSupportChallenge, boundary_id) is None
        for row_id in (earliest_id, live_id, other_id):
            assert await first.get(StudyGuestSupportChallenge, row_id) is not None
        assert await purge_expired_guest_support_challenges(
            first, org_id=org_id, limit=10, now=now, execute=True,
        ) == 1
        await first.commit()


@pytest.mark.studytomy_postgres
async def test_case_scope_and_accountable_owner_constraints(postgres_session_factory, monkeypatch):
    tag = uuid4().hex
    async with postgres_session_factory() as db:
        organizations = [
            Organization(
                name="Synthetic case organization",
                slug=f"case-{tenant}-{tag}",
                org_uuid=f"org_{tenant}_{tag}",
                email=f"{tenant}-{tag}@example.invalid",
            )
            for tenant in ("first", "second")
        ]
        reporter = User(
            username=f"case_reporter_{tag}",
            user_uuid=f"user_case_reporter_{tag}",
            first_name="Synthetic",
            last_name="Reporter",
            email=f"reporter-{tag}@example.invalid",
        )
        excluded_admin = User(
            username=f"case_admin_{tag}",
            user_uuid=f"user_case_admin_{tag}",
            first_name="Synthetic",
            last_name="Administrator",
            email=f"admin-{tag}@example.invalid",
            is_superadmin=True,
        )
        reviewers = [
            User(
                username=f"reviewer_{index}_{tag}",
                user_uuid=f"user_reviewer_{index}_{tag}",
                first_name="Synthetic", last_name="Reviewer",
                email=f"reviewer-{index}-{tag}@example.invalid",
            )
            for index in range(2)
        ]
        db.add_all([*organizations, reporter, excluded_admin, *reviewers])
        await db.flush()
        first, other = organizations
        case = StudyServiceCase(
            org_id=first.id, reporter_user_id=reporter.id, category="safeguarding"
        )
        db.add(case)
        await db.flush()

        def make_evidence(org_id, purpose):
            return StudyEvidenceFile(
                org_id=org_id, evidence_uuid=f"ev_{uuid4()}", safeguarding_case_uuid=case.case_uuid,
                purpose=purpose, storage_reference="synthetic-private-object", content_sha256="a" * 64,
                media_type="application/pdf", size_bytes=100, retention_category="synthetic",
                upload_status="ready", uploaded_by_user_id=reporter.id,
            )

        stored_evidence = make_evidence(first.id, "safeguarding_case_evidence")
        filename = f"{stored_evidence.evidence_uuid}.pdf"
        stored_evidence.storage_reference = f"content/orgs/{first.org_uuid}/studytomy/evidence/{filename}"
        db.add(stored_evidence)
        await db.flush()
        for org_id, purpose, constraint in (
            (other.id, "safeguarding_case_evidence", "fk_st_evidence_file_safeguarding_case"),
            (first.id, "eligibility_evidence", "ck_st_evidence_file_scope"),
        ):
            with pytest.raises(IntegrityError, match=constraint):
                async with db.begin_nested():
                    db.add(make_evidence(org_id, purpose))
                    await db.flush()

        with pytest.raises(IntegrityError, match="fk_st_service_case_exclusion_org_case"):
            async with db.begin_nested():
                db.add(StudyServiceCaseExclusion(
                    org_id=other.id, case_uuid=case.case_uuid, user_id=reporter.id,
                ))
                await db.flush()

        for owner_id, constraint in (
            (None, "ck_st_service_case_state_owner"),
            (reporter.id, "ck_st_service_case_owner_not_reporter"),
        ):
            with pytest.raises(IntegrityError, match=constraint):
                async with db.begin_nested():
                    db.add(StudyServiceCase(
                        org_id=first.id,
                        reporter_user_id=reporter.id,
                        owner_user_id=owner_id,
                        category="safeguarding",
                        status="assigned",
                    ))
                    await db.flush()

        db.add(StudyServiceCaseExclusion(
            org_id=first.id, case_uuid=case.case_uuid, user_id=excluded_admin.id,
        ))
        await db.flush()

        decisions = []
        evaluate = cases.evaluate_studytomy_staff_access

        async def record_decision(*args, **kwargs):
            decision = await evaluate(*args, **kwargs)
            decisions.append(decision.code)
            return decision

        monkeypatch.setattr(cases, "evaluate_studytomy_staff_access", record_decision)
        reference = UUID(case.case_uuid)
        current_user = PublicUser.model_validate(excluded_admin)
        for org, actor in ((first, current_user), (first, PublicUser.model_validate(reporter))):
            with pytest.raises(HTTPException) as denied:
                await cases.require_safeguarding_case_review(db, org, actor, reference)
            assert denied.value.status_code == 404
            assert denied.value.detail == "Case not found."

        with pytest.raises(HTTPException) as cross_tenant:
            await cases.require_safeguarding_case_review(db, other, current_user, reference)
        assert cross_tenant.value.status_code == 404
        assert cross_tenant.value.detail == "Case not found."
        assert decisions == ["CONFLICT_OF_INTEREST_EXCLUDED"] * 2

        # Exercise actual assignment and audit writes with a synthetic staff decision.
        eligible_ids = {reviewer.id for reviewer in reviewers}

        real_policy_gate = cases.require_safeguarding_policies

        async def fixture_review_policies(*args):
            return []

        monkeypatch.setattr(cases, "require_safeguarding_policies", fixture_review_policies)
        async def allow_fixture_reviewers(db, actor, org, capability, **kwargs):
            if actor.id in eligible_ids:
                return cases.StudytomyAccessDecision(
                    allowed=True, code="ALLOWED", public_message="Synthetic",
                    policy_version_ids=await cases.require_safeguarding_policies(db, org),
                )
            return await evaluate(db, actor, org, capability, **kwargs)

        monkeypatch.setattr(cases, "evaluate_studytomy_staff_access", allow_fixture_reviewers)
        owner, replacement = (PublicUser.model_validate(reviewer) for reviewer in reviewers)
        await cases.assign_safeguarding_case(db, first, owner, reference, expected_version=1)
        assert (case.status, case.owner_user_id, case.version) == ("assigned", owner.id, 2)

        with pytest.raises(HTTPException) as transfer_to_excluded:
            await cases.assign_safeguarding_case(
                db, first, owner, reference, expected_version=2, next_owner_uuid=current_user.user_uuid,
            )
        assert transfer_to_excluded.value.status_code == 404

        with pytest.raises(HTTPException) as stale:
            await cases.assign_safeguarding_case(
                db, first, owner, reference, expected_version=1, next_owner_uuid=replacement.user_uuid,
            )
        assert stale.value.status_code == 409
        await cases.assign_safeguarding_case(
            db, first, owner, reference, expected_version=2, next_owner_uuid=replacement.user_uuid,
        )
        assert (case.owner_user_id, case.version) == (replacement.id, 3)
        events = (await db.exec(
            select(StudyBusinessEvent)
            .where(StudyBusinessEvent.org_id == first.id, StudyBusinessEvent.resource_uuid == case.case_uuid)
            .order_by(StudyBusinessEvent.id)
        )).all()
        assert [event.action for event in events] == ["studytomy.safeguarding.assign_case"] * 2
        assert events[0].before_state["case_owner_user_id"] is None
        assert events[1].before_state["case_owner_user_id"] == owner.id
        assert events[1].after_state["case_owner_user_id"] == replacement.id

        status = cases.SafeguardingReviewStatus
        with pytest.raises(HTTPException) as non_owner:
            await cases.transition_safeguarding_case(
                db, first, owner, reference, expected_version=3, next_status=status.UNDER_REVIEW,
            )
        assert non_owner.value.status_code == 403
        with pytest.raises(HTTPException) as skipped_review:
            await cases.transition_safeguarding_case(
                db, first, replacement, reference, expected_version=3, next_status=status.RESOLVED,
            )
        assert skipped_review.value.status_code == 409

        for version, next_status in enumerate(
            (status.AWAITING_INFORMATION, status.UNDER_REVIEW, status.RESOLVED, status.CLOSED),
            start=3,
        ):
            await cases.transition_safeguarding_case(
                db, first, replacement, reference, expected_version=version, next_status=next_status,
            )
        assert (case.status, case.owner_user_id, case.version) == ("closed", replacement.id, 7)
        transitions = (await db.exec(
            select(StudyBusinessEvent).where(
                StudyBusinessEvent.org_id == first.id,
                StudyBusinessEvent.resource_uuid == case.case_uuid,
                StudyBusinessEvent.action == "studytomy.safeguarding.transition_case",
            ).order_by(StudyBusinessEvent.id)
        )).all()
        assert [event.after_state["status"] for event in transitions] == [
            "awaiting_information", "under_review", "resolved", "closed",
        ]
        assert [event.before_state["version"] for event in transitions] == [3, 4, 5, 6]

        def file_policy(key, version, area, value):
            return SimpleNamespace(id=version, policy_key=key.value, body={"decision_items": {
                item: {
                    "state": "specified", "value": value if item == area else {"synthetic": True},
                    "rationale": "synthetic", "evidence_reference": "synthetic",
                }
                for item in POLICY_REQUIRED_DECISION_AREAS[key]
            }})

        d13 = file_policy(StudyPolicyKey.D_13, 104, "retention", {
            "safeguarding_evidence_retention_category": "synthetic",
        })
        d15 = file_policy(StudyPolicyKey.D_15, 105, "supported_formats", {
            "max_bytes": 100, "media_types": ["application/pdf"],
        })

        async def synthetic_file_policies(db, org, keys, now):
            return [policy for policy in (d13, d15) if StudyPolicyKey(policy.policy_key) in keys]

        monkeypatch.setattr(cases, "_current_policies", synthetic_file_policies)
        stored_evidence.policy_version_ids = [d15.id]
        await db.flush()
        uploaded_bytes = {}

        async def store_synthetic_bytes(**location):
            uploaded_bytes[location["file_and_format"]] = location["file_binary"].read()

        monkeypatch.setattr(evidence_files, "upload_content", store_synthetic_bytes)
        storage_reads = []

        async def open_synthetic_bytes(**location):
            storage_reads.append(location)

            async def chunks():
                yield uploaded_bytes.get(location["file_and_format"], b"synthetic private contents")

            return chunks()

        monkeypatch.setattr(evidence_files, "open_content_stream", open_synthetic_bytes)
        app = FastAPI()
        app.include_router(v1_router)
        app.dependency_overrides[get_db_session] = lambda: db
        path = f"/api/v1/studytomy/orgs/{first.org_uuid}/safeguarding/cases/{reference}"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            anonymous = await client.get(path)
            assert anonymous.status_code == 401

            async def authenticate_excluded(*args, **kwargs):
                return current_user

            monkeypatch.setattr(auth, "get_authenticated_user", authenticate_excluded)
            denied = await client.get(path)
            assert denied.status_code == 404
            assert denied.json() == {"detail": "Case not found."}
            evidence_path = f"{path}/evidence/{stored_evidence.evidence_uuid}"
            evidence_denied = await client.get(evidence_path)
            assert evidence_denied.status_code == 404
            assert storage_reads == []

            async def authenticate_reviewer(*args, **kwargs):
                return replacement

            monkeypatch.setattr(auth, "get_authenticated_user", authenticate_reviewer)
            detail = await client.get(path)
            assert detail.status_code == 200
            assert detail.headers["cache-control"] == "private, no-store"
            assert detail.json() == {
                "case_uuid": str(reference), "status": "closed",
                "version": 7, "assigned_to_me": True,
            }
            undeclared_field = await client.post(
                f"{path}/transition",
                json={"expected_version": 7, "next_status": "closed", "owner_user_id": 1},
            )
            assert undeclared_field.status_code == 422

            fresh_case = StudyServiceCase(
                org_id=first.id, reporter_user_id=reporter.id, category="safeguarding",
            )
            db.add(fresh_case)
            await db.flush()
            wrong_case_path = evidence_path.replace(str(reference), fresh_case.case_uuid)
            assert (await client.get(wrong_case_path)).status_code == 404
            assert storage_reads == []

            stored_evidence.policy_version_ids = [999]
            await db.flush()
            assert (await client.get(evidence_path)).status_code == 409
            assert storage_reads == []
            stored_evidence.policy_version_ids = [d15.id]
            await db.flush()
            download = await client.get(evidence_path)
            assert download.status_code == 200
            assert download.content == b"synthetic private contents"
            assert download.headers["cache-control"] == "private, no-store"
            assert download.headers["content-disposition"].startswith("attachment;")
            assert storage_reads == [{
                "directory": "studytomy/evidence", "type_of_dir": "orgs",
                "uuid": first.org_uuid, "file_and_format": filename,
            }]

            assignment_path = path.replace(str(reference), fresh_case.case_uuid) + "/assignment"
            claim = await client.post(assignment_path, json={"expected_version": 1})
            assert claim.status_code == 200
            assert claim.json() == {
                "case_uuid": fresh_case.case_uuid, "status": "assigned",
                "version": 2, "assigned_to_me": True,
            }
            assert claim.headers["cache-control"] == "private, no-store"
            stale = await client.post(
                assignment_path,
                json={"expected_version": 1, "next_owner_uuid": owner.user_uuid},
            )
            assert stale.status_code == 409

            upload_path = assignment_path.removesuffix("/assignment") + "/evidence"
            upload_key = {"Idempotency-Key": str(uuid4())}
            pdf = b"%PDF-1.4\n%%EOF\n"
            file = {"file": ("private.pdf", pdf, "application/pdf")}
            attached = await client.post(upload_path, headers=upload_key, files=file)
            assert attached.status_code == 201
            assert (await client.post(upload_path, headers=upload_key, files=file)).json() == attached.json()
            assert len(uploaded_bytes) == 1
            reference_path = upload_path + "/" + attached.json()["evidence_uuid"]
            assert (await client.get(reference_path)).content == pdf

        role = Role(
            id=10_000 + int(tag[:7], 16), name="Synthetic member", org_id=first.id,
            role_uuid=f"role_{tag}", role_type=RoleTypeEnum.TYPE_ORGANIZATION,
            rights={"studytomy": {"safeguarding_case_review": True}},
        )
        db.add(role)
        await db.flush()
        db.add_all([
            UserOrganization(org_id=first.id, user_id=user.id, role_id=role.id,
                             creation_date="", update_date="")
            for user in (reporter, excluded_admin, *reviewers)
        ])
        await db.flush()
        reporter_principal = PublicUser.model_validate(reporter)
        intake = {"request_id": uuid4(), "subject_user_uuids": [excluded_admin.user_uuid]}

        monkeypatch.setattr(cases, "require_safeguarding_policies", real_policy_gate)
        with pytest.raises(HTTPException) as missing_policy:
            await cases.open_safeguarding_case(db, first, reporter_principal, **intake)
        assert missing_policy.value.status_code == 409
        assert missing_policy.value.detail["code"] == "POLICY_NOT_APPROVED"

        async def synthetic_approved_policies(*args):
            return [1, 2, 3, 4]

        monkeypatch.setattr(cases, "require_safeguarding_policies", synthetic_approved_policies)
        with pytest.raises(HTTPException) as nonmember:
            await cases.open_safeguarding_case(db, other, reporter_principal, **intake)
        assert nonmember.value.status_code == 403
        receipt = await cases.open_safeguarding_case(db, first, reporter_principal, **intake)
        assert await cases.open_safeguarding_case(db, first, reporter_principal, **intake) == receipt
        assert receipt["status"] == "acknowledged"
        assert await db.get(
            StudyServiceCaseExclusion, (first.id, receipt["case_uuid"], excluded_admin.id)
        ) is not None
        with pytest.raises(HTTPException) as changed_retry:
            await cases.open_safeguarding_case(
                db, first, reporter_principal, request_id=intake["request_id"], subject_user_uuids=[],
            )
        assert changed_retry.value.status_code == 409

        assert await cases.list_unassigned_safeguarding_cases(db, first, owner) == []
        assignment_refs = {}
        for candidate in (owner, current_user):
            assignment = StudyOwnerAssignment(
                org_id=first.id, user_id=candidate.id, responsibility="safeguarding",
                scope_uuid=first.org_uuid, evidence_reference="synthetic-assignment",
                effective_from=datetime.now(UTC), assigned_by_user_id=reporter.id,
            )
            db.add(assignment)
            await db.flush()
            assignment_refs[candidate.id] = assignment.owner_uuid
            db.add(StudyStaffScope(
                org_id=first.id, assignment_id=assignment.id,
                capability="safeguarding_case_review", scope_kind="case",
                scope_uuid=receipt["case_uuid"], evidence_reference="synthetic-scope",
                created_by_user_id=reporter.id,
            ))
        await db.flush()
        assert await cases.list_unassigned_safeguarding_cases(db, first, current_user) == []
        pending = await cases.list_unassigned_safeguarding_cases(db, first, owner)
        assert [item.case_uuid for item in pending] == [receipt["case_uuid"]]

        policy = StudyPolicyVersion(
            org_id=first.id, policy_key="D-04", scope_kind="organization", scope_uuid=first.org_uuid,
            status="approved", evidence_reference="synthetic-policy", effective_from=datetime.now(UTC),
            created_by_user_id=reporter.id, approved_by_user_id=reporter.id, approved_at=datetime.now(UTC),
            body={"decision_items": {
                area: {
                    "state": "specified", "rationale": "synthetic",
                    "evidence_reference": "synthetic-policy",
                    "value": {"safeguarding": {
                        "lead_owner_assignment_uuid": assignment_refs[current_user.id],
                        "backup_owner_assignment_uuid": assignment_refs[owner.id],
                    }} if area == "staffing" else {"fixture": True},
                }
                for area in POLICY_REQUIRED_DECISION_AREAS[StudyPolicyKey.D_04]
            }},
        )
        policy.body["decision_items"]["support_routes"]["value"] = {
            "public_safeguarding": {
                "contact_instructions": "Synthetic public contact",
                "emergency_guidance": "Synthetic emergency guidance",
                "internal_reference": "must stay private",
            },
        }
        policy.body["decision_items"]["monitored_hours"]["value"] = {
            "public_safeguarding": "Synthetic monitored hours",
        }
        db.add(policy)
        await db.flush()

        monkeypatch.setattr(cases, "require_safeguarding_policies", real_policy_gate)
        with pytest.raises(HTTPException) as missing_named_approval:
            await cases.open_safeguarding_case(db, first, reporter_principal, **intake)
        assert missing_named_approval.value.status_code == 409
        assert missing_named_approval.value.detail["code"] == "POLICY_APPROVAL_EVIDENCE_MISSING"

        for responsibility in ("support", "education"):
            approver = StudyOwnerAssignment(
                org_id=first.id, user_id=reporter.id, responsibility=responsibility,
                scope_uuid=first.org_uuid, evidence_reference="synthetic-approver",
                effective_from=datetime.now(UTC), assigned_by_user_id=reporter.id,
            )
            db.add(approver)
            await db.flush()
            db.add(StudyPolicyApproval(
                org_id=first.id, policy_version_id=policy.id, owner_assignment_id=approver.id,
                responsibility=responsibility, approver_user_id=reporter.id,
                evidence_reference="synthetic-approval", rationale="synthetic", approved_at=datetime.now(UTC),
            ))
        await db.flush()

        async def synthetic_routing_policies(*args):
            return [policy.id]

        monkeypatch.setattr(cases, "require_safeguarding_policies", synthetic_routing_policies)
        routed = await cases.open_safeguarding_case(
            db, first, reporter_principal, request_id=uuid4(),
            subject_user_uuids=[current_user.user_uuid],
        )
        assert routed["status"] == "assigned"
        stored = (await db.exec(select(StudyServiceCase).where(
            StudyServiceCase.org_id == first.id, StudyServiceCase.case_uuid == routed["case_uuid"],
        ))).one()
        assert stored.owner_user_id == owner.id
        grants = (await db.exec(select(StudyStaffScope).where(
            StudyStaffScope.org_id == first.id, StudyStaffScope.scope_uuid == stored.case_uuid,
        ))).all()
        assert len(grants) == 1
        routed_to = await db.get(StudyOwnerAssignment, grants[0].assignment_id)
        assert routed_to.user_id == owner.id

        retention_policy = StudyPolicyVersion(
            org_id=first.id, policy_key="D-13", scope_kind="organization", scope_uuid=first.org_uuid,
            status="approved", evidence_reference="synthetic-retention", effective_from=datetime.now(UTC),
            created_by_user_id=reporter.id, approved_by_user_id=reporter.id, approved_at=datetime.now(UTC),
            body=d13.body,
        )
        db.add(retention_policy)
        await db.flush()

        async def narrative_policies(*args):
            return [policy.id, retention_policy.id]

        async def narrative_retention(*args):
            return [retention_policy]

        async def reviewer_with_retention(db, actor, org, capability, **kwargs):
            decision = await allow_fixture_reviewers(db, actor, org, capability, **kwargs)
            if decision.allowed:
                decision.policy_version_ids = [policy.id, retention_policy.id]
            return decision

        async def authenticate_reporter(*args, **kwargs):
            return reporter_principal

        monkeypatch.setattr(cases, "require_safeguarding_policies", narrative_policies)
        monkeypatch.setattr(cases, "_current_policies", narrative_retention)
        monkeypatch.setattr(cases, "evaluate_studytomy_staff_access", reviewer_with_retention)
        monkeypatch.setattr(auth, "get_authenticated_user", authenticate_reporter)
        reports_path = path.split("/cases/")[0] + "/reports"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            public_path = reports_path.removesuffix("/reports") + "/public-contact"
            readiness_path = public_path.replace("/public-contact", "/reporting-availability")
            assert (await client.get(readiness_path)).json() == {"available": True}
            public = await client.get(public_path)
            assert public.status_code == 200
            assert public.headers["cache-control"] == "no-store"
            assert public.json() == {
                "contact_instructions": "Synthetic public contact",
                "emergency_guidance": "Synthetic emergency guidance",
                "monitored_hours": "Synthetic monitored hours",
            }
            assert (await client.get(public_path.replace(first.org_uuid, other.org_uuid))).status_code == 404
            command = {
                "request_id": str(uuid4()), "narrative": "Synthetic confidential report",
                "subject_user_uuids": [current_user.user_uuid],
            }
            submitted = await client.post(reports_path, json=command)
            assert submitted.status_code == 201
            assert set(submitted.json()) == {"case_uuid", "status", "version"}
            assert (await client.post(reports_path, json=command)).json() == submitted.json()
            unidentified = await client.post(reports_path, json={
                "request_id": str(uuid4()),
                "narrative": "Synthetic report requiring independent triage",
            })
            assert unidentified.status_code == 201
            assert unidentified.json()["status"] == "acknowledged"
            unidentified_grants = (await db.exec(select(StudyStaffScope).where(
                StudyStaffScope.org_id == first.id,
                StudyStaffScope.scope_uuid == unidentified.json()["case_uuid"],
            ))).all()
            assert unidentified_grants == []
            receipt_path = reports_path + "/" + submitted.json()["case_uuid"]
            receipt_status = await client.get(receipt_path)
            assert receipt_status.status_code == 200
            assert receipt_status.headers["cache-control"] == "private, no-store"
            assert receipt_status.json() == {
                "case_uuid": submitted.json()["case_uuid"], "status": submitted.json()["status"],
            }
            assert (await client.get(receipt_path.replace(first.org_uuid, other.org_uuid))).status_code == 404
            narrative_path = path.replace(str(reference), submitted.json()["case_uuid"]) + "/narrative"
            monkeypatch.setattr(auth, "get_authenticated_user", authenticate_excluded)
            assert (await client.get(narrative_path)).status_code == 404
            denied_status = await client.get(receipt_path)
            assert denied_status.status_code == 404
            missing_status = await client.get(reports_path + "/" + str(uuid4()))
            assert missing_status.status_code == 404
            assert denied_status.json() == missing_status.json()
            monkeypatch.setattr(auth, "get_authenticated_user", authenticate_reviewer)
            assert (await client.get(receipt_path)).status_code == 404
            result = await client.get(narrative_path)
            assert result.status_code == 200
            assert result.headers["cache-control"] == "private, no-store"
            assert result.json() == {"narrative": command["narrative"]}

            triage_path = reports_path.removesuffix("/reports") + "/triage"
            queued_uuid = unidentified.json()["case_uuid"]
            queued_narrative = path.replace(str(reference), queued_uuid) + "/narrative"

            async def authenticate_backup(*args, **kwargs):
                return owner

            monkeypatch.setattr(auth, "get_authenticated_user", authenticate_reporter)
            assert (await client.get(triage_path)).status_code == 404
            monkeypatch.setattr(auth, "get_authenticated_user", authenticate_excluded)
            hidden_triage = await client.get(triage_path)
            assert hidden_triage.status_code == 200
            assert receipt["case_uuid"] not in {item["case_uuid"] for item in hidden_triage.json()}
            monkeypatch.setattr(auth, "get_authenticated_user", authenticate_backup)
            visible_triage = await client.get(triage_path)
            assert visible_triage.status_code == 200
            assert visible_triage.headers["cache-control"] == "private, no-store"
            assert {"case_uuid": queued_uuid, "version": 1} in visible_triage.json()
            assert all(set(item) == {"case_uuid", "version"} for item in visible_triage.json())

            # A queue entry is no case-specific authorization.
            monkeypatch.setattr(cases, "evaluate_studytomy_staff_access", evaluate)
            assert (await client.get(queued_narrative)).status_code == 404
            monkeypatch.setattr(cases, "evaluate_studytomy_staff_access", reviewer_with_retention)

            monkeypatch.setattr(auth, "get_authenticated_user", authenticate_excluded)
            triage_action = f"{triage_path}/{queued_uuid}"
            triage_command = {
                "expected_version": 1, "verification_reference": str(uuid4()),
                "subject_user_uuids": [replacement.user_uuid],
            }
            conflicted = await client.post(triage_action, json={
                **triage_command, "subject_user_uuids": [current_user.user_uuid],
            })
            assert conflicted.status_code == 403
            triaged = await client.post(triage_action, json=triage_command)
            assert triaged.status_code == 200
            assert triaged.json() == {
                "case_uuid": queued_uuid, "version": 2, "status": "assigned", "assigned_to_me": False,
            }
            assert await db.get(
                StudyServiceCaseExclusion, (first.id, queued_uuid, replacement.id),
            ) is not None
            after_triage = (await db.exec(select(StudyServiceCase).where(
                StudyServiceCase.org_id == first.id, StudyServiceCase.case_uuid == queued_uuid,
            ))).one()
            assert after_triage.owner_user_id == owner.id
            triage_event = (await db.exec(select(StudyBusinessEvent).where(
                StudyBusinessEvent.org_id == first.id, StudyBusinessEvent.resource_uuid == queued_uuid,
                StudyBusinessEvent.action == "studytomy.safeguarding.record_triage",
            ))).one()
            assert triage_event.after_state == {
                "version": 2, "evidence_reference": triage_command["verification_reference"],
            }

            backup = (await db.exec(select(StudyOwnerAssignment).where(
                StudyOwnerAssignment.owner_uuid == assignment_refs[owner.id],
            ))).one()
            backup.effective_to = datetime.now(UTC)
            await db.flush()
            assert (await client.get(readiness_path)).json() == {"available": False}
            assert (await client.get(public_path)).status_code == 200
            monkeypatch.setattr(auth, "get_authenticated_user", authenticate_reporter)
            blocked = await client.post(reports_path, json={**command, "request_id": str(uuid4())})
            assert blocked.status_code == 409
            assert blocked.json()["detail"]["code"] == "SAFEGUARDING_STAFFING_UNAVAILABLE"
        await db.rollback()


@pytest.mark.studytomy_postgres
async def test_concurrent_case_claim_has_one_owner_and_one_audit_event(postgres_session_factory, monkeypatch):
    tag = uuid4().hex
    async with postgres_session_factory() as setup:
        organization = Organization(
            name="Synthetic safeguarding organization", slug=f"case-race-{tag}",
            org_uuid=f"org_{tag}", email=f"case-race-{tag}@example.invalid",
        )
        users = [
            User(
                username=f"case_race_{index}_{tag}", user_uuid=f"user_case_race_{index}_{tag}",
                first_name="Synthetic", last_name="Case Member",
                email=f"member-{index}-{tag}@example.invalid",
            )
            for index in range(3)
        ]
        setup.add_all([organization, *users])
        await setup.flush()
        case = StudyServiceCase(
            org_id=organization.id, reporter_user_id=users[0].id, category="safeguarding",
        )
        setup.add(case)
        await setup.commit()
        org_id = organization.id
        reference = UUID(case.case_uuid)
        winner, contender = (PublicUser.model_validate(user) for user in users[1:])

    async def synthetic_approval(*args):
        return []

    async def synthetic_reviewer(*args, **kwargs):
        return cases.StudytomyAccessDecision(allowed=True, code="ALLOWED", public_message="Synthetic")

    monkeypatch.setattr(cases, "require_safeguarding_policies", synthetic_approval)
    monkeypatch.setattr(cases, "evaluate_studytomy_staff_access", synthetic_reviewer)
    pid_ready: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def competing_claim():
        async with postgres_session_factory() as competing_db:
            org = await competing_db.get(Organization, org_id)
            pid = (await competing_db.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            pid_ready.set_result(pid)
            with pytest.raises(HTTPException) as rejected:
                await cases.assign_safeguarding_case(competing_db, org, contender, reference, expected_version=1)
            assert rejected.value.status_code == 409
            await competing_db.rollback()

    async with postgres_session_factory() as winner_db, postgres_session_factory() as observer:
        org = await winner_db.get(Organization, org_id)
        winner_pid = (await winner_db.execute(text("SELECT pg_backend_pid()"))).scalar_one()
        result = await cases.assign_safeguarding_case(winner_db, org, winner, reference, expected_version=1)
        assert (result.owner_user_id, result.version) == (winner.id, 2)
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(competing_claim())
            async with asyncio.timeout(10):
                other_pid = await pid_ready
                while True:
                    blockers = (await observer.execute(
                        text("SELECT pg_blocking_pids(:pid)"), {"pid": other_pid},
                    )).scalar_one()
                    if winner_pid in blockers:
                        break
                    await asyncio.sleep(0.02)
            await winner_db.commit()

    async with postgres_session_factory() as verify:
        final_case = (await verify.exec(select(StudyServiceCase).where(
            StudyServiceCase.org_id == org_id, StudyServiceCase.case_uuid == str(reference),
        ))).one()
        events = (await verify.exec(select(StudyBusinessEvent).where(
            StudyBusinessEvent.org_id == org_id, StudyBusinessEvent.resource_uuid == str(reference),
            StudyBusinessEvent.action == "studytomy.safeguarding.assign_case",
        ))).all()
        assert (final_case.owner_user_id, final_case.version) == (winner.id, 2)
        assert [event.actor_user_id for event in events] == [winner.id]


@pytest.mark.studytomy_postgres
async def test_report_routing_enforces_persisted_approval_and_exact_case_grant(postgres_session_factory, monkeypatch):
    tag = uuid4().hex
    now = datetime.now(UTC)
    async with postgres_session_factory() as db:
        org = Organization(
            name="Synthetic safeguarding policy organization", slug=f"case-policy-{tag}",
            org_uuid=f"org_{tag}", email=f"case-policy-{tag}@example.invalid",
        )
        users = [
            User(
                username=f"safeguarding_{label}_{tag}", user_uuid=f"user_{label}_{tag}",
                first_name="Synthetic", last_name=label,
                email=f"safeguarding-{label}-{tag}@example.invalid",
            )
            for label in ("reporter", "lead", "backup", "approver")
        ]
        db.add_all([org, *users])
        await db.flush()
        reporter, lead, backup, approver = users
        role_seed = 20_000 + int(tag[:7], 16)
        member = Role(
            id=role_seed, name="Synthetic member", org_id=org.id,
            role_uuid=f"role_member_{tag}", role_type=RoleTypeEnum.TYPE_ORGANIZATION, rights={},
        )
        reviewer = Role(
            id=role_seed + 1, name="Synthetic reviewer", org_id=org.id,
            role_uuid=f"role_reviewer_{tag}", role_type=RoleTypeEnum.TYPE_ORGANIZATION,
            rights={"studytomy": {"safeguarding_case_review": True, "support_operating_report": True}},
        )
        privacy = Role(
            id=role_seed + 2, name="Synthetic retention reviewer", org_id=org.id,
            role_uuid=f"role_privacy_{tag}", role_type=RoleTypeEnum.TYPE_ORGANIZATION,
            rights={"studytomy": {"privacy_retention_review": True}},
        )
        db.add_all([member, reviewer, privacy])
        await db.flush()
        db.add_all([
            UserOrganization(
                org_id=org.id, user_id=user.id, role_id=role.id, creation_date="", update_date="",
            )
            for user, role in ((reporter, member), (lead, reviewer), (backup, reviewer), (approver, privacy))
        ])

        def owner(user: User, responsibility: str) -> StudyOwnerAssignment:
            return StudyOwnerAssignment(
                org_id=org.id, user_id=user.id, responsibility=responsibility,
                scope_uuid=org.org_uuid, evidence_reference="synthetic-owner",
                effective_from=now, assigned_by_user_id=approver.id,
            )

        needed = (
            StudyPolicyKey.D_04, StudyPolicyKey.D_11, StudyPolicyKey.D_12, StudyPolicyKey.D_13, StudyPolicyKey.D_15,
            StudyPolicyKey.D_17, StudyPolicyKey.D_18,
        )
        responsibilities = set().union(*(POLICY_REQUIRED_RESPONSIBILITIES[key] for key in needed))
        approvers = {function: owner(approver, function.value) for function in responsibilities}
        lead_owner = owner(lead, "safeguarding")
        backup_owner = owner(backup, "safeguarding")
        support_backup = owner(lead, "support")
        db.add_all([lead_owner, backup_owner, support_backup, *approvers.values()])
        await db.flush()
        fields = {
            StudyPolicyKey.D_04: {
                "support_routes": {"public_safeguarding": {
                    "contact_instructions": "Synthetic public contact",
                    "emergency_guidance": "Synthetic guidance",

                }, "public_support": {
                    "categories": ["Account access", "Orders"],
                    "contact_instructions": "Synthetic contact instructions",
                    "service_limits": "Synthetic service limits",
                    "internal_contact": "Private internal contact",
                    "guest_intake_enabled": True,
                }},
                "monitored_hours": {"public_safeguarding": "Synthetic hours", "public_support": "Synthetic support hours"},
                "response_targets": {
                    "public_support": "Synthetic acknowledgement and review expectations",
                    "internal_escalation": "Private escalation threshold",
                },
                "staffing": {"safeguarding": {
                    "lead_owner_assignment_uuid": lead_owner.owner_uuid,
                    "backup_owner_assignment_uuid": backup_owner.owner_uuid,
                }, "support": {
                    "lead_owner_assignment_uuid": next(
                        assignment.owner_uuid for function, assignment in approvers.items()
                        if function.value == "support"
                    ),
                    "backup_owner_assignment_uuid": support_backup.owner_uuid,
                }},
            },
            StudyPolicyKey.D_13: {"retention": {
                "safeguarding_evidence_retention_category": "synthetic",
                "safeguarding_evidence_holds": {
                    "reason_codes": ["synthetic_review"],
                    "release_reason_codes": ["synthetic_complete"],
                    "max_review_days": 3,
                },
            }},
            StudyPolicyKey.D_18: {"selected_market_requirements": {
                "safeguarding_evidence_holds_enabled": True,
                "safeguarding_evidence_hold_release_enabled": True,
            }},
        }
        for key in needed:
            policy = StudyPolicyVersion(
                org_id=org.id, policy_key=key.value, scope_kind="organization", scope_uuid=org.org_uuid,
                status="approved", evidence_reference="synthetic-policy", effective_from=now,
                created_by_user_id=approver.id, approved_by_user_id=approver.id, approved_at=now,
                body={"decision_items": {
                    area: {
                        "state": "specified", "rationale": "synthetic", "evidence_reference": "synthetic-policy",
                        "value": fields.get(key, {}).get(area, {"synthetic": True}),
                    }
                    for area in POLICY_REQUIRED_DECISION_AREAS[key]
                }},
            )
            db.add(policy)
            await db.flush()
            db.add_all([
                StudyPolicyApproval(
                    org_id=org.id, policy_version_id=policy.id, owner_assignment_id=approvers[function].id,
                    responsibility=function.value, approver_user_id=approver.id,
                    evidence_reference="synthetic-approval", rationale="synthetic", approved_at=now,
                )
                for function in POLICY_REQUIRED_RESPONSIBILITIES[key]
            ])
        await db.commit()

        app = FastAPI()
        app.include_router(v1_router)
        app.dependency_overrides[get_db_session] = lambda: db
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            monkeypatch.delenv("LEARNHOUSE_STUDYTOMY_SUPPORT_INTAKE_ENABLED", raising=False)
            response = await client.get(f"/api/v1/studytomy/orgs/{org.org_uuid}/support/public-contact")
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            assert response.json() == {
                "categories": ["Account access", "Orders"],
                "contact_instructions": "Synthetic contact instructions",
                "service_limits": "Synthetic service limits",
                "monitored_hours": "Synthetic support hours",
                "response_expectations": "Synthetic acknowledgement and review expectations",
                "intake_available": False,
            }
            assert (await client.get("/api/v1/studytomy/orgs/org_other/support/public-contact")).status_code == 404

        reporter_actor, lead_actor, backup_actor, approver_actor = map(PublicUser.model_validate, users)
        support_path = f"/api/v1/studytomy/orgs/{org.org_uuid}/support/cases"

        async def authenticate_reporter(*args, **kwargs):
            return reporter_actor

        async def authenticate_other_member(*args, **kwargs):
            return backup_actor

        payload = {"request_id": str(uuid4()), "topic": "Orders"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            monkeypatch.setattr(auth, "get_authenticated_user", authenticate_reporter)
            monkeypatch.delenv("LEARNHOUSE_STUDYTOMY_SUPPORT_INTAKE_ENABLED", raising=False)
            assert (await client.post(support_path, json=payload)).status_code == 409
            await db.rollback()
            await db.refresh(org)
            for user in users:
                await db.refresh(user)
            monkeypatch.setenv("LEARNHOUSE_STUDYTOMY_SUPPORT_INTAKE_ENABLED", "true")
            from src.services.studytomy import support

            read_policy_area = support._policy_area_values
            with monkeypatch.context() as missing_target:
                missing_target.setattr(
                    support, "_policy_area_values",
                    lambda policy, area: {"public_support": {"internal": "Private"}} if area == "response_targets"
                    else read_policy_area(policy, area),
                )
                projected = await client.get(support_path.replace("/cases", "/public-contact"))
                assert projected.status_code == 200
                assert projected.json()["response_expectations"] is None
                assert projected.json()["intake_available"] is False
            public = await client.get(f"/api/v1/studytomy/orgs/{org.org_uuid}/support/public-contact")
            assert public.json()["intake_available"] is True
            created = await client.post(support_path, json=payload)
            assert created.status_code == 201
            assert created.headers["cache-control"] == "private, no-store"
            saved_support = created.json()
            assert saved_support == {"case_uuid": saved_support["case_uuid"], "topic": "Orders", "status": "assigned"}
            stored_support = (await db.exec(select(StudyServiceCase).where(
                StudyServiceCase.org_id == org.id,
                StudyServiceCase.case_uuid == saved_support["case_uuid"],
            ))).one()
            assert stored_support.owner_user_id == approver.id
            assert (await client.post(support_path, json=payload)).json() == saved_support
            detail = f"{support_path}/{saved_support['case_uuid']}"
            assert (await client.get(detail)).json() == saved_support
            monkeypatch.setattr(auth, "get_authenticated_user", authenticate_other_member)
            assert (await client.get(detail)).status_code == 404
            staff_path = support_path.replace("/support/cases", "/support/staff/cases")
            staff_case = f"{staff_path}/{saved_support['case_uuid']}"
            assert (await client.get(staff_path)).status_code == 404
            command = {"expected_version": 1, "next_status": "under_review"}
            assert (await client.post(f"{staff_case}/transition", json=command)).status_code == 404

            async def authenticate_owner(*args, **kwargs):
                return approver_actor

            monkeypatch.setattr(auth, "get_authenticated_user", authenticate_owner)
            staff_receipt = {
                **saved_support, "version": 1, "allowed_transitions": ["awaiting_information", "under_review"],
            }
            assert (await client.get(staff_case)).json() == staff_receipt
            queue = await client.get(staff_path)
            assert queue.status_code == 200
            assert queue.headers["cache-control"] == "private, no-store"
            assert queue.json() == {"cases": [staff_receipt], "next_cursor": None}
            assert (await client.get(staff_path, params={"after": saved_support["case_uuid"]})).json() == {
                "cases": [], "next_cursor": None,
            }
            invalid = await client.post(
                f"{staff_case}/transition", json={**command, "next_status": "resolved"},
            )
            assert invalid.status_code == 409
            updated = await client.post(f"{staff_case}/transition", json=command)
            assert updated.status_code == 200
            assert updated.json() == {
                **saved_support, "status": "under_review", "version": 2,
                "allowed_transitions": ["awaiting_information", "resolved"],
            }
            assert (await client.post(f"{staff_case}/transition", json=command)).status_code == 409
            event = (await db.exec(select(StudyBusinessEvent).where(
                StudyBusinessEvent.org_id == org.id,
                StudyBusinessEvent.resource_uuid == saved_support["case_uuid"],
                StudyBusinessEvent.action == "studytomy.support.transition_case",
            ))).one()
            assert event.before_state == {"status": "assigned", "version": 1}
            assert event.after_state == {"status": "under_review", "version": 2}

            fallback = await client.post(support_path, json={**payload, "request_id": str(uuid4())})
            assert fallback.status_code == 201
            fallback_case = (await db.exec(select(StudyServiceCase).where(
                StudyServiceCase.org_id == org.id,
                StudyServiceCase.case_uuid == fallback.json()["case_uuid"],
            ))).one()
            assert fallback_case.owner_user_id == lead.id
            assert [case["case_uuid"] for case in (await client.get(staff_path)).json()["cases"]] == [
                saved_support["case_uuid"],
            ]

            monkeypatch.setattr(auth, "get_authenticated_user", authenticate_reporter)
            assert (await client.get(detail)).json() == {**saved_support, "status": "under_review"}
            complaint = await client.post(support_path, json={
                **payload, "request_id": str(uuid4()), "subject_user_uuids": [approver.user_uuid],
            })
            assert complaint.status_code == 201
            complaint_ref = complaint.json()["case_uuid"]
            complaint_case = (await db.exec(select(StudyServiceCase).where(
                StudyServiceCase.org_id == org.id, StudyServiceCase.case_uuid == complaint_ref,
            ))).one()
            assert complaint_case.owner_user_id == lead.id
            assert await db.get(StudyServiceCaseExclusion, (org.id, complaint_ref, approver.id)) is not None
            monkeypatch.setattr(auth, "get_authenticated_user", authenticate_owner)
            assert (await client.get(f"{staff_path}/{complaint_ref}")).status_code == 404
            assert complaint_ref not in {case["case_uuid"] for case in (await client.get(staff_path)).json()["cases"]}
            monkeypatch.setattr(auth, "get_authenticated_user", authenticate_reporter)
            all_conflicted = await client.post(support_path, json={
                **payload, "request_id": str(uuid4()),
                "subject_user_uuids": [approver.user_uuid, lead.user_uuid],
            })
            assert all_conflicted.status_code == 409

            async def authenticate_support_backup(*args, **kwargs):
                return lead_actor

            monkeypatch.setattr(auth, "get_authenticated_user", authenticate_support_backup)
            excluded = await client.post(f"{staff_path}/{complaint_ref}/handoff", json={"expected_version": 1})
            assert excluded.status_code == 409
            monkeypatch.setattr(auth, "get_authenticated_user", authenticate_owner)
            assert (await client.post(f"{staff_case}/handoff", json={"expected_version": 1})).status_code == 409
            handed_off = await client.post(f"{staff_case}/handoff", json={"expected_version": 2})
            assert handed_off.status_code == 200
            assert handed_off.json() == {**saved_support, "status": "under_review"}
            assert (await client.get(staff_case)).status_code == 404
            monkeypatch.setattr(auth, "get_authenticated_user", authenticate_support_backup)
            new_owner = await client.get(staff_case)
            assert new_owner.status_code == 200
            assert new_owner.json()["version"] == 3
            handoff_event = (await db.exec(select(StudyBusinessEvent).where(
                StudyBusinessEvent.org_id == org.id,
                StudyBusinessEvent.resource_uuid == saved_support["case_uuid"],
                StudyBusinessEvent.action == "studytomy.support.handoff_case",
            ))).one()
            assert handoff_event.before_state == {"case_owner_user_id": approver.id, "version": 2}
            assert handoff_event.after_state == {"case_owner_user_id": lead.id, "version": 3}

            complaint_transition = f"{staff_path}/{complaint_ref}/transition"
            for version, status in enumerate(("under_review", "resolved", "under_review"), start=1):
                result = await client.post(
                    complaint_transition, json={"expected_version": version, "next_status": status},
                )
                assert result.status_code == 200
                assert result.json()["status"] == status
                assert result.json()["version"] == version + 1
            assert (await client.post(
                complaint_transition, json={"expected_version": 3, "next_status": "under_review"},
            )).status_code == 409
            transitions = (await db.exec(select(StudyBusinessEvent).where(
                StudyBusinessEvent.org_id == org.id,
                StudyBusinessEvent.resource_uuid == complaint_ref,
                StudyBusinessEvent.action == "studytomy.support.transition_case",
            ).order_by(StudyBusinessEvent.id))).all()
            resolution, reopening = transitions[1:]
            assert resolution.after_state["status"] == "resolved"
            assert reopening.before_state["status"] == "resolved"
            until = reopening.created_at + timedelta(seconds=1)
            assert await support_resolution_activity(
                db, org_id=org.id, start=resolution.created_at, end=until,
            ) == {
                "first_resolved_cases": 1,
                "first_resolution_elapsed_seconds": (
                    resolution.created_at - complaint_case.created_at
                ).total_seconds(),
                "reopened_transitions": 1,
                "reopened_cases": 1,
            }
            assert await support_resolution_activity(
                db, org_id=org.id, start=reopening.created_at, end=until,
            ) == {
                "first_resolved_cases": 0,
                "first_resolution_elapsed_seconds": 0,
                "reopened_transitions": 1,
                "reopened_cases": 1,
            }
            report_path = staff_path.removesuffix("/cases") + "/operating-report"
            report_period = {"start": resolution.created_at.isoformat(), "end": datetime.now(UTC).isoformat()}
            assert (await client.get(report_path, params=report_period)).status_code == 403
            db.add(StudyStaffScope(
                org_id=org.id, assignment_id=support_backup.id, capability="support_operating_report",
                scope_kind="function", scope_uuid="support",
                evidence_reference="synthetic-support-report-scope", created_by_user_id=approver.id,
            ))
            await db.commit()
            report = await client.get(report_path, params=report_period)
            assert report.status_code == 200, report.text
            assert report.headers["cache-control"] == "private, no-store"
            assert report.json()["first_resolved_cases"] == 1
            assert report.json()["reopened_cases"] == 1
            assert complaint_ref not in report.text
            monkeypatch.setattr(auth, "get_authenticated_user", authenticate_reporter)
            assert (await client.get(report_path, params=report_period)).status_code == 403
            monkeypatch.setattr(auth, "get_authenticated_user", authenticate_support_backup)

            guest_token = secrets.token_urlsafe(32)
            issued_at = datetime.now(UTC)
            db.add(StudyGuestSupportChallenge(
                org_id=org.id, token_digest=hashlib.sha256(guest_token.encode("ascii")).hexdigest(),
                contact_ciphertext="synthetic fixture ciphertext", topic="Orders",
                created_at=issued_at, expires_at=issued_at + timedelta(minutes=10),
            ))
            await db.commit()
            monkeypatch.setenv("LEARNHOUSE_STUDYTOMY_GUEST_SUPPORT_ENABLED", "true")

            async def redeem():
                async with postgres_session_factory() as redemption_db:
                    result = await redeem_guest_support_challenge(redemption_db, org, guest_token)
                    await redemption_db.commit()
                    return result

            first, retry = await asyncio.wait_for(asyncio.gather(redeem(), redeem()), timeout=10)
            assert first == retry
            assert first.status == "assigned"
            guest_event = (await db.exec(select(StudyBusinessEvent).where(
                StudyBusinessEvent.org_id == org.id,
                StudyBusinessEvent.resource_uuid == str(first.case_uuid),
                StudyBusinessEvent.action == "studytomy.support.redeem_guest_challenge",
            ))).one()
            assert guest_event.actor_user_id is None
            assert guest_event.after_state == {"status": "assigned", "case_owner_user_id": approver.id}

            monkeypatch.setattr(auth, "get_authenticated_user", authenticate_owner)
            assert (await client.post(f"{staff_case}/recover", json={"expected_version": 3})).status_code == 404
            assert (await client.get(staff_path, params={"view": "recoverable"})).json() == {
                "cases": [], "next_cursor": None,
            }
            support_backup.effective_to = datetime.now(UTC)
            await db.flush()
            public = await client.get(support_path.replace("/cases", "/public-contact"))
            assert public.json()["intake_available"] is False
            recoverable = await client.get(staff_path, params={"view": "recoverable"})
            assert recoverable.status_code == 200
            assert recoverable.json() == {
                "cases": [{**saved_support, "status": "under_review", "version": 3, "allowed_transitions": []}],
                "next_cursor": None,
            }
            assert (await client.post(f"{staff_case}/recover", json={"expected_version": 2})).status_code == 409
            recovered = await client.post(f"{staff_case}/recover", json={"expected_version": 3})
            assert recovered.status_code == 200
            assert recovered.json()["version"] == 4
            assert (await client.get(staff_path, params={"view": "recoverable"})).json() == {
                "cases": [], "next_cursor": None,
            }
            recovery_event = (await db.exec(select(StudyBusinessEvent).where(
                StudyBusinessEvent.org_id == org.id,
                StudyBusinessEvent.resource_uuid == saved_support["case_uuid"],
                StudyBusinessEvent.action == "studytomy.support.recover_case",
            ))).one()
            assert recovery_event.before_state == {"case_owner_user_id": lead.id, "version": 3}
            assert recovery_event.after_state == {"case_owner_user_id": approver.id, "version": 4}
            assert (await client.post(f"{staff_path}/{complaint_ref}/recover", json={"expected_version": 1})).status_code == 404

            # The first submission committed, but the reporter may not have received its response.
            monkeypatch.setattr(auth, "get_authenticated_user", authenticate_reporter)
            replay = await client.post(support_path, json=payload)
            assert replay.status_code == 201
            assert replay.json() == saved_support
            assert (await client.get(detail)).json() == {**saved_support, "status": "under_review"}
            assert (await client.post(
                support_path, json={**payload, "request_id": str(uuid4())},
            )).status_code == 409
            open_events = (await db.exec(select(StudyBusinessEvent).where(
                StudyBusinessEvent.org_id == org.id,
                StudyBusinessEvent.resource_uuid == saved_support["case_uuid"],
                StudyBusinessEvent.action == "studytomy.support.open_case",
            ))).all()
            assert len(open_events) == 1

            guest_case = StudyServiceCase(
                org_id=org.id, category="support", support_topic="Orders",
                guest_contact_verified_at=datetime.now(UTC), owner_user_id=approver.id, status="assigned",
            )
            db.add(guest_case)
            await db.flush()
            guest_ref = guest_case.case_uuid
            assert (await client.get(f"{support_path}/{guest_ref}")).status_code == 404
            monkeypatch.setattr(auth, "get_authenticated_user", authenticate_owner)
            staff_guest = await client.get(f"{staff_path}/{guest_ref}")
            assert staff_guest.status_code == 200
            assert staff_guest.json() == {
                "case_uuid": guest_ref, "topic": "Orders", "status": "assigned",
                "version": 1, "allowed_transitions": ["awaiting_information", "under_review"],
            }
            for invalid in (
                {"category": "support", "support_topic": "Orders"},
                {"category": "safeguarding", "guest_contact_verified_at": datetime.now(UTC)},
                {"category": "support", "support_topic": "Orders", "reporter_user_id": reporter.id,
                 "guest_contact_verified_at": datetime.now(UTC)},
            ):
                with pytest.raises(IntegrityError, match="ck_st_service_case_reporter_kind"):
                    async with db.begin_nested():
                        db.add(StudyServiceCase(org_id=org.id, **invalid))
                        await db.flush()

        assert await cases.safeguarding_reporting_available(db, org)
        with monkeypatch.context() as inactive:
            inactive.delenv("LEARNHOUSE_STUDYTOMY_SAFEGUARDING_REPORTS_ENABLED", raising=False)
            assert not await cases.safeguarding_reporting_available(db, org)
            async with db.begin_nested():
                with pytest.raises(HTTPException) as disabled:
                    await cases.open_safeguarding_case(
                        db, org, reporter_actor, request_id=uuid4(), subject_user_uuids=[],
                        narrative="Synthetic report while intake is disabled",
                    )
                assert disabled.value.status_code == 409
        saved = await cases.open_safeguarding_case(
            db, org, reporter_actor, request_id=uuid4(), subject_user_uuids=[lead.user_uuid],
            narrative="Synthetic safeguarding evidence",
        )
        await db.commit()
        reference = UUID(saved["case_uuid"])
        assert saved["status"] == "assigned"
        for denied_actor in (lead_actor, approver_actor, reporter_actor):
            with pytest.raises(HTTPException) as denied:
                await cases.read_safeguarding_narrative(db, org, denied_actor, reference)
            assert denied.value.status_code == 404

        case = await cases.require_safeguarding_case_review(db, org, backup_actor, reference)
        assert case.owner_user_id == backup.id
        assert await cases.read_safeguarding_narrative(db, org, backup_actor, reference) == "Synthetic safeguarding evidence"

        with pytest.raises(HTTPException) as no_retention_scope:
            await safeguarding_retention_inventory(db, org, approver_actor, reference)
        assert no_retention_scope.value.status_code == 404
        privacy_owner = owner(approver, "privacy")
        db.add(privacy_owner)
        await db.flush()
        db.add(StudyStaffScope(
            org_id=org.id, assignment_id=privacy_owner.id, capability="privacy_retention_review",
            scope_kind="case", scope_uuid=str(reference), evidence_reference="synthetic-retention-scope",
            created_by_user_id=approver.id,
        ))
        db.add(StudyEvidenceFile(
            org_id=org.id, evidence_uuid=f"ev_{uuid4()}", safeguarding_case_uuid=str(reference),
            purpose="safeguarding_case_evidence", storage_reference="synthetic-private-object",
            content_sha256="a" * 64, media_type="application/pdf", size_bytes=100,
            retention_category="synthetic", upload_status="ready", uploaded_by_user_id=backup.id,
        ))
        await db.flush()
        inventory = await safeguarding_retention_inventory(db, org, approver_actor, reference)
        assert inventory.model_dump(mode="json") == {
            "case_uuid": str(reference), "case_status": "assigned", "active_holds": [],
            "categories": [{"category": "synthetic", "narrative_records": 1, "file_records": 1}],
        }
        with pytest.raises(HTTPException) as no_narrative_access:
            await cases.read_safeguarding_narrative(db, org, approver_actor, reference)
        assert no_narrative_access.value.status_code == 404

        hold = uuid4()
        command = CaseEvidenceHoldCommand(
            reason_code="synthetic_review", review_due_at=datetime.now(UTC) + timedelta(days=1),
        )
        receipt = await place_safeguarding_hold(db, org, approver_actor, reference, hold, command)
        assert await place_safeguarding_hold(db, org, approver_actor, reference, hold, command) == receipt
        assert (await safeguarding_retention_inventory(db, org, approver_actor, reference)).active_holds == [hold]
        with pytest.raises(HTTPException) as missing_reason:
            await release_safeguarding_hold(
                db, org, approver_actor, reference, hold,
                CaseEvidenceHoldReleaseCommand(reason_code="unapproved"),
            )
        assert missing_reason.value.status_code == 409
        release = CaseEvidenceHoldReleaseCommand(reason_code="synthetic_complete")
        released = await release_safeguarding_hold(db, org, approver_actor, reference, hold, release)
        assert await release_safeguarding_hold(db, org, approver_actor, reference, hold, release) == released
        assert (await safeguarding_retention_inventory(db, org, approver_actor, reference)).active_holds == []

        business_approver = next(record for function, record in approvers.items() if function.value == "business")
        business_approver.effective_to = datetime.now(UTC)
        await db.flush()
        assert not await cases.safeguarding_reporting_available(db, org)
        with pytest.raises(HTTPException) as ended:
            await cases.read_safeguarding_narrative(db, org, backup_actor, reference)
        assert ended.value.status_code == 404
        assert await cases.get_own_safeguarding_report_status(db, org, reporter_actor, reference) == "assigned"
        await db.rollback()
