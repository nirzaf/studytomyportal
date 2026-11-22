import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request
from pydantic import TypeAdapter, ValidationError

from src.services.ai import learning_generation
from src.services.ai.learning_practice import PracticeSource, _project_surface
from src.services.ai.schemas.learning_surface import (
    ActionRequest,
    LearningPresentation,
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
    )
    record = SimpleNamespace(
        source_identity=surface.source_identity,
        presentation=surface.model_dump(mode="json"),
    )

    recovered = _project_surface(source, record, None)

    assert "request_hint" not in recovered.actions
    assert all(component.kind != "hint" for component in recovered.components)


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
