from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.db.organizations import Organization
from src.db.studytomy.syllabus import (
    StudySyllabusChunk,
    StudySyllabusSource,
    StudySyllabusSourceVersion,
)
from src.db.users import User
from src.services.studytomy.syllabus_library import ExploreTopicRequest, _retrieve


class _RollbackTestRows(Exception):
    """Force the synthetic integration rows to roll back after assertions."""


@pytest.mark.studytomy_postgres
async def test_topic_retrieval_excludes_unapproved_and_cross_organization_pdfs(postgres_session_factory, postgres_test_rows):
    tag = uuid4().hex
    now = datetime.now(UTC)
    try:
        async with postgres_session_factory() as db, db.begin():
            reviewer = User(
                username=f"syllabus_{tag}",
                first_name="Synthetic",
                last_name="Reviewer",
                email=f"syllabus-{tag}@example.invalid",
                user_uuid=f"syllabus_{tag}",
                creation_date=now.isoformat(),
                update_date=now.isoformat(),
            )
            other_org = Organization(
                name="Synthetic second organization",
                slug=f"syllabus-{tag}",
                org_uuid=f"syllabus-{tag}",
                email=f"syllabus-{tag}@example.invalid",
            )
            db.add_all([reviewer, other_org])
            await db.flush()

            def add_source(org_id: int, *, suffix: str):
                source = StudySyllabusSource(
                    org_id=org_id,
                    source_uuid=f"syllabus_{uuid4()}",
                    title=f"Synthetic {suffix}",
                    created_by_user_id=postgres_test_rows.user_id,
                    created_at=now,
                    updated_at=now,
                )
                db.add(source)
                return source

            approved = add_source(postgres_test_rows.org_id, suffix="official")
            approved_support = add_source(postgres_test_rows.org_id, suffix="supporting")
            pending = add_source(postgres_test_rows.org_id, suffix="pending")
            revoked = add_source(postgres_test_rows.org_id, suffix="revoked")
            superseded = add_source(postgres_test_rows.org_id, suffix="superseded")
            restricted = add_source(postgres_test_rows.org_id, suffix="restricted")
            foreign = add_source(other_org.id, suffix="foreign")
            await db.flush()

            def add_version(source: StudySyllabusSource, *, review: str, rights_status: str, suffix: str):
                return StudySyllabusSourceVersion(
                    org_id=source.org_id,
                    source_id=source.id,
                    version_uuid=f"syllabus_version_{uuid4()}",
                    version_number=1,
                    title=f"Synthetic {suffix} PDF",
                    extracted_pages=[{"page": 1, "text": f"Approved isotope evidence for {suffix}."}],
                    jurisdiction="Sri Lanka",
                    grade="Grade 12",
                    subject="Chemistry",
                    language="en",
                    syllabus_edition="2017",
                    effective_cohort="2026",
                    publisher="Synthetic fixture",
                    source_type="syllabus",
                    source_revision="fixture-1",
                    rights_status=rights_status,
                    rights_reference="synthetic fixture permission",
                    original_filename=f"{suffix}.pdf",
                    storage_reference=f"fixture/{suffix}.pdf",
                    content_sha256=(suffix.encode().hex() * 32)[:64],
                    size_bytes=100,
                    page_count=1,
                    ingestion_status="ready",
                    review_status=review,
                    uploaded_by_user_id=postgres_test_rows.user_id,
                    reviewed_by_user_id=reviewer.id if review == "approved" else None,
                    review_evidence="Independent synthetic fixture review" if review == "approved" else None,
                    approved_at=now if review == "approved" else None,
                    created_at=now,
                    updated_at=now,
                )

            approved_version = add_version(approved, review="approved", rights_status="verified_permitted", suffix="official")
            approved_support_version = add_version(approved_support, review="approved", rights_status="verified_permitted", suffix="supporting")
            pending_version = add_version(pending, review="pending_review", rights_status="unverified", suffix="pending")
            revoked_version = add_version(revoked, review="revoked", rights_status="verified_permitted", suffix="revoked")
            superseded_version = add_version(superseded, review="superseded", rights_status="verified_permitted", suffix="superseded")
            restricted_version = add_version(restricted, review="pending_review", rights_status="restricted", suffix="restricted")
            foreign_version = add_version(foreign, review="approved", rights_status="verified_permitted", suffix="foreign")
            db.add_all([approved_version, approved_support_version, pending_version, revoked_version, superseded_version, restricted_version, foreign_version])
            await db.flush()

            db.add_all([
                StudySyllabusChunk(
                    org_id=source.org_id,
                    source_version_id=version.id,
                    page_number=1,
                    chunk_index=0,
                    chunk_text=f"Approved isotope evidence for {suffix}.",
                    text_sha256=(suffix.encode().hex() * 32)[:64],
                    embedding_space="f" * 64,
                    embedding=[0.0] * 768,
                    created_at=now,
                )
                for source, version, suffix in (
                    (approved, approved_version, "official"),
                    (approved_support, approved_support_version, "supporting"),
                    (pending, pending_version, "pending"),
                    (revoked, revoked_version, "revoked"),
                    (superseded, superseded_version, "superseded"),
                    (restricted, restricted_version, "restricted"),
                    (foreign, foreign_version, "foreign"),
                )
            ])
            await db.flush()

            rows = await _retrieve(
                db,
                Organization(id=postgres_test_rows.org_id, name="Synthetic", slug="synthetic", org_uuid="synthetic", email="synthetic@example.invalid"),
                ExploreTopicRequest(query="isotope evidence"),
                semantic=False,
            )
            assert {row["version"].version_uuid for row in rows} == {
                approved_version.version_uuid,
                approved_support_version.version_uuid,
            }
            assert all(row["source"].org_id == postgres_test_rows.org_id for row in rows)
            raise _RollbackTestRows()
    except _RollbackTestRows:
        pass
