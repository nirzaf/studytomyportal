from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.security.rbac import AccessAction
from src.services.mcp.draft_authoring import (
    SPECS,
    DraftCourseInput,
    DraftLessonInput,
    GroundedLessonSaveInput,
    GroundedQuizSaveInput,
    lesson_text_document,
)

ID = "dceea250-1ada-4e20-9b73-f0c9c61901bc"
COURSE = {"request_id": ID, "title": "Synthetic course"}
LESSON = COURSE | {"course_uuid": f"course_{ID}", "chapter_uuid": f"chapter_{ID}"}


@pytest.mark.parametrize("change", [
    {"published": True},
    {"public": True},
    {"org_id": 5},
    {"title": 123},
    {"title": "\x00draft"},
    {"request_id": "draft"},
])
def test_course_rejects_non_draft_and_malformed_input(change):
    with pytest.raises(ValidationError):
        DraftCourseInput.model_validate(COURSE | change)


@pytest.mark.parametrize("change", [
    {"content": {"type": "iframe"}},
    {"chapter_uuid": f"course_{ID}"},
    {"course_uuid": ID},
    {"text": "a" * 12001},
    {"text": "a\n" * 101},
    {"text": "control\x00"},
])
def test_lesson_rejects_unbounded_or_ambiguous_input(change):
    with pytest.raises(ValidationError):
        DraftLessonInput.model_validate(LESSON | change)


def test_lesson_markup_and_urls_remain_literal_text():
    text = '<img src="https://example.invalid/a.png">\n\n# Plain text'
    command = DraftLessonInput.model_validate(LESSON | {"text": text})
    document = lesson_text_document(command.text)
    assert document["content"] == [
        {"type": "paragraph", "content": [{"type": "text", "text": text.splitlines()[0]}]},
        {"type": "paragraph"},
        {"type": "paragraph", "content": [{"type": "text", "text": "# Plain text"}]},
    ]


def test_grounded_mcp_save_tools_are_scoped_and_approval_gated():
    tools = {spec.name: spec for spec in SPECS}
    for name in (
        "studytomy_save_grounded_lesson_draft",
        "studytomy_save_grounded_quiz_draft",
    ):
        spec = tools[name]
        assert spec.approval is True
        assert spec.api_tokens is False
        assert spec.audit_arguments is False
        assert spec.access_action is AccessAction.UPDATE
        assert spec.target_param == "course_uuid"
        assert spec.target_kind == "course"

    citation = {
        "source_uuid": str(uuid4()),
        "sha256": "a" * 64,
        "page": 1,
        "quote": "A sufficiently long source quotation.",
    }
    lesson = GroundedLessonSaveInput.model_validate({
        "course_uuid": f"course_{ID}", "lesson_uuid": f"activity_{ID}",
        "expected_current_version": 1, "title": "Reviewed draft",
        "blocks": [{"text": "Edited lesson text.", "citation": citation}],
    })
    assert lesson.expected_current_version == 1
    assert lesson.request_id is None
    keyed_lesson = GroundedLessonSaveInput.model_validate(lesson.model_dump() | {"request_id": ID})
    assert str(keyed_lesson.request_id) == ID
    with pytest.raises(ValidationError):
        GroundedLessonSaveInput.model_validate(lesson.model_dump() | {"request_id": "invalid"})
    quiz = GroundedQuizSaveInput.model_validate({
        "course_uuid": f"course_{ID}", "lesson_uuid": f"activity_{ID}",
        "request_id": ID, "assignment_uuid": f"assignment_{ID}", "title": "Reviewed quiz",
        "questions": [{"question": "A question?", "options": [
            {"text": "Correct", "is_correct": True}, {"text": "Other", "is_correct": False},
        ], "citation": citation}],
    })
    assert quiz.assignment_uuid == f"assignment_{ID}"
    with pytest.raises(ValidationError):
        GroundedLessonSaveInput.model_validate(lesson.model_dump() | {"published": True})
