"""Format-level text helpers shared by the source modules."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from io import BytesIO
from typing import Iterable, Iterator

from src.services.courses.transfer.storage_utils import read_file_content

logger = logging.getLogger(__name__)

MAX_PDF_CHARS = 100_000
MAX_GROUNDING_PDF_PAGES = 200
VTT_WINDOW_SECONDS = 60.0

_CUE_RE = re.compile(r"((?:\d+:)?\d{1,2}:\d{2}[.,]\d{3})\s*-->\s*(?:\d+:)?\d{1,2}:\d{2}[.,]\d{3}")
_TAG_RE = re.compile(r"<[^>]+>")
# Keys whose string values are identifiers or links, not prose.
_NON_PROSE_KEY = re.compile(r"(?:^|_)(?:uuid|id|url|uri|href|src|file|filename|format|type|color|icon|emoji)s?$", re.I)


class GroundingPDFError(ValueError):
    """A PDF cannot be accepted as an explicit drafting source."""


def pdf_pages(
    pdf_bytes: bytes, max_chars: int = MAX_PDF_CHARS, *, for_grounding: bool = False,
    allow_empty: bool = False,
) -> list[tuple[int, str]]:
    """Extract numbered pages. Explicit sources reject unreadable or oversized files.

    ``allow_empty`` is for source-library ingestion only: it keeps the original
    page count for an image-only scan so the educator can see why it failed and
    receive OCR guidance. Such an extraction must never be indexed or approved.
    """
    try:
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(pdf_bytes))
        if for_grounding:
            if reader.is_encrypted:
                raise GroundingPDFError("Upload a PDF without password protection.")
            if len(reader.pages) > MAX_GROUNDING_PDF_PAGES:
                raise GroundingPDFError("Split the PDF into files of at most 200 pages.")
        pages: list[tuple[int, str]] = []
        budget = max_chars
        for number, page in enumerate(reader.pages, start=1):
            if budget <= 0 and not for_grounding:
                break
            text = (page.extract_text() or "").replace("\x00", "").strip()
            if for_grounding and len(text) > budget:
                raise GroundingPDFError("Split the PDF into smaller files before using it as a source.")
            text = text[:budget]
            if text or for_grounding:
                pages.append((number, text))
                budget -= len(text)
        if for_grounding and not allow_empty and not any(text for _, text in pages):
            raise GroundingPDFError("The PDF has no readable text. Upload a searchable or OCR-processed PDF.")
        return pages
    except GroundingPDFError:
        raise
    except Exception as exc:
        if for_grounding:
            raise GroundingPDFError("The PDF could not be read. Check the file and upload it again.") from exc
        logger.warning("Failed to extract PDF text")
        return []


async def read_pdf_pages(key: str) -> list[tuple[int, str]]:
    """Read a stored PDF and extract its pages, both off the event loop
    (storage reads and PDF parsing block)."""
    def read() -> list[tuple[int, str]]:
        data = read_file_content(key)
        return pdf_pages(data) if data else []

    return await asyncio.to_thread(read)


def _seconds(ts: str) -> float:
    parts = ts.replace(",", ".").split(":")
    total = 0.0
    for part in parts:
        total = total * 60 + float(part)
    return total


def vtt_windows(vtt: str, window: float = VTT_WINDOW_SECONDS) -> list[tuple[float, str]]:
    """Group WebVTT cues into windows of about ``window`` seconds.

    Returns (start seconds, text) pairs, so each chunk can link to the moment
    it was said.
    """
    windows: list[tuple[float, list[str]]] = []
    for block in re.split(r"\n\s*\n", (vtt or "").replace("\r\n", "\n")):
        lines = block.strip().split("\n")
        for i, line in enumerate(lines):
            match = _CUE_RE.search(line)
            if not match:
                continue
            text = " ".join(_TAG_RE.sub("", t).strip() for t in lines[i + 1:]).strip()
            if not text:
                break
            start = _seconds(match.group(1))
            if not windows or start - windows[-1][0] >= window:
                windows.append((start, []))
            windows[-1][1].append(text)
            break
    return [(start, " ".join(texts)) for start, texts in windows]


def html_text(html: str) -> str:
    """Visible text of an HTML document."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    return re.sub(r"\n\s*\n+", "\n\n", soup.get_text("\n")).strip()


def prose_strings(value, _key: str = "") -> Iterator[str]:
    """Every human-readable string in a JSON value, skipping ids, links and enums."""
    if isinstance(value, str):
        text = value.strip()
        if text and not _NON_PROSE_KEY.search(_key) and not text.startswith(("http://", "https://")):
            yield text
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from prose_strings(child, str(key))
    elif isinstance(value, list):
        for child in value:
            yield from prose_strings(child, _key)


def join(parts: Iterable[str | None], sep: str = "\n") -> str:
    return sep.join(p.strip() for p in parts if isinstance(p, str) and p.strip())


def is_safe_content_path(file_path: str) -> bool:
    """Reject empty, absolute, NUL-containing or ``..`` paths before reading.

    Stored file names come from user input; this guards in addition to the
    containment check in read_file_content.
    """
    if not file_path or not isinstance(file_path, str) or "\x00" in file_path:
        return False
    normalized = file_path.replace("\\", "/")
    if normalized.startswith("/") or os.path.isabs(file_path):
        return False
    return ".." not in normalized.split("/")


def is_safe_file_name(name) -> bool:
    """A single path component: no separators, traversal or NUL."""
    return (
        isinstance(name, str)
        and bool(name)
        and "/" not in name
        and "\\" not in name
        and "\x00" not in name
        and name not in (".", "..")
    )
