"""Provision guarded synthetic identities and curriculum for the live E2E run.

Run only against the loopback ``_test`` database already used by the API. The
authenticated runtime identity check happens before opening a write session.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

API_ROOT = Path(__file__).resolve().parents[3]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from src.core.migrations import current_revisions, head_revisions  # noqa: E402
from src.db.organizations import Organization  # noqa: E402
from src.db.roles import Role, RoleTypeEnum  # noqa: E402
from src.db.studytomy.adaptive import StudyConcept, StudyCurriculumRevision  # noqa: E402
from src.db.studytomy.governance import (  # noqa: E402
    POLICY_REQUIRED_DECISION_AREAS,
    StudyOwnerAssignment,
    StudyOwnerFunction,
    StudyPolicyApproval,
    StudyPolicyDecisionState,
    StudyPolicyKey,
    StudyPolicyStatus,
    StudyPolicyVersion,
    StudyStaffScope,
    StudyPolicyDecisionValue,
    StudyPolicyBody,
)
from src.db.user_organizations import UserOrganization  # noqa: E402
from src.db.users import User  # noqa: E402
from src.security.security import security_hash_password  # noqa: E402


def _loopback(host: str | None) -> bool:
    if not host:
        return False
    normalized = host.lower().strip("[]").rstrip(".")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _test_database(raw: str | None) -> tuple[URL, str]:
    if not raw or "#" in raw:
        raise SystemExit("Set a direct, query-free loopback STUDYTOMY_TEST_DATABASE_URL.")
    try:
        url = make_url(raw)
    except Exception as error:
        raise SystemExit("STUDYTOMY_TEST_DATABASE_URL is not a valid database URL.") from error
    if (
        url.get_backend_name() != "postgresql"
        or not _loopback(url.host)
        or url.query
        or not url.database
        or not re.fullmatch(r"[A-Za-z0-9_-]+_test", url.database, re.IGNORECASE)
    ):
        raise SystemExit("Refusing fixture writes outside a direct loopback PostgreSQL _test database.")
    host = (url.host or "").lower().strip("[]")
    port = url.port or 5432
    identity = hashlib.sha256(f"{host}\0{port}\0{url.database}".encode()).hexdigest()
    return url.set(drivername="postgresql+asyncpg"), identity


def _verify_runtime(api_root: str, database_identity: str) -> None:
    parsed = urlsplit(api_root)
    if (
        parsed.scheme != "http"
        or not _loopback(parsed.hostname)
        or parsed.query
        or parsed.fragment
        or not parsed.path.rstrip("/").endswith("/api/v1")
    ):
        raise SystemExit("E2E_API_URL must be a loopback HTTP API root ending in /api/v1.")
    email = os.environ.get("E2E_ADMIN_EMAIL", "admin@school.dev")
    password = os.environ.get("E2E_ADMIN_PASSWORD")
    build_sha = os.environ.get("STUDYTOMY_BUILD_SHA", "")
    tree_sha = os.environ.get("STUDYTOMY_BUILD_TREE_SHA256", "")
    if (
        os.environ.get("STUDYTOMY_TEST_RUNTIME_IDENTITY") != "1"
        or not password
        or not re.fullmatch(r"[0-9a-f]{40}", build_sha)
        or not re.fullmatch(r"[0-9a-f]{64}", tree_sha)
    ):
        raise SystemExit("Runtime identity and synthetic admin credentials must be configured.")

    def request(path: str, body: bytes | None, token: str | None = None) -> dict:
        headers = {"Content-Type": "application/x-www-form-urlencoded"} if body is not None else {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            with urlopen(Request(f"{api_root.rstrip('/')}{path}", data=body, headers=headers), timeout=10) as response:
                return json.loads(response.read())
        except Exception as error:
            raise SystemExit("The authenticated loopback API identity check failed; no fixtures were written.") from error

    login = request(
        "/auth/login",
        urlencode({"username": email, "password": password}).encode(),
    )
    token = (login.get("tokens") or {}).get("access_token") or login.get("access_token")
    if not token:
        raise SystemExit("The synthetic E2E administrator could not authenticate; no fixtures were written.")
    identity = request("/instance/runtime-identity", None, token)
    expected_heads = sorted(head_revisions())
    if (
        identity.get("build_sha") != build_sha
        or identity.get("source_tree_sha256") != tree_sha
        or identity.get("database_identity_sha256") != database_identity
        or sorted(identity.get("source_migration_heads", [])) != expected_heads
        or sorted(identity.get("database_migration_heads", [])) != expected_heads
    ):
        raise SystemExit("The running API does not match this checkout and test database; no fixtures were written.")


def _policy_body(key: StudyPolicyKey) -> dict:
    items = {
        area: StudyPolicyDecisionValue(
            state=StudyPolicyDecisionState.NOT_APPLICABLE,
            rationale="Synthetic isolated E2E fixture; this is not a production policy decision.",
            evidence_reference=f"TEST-{key.value}-{area}",
        )
        for area in POLICY_REQUIRED_DECISION_AREAS[key]
    }
    return StudyPolicyBody(decision_items=items).model_dump(mode="json")


async def _provision(database_url: URL) -> None:
    author_email = os.environ.get("E2E_STUDYTOMY_SOURCE_AUTHOR_EMAIL", "")
    author_password = os.environ.get("E2E_STUDYTOMY_SOURCE_AUTHOR_PASSWORD", "")
    reviewer_email = os.environ.get("E2E_STUDYTOMY_SOURCE_REVIEWER_EMAIL", "")
    reviewer_password = os.environ.get("E2E_STUDYTOMY_SOURCE_REVIEWER_PASSWORD", "")
    if not all((author_email, author_password, reviewer_email, reviewer_password)):
        raise SystemExit("Synthetic source-author and independent-reviewer credentials are required.")
    if author_email.lower() == reviewer_email.lower():
        raise SystemExit("The source author and independent reviewer must be separate identities.")

    engine = create_async_engine(database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as db, db.begin():
            heads = await db.connection()
            if await heads.run_sync(current_revisions) != head_revisions():
                raise SystemExit("The test database migrations do not match the current source heads.")

            org = (await db.execute(select(Organization).where(Organization.slug == "default"))).scalar_one_or_none()
            admin = (await db.execute(select(User).where(User.email == os.environ.get("E2E_ADMIN_EMAIL", "admin@school.dev")))).scalar_one_or_none()
            base_role = await db.get(Role, 4)
            if org is None or admin is None or not admin.is_superadmin or base_role is None:
                raise SystemExit("Run the established API install bootstrap before provisioning live fixtures.")
            if (await db.execute(select(User.id).where(User.email.in_([author_email, reviewer_email])))).first():
                raise SystemExit("Fixture identities already exist; use a fresh disposable database.")

            # The install bootstrap assigns fixed IDs 1-4 to global roles. Align
            # the disposable database sequence before inserting organization roles.
            await db.execute(text(
                "SELECT setval(pg_get_serial_sequence('role', 'id'), "
                "(SELECT COALESCE(MAX(id), 1) FROM role))"
            ))

            stamp = datetime.now(UTC)
            suffix = uuid4().hex
            rights_base = base_role.rights.model_dump(mode="json") if hasattr(base_role.rights, "model_dump") else deepcopy(base_role.rights)
            policies: dict[str, StudyPolicyVersion] = {}
            for key in (StudyPolicyKey.D_02, StudyPolicyKey.D_10, StudyPolicyKey.D_15, StudyPolicyKey.D_16):
                policy = StudyPolicyVersion(
                    org_id=org.id,
                    policy_key=key.value,
                    scope_kind="organization",
                    scope_uuid=org.org_uuid,
                    version=1,
                    status=StudyPolicyStatus.APPROVED.value,
                    body=_policy_body(key),
                    evidence_reference=f"TEST-only synthetic E2E policy {key.value}",
                    effective_from=stamp - timedelta(minutes=1),
                    effective_to=stamp + timedelta(days=1),
                    created_by_user_id=admin.id,
                    approved_by_user_id=admin.id,
                    approved_at=stamp,
                )
                db.add(policy)
                policies[key.value] = policy
            await db.flush()

            users: dict[str, tuple[User, Role, str]] = {}
            education_assignments: dict[str, StudyOwnerAssignment] = {}
            for label, email, password, capability in (
                ("author", author_email, author_password, "adaptive_curriculum_author"),
                ("reviewer", reviewer_email, reviewer_password, "content_release_review"),
            ):
                rights = deepcopy(rights_base)
                rights["studytomy"] = {capability: True}
                role = Role(
                    name=f"E2E syllabus {label}",
                    description="Synthetic role for isolated live acceptance only.",
                    org_id=org.id,
                    role_type=RoleTypeEnum.TYPE_ORGANIZATION,
                    role_uuid=f"role_e2e_{label}_{suffix}",
                    rights=rights,
                    creation_date=stamp.isoformat(),
                    update_date=stamp.isoformat(),
                )
                user = User(
                    username=f"e2e_{label}_{suffix}",
                    first_name="Synthetic",
                    last_name=label.title(),
                    email=email,
                    password=security_hash_password(password),
                    user_uuid=f"user_e2e_{label}_{suffix}",
                    email_verified=True,
                    signup_method="test_fixture",
                    creation_date=stamp.isoformat(),
                    update_date=stamp.isoformat(),
                )
                db.add_all([role, user])
                await db.flush()
                db.add(UserOrganization(
                    user_id=user.id,
                    org_id=org.id,
                    role_id=role.id,
                    creation_date=stamp.isoformat(),
                    update_date=stamp.isoformat(),
                ))
                assignment = StudyOwnerAssignment(
                    org_id=org.id,
                    user_id=user.id,
                    responsibility=StudyOwnerFunction.EDUCATION.value,
                    scope_kind="organization",
                    scope_uuid=org.org_uuid,
                    status="active",
                    evidence_reference=f"TEST-only E2E education owner assignment {label}",
                    effective_from=stamp - timedelta(minutes=1),
                    assigned_by_user_id=admin.id,
                )
                db.add(assignment)
                await db.flush()
                db.add(StudyStaffScope(
                    org_id=org.id,
                    assignment_id=assignment.id,
                    capability=capability,
                    scope_kind="function",
                    scope_uuid="syllabus_library",
                    status="active",
                    evidence_reference=f"TEST-only E2E syllabus library scope {label}",
                    created_by_user_id=admin.id,
                ))
                users[label] = (user, role, email)
                education_assignments[label] = assignment

            # An approved status alone is insufficient: D-16 also requires a
            # named education-owner approval record for its active policy gate.
            reviewer = users["reviewer"][0]
            db.add(StudyPolicyApproval(
                org_id=org.id,
                policy_version_id=policies[StudyPolicyKey.D_16.value].id,
                owner_assignment_id=education_assignments["reviewer"].id,
                responsibility=StudyOwnerFunction.EDUCATION.value,
                approver_user_id=reviewer.id,
                evidence_reference="TEST-only independent education approval for synthetic D-16 policy",
                rationale="Disposable acceptance fixture; permits only original synthetic test material.",
                approved_at=stamp,
            ))

            curriculum = StudyCurriculumRevision(
                org_id=org.id,
                curriculum_key=f"e2e-synthetic-science-{suffix}",
                revision=1,
                version=1,
                status="draft",
                subject="Science",
                level="Grade 7",
                language="en",
                jurisdiction="Sri Lanka",
                source_reference="Synthetic E2E curriculum; not an official syllabus source.",
                rights_reference="Original synthetic test content only.",
                programme_metadata={
                    "authority": "Synthetic E2E fixture",
                    "syllabus_edition": "Synthetic",
                    "effective_cohort": "2026",
                    "stage": "other",
                    "coverage_status": "unverified",
                    "source_language": "en",
                },
                updated_at=stamp,
            )
            db.add(curriculum)
            await db.flush()
            db.add(StudyConcept(
                org_id=org.id,
                curriculum_revision_id=curriculum.id,
                code="E2E-PHOTOSYNTHESIS",
                title="Photosynthesis",
                objective="Describe how plants use light to make food; synthetic fixture only.",
                sequence=1,
            ))
            curriculum.status = "published"
            curriculum.version = 2
            curriculum.reviewer_id = admin.id
            curriculum.approved_at = stamp
            curriculum.content_digest = hashlib.sha256(
                f"synthetic-curriculum:{suffix}".encode()
            ).hexdigest()
            curriculum.policy_version_ids = [int(policy.id) for policy in policies.values()]
            await db.flush()
        print("Provisioned synthetic author, independent reviewer, test-only policies and one unverified curriculum.")
    finally:
        await engine.dispose()


def main() -> None:
    database_url, identity = _test_database(os.environ.get("STUDYTOMY_TEST_DATABASE_URL"))
    api_root = os.environ.get("E2E_API_URL", "")
    _verify_runtime(api_root, identity)
    asyncio.run(_provision(database_url))


if __name__ == "__main__":
    main()
