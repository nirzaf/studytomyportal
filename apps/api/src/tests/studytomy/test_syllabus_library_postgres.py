from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import BytesIO
import json
from time import perf_counter
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import delete, select

from src.db.organizations import Organization
from src.db.roles import Role, RoleTypeEnum
from src.db.studytomy.governance import (
    POLICY_REQUIRED_DECISION_AREAS,
    StudyBusinessEvent,
    StudyOwnerAssignment,
    StudyOwnerFunction,
    StudyPolicyApproval,
    StudyPolicyKey,
    StudyPolicyStatus,
    StudyPolicyVersion,
)
from src.db.studytomy.adaptive import StudyConcept, StudyCurriculumRevision
from src.db.studytomy.syllabus import (
    StudySyllabusChunk,
    StudySyllabusConceptMapping,
    StudySyllabusSource,
    StudySyllabusSourceVersion,
)
from src.db.user_organizations import UserOrganization
from src.db.users import PublicUser, User
from src.services.studytomy import syllabus_library as library
from src.services.studytomy.syllabus_library import ExploreTopicRequest, _retrieve
from src.tests.services.rag_helpers import make_pdf


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


@pytest.mark.studytomy_postgres
async def test_topic_retrieval_evaluation_covers_paraphrases_scopes_codes_and_no_match(
    postgres_session_factory, postgres_test_rows, monkeypatch, record_property,
):
    """Synthetic query set measures keyword-only retrieval with no provider calls."""
    now = datetime.now(UTC)
    query_embedding = AsyncMock(side_effect=AssertionError("Keyword-only evaluation must not call the embedding provider"))
    monkeypatch.setattr(library, "embed_single_text", query_embedding)
    try:
        async with postgres_session_factory() as db, db.begin():
            org = await db.get(Organization, postgres_test_rows.org_id)
            reviewer = User(
                username=f"syllabus_eval_{postgres_test_rows.token}",
                user_uuid=f"syllabus_eval_{postgres_test_rows.token}",
                email=f"syllabus-eval-{postgres_test_rows.token}@example.invalid",
                first_name="Synthetic", last_name="Reviewer",
            )
            db.add(reviewer)
            await db.flush()
            curriculum = StudyCurriculumRevision(
                org_id=org.id,
                curriculum_key=f"synthetic-chemistry-{postgres_test_rows.token}",
                revision=1,
                version=1,
                status="draft",
                subject="Chemistry",
                level="Grade 12",
                language="en",
                jurisdiction="Sri Lanka",
                source_reference="TEST synthetic syllabus",
                rights_reference="TEST permission reference",
                programme_metadata={
                    "authority": "Synthetic fixture",
                    "syllabus_edition": "2017",
                    "effective_cohort": "2026",
                },
            )
            db.add(curriculum)
            await db.flush()

            documents = [
                ("plants", "How plants use chlorophyll and light in photosynthesis to produce food.", "Grade 7", "Science", "resource_book"),
                ("float", "Objects float when buoyancy supports their weight; density compared with water matters.", "Grade 8", "Science", "resource_book"),
                ("metals", "Metals conduct electricity because electrons move through the material.", "Grade 10", "Physics", "textbook"),
                ("electrolysis", "Electrolysis uses electrical energy to drive chemical changes.", "Grade 11", "Chemistry", "textbook"),
                ("refraction", "Light changes direction when it enters water; this bending is refraction.", "Grade 9", "Science", "resource_book"),
                ("atomic", "The atomic number is the number of protons in an atom. CHEM-ISO: Define atomic number by proton count.", "Grade 12", "Chemistry", "syllabus"),
                ("atomic_support", "Atomic number gives the proton count of an element.", "Grade 12", "Chemistry", "resource_book"),
            ]
            versions = {}
            for key, content, grade, subject, source_type in documents:
                source = StudySyllabusSource(
                    org_id=org.id,
                    source_uuid=f"syllabus_{uuid4()}",
                    title=f"Synthetic {key} source",
                    created_by_user_id=postgres_test_rows.user_id,
                    created_at=now,
                    updated_at=now,
                )
                db.add(source)
                await db.flush()
                digest = sha256(f"{key}:{content}".encode()).hexdigest()
                version = StudySyllabusSourceVersion(
                    org_id=org.id,
                    source_id=source.id,
                    version_uuid=f"syllabus_version_{uuid4()}",
                    version_number=1,
                    title=source.title,
                    extracted_pages=[{"page": 1, "text": content}],
                    jurisdiction="Sri Lanka",
                    grade=grade,
                    subject=subject,
                    language="en",
                    syllabus_edition="2017" if grade == "Grade 12" else "2020",
                    effective_cohort="2026" if grade == "Grade 12" else "2024",
                    publisher="Synthetic fixture",
                    source_type=source_type,
                    source_revision="eval-1",
                    rights_status="verified_permitted",
                    rights_reference="TEST synthetic permitted use",
                    original_filename=f"{key}.pdf",
                    storage_reference=f"synthetic/{key}.pdf",
                    content_sha256=digest,
                    size_bytes=100,
                    page_count=1,
                    ingestion_status="ready",
                    review_status="approved",
                    uploaded_by_user_id=postgres_test_rows.user_id,
                    reviewed_by_user_id=reviewer.id,
                    review_evidence="Synthetic independent review",
                    approved_at=now,
                    created_at=now,
                    updated_at=now,
                )
                db.add(version)
                await db.flush()
                versions[key] = version
                db.add(StudySyllabusChunk(
                    org_id=org.id,
                    source_version_id=version.id,
                    page_number=1,
                    chunk_index=0,
                    chunk_text=content,
                    text_sha256=sha256(content.encode()).hexdigest(),
                    embedding_space="e" * 64,
                    embedding=[0.0] * 768,
                    created_at=now,
                ))
            await db.flush()
            concept = StudyConcept(
                org_id=org.id,
                curriculum_revision_id=curriculum.id,
                code="CHEM-ISO",
                title="Atomic Number",
                objective="Define atomic number as the number of protons in an atom.",
            )
            water_concept = StudyConcept(
                org_id=org.id,
                curriculum_revision_id=curriculum.id,
                code="SCI-WATER",
                title="Water density",
                objective="Describe water density and why objects float.",
            )
            db.add_all([concept, water_concept])
            await db.flush()
            curriculum.status = "published"
            curriculum.version = 2
            curriculum.reviewer_id = reviewer.id
            curriculum.approved_at = now
            curriculum.content_digest = "c" * 64
            await db.flush()
            db.add(StudySyllabusConceptMapping(
                org_id=org.id,
                mapping_uuid=f"syllabus_map_{uuid4()}",
                source_version_id=versions["atomic"].id,
                curriculum_revision_id=curriculum.id,
                concept_id=concept.id,
                page_start=1,
                page_end=1,
                evidence_quote="The atomic number is the number of protons in an atom.",
                evidence_kind="official",
                status="approved",
                created_by_user_id=postgres_test_rows.user_id,
                reviewed_by_user_id=reviewer.id,
                review_evidence="Synthetic curriculum mapping review",
                approved_at=now,
                created_at=now,
            ))
            db.add(StudySyllabusConceptMapping(
                org_id=org.id,
                mapping_uuid=f"syllabus_map_{uuid4()}",
                source_version_id=versions["float"].id,
                curriculum_revision_id=curriculum.id,
                concept_id=water_concept.id,
                page_start=1,
                page_end=1,
                evidence_quote="Objects float when buoyancy supports their weight; density compared with water matters.",
                evidence_kind="supplementary",
                status="approved",
                created_by_user_id=postgres_test_rows.user_id,
                reviewed_by_user_id=reviewer.id,
                review_evidence="Synthetic supplementary source mapping review",
                approved_at=now,
                created_at=now,
            ))
            await db.flush()

            query_cases = [
                ("How do plants make food?", {"plants"}),
                ("Why do objects float?", {"float"}),
                ("Why do metals conduct electricity?", {"metals"}),
                ("Explain electrolysis in simple words.", {"electrolysis"}),
                ("What happens when light passes through water?", {"refraction"}),
                ("What is atomic number?", {"atomic", "atomic_support"}),
                ("CHEM-ISO", {"atomic"}),
            ]
            reciprocal_ranks = []
            precisions = []
            recalls = []
            latencies_ms = []
            for question, relevant in query_cases:
                started = perf_counter()
                rows = await _retrieve(
                    db,
                    org,
                    ExploreTopicRequest(query=question),
                    semantic=False,
                    selected=curriculum,
                )
                latencies_ms.append((perf_counter() - started) * 1000)
                retrieved = [row["version"].version_uuid for row in rows]
                expected_ids = {versions[key].version_uuid for key in relevant}
                top3 = retrieved[:3]
                hits = len(set(top3).intersection(expected_ids))
                assert hits > 0, f"No expected source in top 3 for: {question}"
                relevant_ranks = [rank for rank, version_id in enumerate(retrieved, 1) if version_id in expected_ids]
                reciprocal_ranks.append(1 / min(relevant_ranks))
                precisions.append(hits / max(1, len(top3)))
                recalls.append(hits / len(expected_ids))

            recall_at_3 = sum(recalls) / len(recalls)
            precision_at_3 = sum(precisions) / len(precisions)
            mean_reciprocal_rank = sum(reciprocal_ranks) / len(reciprocal_ranks)
            record_property("synthetic_recall_at_3", recall_at_3)
            record_property("synthetic_precision_at_3", precision_at_3)
            record_property("synthetic_mean_reciprocal_rank", mean_reciprocal_rank)
            record_property("synthetic_p50_query_latency_ms", sorted(latencies_ms)[len(latencies_ms) // 2])
            assert recall_at_3 == 1.0
            query_embedding.assert_not_awaited()
            record_property("synthetic_query_embedding_calls", query_embedding.await_count)
            assert precision_at_3 >= 0.6

            unsupported = await _retrieve(
                db,
                org,
                ExploreTopicRequest(query="What happens to water in black holes?"),
                semantic=False,
                selected=curriculum,
            )
            assert unsupported == []
            record_property("synthetic_unsupported_query_retrieval_hits", len(unsupported))

            all_scope = await _retrieve(
                db,
                org,
                ExploreTopicRequest(query="How do plants make food?", search_scope="all_approved"),
                semantic=False,
                selected=curriculum,
            )
            assert versions["plants"].version_uuid in {row["version"].version_uuid for row in all_scope}
            subject_scope = await _retrieve(
                db,
                org,
                ExploreTopicRequest(query="Explain electrolysis.", search_scope="selected_subject"),
                semantic=False,
                selected=curriculum,
            )
            assert versions["electrolysis"].version_uuid in {row["version"].version_uuid for row in subject_scope}
            assert versions["plants"].version_uuid not in {row["version"].version_uuid for row in subject_scope}
            syllabus_scope = await _retrieve(
                db,
                org,
                ExploreTopicRequest(query="atomic number", search_scope="selected_syllabus"),
                semantic=False,
                selected=curriculum,
            )
            assert {versions["atomic"].version_uuid, versions["atomic_support"].version_uuid} <= {
                row["version"].version_uuid for row in syllabus_scope
            }
            assert {versions["plants"].version_uuid, versions["float"].version_uuid}.isdisjoint(
                {row["version"].version_uuid for row in syllabus_scope}
            )
            raise _RollbackTestRows()
    except _RollbackTestRows:
        pass


@pytest.mark.studytomy_postgres
async def test_reindex_releases_read_transaction_before_embedding_provider(
    postgres_session_factory, postgres_test_rows, monkeypatch,
):
    now = datetime.now(UTC)
    org = Organization(
        id=postgres_test_rows.org_id,
        name="Synthetic",
        slug="synthetic",
        org_uuid="synthetic",
        email="synthetic@example.invalid",
    )
    pdf = make_pdf(["The atomic number is the number of protons in an atom."])
    source_uuid = f"syllabus_{uuid4()}"
    version_uuid = f"syllabus_version_{uuid4()}"
    role = Role(
        name="Synthetic syllabus reindexer",
        org_id=postgres_test_rows.org_id,
        role_type=RoleTypeEnum.TYPE_ORGANIZATION,
        role_uuid=f"syllabus_reindex_{uuid4()}",
        rights={"courses": {"action_update": True}},
    )
    async with postgres_session_factory() as setup_db:
        user = await setup_db.get(User, postgres_test_rows.user_id)
        setup_db.add(role)
        await setup_db.flush()
        setup_db.add(UserOrganization(
            user_id=user.id,
            org_id=postgres_test_rows.org_id,
            role_id=role.id,
            creation_date=now.isoformat(),
            update_date=now.isoformat(),
        ))
        source = StudySyllabusSource(
            org_id=postgres_test_rows.org_id,
            source_uuid=source_uuid,
            title="Synthetic reindex source",
            created_by_user_id=user.id,
            created_at=now,
            updated_at=now,
        )
        setup_db.add(source)
        await setup_db.flush()
        version = StudySyllabusSourceVersion(
            org_id=postgres_test_rows.org_id,
            source_id=source.id,
            version_uuid=version_uuid,
            version_number=1,
            title=source.title,
            extracted_pages=[{"page": 1, "text": "The atomic number is the number of protons in an atom."}],
            jurisdiction="Sri Lanka",
            grade="Grade 12",
            subject="Chemistry",
            language="en",
            syllabus_edition="2017",
            effective_cohort="2026",
            publisher="Synthetic fixture",
            source_type="syllabus",
            source_revision="reindex-test",
            original_filename="source.pdf",
            storage_reference=f"synthetic/{version_uuid}.pdf",
            content_sha256=sha256(pdf).hexdigest(),
            size_bytes=len(pdf),
            page_count=1,
            ingestion_status="failed",
            ingestion_error_code="embedding_failed",
            ingestion_error_message="Provider unavailable",
            ingestion_attempts=1,
            review_status="pending_review",
            uploaded_by_user_id=user.id,
            created_at=now,
            updated_at=now,
        )
        setup_db.add(version)
        await setup_db.commit()

    monkeypatch.setattr(library, "read_file_content", lambda _reference: pdf)
    monkeypatch.setattr(library, "embedding_provenance", lambda: ("e" * 64, {"dimensions": 768}))

    async with postgres_session_factory() as db:
        actor_row = await db.get(User, postgres_test_rows.user_id)
        actor = PublicUser.model_validate(actor_row, from_attributes=True)

        async def embedding_batch(chunks):
            assert not db.in_transaction(), "Provider operation must run outside the read/write transaction."
            return "e" * 64, [[1.0] + [0.0] * 767 for _ in chunks]

        monkeypatch.setattr(library, "_embedding_batch", embedding_batch)
        result = await library.retry_syllabus_index(db, org, actor, version_uuid)
        assert result.ingestion_status == "ready"
        assert result.ingestion_attempts == 2
        await db.commit()

    async with postgres_session_factory() as cleanup_db, cleanup_db.begin():
        source_id = (await cleanup_db.execute(
            select(StudySyllabusSource.id).where(StudySyllabusSource.source_uuid == source_uuid)
        )).scalar_one()
        version_id = (await cleanup_db.execute(
            select(StudySyllabusSourceVersion.id).where(StudySyllabusSourceVersion.version_uuid == version_uuid)
        )).scalar_one()
        await cleanup_db.execute(delete(StudySyllabusChunk).where(StudySyllabusChunk.source_version_id == version_id))
        await cleanup_db.execute(delete(StudySyllabusSourceVersion).where(StudySyllabusSourceVersion.id == version_id))
        await cleanup_db.execute(delete(StudySyllabusSource).where(StudySyllabusSource.id == source_id))
        await cleanup_db.execute(delete(UserOrganization).where(
            UserOrganization.org_id == postgres_test_rows.org_id,
            UserOrganization.role_id == role.id,
        ))
        await cleanup_db.execute(delete(Role).where(Role.id == role.id))


@pytest.mark.studytomy_postgres
async def test_uploaded_syllabus_requires_review_and_revocation_removes_learner_citations(
    postgres_session_factory, postgres_test_rows, monkeypatch,
):
    """Real service checks; substitute signed sessions with fixture actors, not role checks."""
    pdf = make_pdf([
        "Original test introduction.",
        "Original test background.",
        "Boron isotopes have the same proton number.",
    ])
    files: dict[str, bytes] = {}

    def store_file(reference: str, data: bytes) -> bool:
        files[reference] = data
        return True

    monkeypatch.setattr(library, "write_file_content", store_file)
    monkeypatch.setattr(library, "read_file_content", files.get)
    monkeypatch.setattr(library, "delete_storage_file", files.pop)
    embeddings = AsyncMock(return_value=[[1.0] + [0.0] * 767 for _ in range(3)])
    monkeypatch.setattr(library, "generate_embeddings", embeddings)
    embedding_space = ["d" * 64]
    monkeypatch.setattr(library, "embedding_provenance", lambda: (embedding_space[0], {"dimensions": 768}))
    query_embedding = AsyncMock(side_effect=AssertionError("No query-provider calls without consent"))
    monkeypatch.setattr(library, "embed_single_text", query_embedding)

    now = datetime.now(UTC)
    stamp = now.isoformat()
    tag = postgres_test_rows.token
    query = ExploreTopicRequest(query="boron isotopes", subject="Chemistry")
    approval = library.SyllabusSourceReview(
        decision="approve",
        evidence="Independent synthetic education review",
        rights_reference="TEST permitted-use reference for original synthetic PDF",
    )
    try:
        async with postgres_session_factory() as db, db.begin():
            org = await db.get(Organization, postgres_test_rows.org_id)
            uploader_row = await db.get(User, postgres_test_rows.user_id)
            reviewer_row = User(
                username=f"syllabus_reviewer_{tag}", user_uuid=f"syllabus_reviewer_{tag}",
                email=f"reviewer-{tag}@example.invalid", first_name="Synthetic", last_name="Reviewer",
            )
            learner_row = User(
                username=f"syllabus_learner_{tag}", user_uuid=f"syllabus_learner_{tag}",
                email=f"learner-{tag}@example.invalid", first_name="Synthetic", last_name="Learner",
            )
            educator_role = Role(
                name="Synthetic syllabus educator", org_id=org.id,
                role_type=RoleTypeEnum.TYPE_ORGANIZATION,
                role_uuid=f"syllabus_educator_{tag}",
                rights={"courses": {"action_create": True, "action_update": True}},
            )
            learner_role = Role(
                name="Synthetic syllabus learner", org_id=org.id,
                role_type=RoleTypeEnum.TYPE_ORGANIZATION,
                role_uuid=f"syllabus_learner_role_{tag}",
                rights={"courses": {"action_read": True}},
            )
            db.add_all([reviewer_row, learner_row, educator_role, learner_role])
            await db.flush()
            db.add_all([
                UserOrganization(
                    user_id=user.id, org_id=org.id, role_id=role.id,
                    creation_date=stamp, update_date=stamp,
                )
                for user, role in (
                    (uploader_row, educator_role), (reviewer_row, educator_role), (learner_row, learner_role),
                )
            ])
            await db.flush()
            uploader, reviewer, learner = (
                PublicUser.model_validate(row, from_attributes=True)
                for row in (uploader_row, reviewer_row, learner_row)
            )

            uploaded = await library.upload_syllabus_source(
                db, org, uploader, UploadFile(BytesIO(pdf), filename="synthetic-syllabus.pdf"),
                library.SyllabusSourceUpload(
                    title="Synthetic Chemistry", jurisdiction="Sri Lanka", grade="Grade 12",
                    subject="Chemistry", language="en", syllabus_edition="2017",
                    effective_cohort="2026", publisher="Synthetic fixture", source_type="syllabus",
                    source_revision="test-1",
                ),
            )
            assert uploaded.ingestion_status == "ready"
            assert uploaded.review_status == "pending_review"
            assert uploaded.page_count == 3
            assert uploaded.content_sha256 == sha256(pdf).hexdigest()
            assert list(files.values()) == [pdf]
            assert len(embeddings.await_args.args[0]) == 3
            assert (await library.explore_topic(db, org, learner, query)).status == "insufficient_evidence"

            version_uuid = uploaded.version_uuid
            with pytest.raises(HTTPException) as self_review:
                await library.review_syllabus_source(db, org, uploader, version_uuid, approval)
            assert self_review.value.status_code == 403
            with pytest.raises(HTTPException) as policy_missing:
                await library.review_syllabus_source(db, org, reviewer, version_uuid, approval)
            assert policy_missing.value.status_code == 409
            assert policy_missing.value.detail["code"] == "POLICY_NOT_APPROVED"

            # Test-only rollback-scoped D-16 fixtures, not a live policy issuance.
            key = StudyPolicyKey.D_16
            responsibility = StudyOwnerFunction.EDUCATION.value
            policy = StudyPolicyVersion(
                org_id=org.id, policy_key=key.value, scope_uuid=org.org_uuid,
                status=StudyPolicyStatus.APPROVED.value,
                evidence_reference="TEST only D-16",
                effective_from=now - timedelta(minutes=1), effective_to=now + timedelta(hours=1),
                created_by_user_id=uploader.id, approved_by_user_id=reviewer.id, approved_at=now,
                body={"decision_items": {
                    area: {
                        "state": "not_applicable", "rationale": "Not a live policy decision.",
                        "evidence_reference": f"TEST-{area}",
                    }
                    for area in POLICY_REQUIRED_DECISION_AREAS[key]
                }},
            )
            owner = StudyOwnerAssignment(
                org_id=org.id, user_id=reviewer.id, responsibility=responsibility,
                scope_uuid=org.org_uuid, evidence_reference="TEST education owner",
                effective_from=now - timedelta(minutes=1), effective_to=now + timedelta(hours=1),
                assigned_by_user_id=uploader.id,
            )
            db.add_all([policy, owner])
            await db.flush()
            db.add(StudyPolicyApproval(
                org_id=org.id, policy_version_id=policy.id, owner_assignment_id=owner.id,
                responsibility=responsibility, approver_user_id=reviewer.id,
                evidence_reference="TEST D-16 decision", rationale="Synthetic fixture only",
                approved_at=now,
            ))
            curriculum_policy = StudyPolicyVersion(
                org_id=org.id, policy_key=StudyPolicyKey.D_02.value, scope_uuid=org.org_uuid,
                status=StudyPolicyStatus.APPROVED.value,
                evidence_reference="TEST only D-02",
                effective_from=now - timedelta(minutes=1), effective_to=now + timedelta(hours=1),
                created_by_user_id=uploader.id, approved_by_user_id=reviewer.id, approved_at=now,
                body={"decision_items": {
                    area: {
                        "state": "not_applicable", "rationale": "Not a live curriculum decision.",
                        "evidence_reference": f"TEST-D02-{area}",
                    }
                    for area in POLICY_REQUIRED_DECISION_AREAS[StudyPolicyKey.D_02]
                }},
            )
            db.add(curriculum_policy)
            await db.flush()
            db.add(StudyPolicyApproval(
                org_id=org.id, policy_version_id=curriculum_policy.id, owner_assignment_id=owner.id,
                responsibility=responsibility, approver_user_id=reviewer.id,
                evidence_reference="TEST D-02 decision", rationale="Synthetic fixture only",
                approved_at=now,
            ))
            await db.flush()

            approved = await library.review_syllabus_source(db, org, reviewer, version_uuid, approval)
            assert approved.review_status == "approved"
            assert approved.rights_status == "verified_permitted"
            assert approved.rights_reference == approval.rights_reference
            assert approved.reviewed_by_user_id == reviewer.id
            result = await library.explore_topic(db, org, learner, query)
            assert result.status == "ai_unavailable"
            assert result.answer_blocks == []
            assert len(result.sources) == 1
            citation = result.sources[0]
            assert citation.version_uuid == version_uuid
            assert citation.page == 3
            assert citation.excerpt == "Boron isotopes have the same proton number."
            assert citation.pdf_url.endswith(f"/{version_uuid}/pdf#page=3")
            assert await library.read_syllabus_pdf(db, org, learner, version_uuid) == (
                pdf, "synthetic-syllabus.pdf",
            )
            query_embedding.assert_not_awaited()

            async def synthetic_generate(**kwargs):
                prompt = json.loads(kwargs["user_prompt"])
                reference = next(
                    row["reference"]
                    for row in prompt["authorized_excerpts"]
                    if "Boron isotopes have the same proton number." in row["text"]
                )
                supported_text = "Boron isotopes have the same proton number."
                return {
                    "explanation": "This free-form field is intentionally ignored.",
                    "claims": [{"reference": reference, "text": supported_text, "quote": supported_text}],
                }

            monkeypatch.setattr(library, "_current_ai_consent", AsyncMock(return_value=(True, None)))
            monkeypatch.setattr(library, "resolve_model_for_org", AsyncMock(return_value="synthetic-test-model"))
            monkeypatch.setattr(library, "enforce_ai_rate_limit", lambda *_args: None)
            monkeypatch.setattr(library, "reserve_ai_credit", AsyncMock(return_value=None))
            query_embedding.side_effect = None
            query_embedding.return_value = [1.0] + [0.0] * 767
            generate_answer = AsyncMock(side_effect=synthetic_generate)
            monkeypatch.setattr(library, "generate", generate_answer)

            grounded = await library.explore_topic(db, org, learner, query)
            assert grounded.status == "grounded_answer"
            assert grounded.explanation is None
            assert grounded.answer_blocks[0].text == "Boron isotopes have the same proton number."
            assert grounded.sources[0].page == 3
            follow_up = await library.explore_topic(
                db,
                org,
                learner,
                ExploreTopicRequest(
                    query="What does the passage say about protons?",
                    context_question=query.query,
                    subject="Chemistry",
                    source_version_uuids=[version_uuid],
                ),
            )
            assert follow_up.status == "grounded_answer"
            assert {citation.version_uuid for citation in follow_up.sources} == {version_uuid}
            follow_up_prompt = json.loads(generate_answer.await_args.kwargs["user_prompt"])
            assert follow_up_prompt["previous_question_context"] == query.query
            assert follow_up_prompt["question"] == "What does the passage say about protons?"
            assert "Boron isotopes have the same proton number." in " ".join(
                row["text"] for row in follow_up_prompt["authorized_excerpts"]
            )

            source_version_record = (await db.execute(select(StudySyllabusSourceVersion).where(
                StudySyllabusSourceVersion.version_uuid == version_uuid,
            ))).scalar_one()
            files[source_version_record.storage_reference] = pdf + b"synthetic-corruption"
            with pytest.raises(HTTPException) as corrupted_pdf:
                await library.read_syllabus_pdf(db, org, learner, version_uuid)
            assert corrupted_pdf.value.status_code == 409
            files[source_version_record.storage_reference] = pdf
            version_id = source_version_record.id
            old_index = (await db.execute(select(
                StudySyllabusChunk.page_number,
                StudySyllabusChunk.chunk_index,
                StudySyllabusChunk.text_sha256,
                StudySyllabusChunk.embedding_space,
            ).where(StudySyllabusChunk.source_version_id == version_id))).all()
            assert len(old_index) == 3
            source_version = await db.get(StudySyllabusSourceVersion, version_id)
            assert source_version is not None
            source_version.ingestion_attempts = 5
            await db.flush()
            embedding_space[0] = "e" * 64
            embeddings.side_effect = RuntimeError("Synthetic provider outage")
            with pytest.raises(HTTPException) as failed_replacement:
                await library.retry_syllabus_index(db, org, reviewer, version_uuid, commit_snapshot=False)
            assert failed_replacement.value.status_code == 503
            preserved_index = (await db.execute(select(
                StudySyllabusChunk.page_number,
                StudySyllabusChunk.chunk_index,
                StudySyllabusChunk.text_sha256,
                StudySyllabusChunk.embedding_space,
            ).where(StudySyllabusChunk.source_version_id == version_id))).all()
            assert preserved_index == old_index
            assert (await library.explore_topic(db, org, learner, query)).sources[0].version_uuid == version_uuid

            embeddings.side_effect = None
            reindexed = await library.retry_syllabus_index(db, org, reviewer, version_uuid, commit_snapshot=False)
            assert reindexed.ingestion_status == "ready"
            assert reindexed.ingestion_attempts == 6
            replacement_index = (await db.execute(select(
                StudySyllabusChunk.page_number,
                StudySyllabusChunk.chunk_index,
                StudySyllabusChunk.text_sha256,
                StudySyllabusChunk.embedding_space,
            ).where(StudySyllabusChunk.source_version_id == version_id))).all()
            assert len(replacement_index) == len(old_index)
            assert {row[3] for row in replacement_index} == {"e" * 64}
            assert [row[:3] for row in replacement_index] == [row[:3] for row in old_index]

            curriculum = StudyCurriculumRevision(
                org_id=org.id,
                curriculum_key=f"synthetic-chemistry-{tag}",
                revision=1,
                version=1,
                status="draft",
                subject="Chemistry",
                level="Grade 12",
                language="en",
                jurisdiction="Sri Lanka",
                source_reference="TEST synthetic curriculum",
                rights_reference="TEST original synthetic curriculum",
                programme_metadata={
                    "authority": "Synthetic fixture",
                    "syllabus_edition": "2017",
                    "effective_cohort": "2026",
                },
            )
            db.add(curriculum)
            await db.flush()
            concept = StudyConcept(
                org_id=org.id,
                curriculum_revision_id=curriculum.id,
                code="CHEM-ISO",
                title="Boron isotopes",
                objective="Describe boron isotopes and compare their atomic particles.",
            )
            db.add(concept)
            await db.flush()
            # Definition rows are writable only while the parent is a draft.
            # Publish it only after its reviewed contents are assembled.
            curriculum.status = "published"
            curriculum.version = 2
            curriculum.reviewer_id = reviewer.id
            curriculum.approved_at = now
            curriculum.content_digest = "c" * 64
            await db.flush()
            mapping_command = library.SyllabusMappingCreate(
                version_uuid=version_uuid,
                curriculum_uuid=curriculum.uuid,
                concept_uuid=concept.uuid,
                page_start=3,
                page_end=3,
                evidence_kind="official",
                evidence_quote="Boron isotopes have the same proton number.",
            )
            mapping = await library.create_syllabus_mapping(db, org, uploader, mapping_command)
            reviewed_mapping = await library.review_syllabus_mapping(
                db,
                org,
                reviewer,
                mapping.mapping_uuid,
                library.SyllabusMappingReview(
                    decision="approve", evidence="Independent synthetic curriculum mapping review",
                ),
            )
            assert reviewed_mapping.status == "approved"
            approval_review_evidence = reviewed_mapping.review_evidence
            approval_reviewer_id = reviewed_mapping.reviewed_by_user_id
            mapped_topic = await library.explore_topic(
                db,
                org,
                learner,
                ExploreTopicRequest(
                    query="boron isotopes",
                    subject="Chemistry",
                    selected_curriculum_uuid=curriculum.uuid,
                ),
            )
            assert mapped_topic.coverage == "confirmed_selected_syllabus"
            assert mapped_topic.sources[0].coverage == "confirmed_selected_syllabus"
            withdrawn = await library.revoke_syllabus_mapping(
                db, org, reviewer, mapping.mapping_uuid, "Synthetic mapping correction required",
            )
            assert withdrawn.status == "revoked"
            assert withdrawn.review_evidence == approval_review_evidence
            assert withdrawn.reviewed_by_user_id == approval_reviewer_id
            assert withdrawn.revocation_evidence == "Synthetic mapping correction required"
            assert withdrawn.revoked_by_user_id == reviewer.id
            after_mapping_revocation = await library.explore_topic(
                db,
                org,
                learner,
                ExploreTopicRequest(
                    query="boron isotopes",
                    subject="Chemistry",
                    selected_curriculum_uuid=curriculum.uuid,
                ),
            )
            assert after_mapping_revocation.coverage == "unverified"
            corrected = await library.create_syllabus_mapping(db, org, uploader, mapping_command)
            assert corrected.status == "pending_review"
            history, next_offset = await library.list_syllabus_mappings(db, org, uploader, limit=1)
            assert len(history) == 1
            assert next_offset == 1
            assert (await db.execute(select(StudySyllabusConceptMapping.status).where(
                StudySyllabusConceptMapping.mapping_uuid == mapping.mapping_uuid,
            ))).scalar_one() == "revoked"

            revoked = await library.revoke_syllabus_source(
                db, org, reviewer, version_uuid, "Synthetic permitted-use withdrawal",
            )
            assert revoked.review_status == "revoked"
            assert revoked.reviewed_by_user_id == reviewer.id
            assert revoked.review_evidence == approval.evidence
            assert revoked.revocation_evidence == "Synthetic permitted-use withdrawal"
            assert revoked.revoked_by_user_id == reviewer.id
            excluded = await library.explore_topic(db, org, learner, query)
            assert excluded.status == "insufficient_evidence"
            assert excluded.sources == []
            with pytest.raises(HTTPException) as hidden:
                await library.read_syllabus_pdf(db, org, learner, version_uuid)
            assert hidden.value.status_code == 404
            events = (await db.execute(
                select(StudyBusinessEvent.action, StudyBusinessEvent.actor_user_id)
                .where(StudyBusinessEvent.resource_uuid == version_uuid)
            )).all()
            assert set(events) == {
                ("studytomy.syllabus.upload_version", uploader.id),
                ("studytomy.syllabus.review_version", reviewer.id),
                ("studytomy.syllabus.revoke_version", reviewer.id),
            }
            assert embeddings.await_count == 3
            assert query_embedding.await_count >= 4
            assert generate_answer.await_count == 5
            raise _RollbackTestRows()
    except _RollbackTestRows:
        pass
