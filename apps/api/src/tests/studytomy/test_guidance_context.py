from src.services.studytomy.adaptive_guidance import _lesson_prose


def test_guidance_prose_ignores_assessment_blocks_and_caps_context():
    doc = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "Approved explanation"}],
            },
            {"type": "blockQuiz", "content": [{"type": "text", "text": "Hidden key"}]},
            {"type": "flipcard", "attrs": {"answer": "Private answer"}},
            {"type": "staffNote", "content": [{"type": "text", "text": "Staff only"}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "x" * 9000}]},
        ],
    }
    prose, limited = _lesson_prose(doc)
    assert limited
    assert prose.startswith("Approved explanation\n")
    assert len(prose) == 4000
    assert not any(
        secret in prose for secret in ("Hidden key", "Private answer", "Staff only")
    )
