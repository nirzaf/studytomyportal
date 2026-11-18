"""Offline PostgreSQL retrieval comparison over synthetic authorized materials."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.db.course_embeddings import CourseEmbedding
from src.db.courses.activities import Activity, ActivitySubTypeEnum, ActivityTypeEnum
from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.users import AnonymousUser
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
        monkeypatch.delenv("LEARNHOUSE_RAG_SMALL_COURSE_EXACT", raising=False)
        assert await query_service.search_course_content("question", org.id, db, scope) == []
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
        exact = (await db.execute(text(f"""
            WITH authorized AS MATERIALIZED (SELECT * FROM scoped_retrieval WHERE {allowed})
            SELECT id FROM authorized ORDER BY {order}, id LIMIT 3
        """))).scalars().all()
        assert fixed == []
        assert exact == [101, 102, 103]

        await db.execute(text("CREATE INDEX ON scoped_retrieval USING hnsw (embedding vector_cosine_ops)"))
        await db.execute(text("ANALYZE scoped_retrieval"))
        await db.execute(text("SET LOCAL enable_seqscan = off"))
        await db.execute(text("SET LOCAL hnsw.ef_search = 8"))
        await db.execute(text("SET LOCAL hnsw.iterative_scan = off"))
        approximate_query = f"SELECT id FROM scoped_retrieval WHERE {allowed} ORDER BY {order} LIMIT 3"
        plan = (await db.execute(text("EXPLAIN " + approximate_query))).scalars().all()
        assert any("Index Scan" in line and "_embedding_idx" in line for line in plan)
        approximate = (await db.execute(text(approximate_query))).scalars().all()
        assert approximate == []
        # Neither ranking method can expose the ineligible neighbors.
        assert set(fixed + approximate) <= set(exact)
        await db.rollback()
