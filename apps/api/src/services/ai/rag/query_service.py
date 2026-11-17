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

from src.services.ai.rag.access import RagAccessScope, filter_readable_chunks, readable_activity_ids
from src.services.ai.llm.embeddings import DEFAULT_EMBEDDING_DIMENSIONS, embedding_provenance
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


async def search_course_content(
    question: str,
    org_id: int,
    db_session: AsyncSession,
    scope: RagAccessScope,
    top_k: int = TOP_K,
) -> list:
    """Rows most similar to the question that the caller may read, best first."""
    if not scope.course_ids or scope.org_id != org_id:
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
    eligible_activity_ids: set[int] | None = None
    if len(scope.course_ids) == 1 and os.getenv("LEARNHOUSE_RAG_SMALL_COURSE_EXACT") == "true":
        bounded = text("SELECT ce.activity_id " + scope_sql + " LIMIT :chunk_limit").bindparams(
            bindparam("course_ids", expanding=True),
        )
        activities = (await db_session.execute(
            bounded, {**scope_params, "chunk_limit": _EXACT_COURSE_CHUNK_LIMIT + 1},
        )).scalars().all()
        if not activities:
            return []
        if len(activities) <= _EXACT_COURSE_CHUNK_LIMIT:
            eligible_activity_ids = await readable_activity_ids(
                {activity_id for activity_id in activities if activity_id is not None}, scope, db_session,
            )
            if not eligible_activity_ids and None not in activities:
                return []
    else:
        available = text("SELECT EXISTS (SELECT 1 " + scope_sql + ")").bindparams(bindparam("course_ids", expanding=True))
        if not (await db_session.execute(available, scope_params)).scalar_one():
            return []

    query_embedding = await embed_single_text(question)
    if len(query_embedding) != DEFAULT_EMBEDDING_DIMENSIONS:
        raise ValueError("The embedding provider returned an incompatible query vector.")
    embedding_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

    # Course-level access is enforced in SQL. Activity-level rules (drafts,
    # locks, paid access) are applied to the rows afterwards, so over-fetch to
    # keep top_k results when some of them get filtered out.
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
    if eligible_activity_ids is None:
        sql = text(selection + ", ce.embedding <=> :query_embedding AS distance " + scope_sql + """
            ORDER BY ce.embedding <=> :query_embedding LIMIT :limit
        """).bindparams(bindparam("course_ids", expanding=True))
    else:
        # Materialize the authorized cohort so pgvector ranks it exactly.
        sql = text("WITH authorized AS MATERIALIZED (" + selection + ", ce.embedding " + scope_sql + """
            AND (ce.activity_id IS NULL OR ce.activity_id IN :activity_ids)
        )
        SELECT *, embedding <=> :query_embedding AS distance
        FROM authorized ORDER BY distance, id LIMIT :limit
        """).bindparams(bindparam("course_ids", expanding=True), bindparam("activity_ids", expanding=True))
        params["activity_ids"] = sorted(eligible_activity_ids)

    rows = (await db_session.execute(sql, params)).fetchall()
    return (await filter_readable_chunks(rows, scope, db_session))[:top_k]


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


