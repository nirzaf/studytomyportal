import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from src.services.ai.schemas.learning_surface import (
    ActionRequest,
    LearningPresentation,
    LearningSurface,
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
