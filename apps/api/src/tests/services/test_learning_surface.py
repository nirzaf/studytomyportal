import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request
from pydantic import TypeAdapter, ValidationError

from src.services.ai import learning_generation
from src.services.ai.learning_practice import (
    FractionEvaluationBasis,
    PracticeSource,
    ReviewedFractionExample,
    ReviewedNumberLineExample,
    _project_surface,
)
from src.services.ai.learning_actions import _next_question
from src.services.studytomy.learning_practice_retention import (
    PracticeRemovalRules,
    PracticeRetentionHoldCommand,
    PracticeRetentionRules,
)
from src.services.ai.schemas.learning_surface import (
    ActionRequest,
    LearningPresentation,
    LessonTutorAction,
    LearningAvailability,
    LearningSurface,
    StartLearningSurface,
)


@pytest.fixture
def surface():
    path = Path(__file__).resolve().parents[1] / "fixtures" / "learning_surface_v1.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_surface_fixture_round_trips(surface):
    assert LearningSurface.model_validate(surface).model_dump(mode="json") == surface


@pytest.mark.parametrize(
    ("section", "changes"),
    [
        (None, {"schema_version": 2}),
        (None, {"schema_version": True}),
        (None, {"schema_version": "1"}),
        (None, {"revision": True}),
        (None, {"actions": ["request_admin_access"]}),
        (None, {"actions": ["next_question"]}),
        (None, {"mode": "explore"}),
        (0, {"kind": "html"}),
        (0, {"text": "<script>alert(1)</script>"}),
        (0, {"text": "x" * 601}),
        (1, {"selected": 5}),
        (1, {"parts": 0}),
        ("question", {"answer_key": "1/2"}),
    ],
)
def test_surface_rejects_unsafe_or_inconsistent_payloads(surface, section, changes):
    if section == "question":
        target = surface["components"][2]["question"]
    elif section is None:
        target = surface
    else:
        target = surface["components"][section]
    target.update(changes)

    with pytest.raises(ValidationError):
        LearningSurface.model_validate(surface)


def test_proposal_only_selects_an_allowed_visual():
    assert LearningPresentation(visual="number_line").visual == "number_line"
    for proposal in (
        {"visual": "iframe"},
        {"visual": "number_line", "html": "<script>alert(1)</script>"},
        {"visual": "fraction_explorer", "action": "submit_answer"},
    ):
        with pytest.raises(ValidationError):
            LearningPresentation.model_validate(proposal)


def _reviewed_examples():
    return (
        ReviewedFractionExample(
            example_id="fraction_half",
            visual="fraction_explorer",
            parts=6,
            selected=2,
            numerator=1,
            denominator=3,
            prompt="What fraction of the whole is selected?",
            explanation="Two of six equal parts are selected.",
            hint="Count the selected parts and the total parts.",
            correct_feedback="Two of six is equal to one of three.",
            incorrect_feedback="Compare the selected parts with all equal parts.",
        ),
        ReviewedNumberLineExample(
            example_id="line_quarter",
            visual="number_line",
            first_mark=0,
            last_mark=1,
            divisions_per_unit=4,
            numerator=1,
            denominator=4,
            prompt="What fraction is the first mark after zero?",
            explanation="Four equal intervals span zero to one.",
            hint="Count the equal intervals between the endpoints.",
            correct_feedback="The first interval is one of four equal intervals.",
            incorrect_feedback="Compare the first interval with the full unit.",
        ),
    )


def test_surface_instantiates_exact_reviewed_example_and_keeps_answer_private():
    start = StartLearningSurface(
        schema_version=1,
        request_id=uuid4(),
        activity_uuid="activity_synthetic_fractions",
        mode="practice",
    )
    examples = _reviewed_examples()
    source = PracticeSource(
        activity_uuid=start.activity_uuid,
        course_uuid="course_synthetic",
        org_id=1,
        identity="a" * 64,
        approved_visuals=frozenset({"fraction_explorer", "number_line"}),
        approved_examples=examples,
        allow_hints=True,
        allow_practice=True,
    )

    surface, basis = learning_generation._instantiate(start, source, LearningPresentation(visual="fraction_explorer"))

    assert surface.components[1].model_dump() == {
        "kind": "fraction_explorer", "id": "visual", "parts": 6, "selected": 2,
    }
    assert surface.components[0].text == examples[0].explanation
    assert surface.components[2].question.prompt == examples[0].prompt
    assert "request_hint" in surface.actions
    assert basis == FractionEvaluationBasis(
        template="equivalent_fractions_v1", numerator=1, denominator=3,
        reviewed_example_id="fraction_half",
    )
    assert "numerator" not in surface.model_dump_json()
    assert "fraction_half" not in surface.model_dump_json()


def test_next_question_uses_only_next_approved_example():
    examples = _reviewed_examples()
    start = StartLearningSurface(
        schema_version=1, request_id=uuid4(), activity_uuid="activity_synthetic_fractions", mode="practice",
    )
    source = PracticeSource(
        activity_uuid=start.activity_uuid, course_uuid="course_synthetic", org_id=1,
        identity="b" * 64, approved_visuals=frozenset({"fraction_explorer", "number_line"}),
        approved_examples=examples, allow_hints=True, allow_practice=True,
    )
    surface, basis = learning_generation._instantiate(start, source, LearningPresentation(visual="fraction_explorer"))
    components, next_basis, _ = _next_question(surface, basis, source, retry=False)

    assert components[1] == {
        "kind": "number_line", "id": "visual", "first_mark": 0,
        "last_mark": 1, "divisions_per_unit": 4,
    }
    assert components[0]["text"] == examples[1].explanation
    assert components[2]["question"]["prompt"] == examples[1].prompt
    assert next_basis.reviewed_example_id == "line_quarter"


def test_reviewed_fraction_rejects_answer_that_disagrees_with_visual():
    with pytest.raises(ValidationError):
        ReviewedFractionExample(
            example_id="wrong_fraction", visual="fraction_explorer", parts=6, selected=2,
            numerator=1, denominator=2, prompt="What fraction of the whole is selected?",
            explanation="The text is not enough to override the visual.",
            correct_feedback="Correct.", incorrect_feedback="Try again.",
        )


def test_practice_retention_requires_explicit_bounded_policy_values():
    command = PracticeRetentionHoldCommand.model_validate({
        "operation_uuid": "08a47f68-8acd-4dde-80f2-1d89db39ab84",
        "reason_code": "LEGAL_HOLD",
    })
    assert str(command.operation_uuid) == "08a47f68-8acd-4dde-80f2-1d89db39ab84"

    policy = PracticeRetentionRules.model_validate({
        "retention_hours": 24,
        "retention_category": "lesson_visual_practice",
        "hold_reason_codes": ["LEGAL_HOLD"],
        "hold_release_reason_codes": ["REVIEW_COMPLETE"],
        "removal_reason_codes": ["PRIVACY_REQUEST"],
        "max_hold_review_days": 30,
    })
    assert policy.retention_hours == 24
    assert PracticeRemovalRules(automatic_expiry_enabled=False, authorized_removal_enabled=True)
    with pytest.raises(ValidationError):
        PracticeRetentionRules.model_validate({
            "retention_hours": 0,
            "retention_category": "lesson_visual_practice",
            "hold_reason_codes": ["LEGAL_HOLD"],
            "hold_release_reason_codes": ["REVIEW_COMPLETE"],
            "removal_reason_codes": ["PRIVACY_REQUEST"],
            "max_hold_review_days": 30,
        })
    with pytest.raises(ValidationError):
        PracticeRetentionRules.model_validate({
            "retention_hours": 24,
            "retention_category": "lesson_visual_practice",
            "hold_reason_codes": ["LEGAL_HOLD", "LEGAL_HOLD"],
            "hold_release_reason_codes": ["REVIEW_COMPLETE"],
            "removal_reason_codes": ["PRIVACY_REQUEST"],
            "max_hold_review_days": 30,
        })


@pytest.mark.asyncio
async def test_only_dedicated_practice_routes_bypass_legacy_ai_guard(monkeypatch):
    from types import SimpleNamespace

    import src.router as main_router

    calls = []

    async def legacy_guard(db, viewer):
        calls.append(viewer)

    monkeypatch.setattr(main_router, "require_studytomy_learner_ai_policy", legacy_guard)
    viewer = object()
    practice_request = Request({"type": "http", "headers": []})
    practice_request.scope["route"] = SimpleNamespace(name="api_start_learning_surface")
    await main_router._require_studytomy_ai_policy(practice_request, viewer, object())
    assert calls == []

    legacy_request = Request({"type": "http", "headers": []})
    legacy_request.scope["route"] = SimpleNamespace(name="api_ai_start_activity_chat_session")
    await main_router._require_studytomy_ai_policy(legacy_request, viewer, object())
    assert calls == [viewer]


def test_action_input_has_no_evaluation_authority(surface):
    action = {
        "surface_id": surface["surface_id"],
        "request_id": surface["request_id"],
        "expected_revision": 1,
        "action": "submit_answer",
        "answer": "2/4",
    }
    adapter = TypeAdapter(ActionRequest)
    assert adapter.validate_python(action).answer == "2/4"
    for unexpected in ({"correct": True}, {"action": "request_hint"}, {"answer": "x" * 49}):
        with pytest.raises(ValidationError):
            adapter.validate_python({**action, **unexpected})


def test_recovery_rejects_a_visual_removed_from_the_current_approval():
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "learning_surface_v1.json"
    surface = LearningSurface.model_validate(json.loads(fixture.read_text(encoding="utf-8")))
    source = PracticeSource(
        activity_uuid=surface.activity_id,
        course_uuid="course_synthetic",
        org_id=1,
        identity=surface.source_identity,
        approved_visuals=frozenset({"number_line"}),
        allow_practice=True,
    )
    record = SimpleNamespace(
        source_identity=surface.source_identity,
        presentation=surface.model_dump(mode="json"),
    )

    with pytest.raises(HTTPException) as denied:
        _project_surface(source, record, None)

    assert denied.value.status_code == 409
    assert denied.value.detail["code"] == "POLICY_NOT_APPROVED"


def test_recovery_removes_saved_hints_when_current_permission_is_revoked():
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "learning_surface_v1.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    payload["components"].append({"kind": "hint", "id": "saved-hint", "text": "Synthetic approved hint."})
    surface = LearningSurface.model_validate(payload)
    source = PracticeSource(
        activity_uuid=surface.activity_id,
        course_uuid="course_synthetic",
        org_id=1,
        identity=surface.source_identity,
        approved_visuals=frozenset({"fraction_explorer"}),
        allow_hints=False,
        allow_practice=True,
    )
    record = SimpleNamespace(
        source_identity=surface.source_identity,
        presentation=surface.model_dump(mode="json"),
    )

    recovered = _project_surface(source, record, None)

    assert "request_hint" not in recovered.actions
    assert all(component.kind != "hint" for component in recovered.components)


def test_recovery_denies_a_mode_removed_from_current_authority():
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "learning_surface_v1.json"
    surface = LearningSurface.model_validate(json.loads(fixture.read_text(encoding="utf-8")))
    source = PracticeSource(
        activity_uuid=surface.activity_id,
        course_uuid="course_synthetic",
        org_id=1,
        identity=surface.source_identity,
        approved_visuals=frozenset({"fraction_explorer"}),
        allow_practice=False,
    )
    record = SimpleNamespace(
        source_identity=surface.source_identity,
        presentation=surface.model_dump(mode="json"),
    )

    with pytest.raises(HTTPException) as denied:
        _project_surface(source, record, None)

    assert denied.value.status_code == 409
    assert denied.value.detail["code"] == "PRACTICE_ACTION_UNAVAILABLE"


def test_learning_availability_defaults_legacy_modes_to_disabled():
    assert LearningAvailability.model_validate({"available": False}).model_dump() == {
        "available": False,
        "surface": None,
        "allow_explore": False,
        "allow_practice": False,
    }


async def test_unapproved_practice_never_calls_provider_or_reserves_credit(monkeypatch):
    async def deny_unapproved_source(*_args, **_kwargs):
        raise HTTPException(
            status_code=409,
            detail={"code": "POLICY_NOT_APPROVED", "message": "No approved practice binding."},
        )

    provider = AsyncMock()
    reserve_credit = AsyncMock()
    monkeypatch.setattr(learning_generation, "_authorize_source", deny_unapproved_source)
    monkeypatch.setattr(learning_generation, "generate", provider)
    monkeypatch.setattr(learning_generation, "reserve_ai_credit", reserve_credit)

    with pytest.raises(HTTPException) as denied:
        await learning_generation.start_learning_surface(
            Request({"type": "http", "headers": []}),
            object(),
            object(),
            StartLearningSurface(
                schema_version=1,
                request_id=uuid4(),
                activity_uuid="activity_synthetic",
                mode="practice",
            ),
        )

    assert denied.value.status_code == 409
    assert denied.value.detail["code"] == "POLICY_NOT_APPROVED"
    provider.assert_not_awaited()
    reserve_credit.assert_not_awaited()


@pytest.mark.parametrize(
    ("value", "action"),
    [
        ("ask_question", LessonTutorAction.ASK_QUESTION),
        ("explain_visual", LessonTutorAction.EXPLAIN_VISUAL),
        ("deepen_topic", LessonTutorAction.DEEPEN_TOPIC),
        ("evaluate_answer", LessonTutorAction.EVALUATE_ANSWER),
    ],
)
def test_lesson_tutor_action_contract(value, action):
    adapter = TypeAdapter(LessonTutorAction)
    parsed = adapter.validate_python(value)
    assert parsed is action
    assert adapter.dump_python(parsed, mode="json") == value
