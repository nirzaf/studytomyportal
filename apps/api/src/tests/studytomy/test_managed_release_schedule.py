from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.db.studytomy.governance import StudyPolicyKey
from src.services.studytomy.access_policy import _project_media_activity, _project_text_lesson
from src.services.studytomy.awards import validate_release_certificate_conditions
from src.services.studytomy.grading import grading_contract_digest
from src.services.studytomy.grading_contract import retained_grading_contract_digest
from src.services.studytomy.idempotency import canonical_request_hash
from src.services.studytomy.prerequisites import (
    ungated_by_assessed_pass,
    validate_release_pass_prerequisites,
)
from src.tests.studytomy.test_studytomy_access import _synthetic_policy_body


@pytest.mark.parametrize("location,rule", [
    *((location, rule) for location in ("course", "chapter")
      for rule in ({}, [], False, 0, "", {"enabled": True})),
    ("policy", None),
    ("policy", "explicit"),
])
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
    decision = {
        "release_schedule": {
            "ungated_text_activities": {course_uuid: [text_uuid]},
            "ungated_media_activities": {course_uuid: [media_uuid]},
        },
    }
    policy = SimpleNamespace(
        id=1, body=_synthetic_policy_body(StudyPolicyKey.D_09, decision).model_dump(mode="json"),
    )

    assert _project_text_lesson(release, policy, text_uuid).paragraphs == ["Synthetic lesson"]
    assert _project_media_activity(release, policy, media_uuid)[0] == reference

    if location == "course":
        course["drip_settings"] = rule
    elif location == "chapter":
        chapter["drip_rule"] = rule
    else:
        decision["completion_prerequisites"] = {"assessed_pass_by_course": (
            {course_uuid: [
                {"kind": "assessed_pass", "target_activity_uuid": target,
                 "assessment_assignment_uuid": "assignment_synthetic"}
                for target in (text_uuid, media_uuid)
            ]} if rule == "explicit" else None
        )}
        policy.body = _synthetic_policy_body(StudyPolicyKey.D_09, decision).model_dump(mode="json")
        body = _synthetic_policy_body(StudyPolicyKey.D_09, decision)
        assert not ungated_by_assessed_pass(body, course_uuid)
        if rule == "explicit":
            assert ungated_by_assessed_pass(body, "course_other")
            assert ungated_by_assessed_pass(body, course_uuid, "activity_other")

    for project, activity_uuid in ((_project_text_lesson, text_uuid), (_project_media_activity, media_uuid)):
        with pytest.raises(HTTPException) as error:
            project(release, policy, activity_uuid)
        assert error.value.status_code == 404
        if location == "policy" and rule == "explicit":
            assert project(release, policy, activity_uuid, assessed_pass_verified=True)


@pytest.mark.parametrize("scenario", [
    "forward", "self", "cross_course", "ungraded", "private", "duplicate", "zero_points", "missing_tasks",
    "approval_without_rule", "approval_wrong_format", "malformed_approval",
])
def test_release_validates_assessed_pass_edges(scenario):
    source = {"id": 1, "activity_uuid": "activity_assessment", "published": True,
              "activity_sub_type": "SUBTYPE_ASSIGNMENT_ANY"}
    target = {"id": 2, "activity_uuid": "activity_lesson", "published": True,
              "activity_sub_type": "SUBTYPE_DYNAMIC_PAGE"}
    assignment = {
        "id": 1, "assignment_uuid": "assignment_assessment", "activity_id": 1, "published": True,
        "grading_type": "NUMERIC", "pass_threshold_percentage": None, "ungraded": False,
    }
    task = {"assignment_id": 1, "assignment_task_uuid": "task_1", "assignment_type": "QUIZ",
            "max_grade_value": 10, "contents": {}}
    snapshot = {
        "course": {"course_uuid": "course_synthetic"},
        "activities": [source, target],
        "assignments": [assignment],
        "assignment_tasks": [task],
        "chapter_order": [[1, 1], [2, 2]],
        "activity_order": [[1, 1, 10], [2, 2, 1]],
    }
    rule = {"kind": "assessed_pass", "target_activity_uuid": target["activity_uuid"],
            "assessment_assignment_uuid": assignment["assignment_uuid"]}
    rules = [rule]
    schedule = {
        "assessed_pass_delivery_activities": {"course_synthetic": [target["activity_uuid"]]},
        "ungated_text_activities": {"course_synthetic": [target["activity_uuid"]]},
    }
    decisions = {
        "completion_prerequisites": {"assessed_pass_by_course": {"course_synthetic": rules}},
        "release_schedule": schedule,
    }
    validate_release_pass_prerequisites(_synthetic_policy_body(StudyPolicyKey.D_09, decisions), snapshot)

    if scenario == "forward":
        snapshot["chapter_order"] = [[2, 1], [1, 2]]
    elif scenario == "self":
        rule["target_activity_uuid"] = source["activity_uuid"]
    elif scenario == "cross_course":
        rule["assessment_assignment_uuid"] = "assignment_other_course"
    elif scenario == "ungraded":
        assignment["ungraded"] = True
    elif scenario == "private":
        source["published"] = False
    elif scenario == "zero_points":
        task["max_grade_value"] = 0
    elif scenario == "missing_tasks":
        snapshot["assignment_tasks"] = []
    elif scenario == "approval_without_rule":
        del decisions["completion_prerequisites"]
    elif scenario == "approval_wrong_format":
        target["activity_sub_type"] = "SUBTYPE_DOCUMENT_PDF"
    elif scenario == "malformed_approval":
        schedule["assessed_pass_delivery_activities"]["course_synthetic"].append(42)
    else:
        rules.append(rule.copy())
    body = _synthetic_policy_body(StudyPolicyKey.D_09, decisions)
    if scenario in {"approval_without_rule", "malformed_approval"}:
        assert not ungated_by_assessed_pass(body, "course_synthetic", target["activity_uuid"])
    with pytest.raises(HTTPException) as error:
        validate_release_pass_prerequisites(body, snapshot)
    assert error.value.detail["code"] == "RELEASE_ASSESSED_PASS_INVALID"


def test_retained_grading_contract_matches_existing_native_digest():
    assignment = {
        "id": 1, "assignment_uuid": "assignment_1", "grading_type": "PERCENTAGE",
        "pass_threshold_percentage": 65.5, "ungraded": False,
    }
    task = {"assignment_id": 1, "assignment_task_uuid": "task_1", "assignment_type": "QUIZ",
            "max_grade_value": 10, "contents": {"question": "Synthetic"}}
    previous_format = {
        "version": "native-assessment-v1", "assignment": "assignment_1",
        "grading_type": "PERCENTAGE", "pass_threshold_percentage": 65.5, "ungraded": False,
        "tasks": [["task_1", "QUIZ", 10, {"question": "Synthetic"}]],
    }
    live_digest = grading_contract_digest(SimpleNamespace(**assignment), [SimpleNamespace(**task)])
    assert retained_grading_contract_digest(assignment, [task]) == live_digest
    assert live_digest == canonical_request_hash(previous_format)
    assert retained_grading_contract_digest(assignment, [{**task, "max_grade_value": 11}]) != live_digest


@pytest.mark.parametrize("scenario", ["no_completion", "unpublished", "foreign_assessment", "public_verification"])
def test_reviewed_certificate_conditions_require_retained_completion_and_assessments(scenario):
    course_uuid = "course_synthetic"
    assessment_uuid = "assignment_synthetic"
    assignment = {
        "id": 1, "assignment_uuid": assessment_uuid, "activity_id": 1, "published": True,
        "grading_type": "NUMERIC", "pass_threshold_percentage": None, "ungraded": False,
    }
    snapshot = {
        "course": {"course_uuid": course_uuid},
        "assignments": [assignment],
        "activities": [{"id": 1, "published": True, "activity_sub_type": "SUBTYPE_ASSIGNMENT_ANY"}],
        "assignment_tasks": [{"assignment_id": 1, "assignment_task_uuid": "task_synthetic",
                              "assignment_type": "QUIZ", "max_grade_value": 100, "contents": {}}],
    }
    progression = _synthetic_policy_body(StudyPolicyKey.D_09, {
        "completion_prerequisites": {"course_completion_text_activities": {course_uuid: ["activity_synthetic"]}},
    })
    certificate = {
        "award_name": "Synthetic certificate", "issuer_name": "Synthetic issuer", "verification": "private",
        "required_assessment_uuids": [assessment_uuid],
    }

    def presentation():
        return _synthetic_policy_body(StudyPolicyKey.D_16, {
            "certificate_presentation": {"managed_certificates_by_course": {course_uuid: certificate}},
        })

    validate_release_certificate_conditions(presentation(), progression, snapshot)
    if scenario == "no_completion":
        progression = _synthetic_policy_body(StudyPolicyKey.D_09)
    elif scenario == "unpublished":
        assignment["published"] = False
    elif scenario == "foreign_assessment":
        certificate["required_assessment_uuids"] = ["assignment_other_course"]
    else:
        certificate["verification"] = "public"
    with pytest.raises(HTTPException) as invalid:
        validate_release_certificate_conditions(presentation(), progression, snapshot)
    assert invalid.value.detail["code"] == "RELEASE_CERTIFICATE_CONDITIONS_INVALID"
