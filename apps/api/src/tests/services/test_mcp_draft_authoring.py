import pytest
from pydantic import ValidationError

from src.services.mcp.draft_authoring import DraftCourseInput, DraftLessonInput, lesson_text_document


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
