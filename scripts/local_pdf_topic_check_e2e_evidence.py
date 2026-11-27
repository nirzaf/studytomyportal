"""Build the test-only D-16 fractions evidence for a synthetic PDF fixture."""

from hashlib import sha256
from io import BytesIO

from pypdf import PdfReader

from src.services.ai.learning_practice import _PracticeReview
from src.services.studytomy.adaptive_curriculum import curriculum_activity_snapshot_digest
from src.services.studytomy.idempotency import canonical_request_hash


def reviewed_fractions_fixture(
    *,
    snapshot: dict,
    candidate_digest: str,
    activity_uuid: str,
    curriculum_uuid: str,
    curriculum_digest: str,
    concept_uuid: str,
    reviewer_user_uuid: str,
    pdf: bytes,
    pdf_page: int,
    retained_pdf_sha256: str,
) -> dict:
    """Derive synthetic approval inputs from the exact pre-publication candidate.

    This prepares a policy payload only. It performs no approval or release
    writes and requires the caller to use isolated test records.
    """
    course = snapshot["course"]
    course_uuid = course["course_uuid"]
    if snapshot.get("schema") != "course_content_evidence_v4" or not course.get("name", "").startswith("Synthetic "):
        raise ValueError("Require a synthetic managed-course candidate.")
    matching = [activity for activity in snapshot["activities"] if activity["activity_uuid"] == activity_uuid]
    if len(matching) != 1:
        raise ValueError("Require exactly one frozen PDF lesson.")
    activity = matching[0]
    if (
        activity.get("published") is not True
        or activity.get("activity_sub_type") != "SUBTYPE_DOCUMENT_PDF"
        or activity["course_id"] != course["id"]
        or activity["org_id"] != course["org_id"]
    ):
        raise ValueError("The candidate lesson does not bind the synthetic course PDF.")
    if canonical_request_hash(snapshot) != candidate_digest:
        raise ValueError("The synthetic snapshot does not match the current review candidate.")
    digest = sha256(pdf).hexdigest()
    if digest != retained_pdf_sha256:
        raise ValueError("Fixture bytes differ from retained PDF evidence.")
    reader = PdfReader(BytesIO(pdf), strict=True)
    if reader.is_encrypted or not 1 <= pdf_page <= len(reader.pages):
        raise ValueError("The reviewed physical page is unavailable in the synthetic PDF.")

    examples = [
        {
            "example_id": example,
            "visual": "fraction_explorer",
            "parts": parts,
            "selected": 2,
            "numerator": 2,
            "denominator": parts,
            "prompt": "What fraction of the whole is selected?",
            "explanation": "Find the equal parts of the whole.",
            "correct_feedback": "Correct. The fraction matches the selected parts.",
            "incorrect_feedback": "Check the number of equal parts in the whole.",
        }
        for example, parts in (("initial", 4), ("recheck", 6))
    ]
    review = _PracticeReview.model_validate({
        "curriculum_revision_uuid": curriculum_uuid,
        "curriculum_digest": curriculum_digest,
        "concept_uuid": concept_uuid,
        "course_uuid": course_uuid,
        "activity_uuid": activity_uuid,
        "activity_version": activity["current_version"],
        "activity_digest": curriculum_activity_snapshot_digest(activity),
        "rights_reference": "SYNTHETIC/fractions-rights",
        "evidence_reference": "SYNTHETIC/fractions-reviewed-pdf",
        "review_reference": "SYNTHETIC/fractions-topic-check",
        "reviewer_user_uuid": reviewer_user_uuid,
        "examples": examples,
        "topic_check": {
            "release_snapshot_sha256": candidate_digest,
            "pdf_sha256": digest,
            "pdf_page": pdf_page,
            "initial_example_id": "initial",
            "recheck_example_id": "recheck",
            "reteaching": "Count the two shaded pieces, then count all the equal pieces.",
            "show_visual": True,
        },
    })
    return review.model_dump(mode="json")
