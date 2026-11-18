"""Reviewed conditions for certificates tied to a managed course release."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter

from src.db.studytomy.governance import StudyPolicyBody
from src.services.studytomy.errors import studytomy_http_error
from src.services.studytomy.grading_contract import retained_grading_contract_digest
from src.services.studytomy.prerequisites import ResourceUUID, course_completion_text_activities

_DisplayName = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=160)]


class ManagedCertificateConditions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    award_name: _DisplayName
    issuer_name: _DisplayName
    verification: Literal["private"]
    required_assessment_uuids: Annotated[list[ResourceUUID], Field(max_length=50)] = Field(default_factory=list)


_COURSE_CERTIFICATES = TypeAdapter(dict[ResourceUUID, ManagedCertificateConditions])


def validate_release_certificate_conditions(
    presentation_policy: StudyPolicyBody, progression_policy: StudyPolicyBody, snapshot: dict,
) -> None:
    """Validate opt-in award conditions before retaining a reviewed release."""
    presentation = presentation_policy.specified_value("certificate_presentation")
    if not isinstance(presentation, dict) or "managed_certificates_by_course" not in presentation:
        return
    invalid = studytomy_http_error(
        409, "RELEASE_CERTIFICATE_CONDITIONS_INVALID",
        "A managed certificate needs reviewed completion requirements and valid assessments from this course.",
    )
    try:
        course_uuid = snapshot["course"]["course_uuid"]
        conditions = _COURSE_CERTIFICATES.validate_python(presentation["managed_certificates_by_course"]).get(course_uuid)
        if conditions is None:
            return
        if not course_completion_text_activities(progression_policy, course_uuid):
            raise invalid
        required = conditions.required_assessment_uuids
        if len(set(required)) != len(required):
            raise invalid
        if not required:
            return

        assignments = {row["assignment_uuid"]: row for row in snapshot["assignments"]}
        assessment_activities = {
            row["id"] for row in snapshot["activities"]
            if row.get("published") is True and row.get("activity_sub_type") == "SUBTYPE_ASSIGNMENT_ANY"
        }
        for uuid in required:
            assignment = assignments.get(uuid)
            if (
                assignment is None
                or assignment.get("published") is not True
                or assignment.get("ungraded") is True
                or assignment.get("activity_id") not in assessment_activities
            ):
                raise invalid
            retained_grading_contract_digest(assignment, snapshot["assignment_tasks"])
    except (KeyError, TypeError, ValueError) as error:
        raise invalid from error
