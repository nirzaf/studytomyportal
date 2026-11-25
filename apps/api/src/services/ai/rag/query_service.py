"""
RAG query service.

Handles vector similarity search and streaming LLM responses
grounded in course content.
"""

import json
import logging
import os

from sqlalchemy import bindparam, text
from sqlmodel.ext.asyncio.session import AsyncSession

from src.services.ai.llm.embeddings import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    embedding_provenance,
)
from src.services.ai.rag.access import (
    RagAccessScope,
    filter_readable_chunks,
    readable_activity_ids,
)
from src.services.ai.rag.embedding_service import embed_single_text
from src.services.ai.rag.types import Source, SourceType

logger = logging.getLogger(__name__)

TOP_K = 5
OVERFETCH_FACTOR = 4
_EXACT_COURSE_CHUNK_LIMIT = 1000
SNIPPET_CHARS = 280

_KIND_LABELS = {
    SourceType.COURSE.value: "Course",
    SourceType.CHAPTER.value: "Chapter",
    SourceType.PAGE.value: "Page",
    SourceType.DOCUMENT.value: "PDF",
    SourceType.PDF_BLOCK.value: "PDF",
    SourceType.IMAGE_BLOCK.value: "Image",
    SourceType.AUDIO_BLOCK.value: "Audio",
    SourceType.VIDEO_BLOCK.value: "Video",
    SourceType.VIDEO.value: "Video",
    SourceType.ASSIGNMENT.value: "Assignment",
    SourceType.SCORM.value: "SCORM module",
    SourceType.CUSTOM.value: "Activity",
    SourceType.CUSTOM_BLOCK.value: "Activity",
    SourceType.ACTIVITY.value: "Activity",
}


def _activity_scope_predicate(
    eligible_activity_ids: set[int],
) -> tuple[str, dict, list]:
    """Build parameterized SQL predicates for authorized course/activity rows."""
    if eligible_activity_ids:
        return (
            " AND (ce.activity_id IS NULL OR ce.activity_id IN :activity_ids)",
            {"activity_ids": sorted(eligible_activity_ids)},
            [bindparam("activity_ids", expanding=True)],
        )
    return (" AND ce.activity_id IS NULL", {}, [])


async def search_course_content(
    question: str,
    org_id: int,
    db_session: AsyncSession,
    scope: RagAccessScope,
    top_k: int = TOP_K,
) -> list:
    """Rows most similar to the question that the caller may read, best first."""
    if not scope.course_ids or scope.org_id != org_id or top_k <= 0:
        return []

    embedding_space, provenance = embedding_provenance()
    if provenance["dimensions"] != DEFAULT_EMBEDDING_DIMENSIONS:
        raise ValueError("The embedding dimensions do not match the course vector index.")
    scope_sql = """
        FROM course_embedding ce
        JOIN course c ON c.id = ce.course_id
        WHERE ce.org_id = :org_id AND c.org_id = :org_id AND NOT c.studytomy_managed
          AND ce.course_id IN :course_ids AND ce.embedding_space = :embedding_space
    """
    scope_params = {"org_id": org_id, "course_ids": scope.course_ids, "embedding_space": embedding_space}
    available = text("SELECT EXISTS (SELECT 1 " + scope_sql + ")").bindparams(bindparam("course_ids", expanding=True))
    if not (await db_session.execute(available, scope_params)).scalar_one():
        return []

    # For bounded single-course scopes (at most 1,000 compatible rows), resolve
    # readable activity IDs before calling the embedding provider or vector
    # ranker so hidden activities cannot crowd out eligible results. Larger and
    # multi-course scopes keep bounded approximate retrieval.
    prefiltered = False
    eligible_activity_ids: set[int] = set()
    if len(scope.course_ids) == 1:
        bounded = text("SELECT ce.activity_id " + scope_sql + " LIMIT :chunk_limit").bindparams(
            bindparam("course_ids", expanding=True),
        )
        activities = (await db_session.execute(
            bounded, {**scope_params, "chunk_limit": _EXACT_COURSE_CHUNK_LIMIT + 1},
        )).scalars().all()
        if 0 < len(activities) <= _EXACT_COURSE_CHUNK_LIMIT:
            has_course_level = any(activity_id is None for activity_id in activities)
            candidate_activity_ids = {
                activity_id for activity_id in activities if activity_id is not None
            }
            if candidate_activity_ids:
                eligible_activity_ids = await readable_activity_ids(
                    candidate_activity_ids, scope, db_session,
                )
            if not eligible_activity_ids and not has_course_level:
                return []
            prefiltered = True

    query_embedding = await embed_single_text(question)
    if len(query_embedding) != DEFAULT_EMBEDDING_DIMENSIONS:
        raise ValueError("The embedding provider returned an incompatible query vector.")
    embedding_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

    # Course-level access is enforced in SQL. Bounded single-course scopes also
    # apply resolved activity IDs in SQL; larger and multi-course scopes keep
    # bounded over-fetch. Every path re-applies final activity authorization
    # immediately before returning results.
    selection = """
        SELECT ce.id, ce.chunk_text, ce.activity_id, ce.activity_uuid, ce.activity_name,
               ce.chapter_name, ce.course_name, ce.source_type, ce.block_uuid, ce.locator,
               ce.course_id, c.course_uuid, ce.content_hash
    """
    params = {
        "query_embedding": embedding_str,
        **scope_params,
        "limit": top_k * OVERFETCH_FACTOR,
    }
    activity_clause = ""
    activity_params: dict = {}
    extra_binds: list = []
    if prefiltered:
        activity_clause, activity_params, extra_binds = _activity_scope_predicate(
            eligible_activity_ids,
        )

    sql = text(
        selection
        + ", ce.embedding <=> :query_embedding AS distance "
        + scope_sql
        + activity_clause
        + "\n        ORDER BY ce.embedding <=> :query_embedding LIMIT :limit\n    "
    ).bindparams(bindparam("course_ids", expanding=True), *extra_binds)

    rows = (await db_session.execute(sql, {**params, **activity_params})).fetchall()
    readable_rows = (await filter_readable_chunks(rows, scope, db_session))[:top_k]

    # Keep approximate ranking as the default. Exact recovery is deliberately
    # opt-in and bounded because it materializes the complete authorized course.
    if (
        len(readable_rows) < top_k
        and len(scope.course_ids) == 1
        and prefiltered
        and os.getenv("LEARNHOUSE_RAG_SMALL_COURSE_EXACT") == "true"
    ):
        # Reuse this request's resolved activity permissions and recheck the
        # 1,000-row cap in the ranking statement to close a concurrent
        # index-replacement race between candidate discovery and materialization.
        exact_sql = text(f"""
        WITH compatible_scope AS MATERIALIZED (
            SELECT ce.id {scope_sql} LIMIT :candidate_limit
        ), scope_size AS (
            SELECT count(*) AS row_count FROM compatible_scope
        ), authorized AS MATERIALIZED (
            {selection}, ce.embedding {scope_sql}
            {activity_clause}
            AND (SELECT row_count FROM scope_size) <= :exact_limit
        )
        SELECT *, embedding <=> :query_embedding AS distance
        FROM authorized ORDER BY distance, id LIMIT :limit
        """).bindparams(bindparam("course_ids", expanding=True), *extra_binds)
        exact_rows = (await db_session.execute(exact_sql, {
            **params,
            **activity_params,
            "limit": top_k,
            "candidate_limit": _EXACT_COURSE_CHUNK_LIMIT + 1,
            "exact_limit": _EXACT_COURSE_CHUNK_LIMIT,
        })).fetchall()
        exact_readable = await filter_readable_chunks(exact_rows, scope, db_session)
        if len(exact_readable) > len(readable_rows):
            return exact_readable[:top_k]

    return readable_rows


def source_from_row(row) -> Source:
    locator = row.locator
    if isinstance(locator, str):  # JSON columns come back as text on some drivers
        locator = json.loads(locator)
    return Source(
        source_type=row.source_type,
        course_uuid=row.course_uuid,
        course_name=row.course_name,
        chapter_name=row.chapter_name or "",
        activity_uuid=row.activity_uuid or None,
        activity_name=row.activity_name or "",
        block_uuid=row.block_uuid,
        locator=locator or None,
        title=row.activity_name or row.chapter_name or row.course_name,
        snippet=(row.chunk_text or "")[:SNIPPET_CHARS],
        similarity=round(1 - float(row.distance), 4) if row.distance is not None else None,
        content_hash=getattr(row, "content_hash", None),
    )


def format_timestamp(seconds: float) -> str:
    """192 -> "03:12", 3725 -> "1:02:05"."""
    total = int(seconds)
    hours, minutes, secs = total // 3600, total % 3600 // 60, total % 60
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def source_label(source: Source) -> str:
    """Short description of where a source points, for the model's context."""
    label = f'{_KIND_LABELS.get(source.source_type, "Content")} "{source.title}"'
    locator = source.locator or {}
    if "start" in locator:
        return f"{label} at {format_timestamp(locator['start'])}"
    if "page" in locator:
        return f"{label}, page {locator['page']}"
    return label


async def query_course_rag(
    question: str,
    org_id: int,
    db_session: AsyncSession,
    scope: RagAccessScope,
    top_k: int = TOP_K,
) -> dict:
    """
    Retrieve relevant course content via vector similarity search.

    Returns ``{context, sources}``: the numbered context for the model and the
    deduplicated sources (as dicts) its [N] citations refer to.
    """
    rows = await search_course_content(question, org_id, db_session, scope, top_k)

    context_parts = []
    sources: list[Source] = []
    numbers: dict[tuple, int] = {}
    for row in rows:
        # Chunks can share a citation only when they refer to the same revision and locator.
        source = source_from_row(row)
        key = (
            source.course_uuid, source.activity_uuid, source.source_type, source.block_uuid,
            source.content_hash, json.dumps(source.locator, sort_keys=True),
        )
        if key not in numbers:
            sources.append(source)
            numbers[key] = len(sources)
        context_parts.append(f"[Source {numbers[key]}] {source_label(source)}\n{row.chunk_text}")

    return {
        "context": "\n\n---\n\n".join(context_parts),
        "sources": [source.to_dict() for source in sources],
    }


