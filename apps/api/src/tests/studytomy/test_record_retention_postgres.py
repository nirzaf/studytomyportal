import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.db.organizations import Organization
from src.db.roles import Role, RoleTypeEnum
from src.db.studytomy.family import StudyLearnerProfile
from src.db.studytomy.operations import StudyEvidenceFile
from src.db.user_organizations import UserOrganization
from src.db.users import APITokenUser, User
from src.services.admin.admin import anonymize_user, export_user_data
from src.services.studytomy.policies import lock_users
from src.services.studytomy.retention import _candidate


@pytest.mark.parametrize(("held", "case_age_days", "expected"), [
    (False, 5, "candidate"), (False, 4, "retained"), (True, 10, "held"),
])
def test_case_evidence_candidate_uses_case_closure_and_verification(held, case_age_days, expected):
    cutoff = datetime(2026, 1, 20, tzinfo=UTC)
    proof = StudyEvidenceFile(
        evidence_uuid="ev_synthetic_retention", upload_status="ready", retention_category="synthetic",
        verified_at=cutoff - timedelta(days=10),
    )
    actual = _candidate(proof, cutoff, cutoff - timedelta(days=case_age_days), 5, "synthetic", held=held)
    assert actual.disposition == expected
    assert actual.eligible_at == (None if held else cutoff + timedelta(days=5 - case_age_days))


@pytest.mark.studytomy_postgres
@pytest.mark.parametrize("operation", [export_user_data, anonymize_user])
async def test_native_record_operations_serialize_with_learner_intake(postgres_session_factory, operation):
    code = uuid4().hex
    async with postgres_session_factory() as db:
        org = Organization(name="Synthetic record review", slug=code, org_uuid=f"org_{code}", email=f"{code}@example.invalid")
        learner = User(username=code, user_uuid=f"user_{code}", email=org.email, first_name="Synthetic", last_name="Learner")
        db.add_all([org, learner])
        await db.flush()
        role = Role(
            id=10000 + int(code[:7], 16), name="Fixture", org_id=org.id,
            role_uuid=f"role_{code}", role_type=RoleTypeEnum.TYPE_ORGANIZATION, rights={},
        )
        db.add(role)
        await db.flush()
        db.add(UserOrganization(org_id=org.id, user_id=learner.id, role_id=role.id, creation_date="", update_date=""))
        await db.commit()

    token = APITokenUser(org_id=org.id, rights={"users": {"action_read": True, "action_delete": True}})
    async with postgres_session_factory() as intake, postgres_session_factory() as native:
        await lock_users(intake, [learner.id])
        intake.add(StudyLearnerProfile(
            org_id=org.id, user_id=learner.id, created_by_user_id=learner.id,
            status="active", control_mode="independent",
        ))
        await intake.flush()
        pending = asyncio.create_task(operation(token, learner.id, native))
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(pending), timeout=0.2)
            await intake.commit()
            with pytest.raises(HTTPException) as blocked:
                await asyncio.wait_for(pending, timeout=5)
            assert blocked.value.status_code == 409
            assert blocked.value.detail["code"] == "MANAGED_RECORD_REVIEW_REQUIRED"
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    async with postgres_session_factory() as verifier:
        assert (await verifier.get(User, learner.id)).email == org.email
