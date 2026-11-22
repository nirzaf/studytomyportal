from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from pydantic import ValidationError
from pypdf import PdfWriter

from src.db.studytomy.syllabus import StudySyllabusConceptMapping
from src.services.studytomy.syllabus_library import (
    ExploreTopicRequest,
    SyllabusSourceUpload,
    SyllabusSourceReview,
    _GeneratedAnswer,
    _classify_source_coverage,
    _claim_is_supported,
    _claim_answers_question,
    _concept_evidence_is_relevant,
    _extract_pdf,
    _indexable_chunks,
    _literal_like_pattern,
    _lexical_match_is_sufficient,
    _mapping_supports_cited_topic,
    _official_mapping_compatible,
    _normalize_topic_query,
    _query_identifiers,
    _query_matches_concept,
    _query_phrases,
    _semantic_distance_is_sufficient,
    _same_syllabus_index_snapshot,
    _topic_search_terms,
    _validate_generated_claims,
)
from src.tests.services.rag_helpers import make_pdf


_PDF_COVER = "Synthetic Grade 12 Chemistry reference"
_ISOTOPES = "Isotopes have equal proton counts and different neutron counts."


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


def test_partially_extractable_pdf_is_held_instead_of_silently_indexing_missing_pages():
    original = make_pdf([_PDF_COVER, "", _ISOTOPES])
    content, pages, error_code, message = _extract_pdf(
        UploadFile(filename="partial-scan.pdf", file=BytesIO(original))
    )

    assert content == original
    assert [page["page"] for page in pages] == [1, 2, 3]
    assert pages[1]["text"] == ""
    assert _ISOTOPES in pages[2]["text"]
    assert error_code == "ocr_required"
    assert "page(s) 2" in message
    assert "partial indexing is not allowed" in message


def test_searchable_pdf_quote_keeps_physical_page_and_requires_original_page_provenance():
    original = make_pdf([_PDF_COVER, "Contents and source notes.", _ISOTOPES])
    content, pages, error_code, message = _extract_pdf(
        UploadFile(filename="synthetic-chemistry-reference.pdf", file=BytesIO(original))
    )

    assert content == original
    assert (error_code, message) == (None, None)
    assert [page["page"] for page in pages] == [1, 2, 3]
    [(page, _, excerpt)] = [chunk for chunk in _indexable_chunks(pages) if _ISOTOPES in chunk[2]]
    assert page == 3
    answer = _GeneratedAnswer.model_validate({
        "explanation": "Compare the proton and neutron counts.",
        "claims": [{"reference": 1, "text": _ISOTOPES, "quote": _ISOTOPES}],
    })
    version = SimpleNamespace(extracted_pages=pages)
    valid = {"chunk": SimpleNamespace(page_number=page, chunk_text=excerpt), "version": version}
    assert _validate_generated_claims(answer, [valid])[0].citations == [1]

    wrong_page = {"chunk": SimpleNamespace(page_number=2, chunk_text=excerpt), "version": version}
    with pytest.raises(ValueError, match="not supported"):
        _validate_generated_claims(answer, [wrong_page])


def test_indexing_keeps_every_supported_chunk_without_the_old_256_item_cutoff(monkeypatch):
    chunks = [f"complete source chunk {index}" for index in range(300)]
    monkeypatch.setattr("src.services.studytomy.syllabus_library.chunk_text", lambda _text: chunks)

    indexed = _indexable_chunks([{"page": 17, "text": "synthetic long source page"}])

    assert len(indexed) == 300
    assert indexed[0] == (17, 0, chunks[0])
    assert indexed[-1] == (17, 299, chunks[-1])


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
    assert ExploreTopicRequest(query="atomic number", search_scope="all_approved").search_scope == "all_approved"
    with pytest.raises(ValidationError):
        ExploreTopicRequest(query="atomic number", search_scope="tenant_wide")


def test_natural_language_lexical_search_keeps_topic_aliases_codes_and_phrases():
    plants = _topic_search_terms(_normalize_topic_query("How do plants make food?"))
    assert {"plants", "photosynthesis", "chlorophyll"} <= set(plants)
    assert "buoyancy" in _topic_search_terms(_normalize_topic_query("Why do objects float?"))
    assert "refraction" in _topic_search_terms(_normalize_topic_query("What happens when light passes through water?"))
    assert "chem" in _topic_search_terms("CHEM-ISO")
    assert "iso" in _topic_search_terms("CHEM-ISO")
    assert _query_identifiers("What is H2O?") == ["H2O"]
    assert "atomic number" in _query_phrases("What is atomic number?")


def test_keyword_retrieval_rejects_passages_that_match_only_one_part_of_a_question():
    query_terms = {"water", "black", "holes", "evaporate"}
    assert not _lexical_match_is_sufficient(query_terms, [], "Objects float when density differs from water.")
    assert _lexical_match_is_sufficient(query_terms, [], "Water in black holes can evaporate.")
    assert _lexical_match_is_sufficient({"chem", "iso"}, ["CHEM-ISO"], "CHEM-ISO defines atomic number.")


def test_official_mapping_requires_full_curriculum_metadata_compatibility():
    version = SimpleNamespace(
        source_type="syllabus", jurisdiction="Sri Lanka", grade="Grade 12", subject="Chemistry",
        language="en", syllabus_edition="2017", effective_cohort="2026",
    )
    revision = SimpleNamespace(
        jurisdiction="Sri Lanka", level="12", subject="Chemistry", language="en",
        programme_metadata={"syllabus_edition": "2017", "effective_cohort": "2026"},
    )
    assert _official_mapping_compatible(version, revision)
    assert not _official_mapping_compatible(version, SimpleNamespace(**{
        **revision.__dict__, "subject": "Physics",
    }))
    assert not _official_mapping_compatible(version, SimpleNamespace(**{
        **revision.__dict__, "programme_metadata": {"syllabus_edition": "2019", "effective_cohort": "2026"},
    }))
    assert not _official_mapping_compatible(SimpleNamespace(**{**version.__dict__, "source_type": "resource_book"}), revision)
    assert _concept_evidence_is_relevant(
        SimpleNamespace(code="CHEM-ISO", title="Boron isotopes", objective="Compare boron isotopes."),
        "Boron isotopes have the same proton number.",
    )


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
        "claims": [{"reference": 1, "text": quote, "quote": quote}],
    })
    blocks = _validate_generated_claims(accepted, [candidate])
    assert blocks[0].citations == [1]

    fabricated = _GeneratedAnswer.model_validate({
        "explanation": "Fabricated citation.",
        "claims": [{"reference": 1, "text": "Unsupported.", "quote": "Carbon-14 has twelve protons."}],
    })
    with pytest.raises(ValueError, match="not supported"):
        _validate_generated_claims(fabricated, [candidate])

    unsupported_paraphrase = _GeneratedAnswer.model_validate({
        "explanation": "The claim adds an unsupported detail.",
        "claims": [{
            "reference": 1,
            "text": "Carbon-14 has eight neutrons and twelve protons.",
            "quote": quote,
        }],
    })
    with pytest.raises(ValueError, match="not supported"):
        _validate_generated_claims(unsupported_paraphrase, [candidate])

    teaching = _GeneratedAnswer.model_validate({
        "claims": [{
            "reference": 1,
            "kind": "teaching",
            "text": "Atomic number gives the number of protons in the atom.",
            "quote": "The atomic number is the number of protons in an atom.",
        }],
    })
    teaching_candidate = {
        "chunk": SimpleNamespace(page_number=2, chunk_text="The atomic number is the number of protons in an atom."),
        "version": SimpleNamespace(extracted_pages=[{"page": 2, "text": "The atomic number is the number of protons in an atom."}]),
    }
    block = _validate_generated_claims(teaching, [teaching_candidate])[0]
    assert block.text.startswith("Atomic number gives")
    assert block.evidence_quote == "The atomic number is the number of protons in an atom."


def test_supported_claim_must_answer_the_question_as_well_as_quote_the_source():
    assert _claim_answers_question(
        "Plants use photosynthesis to make their food.",
        "How do plants make food?",
    )
    assert not _claim_answers_question(
        "Carbon-14 has eight neutrons.",
        "Why do metals conduct electricity?",
    )
    quote = "Carbon-14 has eight neutrons and six protons."
    candidate = {
        "chunk": SimpleNamespace(page_number=4, chunk_text=quote),
        "version": SimpleNamespace(extracted_pages=[{"page": 4, "text": quote}]),
    }
    proposal = _GeneratedAnswer.model_validate({
        "claims": [{"reference": 1, "text": "Carbon-14 has eight neutrons.", "quote": quote}],
    })
    with pytest.raises(ValueError, match="question and cited PDF passage"):
        _validate_generated_claims(proposal, [candidate], "Why do metals conduct electricity?")


def test_zero_claims_and_conservative_semantic_evidence_threshold():
    proposal = _GeneratedAnswer.model_validate({"explanation": "Evidence is insufficient.", "claims": []})
    assert proposal.claims == []
    assert _semantic_distance_is_sufficient(0.25)
    assert _semantic_distance_is_sufficient(0.35)
    assert not _semantic_distance_is_sufficient(0.35001)
    assert not _semantic_distance_is_sufficient(float("nan"))
    assert not _semantic_distance_is_sufficient(float("inf"))
    assert _claim_is_supported("Carbon-14 has eight neutrons.", "Carbon-14 has six protons and eight neutrons.")
    assert not _claim_is_supported("Carbon-14 has twelve neutrons.", "Carbon-14 has six protons and eight neutrons.")


def test_reindex_snapshot_fence_rejects_concurrent_source_changes():
    initial = datetime(2026, 11, 22, tzinfo=UTC)
    snapshot = (12, initial, "ready", 3, "a" * 64, "approved")
    current = SimpleNamespace(
        id=12,
        updated_at=initial,
        ingestion_status="ready",
        ingestion_attempts=3,
        content_sha256="a" * 64,
        review_status="approved",
    )
    assert _same_syllabus_index_snapshot(current, snapshot)
    current.review_status = "revoked"
    assert not _same_syllabus_index_snapshot(current, snapshot)


def test_mapping_coverage_requires_query_concept_match_and_cited_mapping_quote():
    quote = "Atomic number is the number of protons in an atom."
    assert _mapping_supports_cited_topic("What is atomic number?", "Atomic Number", quote, f"Example: {quote}")
    assert not _mapping_supports_cited_topic("photosynthesis", "Atomic Number", quote, f"Example: {quote}")
    assert not _mapping_supports_cited_topic("atomic number", "Atomic Number", quote, "A different passage on that page.")
    assert not _query_matches_concept("carbon isotopes", "Chemistry / Isotopes")
    assert not _query_matches_concept("carbon isotopes", "Cell division")
    assert _literal_like_pattern("%_\\") == "%\\%\\_\\\\%"


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


def test_mapping_uniqueness_applies_only_while_pending_or_approved():
    index = next(
        item for item in StudySyllabusConceptMapping.__table__.indexes
        if item.name == "uq_st_syllabus_mapping_active_evidence"
    )
    assert index.unique is True
    assert str(index.dialect_options["postgresql"]["where"]) == "status IN ('pending_review', 'approved')"
