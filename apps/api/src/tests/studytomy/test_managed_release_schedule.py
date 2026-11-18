from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.db.studytomy.governance import StudyPolicyKey
from src.services.studytomy.access_policy import _project_media_activity, _project_text_lesson
from src.tests.studytomy.test_studytomy_access import _synthetic_policy_body


@pytest.mark.parametrize("location", ["course", "chapter"])
@pytest.mark.parametrize("rule", [{}, [], False, 0, "", {"enabled": True}])
def test_retained_schedule_values_prevent_ungated_delivery(location, rule):
    course_uuid, text_uuid, media_uuid = "course_synthetic", "activity_text", "activity_pdf"
    course = {"course_uuid": course_uuid}
    chapter = {"id": 1, "lock_type": "public"}
    activity = {"published": True, "lock_type": "public", "name": "Synthetic lesson"}
    reference = f"courses/{course_uuid}/activities/{media_uuid}/documentpdf/source.pdf"
    release = SimpleNamespace(
        uuid="release_synthetic",
        course_uuid=course_uuid,
        progression_policy_id=1,
        snapshot={
            "schema": "course_content_evidence_v4",
            "course": course,
            "chapters": [chapter],
            "activity_order": [[1, 1, 1], [1, 2, 2]],
            "activities": [
                {**activity, "id": 1, "activity_uuid": text_uuid,
                 "activity_type": "TYPE_DYNAMIC", "activity_sub_type": "SUBTYPE_DYNAMIC_PAGE",
                 "content": {"type": "doc", "content": [
                     {"type": "paragraph", "content": [{"type": "text", "text": "Synthetic lesson"}]},
                 ]}},
                {**activity, "id": 2, "activity_uuid": media_uuid,
                 "activity_type": "TYPE_DOCUMENT", "activity_sub_type": "SUBTYPE_DOCUMENT_PDF",
                 "content": {"filename": "source.pdf"}},
            ],
        },
        media_manifest={reference: {
            "sha256": "0" * 64, "size_bytes": 1, "media_type": "application/pdf",
            "storage_reference": "studytomy/releases/synthetic/source.bin",
        }},
    )
    policy = SimpleNamespace(id=1, body=_synthetic_policy_body(StudyPolicyKey.D_09, {
        "release_schedule": {
            "ungated_text_activities": {course_uuid: [text_uuid]},
            "ungated_media_activities": {course_uuid: [media_uuid]},
        },
    }).model_dump(mode="json"))

    assert _project_text_lesson(release, policy, text_uuid).paragraphs == ["Synthetic lesson"]
    assert _project_media_activity(release, policy, media_uuid)[0] == reference

    if location == "course":
        course["drip_settings"] = rule
    else:
        chapter["drip_rule"] = rule

    for project, activity_uuid in ((_project_text_lesson, text_uuid), (_project_media_activity, media_uuid)):
        with pytest.raises(HTTPException) as error:
            project(release, policy, activity_uuid)
        assert error.value.status_code == 404
