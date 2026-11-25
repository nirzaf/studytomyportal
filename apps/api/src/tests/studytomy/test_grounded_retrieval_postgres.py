"""Offline PostgreSQL retrieval comparison over synthetic authorized materials."""

import json
import statistics
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.db.course_embeddings import CourseEmbedding
from src.db.courses.activities import (
    Activity,
    ActivityLockType,
    ActivitySubTypeEnum,
    ActivityTypeEnum,
)
from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.users import AnonymousUser, PublicUser
from src.services.ai.rag import query_service
from src.services.ai.rag.access import CourseGrant, RagAccessScope
from src.services.ai.tools.base import make_synthetic_request

# Hand-authored vectors isolate ranking behavior from provider quality and cost.
_ROWS = (
    ("fraction-comparison", 1, True, True, "Compare two fractions by finding a common denominator.", "[0.98,0.2,0]"),
    ("equivalent-fractions", 1, True, True, "Equal parts can express the same amount in different ways.", "[1,0,0]"),
    ("exact-acara-code", 1, True, True, "AC9M6N03 fractions and common denominators.", "[0.65,0.75,0]"),
    ("energy-transfer", 1, True, True, "Heat moves from warmer objects toward cooler ones.", "[0,1,0.03]"),
    ("thermal-insulation", 1, True, True, "Thermal insulation slows the exchange of heat.", "[0.1,1,0]"),
    ("measurement-chart", 1, True, True, "Read a chart and describe the axis units.", "[0,0.02,1]"),
    ("cross-tenant", 2, True, True, "AC9M6N03 comparison and thermal insulation.", "[1,0,0]"),
    ("draft", 1, False, True, "AC9M6N03 fractions and thermal insulation.", "[0,1,0]"),
    ("revoked", 1, True, False, "AC9M6N03 fractions and thermal insulation.", "[1,0,0]"),
)
_QUERIES = (
    ("AC9M6N03", "[1,0,0]", {"exact-acara-code"}),
    ("Why can different pieces show equal amounts?", "[1,0,0]", {"equivalent-fractions"}),
    ("thermal insulation", "[0,1,0]", {"thermal-insulation"}),
    ("How does warmth move between objects?", "[0,1,0]", {"energy-transfer", "thermal-insulation"}),
)

_RANKED = """
WITH authorized AS MATERIALIZED (
    SELECT ref, document, embedding
    FROM synthetic_grounding
    WHERE org_id = 1 AND approved AND permitted
), vectors AS (
    SELECT ref, row_number() OVER (ORDER BY embedding <=> CAST(:embedding AS vector), ref) AS rank
    FROM authorized
    ORDER BY rank LIMIT 5
), words AS (
    SELECT ref, row_number() OVER (
        ORDER BY ts_rank_cd(document, websearch_to_tsquery('english', :question)) DESC, ref
    ) AS rank
    FROM authorized
    WHERE document @@ websearch_to_tsquery('english', :question)
    ORDER BY rank LIMIT 5
)
"""
_SELECT = {
    "exact_vector": "SELECT ref FROM vectors ORDER BY rank LIMIT 2",
    "full_text": "SELECT ref FROM words ORDER BY rank LIMIT 2",
    "fusion": """
        SELECT coalesce(vectors.ref, words.ref) AS ref
        FROM vectors FULL JOIN words USING (ref)
        ORDER BY coalesce(1.0 / (60 + vectors.rank), 0)
               + coalesce(1.0 / (60 + words.rank), 0) DESC, ref
        LIMIT 2
    """,
}


@pytest.mark.studytomy_postgres
async def test_runtime_rag_excludes_legacy_and_incompatible_vectors(postgres_session_factory, monkeypatch):
    tag = uuid4().hex
    async with postgres_session_factory() as db:
        org = Organization(name="Synthetic embedding space", slug=tag, org_uuid=f"org_{tag}",
                           email=f"{tag}@example.invalid")
        db.add(org)
        await db.flush()
        course, managed = [
            Course(org_id=org.id, course_uuid=f"course_{uuid4()}", name="Synthetic course",
                   public=False, published=False, open_to_contributors=False, studytomy_managed=restricted)
            for restricted in (False, True)
        ]
        db.add_all([course, managed])
        await db.flush()
        near = [1., 0.] + [0.] * 766
        far = [0., 1.] + [0.] * 766
        monkeypatch.setenv("LEARNHOUSE_AI_EMBEDDING_MODEL_REVISION", "fixture-compatible")
        space, _ = query_service.embedding_provenance()
        db.add_all([
            CourseEmbedding(org_id=org.id, course_id=target.id, course_name=target.name,
                            chunk_text=label, embedding=vector, embedding_space=signature)
            for target, label, vector, signature in (
                (course, "Compatible text", far, space), (course, "Unknown legacy", near, None),
                (course, "Different model", near, "f" * 64), (managed, "Managed draft", near, space),
            )
        ])
        await db.flush()
        scope = RagAccessScope(
            request=make_synthetic_request(), current_user=AnonymousUser(), org_id=org.id,
            courses={target.id: CourseGrant(target.course_uuid) for target in (course, managed)},
        )
        provider = AsyncMock(return_value=near)
        monkeypatch.setattr(query_service, "embed_single_text", provider)
        rows = await query_service.search_course_content("question", org.id, db, scope)
        assert [row.chunk_text for row in rows] == ["Compatible text"]
        provider.assert_awaited_once()
        draft = Activity(
            org_id=org.id, course_id=course.id, activity_uuid=f"activity_{uuid4()}", name="Synthetic draft",
            activity_type=ActivityTypeEnum.TYPE_DYNAMIC, activity_sub_type=ActivitySubTypeEnum.SUBTYPE_DYNAMIC_PAGE,
            published=False,
        )
        db.add(draft)
        await db.flush()
        db.add_all([
            CourseEmbedding(
                org_id=org.id, course_id=course.id, activity_id=draft.id, activity_uuid=draft.activity_uuid,
                course_name=course.name, chunk_text="Closer unpublished content", embedding=near, embedding_space=space,
            )
            for _ in range(25)
        ])
        await db.flush()
        scope.courses.pop(managed.id)
        # Bounded single-course prefiltering excludes the 25 closer unpublished rows
        # before vector ranking, recovering the course-level row even with exact fallback off.
        monkeypatch.delenv("LEARNHOUSE_RAG_SMALL_COURSE_EXACT", raising=False)
        rows = await query_service.search_course_content("question", org.id, db, scope)
        assert [row.chunk_text for row in rows] == ["Compatible text"]
        monkeypatch.setenv("LEARNHOUSE_RAG_SMALL_COURSE_EXACT", "true")
        rows = await query_service.search_course_content("question", org.id, db, scope)
        assert [row.chunk_text for row in rows] == ["Compatible text"]
        provider.reset_mock()
        monkeypatch.setenv("LEARNHOUSE_AI_EMBEDDING_MODEL_REVISION", "fixture-not-indexed")
        assert await query_service.search_course_content("question", org.id, db, scope) == []
        provider.assert_not_awaited()
        await db.rollback()


@pytest.mark.studytomy_postgres
async def test_grounded_material_retrieval_comparison(postgres_session_factory):
    async with postgres_session_factory() as db:
        await db.execute(text("""
            CREATE TEMP TABLE synthetic_grounding (
                ref text PRIMARY KEY, org_id integer, approved boolean, permitted boolean,
                document tsvector, embedding vector(3)
            ) ON COMMIT DROP
        """))
        await db.execute(text("""
            INSERT INTO synthetic_grounding VALUES (
                :ref, :org_id, :approved, :permitted,
                to_tsvector('english', :content), CAST(:embedding AS vector)
            )
        """), [
            {"ref": ref, "org_id": org, "approved": approved, "permitted": permitted,
             "content": content, "embedding": embedding}
            for ref, org, approved, permitted, content, embedding in _ROWS
        ])
        allowed = {ref for ref, org, approved, permitted, _, _ in _ROWS if org == 1 and approved and permitted}
        scores = {method: 0.0 for method in _SELECT}
        for question, embedding, relevant in _QUERIES:
            for method, select_ranked in _SELECT.items():
                rows = (await db.execute(text(_RANKED + select_ranked), {
                    "question": question, "embedding": embedding,
                })).scalars().all()
                assert set(rows) <= allowed
                scores[method] += len(relevant.intersection(rows)) / len(relevant)
        assert scores == {"exact_vector": 3.0, "full_text": 2.0, "fusion": 4.0}
        await db.rollback()


@pytest.mark.studytomy_postgres
async def test_selective_lesson_scope_underfills_fixed_overfetch_and_filtered_hnsw(postgres_session_factory):
    async with postgres_session_factory() as db:
        await db.execute(text("""
            CREATE TEMP TABLE scoped_retrieval (
                id integer PRIMARY KEY, org_id integer NOT NULL, lesson_id integer NOT NULL,
                approved boolean NOT NULL, permitted boolean NOT NULL, embedding vector(3) NOT NULL
            ) ON COMMIT DROP
        """))
        await db.execute(text("""
            INSERT INTO scoped_retrieval
            SELECT id, CASE WHEN id <= 64 THEN 1 ELSE 2 END,
                   CASE WHEN id <= 32 THEN 1 ELSE 2 END,
                   id % 2 = 0, id % 2 <> 0, '[1,0,0]'::vector
            FROM generate_series(1, 96) AS id
        """))
        await db.execute(text("""
            INSERT INTO scoped_retrieval VALUES
                (101, 1, 1, true, true, '[0.9,0.2,0]'),
                (102, 1, 1, true, true, '[0.8,0.3,0]'),
                (103, 1, 1, true, true, '[0.7,0.4,0]')
        """))
        order = "embedding <=> '[1,0,0]'::vector"
        allowed = "org_id = 1 AND lesson_id = 1 AND approved AND permitted"
        # Apply the twenty-row cap before activity authorization, as fixed overfetch does.
        fixed = (await db.execute(text(f"""
            WITH ranked AS MATERIALIZED (
                SELECT * FROM scoped_retrieval WHERE org_id = 1 ORDER BY {order} LIMIT 20
            )
            SELECT id FROM ranked WHERE {allowed}
        """))).scalars().all()
        exact_query = f"""
            WITH authorized AS MATERIALIZED (SELECT * FROM scoped_retrieval WHERE {allowed})
            SELECT id FROM authorized ORDER BY {order}, id LIMIT 3
        """
        exact = (await db.execute(text(exact_query))).scalars().all()
        assert fixed == []
        assert exact == [101, 102, 103]

        await db.execute(text("CREATE INDEX ON scoped_retrieval USING hnsw (embedding vector_cosine_ops)"))
        await db.execute(text("ANALYZE scoped_retrieval"))
        await db.execute(text("SET LOCAL enable_seqscan = off"))
        await db.execute(text("SET LOCAL hnsw.ef_search = 8"))
        approximate_query = f"SELECT id FROM scoped_retrieval WHERE {allowed} ORDER BY {order} LIMIT 3"

        async def explain_analyze(query: str):
            result = (await db.execute(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query))).scalar_one()
            return json.loads(result) if isinstance(result, str) else result

        exact_plan = await explain_analyze(exact_query)
        assert exact_plan[0]["Execution Time"] >= 0
        assert not any(
            node.get("Index Name") == "scoped_retrieval_embedding_idx"
            for node in _plan_nodes(exact_plan[0]["Plan"])
        )

        scan_results = {}
        scan_plans = {}
        for iterative_scan in ("off", "strict_order"):
            await db.execute(text(f"SET LOCAL hnsw.iterative_scan = '{iterative_scan}'"))
            scan_plans[iterative_scan] = await explain_analyze(approximate_query)
            scan_results[iterative_scan] = (await db.execute(text(approximate_query))).scalars().all()
            assert any(
                node.get("Index Name") == "scoped_retrieval_embedding_idx"
                for node in _plan_nodes(scan_plans[iterative_scan][0]["Plan"])
            )
        assert scan_results["off"] == []
        assert scan_results["strict_order"] == exact
        assert scan_plans["off"][0]["Execution Time"] >= 0
        assert scan_plans["strict_order"][0]["Execution Time"] >= 0
        # The plans exercise filtered HNSW with iterative scans off/on; the
        # SQL authorization predicate still excludes every control row.
        assert set(fixed + scan_results["off"] + scan_results["strict_order"]) <= set(exact)
        await db.rollback()


def _plan_nodes(plan):
    yield plan
    for child in plan.get("Plans", []):
        yield from _plan_nodes(child)


@pytest.mark.studytomy_postgres
async def test_runtime_small_course_fallback_recovers_only_readable_rows(postgres_session_factory, monkeypatch):
    tag = uuid4().hex
    monkeypatch.setenv("LEARNHOUSE_RAG_SMALL_COURSE_EXACT", "true")
    async with postgres_session_factory() as db:
        org = Organization(
            name="Synthetic scoped retrieval", slug=f"scope-{tag}", org_uuid=f"scope_{tag}",
            email=f"scope-{tag}@example.invalid",
        )
        db.add(org)
        await db.flush()
        course = Course(
            org_id=org.id, course_uuid=f"scope-course-{tag}", name="Synthetic public course",
            public=True, published=True, open_to_contributors=False, studytomy_managed=False,
        )
        db.add(course)
        await db.flush()
        vector = [1.0] + [0.0] * 767
        near = [0.99, 0.1] + [0.0] * 766
        activity_rows = []
        for index in range(25):
            activity_rows.append(Activity(
                name=f"Unpublished control {index}", activity_type=ActivityTypeEnum.TYPE_DYNAMIC,
                activity_sub_type=ActivitySubTypeEnum.SUBTYPE_DYNAMIC_PAGE, content={}, published=False,
                org_id=org.id, course_id=course.id, activity_uuid=f"draft-{tag}-{index}",
            ))
        for index in range(3):
            activity_rows.append(Activity(
                name=f"Readable lesson {index}", activity_type=ActivityTypeEnum.TYPE_DYNAMIC,
                activity_sub_type=ActivitySubTypeEnum.SUBTYPE_DYNAMIC_PAGE, content={}, published=True,
                org_id=org.id, course_id=course.id, activity_uuid=f"published-{tag}-{index}",
            ))
        db.add_all(activity_rows)
        await db.flush()
        space = query_service.embedding_provenance()[0]
        db.add_all([
            CourseEmbedding(
                org_id=org.id, course_id=course.id, activity_id=activity.id,
                activity_uuid=activity.activity_uuid, course_name=course.name, activity_name=activity.name,
                source_type="page", chunk_text=activity.name,
                embedding=vector if not activity.published else near, embedding_space=space,
            )
            for activity in activity_rows
        ])
        await db.flush()
        scope = RagAccessScope(
            request=make_synthetic_request(), current_user=AnonymousUser(), org_id=org.id,
            courses={course.id: CourseGrant(course.course_uuid)},
        )
        embed = AsyncMock(return_value=vector)
        monkeypatch.setattr(query_service, "embed_single_text", embed)

        # Bounded single-course prefiltering resolves readable activity IDs before
        # vector ranking, so the 25 closer unpublished lessons cannot crowd out
        # the 3 eligible published lessons even with exact fallback disabled.
        monkeypatch.delenv("LEARNHOUSE_RAG_SMALL_COURSE_EXACT", raising=False)
        prefiltered_rows = await query_service.search_course_content("question", org.id, db, scope, top_k=5)
        assert {row.chunk_text for row in prefiltered_rows} == {
            "Readable lesson 0", "Readable lesson 1", "Readable lesson 2",
        }
        assert len(prefiltered_rows) == 3
        assert all(row.course_id == course.id and row.activity_id in {item.id for item in activity_rows if item.published}
                   for row in prefiltered_rows)
        assert not any("Unpublished" in row.chunk_text for row in prefiltered_rows)
        embed.assert_awaited_once()

        # Opt-in exact recovery preserves deterministic row-ID tie-breaking and
        # returns every eligible row even when fewer than top_k exist.
        monkeypatch.setenv("LEARNHOUSE_RAG_SMALL_COURSE_EXACT", "true")
        rows = await query_service.search_course_content("question", org.id, db, scope, top_k=5)
        assert [row.chunk_text for row in rows] == ["Readable lesson 0", "Readable lesson 1", "Readable lesson 2"]
        assert len(rows) == 3
        assert all(row.course_id == course.id and row.activity_id in {item.id for item in activity_rows if item.published}
                   for row in rows)
        assert not any("Unpublished" in row.chunk_text for row in rows)
        await db.rollback()


@pytest.mark.studytomy_postgres
@pytest.mark.parametrize("chunk_count", [1000, 1001])
async def test_exact_fallback_respects_course_chunk_and_scope_bounds(
    postgres_session_factory, monkeypatch, chunk_count,
):
    tag = uuid4().hex
    monkeypatch.setenv("LEARNHOUSE_RAG_SMALL_COURSE_EXACT", "true")
    async with postgres_session_factory() as db:
        org = Organization(
            name="Synthetic exact bound", slug=f"bound-{tag}", org_uuid=f"bound_{tag}",
            email=f"bound-{tag}@example.invalid",
        )
        db.add(org)
        await db.flush()
        course = Course(
            org_id=org.id, course_uuid=f"bound-course-{tag}", name="Bounded course",
            public=True, published=True, open_to_contributors=False, studytomy_managed=False,
        )
        other_course = Course(
            org_id=org.id, course_uuid=f"bound-other-{tag}", name="Second course",
            public=True, published=True, open_to_contributors=False, studytomy_managed=False,
        )
        db.add_all([course, other_course])
        await db.flush()
        draft = Activity(
            name="Unpublished control", activity_type=ActivityTypeEnum.TYPE_DYNAMIC,
            activity_sub_type=ActivitySubTypeEnum.SUBTYPE_DYNAMIC_PAGE, content={}, published=False,
            org_id=org.id, course_id=course.id, activity_uuid=f"bound-draft-{tag}",
        )
        db.add(draft)
        await db.flush()
        vector = [1.0] + [0.0] * 767
        space = query_service.embedding_provenance()[0]
        db.add_all([
            CourseEmbedding(
                org_id=org.id, course_id=course.id, activity_id=draft.id,
                activity_uuid=draft.activity_uuid, activity_name=draft.name, course_name=course.name,
                source_type="page", chunk_text=f"Unpublished chunk {index}", chunk_index=index,
                embedding=vector, embedding_space=space,
            )
            for index in range(chunk_count)
        ])
        await db.flush()
        single_scope = RagAccessScope(
            request=make_synthetic_request(), current_user=AnonymousUser(), org_id=org.id,
            courses={course.id: CourseGrant(course.course_uuid)},
        )
        multi_scope = RagAccessScope(
            request=make_synthetic_request(), current_user=AnonymousUser(), org_id=org.id,
            courses={course.id: CourseGrant(course.course_uuid), other_course.id: CourseGrant(other_course.course_uuid)},
        )
        embed = AsyncMock(return_value=vector)
        monkeypatch.setattr(query_service, "embed_single_text", embed)
        original_readable = query_service.readable_activity_ids
        authorization_calls = []

        async def track_authorization(activity_ids, scope, session):
            authorization_calls.append(set(activity_ids))
            return await original_readable(activity_ids, scope, session)

        monkeypatch.setattr(query_service, "readable_activity_ids", track_authorization)
        assert await query_service.search_course_content("question", org.id, db, single_scope, top_k=5) == []
        # At 1,000 rows the bounded single-course prefilter resolves activity
        # permissions once and returns empty before calling the embedding provider;
        # at 1,001 rows it skips prefiltering and exact fallback.
        assert len(authorization_calls) == (1 if chunk_count == 1000 else 0)
        assert embed.await_count == (0 if chunk_count == 1000 else 1)

        authorization_calls.clear()
        embed.reset_mock()
        assert await query_service.search_course_content("question", org.id, db, multi_scope, top_k=5) == []
        assert authorization_calls == []
        embed.assert_awaited_once()
        await db.rollback()


@pytest.mark.studytomy_postgres
async def test_runtime_prefilter_enforces_security_co_readers_and_concurrency(
    postgres_session_factory, monkeypatch,
):
    tag = uuid4().hex
    async with postgres_session_factory() as db:
        org = Organization(
            name="Synthetic security org", slug=f"sec-{tag}", org_uuid=f"sec_{tag}",
            email=f"sec-{tag}@example.invalid",
        )
        other_org = Organization(
            name="Synthetic other org", slug=f"other-{tag}", org_uuid=f"other_{tag}",
            email=f"other-{tag}@example.invalid",
        )
        db.add_all([org, other_org])
        await db.flush()

        course = Course(
            org_id=org.id, course_uuid=f"sec-course-{tag}", name="Security course",
            public=True, published=True, open_to_contributors=False, studytomy_managed=False,
        )
        managed_course = Course(
            org_id=org.id, course_uuid=f"sec-managed-{tag}", name="Managed course",
            public=False, published=False, open_to_contributors=False, studytomy_managed=True,
        )
        other_course = Course(
            org_id=other_org.id, course_uuid=f"sec-other-{tag}", name="Other tenant course",
            public=True, published=True, open_to_contributors=False, studytomy_managed=False,
        )
        db.add_all([course, managed_course, other_course])
        await db.flush()

        readable_a = Activity(
            name="Allowed lesson A", activity_type=ActivityTypeEnum.TYPE_DYNAMIC,
            activity_sub_type=ActivitySubTypeEnum.SUBTYPE_DYNAMIC_PAGE, content={}, published=True,
            org_id=org.id, course_id=course.id, activity_uuid=f"sec-a-{tag}",
        )
        readable_b = Activity(
            name="Allowed lesson B", activity_type=ActivityTypeEnum.TYPE_DYNAMIC,
            activity_sub_type=ActivitySubTypeEnum.SUBTYPE_DYNAMIC_PAGE, content={}, published=True,
            org_id=org.id, course_id=course.id, activity_uuid=f"sec-b-{tag}",
        )
        draft_act = Activity(
            name="Draft lesson", activity_type=ActivityTypeEnum.TYPE_DYNAMIC,
            activity_sub_type=ActivitySubTypeEnum.SUBTYPE_DYNAMIC_PAGE, content={}, published=False,
            org_id=org.id, course_id=course.id, activity_uuid=f"sec-draft-{tag}",
        )
        locked_act = Activity(
            name="Locked lesson", activity_type=ActivityTypeEnum.TYPE_DYNAMIC,
            activity_sub_type=ActivitySubTypeEnum.SUBTYPE_DYNAMIC_PAGE, content={}, published=True,
            lock_type=ActivityLockType.RESTRICTED,
            org_id=org.id, course_id=course.id, activity_uuid=f"sec-locked-{tag}",
        )
        other_act = Activity(
            name="Other tenant lesson", activity_type=ActivityTypeEnum.TYPE_DYNAMIC,
            activity_sub_type=ActivitySubTypeEnum.SUBTYPE_DYNAMIC_PAGE, content={}, published=True,
            org_id=other_org.id, course_id=other_course.id, activity_uuid=f"sec-other-act-{tag}",
        )
        db.add_all([readable_a, readable_b, draft_act, locked_act, other_act])
        await db.flush()

        near = [1.0, 0.0] + [0.0] * 766
        mid = [0.95, 0.2] + [0.0] * 766
        far = [0.85, 0.4] + [0.0] * 766
        space = query_service.embedding_provenance()[0]
        db.add_all([
            CourseEmbedding(
                org_id=org.id, course_id=course.id, activity_id=readable_a.id,
                activity_uuid=readable_a.activity_uuid, activity_name=readable_a.name,
                course_name=course.name, source_type="page", chunk_text="Allowed A",
                embedding=mid, embedding_space=space,
            ),
            CourseEmbedding(
                org_id=org.id, course_id=course.id, activity_id=readable_b.id,
                activity_uuid=readable_b.activity_uuid, activity_name=readable_b.name,
                course_name=course.name, source_type="page", chunk_text="Allowed B",
                embedding=far, embedding_space=space,
            ),
            CourseEmbedding(
                org_id=org.id, course_id=course.id, activity_id=draft_act.id,
                activity_uuid=draft_act.activity_uuid, activity_name=draft_act.name,
                course_name=course.name, source_type="page", chunk_text="Draft secret",
                embedding=near, embedding_space=space,
            ),
            CourseEmbedding(
                org_id=org.id, course_id=course.id, activity_id=locked_act.id,
                activity_uuid=locked_act.activity_uuid, activity_name=locked_act.name,
                course_name=course.name, source_type="page", chunk_text="Locked secret",
                embedding=near, embedding_space=space,
            ),
            CourseEmbedding(
                org_id=org.id, course_id=course.id, activity_id=readable_a.id,
                activity_uuid=readable_a.activity_uuid, activity_name=readable_a.name,
                course_name=course.name, source_type="page", chunk_text="Incompatible vector",
                embedding=near, embedding_space="0" * 64,
            ),
            CourseEmbedding(
                org_id=org.id, course_id=managed_course.id, activity_id=None,
                course_name=managed_course.name, source_type="course", chunk_text="Managed secret",
                embedding=near, embedding_space=space,
            ),
            CourseEmbedding(
                org_id=other_org.id, course_id=other_course.id, activity_id=other_act.id,
                activity_uuid=other_act.activity_uuid, activity_name=other_act.name,
                course_name=other_course.name, source_type="page", chunk_text="Cross-tenant secret",
                embedding=near, embedding_space=space,
            ),
        ])
        await db.flush()

        # Admin caller with an unprivileged co-reader must only receive content
        # readable by both (drafts and locked activities excluded).
        admin_principal = PublicUser(
            id=9001, username=f"adm_{tag}", first_name="A", last_name="Admin",
            email=f"adm-{tag}@example.invalid", user_uuid=f"user_adm_{tag}",
        )
        learner_scope = RagAccessScope(
            request=make_synthetic_request(), current_user=AnonymousUser(), org_id=org.id,
            courses={course.id: CourseGrant(course.course_uuid)},
        )
        admin_with_co_reader = RagAccessScope(
            request=make_synthetic_request(), current_user=admin_principal, org_id=org.id,
            courses={course.id: CourseGrant(course.course_uuid, is_admin=True)},
            co_readers=[learner_scope],
        )
        monkeypatch.delenv("LEARNHOUSE_RAG_SMALL_COURSE_EXACT", raising=False)
        monkeypatch.setattr(query_service, "embed_single_text", AsyncMock(return_value=near))
        rows = await query_service.search_course_content("question", org.id, db, admin_with_co_reader, top_k=5)
        assert [row.chunk_text for row in rows] == ["Allowed A", "Allowed B"]

        # Mid-request revocation: readable_a is unpublished after prefiltering
        # and before final filter_readable_chunks revalidation.
        async def revoke_during_embed(_question):
            readable_a.published = False
            db.add(readable_a)
            await db.flush()
            return near

        monkeypatch.setattr(query_service, "embed_single_text", revoke_during_embed)
        rows_after_revoke = await query_service.search_course_content("question", org.id, db, learner_scope, top_k=5)
        assert [row.chunk_text for row in rows_after_revoke] == ["Allowed B"]

        # Concurrent index replacement: while exact fallback is enabled, inserting
        # rows during the request pushes the course above 1,000 rows so the exact
        # fallback's SQL cap check refuses to materialize >1,000 rows.
        readable_a.published = True
        db.add(readable_a)
        await db.flush()

        async def expand_course_above_cap(_question):
            db.add_all([
                CourseEmbedding(
                    org_id=org.id, course_id=course.id, activity_id=draft_act.id,
                    activity_uuid=draft_act.activity_uuid, activity_name=draft_act.name,
                    course_name=course.name, source_type="page", chunk_text=f"Burst draft {idx}",
                    chunk_index=idx + 100, embedding=near, embedding_space=space,
                )
                for idx in range(1005)
            ])
            await db.flush()
            return near

        monkeypatch.setenv("LEARNHOUSE_RAG_SMALL_COURSE_EXACT", "true")
        monkeypatch.setattr(query_service, "embed_single_text", expand_course_above_cap)
        rows_after_burst = await query_service.search_course_content("question", org.id, db, learner_scope, top_k=5)
        assert {row.chunk_text for row in rows_after_burst} == {"Allowed A", "Allowed B"}
        assert not any("Draft" in row.chunk_text or "Burst" in row.chunk_text for row in rows_after_burst)
        await db.rollback()


# ---------------------------------------------------------------------------
# [A2|P2] 32-query offline benchmark comparing exact cosine, filtered HNSW,
# bounded strict iterative HNSW, PostgreSQL simple FTS, and RRF (k=60).
# ---------------------------------------------------------------------------

def _bench_vec(weights: dict[int, float], dims: int = 16) -> str:
    values = [0.0] * dims
    for axis, weight in weights.items():
        values[axis] = weight
    return "[" + ",".join(f"{v:.4f}" for v in values) + "]"


# 32 synthetic authorized chunks (org_id=1, embedding_space='space_active',
# published=True, locked=False, permitted=True) across STEM/Math/ICT topics.
_BENCH_AUTHORIZED_ROWS = (
    # Conceptual & math/fractions cluster (axes 0, 1)
    ("auth-mat-frac-eq", "LK-MAT-G06-F01", "LK-MAT-G06-F01 Equivalent fractions divide a whole into proportional equal shares using common factors.", _bench_vec({0: 0.96, 1: 0.25})),
    ("auth-mat-frac-add", "LK-MAT-G06-F02", "LK-MAT-G06-F02 Adding unlike fractions requires finding the lowest common denominator before summing numerators.", _bench_vec({0: 0.92, 1: 0.38})),
    ("auth-mat-ratio", "LK-MAT-G07-R01", "LK-MAT-G07-R01 Ratios and direct proportion compare quantities scaled by a constant factor.", _bench_vec({0: 0.85, 1: 0.50})),
    ("auth-mat-linear", "LK-MAT-G08-A01", "LK-MAT-G08-A01 Solving linear equations balances inverse operations across both sides of the equality.", _bench_vec({1: 0.95, 0: 0.20})),
    ("auth-mat-quad", "LK-MAT-G10-Q01", "LK-MAT-G10-Q01 Quadratic roots come from factorization or the discriminant b^2 - 4ac.", _bench_vec({1: 0.90, 2: 0.35})),
    ("auth-mat-trig", "LK-MAT-G11-T01", "LK-MAT-G11-T01 Right-angled triangle ratios define sine, cosine, and tangent of an acute angle.", _bench_vec({1: 0.80, 2: 0.55})),
    # Mechanics & motion cluster (axes 2, 3)
    ("auth-phy-mom-law", "LK-PHY-G10-M02", "LK-PHY-G10-M02 Linear momentum equals mass times velocity in kg m/s and is conserved in isolated collisions.", _bench_vec({2: 0.95, 3: 0.25})),
    ("auth-phy-mom-exp", None, "EXP-MOM-01 Air-track trolley collision trial measuring momentum in kg m/s before and after impact.", _bench_vec({2: 0.40, 3: 0.40, 12: 0.80})),
    ("auth-phy-grav", "LK-PHY-G10-M04", "LK-PHY-G10-M04 Free-fall near Earth accelerates objects downward at 9.81 m/s^2 regardless of mass.", _bench_vec({2: 0.88, 3: 0.45})),
    ("auth-phy-acc-unit", None, "Acceleration records change in velocity per second expressed in SI unit m/s^2 along a straight path.", _bench_vec({2: 0.72, 3: 0.68})),
    ("auth-phy-circ", "LK-PHY-G12-M07", "LK-PHY-G12-M07 Centripetal acceleration points toward the center of circular motion as v^2 / r.", _bench_vec({3: 0.96, 2: 0.22})),
    ("auth-phy-work", "LK-PHY-G10-M05", "LK-PHY-G10-M05 Mechanical energy transforms between gravitational potential energy and kinetic motion without loss in ideal systems.", _bench_vec({3: 0.90, 4: 0.30})),
    # Thermal & gas physics cluster (axes 4, 5)
    ("auth-phy-heat-flow", "LK-PHY-G11-H01", "LK-PHY-G11-H01 Thermal energy migrates spontaneously from hotter bodies toward colder surroundings until equilibrium.", _bench_vec({4: 0.97, 5: 0.18})),
    ("auth-phy-shc", "LK-PHY-G11-H02", "LK-PHY-G11-H02 Specific heat capacity uses J/kg K to relate energy transfer, mass, and temperature rise.", _bench_vec({4: 0.84, 5: 0.52})),
    ("auth-phy-kelvin", None, "Absolute zero reference converts Celsius to Kelvin by adding 273.15 K for gas law calculations.", _bench_vec({4: 0.50, 5: 0.45, 13: 0.74})),
    ("auth-phy-pressure", "LK-PHY-G11-H04", "LK-PHY-G11-H04 Standard atmospheric pressure equals 101.325 kPa and balances a column of mercury.", _bench_vec({5: 0.94, 4: 0.30})),
    ("auth-phy-bar-unit", None, "Gas pressure readings in kPa vary inversely with volume at constant temperature under Boyle's law.", _bench_vec({5: 0.86, 4: 0.48})),
    # Electricity & optics cluster (axes 6, 7)
    ("auth-phy-em-ind", "LK-PHY-G12-E05", "LK-PHY-G12-E05 Electromagnetic induction under Faraday's law produces an EMF proportional to magnetic flux change.", _bench_vec({6: 0.96, 7: 0.22})),
    ("auth-phy-ohm", None, "EQ-OHM-02 Potential difference V equals current I multiplied by resistance R for an ohmic conductor.", _bench_vec({6: 0.45, 7: 0.40, 14: 0.79})),
    ("auth-phy-light-c", "LK-PHY-G12-W01", "LK-PHY-G12-W01 Electromagnetic waves travel in vacuum at 3.0e8 m/s across the spectrum.", _bench_vec({7: 0.95, 6: 0.28})),
    ("auth-phy-ray-fig", None, "FIG-RAY-04 Convex lens ray diagram tracing principal focus, optical center, and inverted real image.", _bench_vec({7: 0.42, 6: 0.38, 15: 0.82})),
    # Chemistry cluster (axes 8, 9)
    ("auth-che-acid", "LK-CHE-G11-A03", "LK-CHE-G11-A03 Acid-base neutralization produces salt and water when hydrogen ions react with hydroxide ions.", _bench_vec({8: 0.95, 9: 0.26})),
    ("auth-che-ph-tab", None, "TAB-PH-07 Reference indicator chart listing acidic, neutral pH 7, and alkaline buffer ranges.", _bench_vec({8: 0.35, 9: 0.45, 12: 0.82})),
    ("auth-che-stoich", "LK-CHE-G12-S01", "LK-CHE-G12-S01 Stoichiometry identifies the limiting reagent to predict theoretical product yield.", _bench_vec({8: 0.88, 9: 0.45})),
    ("auth-che-avogadro", None, "One mole contains Avogadro's constant 6.022e23 elementary entities for molar mass conversion.", _bench_vec({8: 0.44, 9: 0.46, 13: 0.77})),
    ("auth-che-molarity", "LK-CHE-G12-S02", "LK-CHE-G12-S02 Solution concentration is measured in mol/dm^3 by dividing solute moles by volume.", _bench_vec({9: 0.95, 8: 0.25})),
    ("auth-che-enthalpy", "LK-CHE-G12-E03", "LK-CHE-G12-E03 Enthalpy change is positive for an endothermic reaction absorbing heat from surroundings.", _bench_vec({9: 0.89, 4: 0.42})),
    # Biology & ICT cluster (axes 10, 11)
    ("auth-bio-cell", "LK-BIO-G10-C04", "LK-BIO-G10-C04 Eukaryotic cell organelles coordinate respiration in mitochondria and protein synthesis at ribosomes.", _bench_vec({10: 0.96, 11: 0.20})),
    ("auth-bio-osmosis", "LK-BIO-G10-C05", "LK-BIO-G10-C05 Osmosis moves water across a semipermeable membrane from high to low water potential.", _bench_vec({10: 0.90, 11: 0.40})),
    ("auth-bio-photo", "LK-BIO-G10-P01", "LK-BIO-G10-P01 Photosynthesis uses chlorophyll inside chloroplasts to convert sunlight, carbon dioxide, and water into glucose.", _bench_vec({10: 0.82, 11: 0.54})),
    ("auth-ict-logic", "LK-ICT-G11-L06", "LK-ICT-G11-L06 Boolean logic gates combine AND, OR, and NOT truth tables to build digital circuits.", _bench_vec({11: 0.96, 10: 0.20})),
    ("auth-ict-keys", "LK-ICT-G11-D02", "LK-ICT-G11-D02 Relational tables enforce entity integrity with unique primary keys and foreign key references.", _bench_vec({11: 0.88, 10: 0.44})),
)

# 32 judged queries across 6 categories:
# (category, question, query_embedding, relevant_refs)
_BENCH_QUERIES = (
    # 6 conceptual paraphrases (no verbatim keyword overlap; cosine succeeds, simple FTS misses)
    ("paraphrase", "How can two differently sliced shapes still show the same portion?", _bench_vec({0: 0.98, 1: 0.18}), {"auth-mat-frac-eq"}),
    ("paraphrase", "Why does warmth travel from a hot mug into cool air?", _bench_vec({4: 0.99, 5: 0.12}), {"auth-phy-heat-flow"}),
    ("paraphrase", "Which inward pull keeps a whirling stone moving along a curve?", _bench_vec({3: 0.98, 2: 0.18}), {"auth-phy-circ"}),
    ("paraphrase", "How does stored height turn into speed when a ball drops?", _bench_vec({3: 0.92, 4: 0.32}), {"auth-phy-work"}),
    ("paraphrase", "How do plant leaves build sugar using light energy?", _bench_vec({10: 0.80, 11: 0.58}), {"auth-bio-photo"}),
    ("paraphrase", "What keeps database records uniquely identified across linked tables?", _bench_vec({11: 0.87, 10: 0.46}), {"auth-ict-keys"}),
    # 6 syllabus codes (embedding perturbed across general axes so exact cosine can miss; simple FTS + code match + RRF hit)
    ("syllabus_code", "LK-MAT-G06-F01", _bench_vec({2: 0.70, 3: 0.70}), {"auth-mat-frac-eq"}),
    ("syllabus_code", "LK-PHY-G10-M02", _bench_vec({4: 0.70, 5: 0.70}), {"auth-phy-mom-law"}),
    ("syllabus_code", "LK-CHE-G11-A03", _bench_vec({0: 0.70, 1: 0.70}), {"auth-che-acid"}),
    ("syllabus_code", "LK-BIO-G10-C04", _bench_vec({6: 0.70, 7: 0.70}), {"auth-bio-cell"}),
    ("syllabus_code", "LK-PHY-G12-E05", _bench_vec({8: 0.70, 9: 0.70}), {"auth-phy-em-ind"}),
    ("syllabus_code", "LK-ICT-G11-L06", _bench_vec({0: 0.65, 2: 0.75}), {"auth-ict-logic"}),
    # 6 scientific terminology queries
    ("terminology", "stoichiometry limiting reagent", _bench_vec({8: 0.87, 9: 0.46}), {"auth-che-stoich"}),
    ("terminology", "electromagnetic induction Faraday", _bench_vec({6: 0.95, 7: 0.25}), {"auth-phy-em-ind"}),
    ("terminology", "osmosis semipermeable membrane", _bench_vec({10: 0.91, 11: 0.39}), {"auth-bio-osmosis"}),
    ("terminology", "centripetal acceleration circular motion", _bench_vec({3: 0.95, 2: 0.24}), {"auth-phy-circ"}),
    ("terminology", "enthalpy endothermic reaction", _bench_vec({9: 0.88, 4: 0.44}), {"auth-che-enthalpy"}),
    ("terminology", "photosynthesis chlorophyll chloroplasts", _bench_vec({10: 0.83, 11: 0.53}), {"auth-bio-photo"}),
    # 5 units queries
    ("units", "m/s^2", _bench_vec({2: 0.82, 3: 0.55}), {"auth-phy-grav", "auth-phy-acc-unit"}),
    ("units", "mol/dm^3", _bench_vec({9: 0.94, 8: 0.28}), {"auth-che-molarity"}),
    ("units", "kPa", _bench_vec({5: 0.91, 4: 0.38}), {"auth-phy-pressure", "auth-phy-bar-unit"}),
    ("units", "kg m/s", _bench_vec({2: 0.92, 3: 0.32}), {"auth-phy-mom-law", "auth-phy-mom-exp"}),
    ("units", "J/kg K", _bench_vec({4: 0.85, 5: 0.50}), {"auth-phy-shc"}),
    # 5 numeric labels queries
    ("numeric_label", "9.81 m/s^2", _bench_vec({2: 0.87, 3: 0.47}), {"auth-phy-grav"}),
    ("numeric_label", "273.15 K", _bench_vec({4: 0.90, 5: 0.40}), {"auth-phy-kelvin"}),
    ("numeric_label", "6.022e23", _bench_vec({8: 0.90, 9: 0.40}), {"auth-che-avogadro"}),
    ("numeric_label", "3.0e8 m/s", _bench_vec({7: 0.94, 6: 0.30}), {"auth-phy-light-c"}),
    ("numeric_label", "101.325 kPa", _bench_vec({5: 0.93, 4: 0.32}), {"auth-phy-pressure"}),
    # 4 exact identifiers queries (low cosine alignment to simulate opaque doc/lab IDs)
    ("identifier", "EXP-MOM-01", _bench_vec({2: 0.95, 3: 0.25}), {"auth-phy-mom-exp"}),
    ("identifier", "EQ-OHM-02", _bench_vec({6: 0.95, 7: 0.25}), {"auth-phy-ohm"}),
    ("identifier", "TAB-PH-07", _bench_vec({8: 0.95, 9: 0.25}), {"auth-che-ph-tab"}),
    ("identifier", "FIG-RAY-04", _bench_vec({7: 0.95, 6: 0.25}), {"auth-phy-ray-fig"}),
)


async def run_grounded_retrieval_benchmark(db) -> dict[str, dict[str, float | int]]:
    """Run the 32-query offline PostgreSQL benchmark and return measured metrics."""
    assert len(_BENCH_QUERIES) == 32
    await db.execute(text("""
        CREATE TEMP TABLE bench_retrieval (
            ref text PRIMARY KEY,
            org_id integer NOT NULL,
            embedding_space text,
            published boolean NOT NULL,
            locked boolean NOT NULL,
            permitted boolean NOT NULL,
            curriculum_code text,
            document tsvector NOT NULL,
            embedding vector(16) NOT NULL
        ) ON COMMIT DROP
    """))

    rows_to_insert = []
    for ref, code, content, embedding in _BENCH_AUTHORIZED_ROWS:
        rows_to_insert.append({
            "ref": ref,
            "org_id": 1,
            "embedding_space": "space_active",
            "published": True,
            "locked": False,
            "permitted": True,
            "curriculum_code": code,
            "content": f"{code or ''} {content}".strip(),
            "embedding": embedding,
        })

    # Add 256 closer unauthorized control chunks across other tenants, mismatched
    # or NULL embedding spaces, drafts, locked activities, and revoked access.
    control_profiles = (
        ("ctrl-tenant2", 2, "space_active", True, False, True),
        ("ctrl-tenant3", 3, "space_active", True, False, True),
        ("ctrl-space-null", 1, None, True, False, True),
        ("ctrl-space-other", 1, "space_other_model", True, False, True),
        ("ctrl-draft-a", 1, "space_active", False, False, True),
        ("ctrl-draft-b", 1, "space_active", False, False, True),
        ("ctrl-locked", 1, "space_active", True, True, True),
        ("ctrl-revoked", 1, "space_active", True, False, False),
    )
    for query_idx, (_cat, question, q_emb, _rel) in enumerate(_BENCH_QUERIES):
        code_hint = question if question.startswith("LK-") else None
        for profile_idx, (prefix, org_id, emb_space, pub, lck, perm) in enumerate(control_profiles):
            rows_to_insert.append({
                "ref": f"{prefix}-{query_idx:02d}-{profile_idx}",
                "org_id": org_id,
                "embedding_space": emb_space,
                "published": pub,
                "locked": lck,
                "permitted": perm,
                "curriculum_code": code_hint,
                "content": f"{question} unauthorized control distractor {prefix}",
                "embedding": q_emb,
            })

    await db.execute(text("""
        INSERT INTO bench_retrieval (
            ref, org_id, embedding_space, published, locked, permitted,
            curriculum_code, document, embedding
        )
        VALUES (
            :ref, :org_id, :embedding_space, :published, :locked, :permitted,
            :curriculum_code, to_tsvector('simple', :content), CAST(:embedding AS vector)
        )
    """), rows_to_insert)

    await db.execute(text("CREATE INDEX bench_retrieval_hnsw_idx ON bench_retrieval USING hnsw (embedding vector_cosine_ops)"))
    await db.execute(text("ANALYZE bench_retrieval"))

    allowed_refs = {row[0] for row in _BENCH_AUTHORIZED_ROWS}
    auth_where = (
        "org_id = 1 AND embedding_space = 'space_active' "
        "AND published AND NOT locked AND permitted"
    )
    exact_sql = f"""
        WITH authorized AS MATERIALIZED (
            SELECT ref, embedding FROM bench_retrieval WHERE {auth_where}
        )
        SELECT ref FROM authorized
        ORDER BY embedding <=> CAST(:embedding AS vector), ref
        LIMIT 5
    """
    hnsw_sql = f"""
        SELECT ref FROM bench_retrieval
        WHERE {auth_where}
        ORDER BY embedding <=> CAST(:embedding AS vector)
        LIMIT 5
    """
    hybrid_ctes = f"""
        WITH authorized AS MATERIALIZED (
            SELECT ref, curriculum_code, document, embedding
            FROM bench_retrieval
            WHERE {auth_where}
        ), vectors AS (
            SELECT ref, row_number() OVER (
                ORDER BY embedding <=> CAST(:embedding AS vector), ref
            ) AS rank
            FROM authorized
        ), words AS (
            SELECT ref, row_number() OVER (
                ORDER BY (
                    CASE
                        WHEN curriculum_code IS NOT NULL
                             AND position(lower(curriculum_code) IN lower(:question)) > 0
                        THEN 10.0
                        ELSE 0.0
                    END
                    + ts_rank_cd(document, websearch_to_tsquery('simple', :question))
                ) DESC, ref
            ) AS rank
            FROM authorized
            WHERE document @@ websearch_to_tsquery('simple', :question)
               OR (
                   curriculum_code IS NOT NULL
                   AND position(lower(curriculum_code) IN lower(:question)) > 0
               )
        )
    """
    fts_sql = hybrid_ctes + " SELECT ref FROM words ORDER BY rank LIMIT 5"
    rrf_sql = hybrid_ctes + """
        SELECT coalesce(vectors.ref, words.ref) AS ref
        FROM vectors FULL JOIN words USING (ref)
        ORDER BY coalesce(1.0 / (60 + vectors.rank), 0.0)
               + coalesce(1.0 / (60 + words.rank), 0.0) DESC, ref
        LIMIT 5
    """

    async def measure(sql_text: str, params: dict, *, expect_hnsw: bool | None = None):
        plan_raw = (await db.execute(
            text("EXPLAIN (ANALYZE, FORMAT JSON) " + sql_text), params,
        )).scalar_one()
        plan_json = json.loads(plan_raw) if isinstance(plan_raw, str) else plan_raw
        exec_ms = float(plan_json[0]["Execution Time"])
        if expect_hnsw is not None:
            uses_hnsw = any(
                node.get("Index Name") == "bench_retrieval_hnsw_idx"
                for node in _plan_nodes(plan_json[0]["Plan"])
            )
            assert uses_hnsw is expect_hnsw
        refs = list((await db.execute(text(sql_text), params)).scalars().all())
        return refs, exec_ms

    method_specs = (
        ("exact_vector", exact_sql, False, None, False),
        ("filtered_hnsw", hnsw_sql, True, "off", True),
        ("iterative_hnsw", hnsw_sql, True, "strict_order", True),
        ("full_text", fts_sql, False, None, None),
        ("rrf_hybrid", rrf_sql, False, None, None),
    )

    metrics: dict[str, dict[str, float | int]] = {}
    for name, sql_text, force_hnsw, iterative_mode, expect_hnsw in method_specs:
        recalls: list[float] = []
        times_ms: list[float] = []
        underfilled = 0
        unauthorized = 0

        await db.execute(text(f"SET LOCAL enable_seqscan = {'off' if force_hnsw else 'on'}"))
        if iterative_mode is not None:
            await db.execute(text("SET LOCAL hnsw.ef_search = 8"))
            await db.execute(text(f"SET LOCAL hnsw.iterative_scan = '{iterative_mode}'"))
            if iterative_mode == "strict_order":
                await db.execute(text("SET LOCAL hnsw.max_scan_tuples = 2000"))

        for _category, question, q_emb, relevant in _BENCH_QUERIES:
            params = {"question": question, "embedding": q_emb}
            refs, exec_ms = await measure(sql_text, params, expect_hnsw=expect_hnsw)
            unauthorized += sum(1 for ref in refs if ref not in allowed_refs)
            if len(refs) < 5:
                underfilled += 1
            recalls.append(len(relevant.intersection(refs)) / len(relevant))
            times_ms.append(exec_ms)

        sorted_times = sorted(times_ms)
        p95_idx = min(len(sorted_times) - 1, int(0.95 * (len(sorted_times) - 1) + 0.5))
        metrics[name] = {
            "queries": len(_BENCH_QUERIES),
            "recall_at_5": round(sum(recalls) / len(recalls), 4),
            "underfilled": underfilled,
            "median_ms": round(statistics.median(sorted_times), 3),
            "p95_ms": round(sorted_times[p95_idx], 3),
            "unauthorized": unauthorized,
        }

    await db.execute(text("SET LOCAL enable_seqscan = on"))
    return metrics


@pytest.mark.studytomy_postgres
async def test_grounded_retrieval_32_query_benchmark_comparison(postgres_session_factory):
    async with postgres_session_factory() as db:
        results = await run_grounded_retrieval_benchmark(db)
        await db.rollback()

    for name, stats in results.items():
        assert stats["unauthorized"] == 0, f"{name} returned unauthorized rows: {stats}"
        assert stats["queries"] == 32
        assert stats["median_ms"] >= 0
        assert stats["p95_ms"] >= stats["median_ms"]

    # Selective filtered HNSW without iterative scan underfills heavily;
    # bounded strict iterative HNSW eliminates underfill and recovers recall
    # close to (though still approximate relative to) exact authorized cosine ranking.
    assert results["filtered_hnsw"]["underfilled"] > 0
    assert results["iterative_hnsw"]["underfilled"] == 0
    assert results["iterative_hnsw"]["recall_at_5"] > results["filtered_hnsw"]["recall_at_5"]
    assert results["iterative_hnsw"]["recall_at_5"] <= results["exact_vector"]["recall_at_5"]

    # RRF (k=60) over identical authorized sets beats both standalone exact cosine
    # (which misses opaque syllabus codes/identifiers) and standalone simple FTS
    # (which misses conceptual paraphrases).
    assert results["exact_vector"]["recall_at_5"] < 1.0
    assert results["full_text"]["recall_at_5"] < 1.0
    assert results["rrf_hybrid"]["recall_at_5"] > results["exact_vector"]["recall_at_5"]
    assert results["rrf_hybrid"]["recall_at_5"] > results["full_text"]["recall_at_5"]
    assert results["rrf_hybrid"]["underfilled"] == 0
