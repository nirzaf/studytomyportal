"""Retrieval output: citable sources and labelled context for the model."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.ai.rag import query_service
from src.services.ai.rag.types import Source


def _row(**overrides):
    row = {
        "id": 1, "course_id": 1, "activity_id": 7, "activity_uuid": "activity_7", "activity_name": "Lecture",
        "chapter_name": "Week 1", "course_name": "Biology", "course_uuid": "course_bio", "source_type": "video",
        "block_uuid": None, "locator": {"start": 192.4}, "chunk_text": "Mitochondria make ATP.", "distance": 0.25,
    }
    row.update(overrides)
    return SimpleNamespace(**row)


class TestSourceFromRow:
    def test_maps_every_field(self):
        source = query_service.source_from_row(_row())
        assert source.to_dict() == {
            "source_type": "video", "course_uuid": "course_bio", "course_name": "Biology",
            "chapter_name": "Week 1", "activity_uuid": "activity_7", "activity_name": "Lecture",
            "block_uuid": None, "locator": {"start": 192.4}, "title": "Lecture",
            "snippet": "Mitochondria make ATP.", "similarity": 0.75,
        }

    def test_course_and_chapter_rows_are_titled_by_what_they_describe(self):
        chapter = query_service.source_from_row(
            _row(activity_id=None, activity_uuid="", activity_name="", source_type="chapter", locator=None)
        )
        assert (chapter.activity_uuid, chapter.title) == (None, "Week 1")
        course = query_service.source_from_row(
            _row(activity_id=None, activity_uuid="", activity_name="", chapter_name="", source_type="course")
        )
        assert course.title == "Biology"

    def test_locator_stored_as_text_is_decoded(self):
        source = query_service.source_from_row(_row(locator=json.dumps({"page": 3}), distance=None))
        assert (source.locator, source.similarity) == ({"page": 3}, None)

    def test_snippet_is_capped(self):
        assert len(query_service.source_from_row(_row(chunk_text="x" * 1000)).snippet) == query_service.SNIPPET_CHARS


class TestLabels:
    def test_format_timestamp(self):
        assert query_service.format_timestamp(0) == "00:00"
        assert query_service.format_timestamp(192.9) == "03:12"
        assert query_service.format_timestamp(3725) == "1:02:05"

    def test_source_label(self):
        label = query_service.source_label
        assert label(Source("video", "c", "C", title="Intro", locator={"start": 75})) == 'Video "Intro" at 01:15'
        assert label(Source("document_activity", "c", "C", title="Guide", locator={"page": 4})) == 'PDF "Guide", page 4'
        assert label(Source("chapter", "c", "C", title="Week 1")) == 'Chapter "Week 1"'
        assert label(Source("something_new", "c", "C", title="X")) == 'Content "X"'


class TestQueryCourseRag:
    async def test_empty_authorized_course_scope_skips_embedding(self):
        embed = AsyncMock()
        with patch.object(query_service, "embed_single_text", new=embed):
            rows = await query_service.search_course_content(
                "question", 1, None, SimpleNamespace(org_id=1, course_ids=[]),
            )
        assert rows == []
        embed.assert_not_awaited()

    async def test_numbers_sources_per_place_and_labels_context(self):
        rows = [
            _row(chunk_text="first"),
            _row(chunk_text="second", locator={"start": 900}),
            _row(chunk_text="another chunk at the same timestamp", locator={"start": 900}),
            _row(activity_id=None, activity_uuid="", activity_name="", chapter_name="", source_type="course",
                 locator=None, chunk_text="course one"),
            # The course row of another course must not merge with the first one.
            _row(activity_id=None, activity_uuid="", activity_name="", chapter_name="", source_type="course",
                 locator=None, course_uuid="course_chem", course_name="Chemistry", chunk_text="course two"),
        ]
        scope = SimpleNamespace(course_ids=[1])
        with patch.object(query_service, "search_course_content", new=AsyncMock(return_value=rows)):
            result = await query_service.query_course_rag("q", 1, None, scope)

        assert [s["title"] for s in result["sources"]] == ["Lecture", "Lecture", "Biology", "Chemistry"]
        assert result["sources"][0]["locator"] == {"start": 192.4}
        assert result["sources"][1]["locator"] == {"start": 900}
        parts = result["context"].split("\n\n---\n\n")
        assert parts[0] == '[Source 1] Video "Lecture" at 03:12\nfirst'
        assert parts[1] == '[Source 2] Video "Lecture" at 15:00\nsecond'
        assert parts[2].startswith('[Source 2] Video "Lecture"')
        assert parts[3].startswith('[Source 3] Course "Biology"')
        assert parts[4].startswith('[Source 4] Course "Chemistry"')

    async def test_pdf_pages_and_revisions_have_distinct_citations(self):
        rows = [
            _row(source_type="document_activity", locator={"page": 2}, content_hash="a" * 64),
            _row(source_type="document_activity", locator='{"page": 3}', content_hash="a" * 64),
            _row(source_type="document_activity", locator={"page": 3}, content_hash="b" * 64),
        ]
        with patch.object(query_service, "search_course_content", new=AsyncMock(return_value=rows)):
            result = await query_service.query_course_rag("q", 1, None, SimpleNamespace(course_ids=[1]))
        assert [(source["locator"]["page"], source["content_hash"]) for source in result["sources"]] == [
            (2, "a" * 64), (3, "a" * 64), (3, "b" * 64),
        ]

    async def test_nothing_found(self):
        with patch.object(query_service, "search_course_content", new=AsyncMock(return_value=[])):
            assert await query_service.query_course_rag("q", 1, None, SimpleNamespace(course_ids=[1])) == {
                "context": "", "sources": [],
            }


def _mock_result(*, scalar=None, scalars=None, rows=None):
    result = MagicMock()
    result.scalar_one.return_value = scalar
    result.scalars.return_value.all.return_value = [] if scalars is None else scalars
    result.fetchall.return_value = [] if rows is None else rows
    return result


class TestSearchCourseContentPrefiltering:
    @pytest.mark.parametrize("org_id,scope_org,course_ids,top_k", [
        (1, 2, [10], 5),
        (1, 1, [10], 0),
        (1, 1, [10], -3),
        (1, 1, [], 5),
    ])
    async def test_invalid_or_empty_scope_skips_db_and_embedding(self, org_id, scope_org, course_ids, top_k):
        db = AsyncMock()
        embed = AsyncMock()
        scope = SimpleNamespace(org_id=scope_org, course_ids=course_ids)
        with patch.object(query_service, "embed_single_text", new=embed):
            rows = await query_service.search_course_content("question", org_id, db, scope, top_k=top_k)
        assert rows == []
        db.execute.assert_not_awaited()
        embed.assert_not_awaited()

    async def test_fully_unauthorized_single_course_skips_embedding(self):
        db = AsyncMock()
        db.execute.side_effect = [
            _mock_result(scalar=True),
            _mock_result(scalars=[101, 102, 102]),
        ]
        embed = AsyncMock()
        readable = AsyncMock(return_value=set())
        scope = SimpleNamespace(org_id=1, course_ids=[10])
        with (
            patch.object(query_service, "embed_single_text", new=embed),
            patch.object(query_service, "readable_activity_ids", new=readable),
        ):
            rows = await query_service.search_course_content("question", 1, db, scope, top_k=5)
        assert rows == []
        readable.assert_awaited_once_with({101, 102}, scope, db)
        embed.assert_not_awaited()
        assert db.execute.await_args_list[1].args[1]["chunk_limit"] == 1001

    async def test_single_course_prefilters_activity_ids_and_revalidates(self, monkeypatch):
        monkeypatch.delenv("LEARNHOUSE_RAG_SMALL_COURSE_EXACT", raising=False)
        returned_rows = [_row(id=1, activity_id=201), _row(id=2, activity_id=202)]
        db = AsyncMock()
        db.execute.side_effect = [
            _mock_result(scalar=True),
            _mock_result(scalars=[100] * 25 + [202, 201]),
            _mock_result(rows=returned_rows),
        ]
        embed = AsyncMock(return_value=[0.0] * 768)
        readable = AsyncMock(return_value={202, 201})
        final_filter = AsyncMock(return_value=[returned_rows[0]])
        scope = SimpleNamespace(org_id=1, course_ids=[10])
        with (
            patch.object(query_service, "embed_single_text", new=embed),
            patch.object(query_service, "readable_activity_ids", new=readable),
            patch.object(query_service, "filter_readable_chunks", new=final_filter),
        ):
            rows = await query_service.search_course_content("question", 1, db, scope, top_k=5)

        assert rows == [returned_rows[0]]
        embed.assert_awaited_once_with("question")
        readable.assert_awaited_once_with({100, 201, 202}, scope, db)
        final_filter.assert_awaited_once_with(returned_rows, scope, db)
        vector_sql, vector_params = db.execute.await_args_list[2].args
        sql_text = vector_sql.text
        assert "ce.activity_id IS NULL OR ce.activity_id IN :activity_ids" in sql_text
        assert "NOT c.studytomy_managed" in sql_text
        assert vector_sql._bindparams["activity_ids"].expanding is True
        assert vector_params["activity_ids"] == [201, 202]
        assert vector_params["limit"] == 5 * query_service.OVERFETCH_FACTOR

    async def test_course_level_only_uses_null_activity_predicate(self, monkeypatch):
        monkeypatch.delenv("LEARNHOUSE_RAG_SMALL_COURSE_EXACT", raising=False)
        course_row = _row(id=1, activity_id=None, source_type="course")
        db = AsyncMock()
        db.execute.side_effect = [
            _mock_result(scalar=True),
            _mock_result(scalars=[None, 301]),
            _mock_result(rows=[course_row]),
        ]
        embed = AsyncMock(return_value=[0.0] * 768)
        readable = AsyncMock(return_value=set())
        final_filter = AsyncMock(return_value=[course_row])
        scope = SimpleNamespace(org_id=1, course_ids=[10])
        with (
            patch.object(query_service, "embed_single_text", new=embed),
            patch.object(query_service, "readable_activity_ids", new=readable),
            patch.object(query_service, "filter_readable_chunks", new=final_filter),
        ):
            rows = await query_service.search_course_content("question", 1, db, scope, top_k=5)

        assert rows == [course_row]
        vector_sql, vector_params = db.execute.await_args_list[2].args
        assert "AND ce.activity_id IS NULL" in str(vector_sql)
        assert "activity_ids" not in vector_params
        final_filter.assert_awaited_once_with([course_row], scope, db)

    @pytest.mark.parametrize("course_ids,discovered", [
        ([10], [501] * 1001),
        ([10, 11], None),
    ])
    async def test_over_limit_and_multi_course_keep_bounded_approximate_path(
        self, monkeypatch, course_ids, discovered,
    ):
        monkeypatch.setenv("LEARNHOUSE_RAG_SMALL_COURSE_EXACT", "true")
        hit = _row(id=1, activity_id=501)
        db = AsyncMock()
        side_effects = [_mock_result(scalar=True)]
        if discovered is not None:
            side_effects.append(_mock_result(scalars=discovered))
        side_effects.append(_mock_result(rows=[hit]))
        db.execute.side_effect = side_effects
        embed = AsyncMock(return_value=[0.0] * 768)
        readable = AsyncMock()
        final_filter = AsyncMock(return_value=[hit])
        scope = SimpleNamespace(org_id=1, course_ids=course_ids)
        with (
            patch.object(query_service, "embed_single_text", new=embed),
            patch.object(query_service, "readable_activity_ids", new=readable),
            patch.object(query_service, "filter_readable_chunks", new=final_filter),
        ):
            rows = await query_service.search_course_content("question", 1, db, scope, top_k=5)

        assert rows == [hit]
        readable.assert_not_awaited()
        vector_sql, vector_params = db.execute.await_args_list[-1].args
        assert "activity_ids" not in vector_params
        assert "MATERIALIZED" not in str(vector_sql)
        final_filter.assert_awaited_once_with([hit], scope, db)

    async def test_exact_fallback_reuses_same_request_permissions_and_revalidates(self, monkeypatch):
        monkeypatch.setenv("LEARNHOUSE_RAG_SMALL_COURSE_EXACT", "true")
        recovered_allowed = _row(id=1, activity_id=201)
        recovered_revoked = _row(id=2, activity_id=202)
        db = AsyncMock()
        db.execute.side_effect = [
            _mock_result(scalar=True),
            _mock_result(scalars=[100] * 25 + [201, 202]),
            _mock_result(rows=[]),
            _mock_result(rows=[recovered_allowed, recovered_revoked]),
        ]
        embed = AsyncMock(return_value=[0.0] * 768)
        readable = AsyncMock(return_value={201, 202})
        # Simulate mid-request revocation of activity 202 during final revalidation.
        final_filter = AsyncMock(side_effect=[[], [recovered_allowed]])
        scope = SimpleNamespace(org_id=1, course_ids=[10])
        with (
            patch.object(query_service, "embed_single_text", new=embed),
            patch.object(query_service, "readable_activity_ids", new=readable),
            patch.object(query_service, "filter_readable_chunks", new=final_filter),
        ):
            rows = await query_service.search_course_content("question", 1, db, scope, top_k=5)

        assert rows == [recovered_allowed]
        readable.assert_awaited_once_with({100, 201, 202}, scope, db)
        assert final_filter.await_count == 2
        exact_sql, exact_params = db.execute.await_args_list[3].args
        assert "WITH compatible_scope AS MATERIALIZED" in str(exact_sql)
        assert "ORDER BY distance, id LIMIT :limit" in str(exact_sql)
        assert exact_params["activity_ids"] == [201, 202]
        assert exact_params["candidate_limit"] == 1001
        assert exact_params["exact_limit"] == 1000
