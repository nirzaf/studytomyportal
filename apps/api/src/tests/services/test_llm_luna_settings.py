"""Offline Luna structured-output settings regressions; no provider requests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from src.services.ai.llm import client
from src.services.studytomy.grounding import _LessonProposal, _QuizProposal


class Shape(BaseModel):
    title: str


@pytest.mark.parametrize(
    "name,output,endpoint,is_openai,expected",
    [
        ("gpt-6-luna", _LessonProposal, "https://api.openai.com/v1", True, True),
        ("gpt-6-luna", _QuizProposal, "https://api.openai.com/v1", True, True),
        ("gpt-6-luna", Shape, "https://api.openai.com/v1", True, True),
        ("gpt-6-luna", str, "https://api.openai.com/v1", True, False),
        ("gpt-4o", Shape, "https://api.openai.com/v1", True, False),
        ("gpt-6-luna", Shape, "https://gateway.example.invalid/v1", True, False),
        ("gpt-6-luna", Shape, None, False, False),
    ],
)
async def test_only_official_luna_structured(
    monkeypatch, name, output, endpoint, is_openai, expected
):
    model = (
        OpenAIChatModel(
            name,
            provider=OpenAIProvider(api_key="synthetic-not-a-key", base_url=endpoint),
        )
        if is_openai else object()
    )
    run = AsyncMock(return_value=SimpleNamespace(output="synthetic-output"))
    agent = SimpleNamespace(model=model, run=run)
    monkeypatch.setattr(client, "_agent", lambda *args: agent)

    result = await client.generate(
        model_name=name,
        user_prompt="synthetic",
        output_type=output,
        max_tokens=512,
        timeout=30,
        retries=0,
    )

    assert result == "synthetic-output"
    expected_settings = {"timeout": 30, "max_tokens": 512}
    if expected:
        expected_settings["openai_reasoning_effort"] = "none"
    assert run.call_args.kwargs["model_settings"] == expected_settings
    assert run.call_args.kwargs["retries"] == 0
    run.assert_awaited_once()
