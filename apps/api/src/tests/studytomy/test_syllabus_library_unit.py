from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from pydantic import ValidationError
from pypdf import PdfWriter

from src.services.studytomy.syllabus_library import (
    ExploreTopicRequest,
    SyllabusSourceUpload,
    SyllabusSourceReview,
    _GeneratedAnswer,
    _classify_source_coverage,
    _extract_pdf,
    _indexable_chunks,
    _validate_generated_claims,
)


def _blank_pdf(page_count: int = 2) -> bytes:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=72, height=72)
    content = BytesIO()
    writer.write(content)
    return content.getvalue()


def test_image_only_pdf_is_retained_as_failed_with_original_page_numbers():
    content, pages, error_code, message = _extract_pdf(
        UploadFile(filename="scanned-syllabus.pdf", file=BytesIO(_blank_pdf()))
    )

    assert content.startswith(b"%PDF-")
    assert [page["page"] for page in pages] == [1, 2]
    assert all(page["text"] == "" for page in pages)
    assert error_code == "ocr_required"
    assert "OCR" in message
    assert _indexable_chunks(pages) == []


def test_partially_extractable_pdf_is_held_instead_of_silently_indexing_missing_pages(monkeypatch):
    monkeypatch.setattr(
        "src.services.studytomy.syllabus_library.pdf_pages",
        lambda *_args, **_kwargs: [(1, "Readable passage"), (2, "")],
    )
    _content, pages, error_code, message = _extract_pdf(
        UploadFile(filename="partial-scan.pdf", file=BytesIO(_blank_pdf()))
    )

    assert [page["page"] for page in pages] == [1, 2]
    assert error_code == "ocr_required"
    assert "page(s) 2" in message
    assert "partial indexing is not allowed" in message


def test_unreadable_and_over_page_limit_pdfs_get_durable_failure_status():
    unreadable = _extract_pdf(
        UploadFile(filename="damaged.pdf", file=BytesIO(b"%PDF-not-readable"))
    )
    oversized_page_count = _extract_pdf(
        UploadFile(filename="too-many-pages.pdf", file=BytesIO(_blank_pdf(201)))
    )

    assert unreadable[2] == "pdf_unreadable"
    assert unreadable[3] == "The PDF could not be read. Check the file and upload it again."
    assert oversized_page_count[2] == "pdf_unreadable"
    assert "at most 200 pages" in oversized_page_count[3]
    assert unreadable[1] == oversized_page_count[1] == []


def test_pdf_upload_size_is_checked_before_pdf_parsing():
    oversized = UploadFile(
        filename="oversized.pdf",
        file=BytesIO(b"%PDF-" + b"x" * (4 * 1024 * 1024)),
    )

    with pytest.raises(HTTPException) as error:
        _extract_pdf(oversized)

    assert error.value.status_code == 413


def test_source_metadata_rejects_missing_rights_shape_and_extra_fields():
    with pytest.raises(ValidationError):
        SyllabusSourceUpload.model_validate({"title": "Only a title", "unexpected": True})


def test_explore_query_is_bounded_and_requires_a_question():
    with pytest.raises(ValidationError):
        ExploreTopicRequest(query=" ")
    assert ExploreTopicRequest(query="atomic number").query == "atomic number"


def test_independent_source_approval_requires_its_own_rights_evidence():
    with pytest.raises(ValidationError, match="permission reference"):
        SyllabusSourceReview(decision="approve", evidence="Checked the source.")

    approval = SyllabusSourceReview(
        decision="approve",
        evidence="Independent source and content review.",
        rights_reference="Publisher license record 2026-04",
    )
    assert approval.rights_reference == "Publisher license record 2026-04"
    assert SyllabusSourceReview(decision="reject", evidence="Permission is not established.").rights_reference is None


def test_generated_citations_are_checked_against_the_exact_original_page():
    quote = "Carbon-14 has six protons and eight neutrons."
    candidate = {
        "chunk": SimpleNamespace(page_number=11, chunk_text=f"Isotopes: {quote}"),
        "version": SimpleNamespace(extracted_pages=[{"page": 11, "text": f"Text. {quote} End."}]),
    }
    accepted = _GeneratedAnswer.model_validate({
        "explanation": "The isotopes have different neutron counts.",
        "claims": [{"reference": 1, "text": "Carbon-14 has eight neutrons.", "quote": quote}],
    })
    blocks = _validate_generated_claims(accepted, [candidate])
    assert blocks[0].citations == [1]

    fabricated = _GeneratedAnswer.model_validate({
        "explanation": "Fabricated citation.",
        "claims": [{"reference": 1, "text": "Unsupported.", "quote": "Carbon-14 has twelve protons."}],
    })
    with pytest.raises(ValueError, match="does not match"):
        _validate_generated_claims(fabricated, [candidate])


def test_generated_citations_cannot_reference_a_different_excerpt():
    answer = _GeneratedAnswer.model_validate({
        "explanation": "Unsupported reference.",
        "claims": [{"reference": 2, "text": "Unsupported.", "quote": "A sufficiently long quote."}],
    })
    with pytest.raises(ValueError, match="unknown excerpt"):
        _validate_generated_claims(answer, [{"chunk": SimpleNamespace(page_number=1, chunk_text="x"), "version": SimpleNamespace(extracted_pages=[])}])


def test_grounded_explanation_is_limited_to_four_claim_blocks():
    claim = {"reference": 1, "text": "A concise supported claim.", "quote": "A sufficiently long source quote."}
    with pytest.raises(ValidationError):
        _GeneratedAnswer.model_validate({"explanation": "Concise explanation.", "claims": [claim] * 5})


def test_syllabus_coverage_requires_reviewed_mapping_and_distinguishes_other_syllabus():
    coverage = _classify_source_coverage(
        {(1, 10), (1, 11), (2, 11), (3, 10), (4, 10)},
        [
            (1, 10, 9, 10, "official", "syllabus"),
            (2, 11, 11, 11, "supplementary", "resource_book"),
            # A resource book cannot upgrade a mention to official coverage.
            (3, 10, 10, 10, "official", "resource_book"),
        ],
        selected_curriculum_id=None,
    )

    assert coverage == {
        (1, 10): "other_approved_syllabus",
        (1, 11): "unverified",
        (2, 11): "supplementary",
        (3, 10): "supplementary",
        (4, 10): "unverified",
    }
    assert _classify_source_coverage({(1, 10), (1, 11)}, [(1, 10, 9, 10, "official", "syllabus")], 10) == {
        (1, 10): "confirmed_selected_syllabus",
        (1, 11): "unverified",
    }
