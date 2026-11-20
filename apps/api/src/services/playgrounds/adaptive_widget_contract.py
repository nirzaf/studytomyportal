"""Validate and provide deterministic client-side behavior for adaptive widgets."""

import re
from html.parser import HTMLParser

from src.services.playgrounds.schemas.playgrounds_generator import (
    DEFAULT_LEARNER_LEVELS,
    MAX_TARGET_SKILLS,
    LearnerAdaptationContext,
)


class AdaptiveWidgetValidationError(ValueError):
    """The generated HTML does not satisfy the adaptive-widget contract."""


class _AdaptiveWidgetParser(HTMLParser):
    _VOID_ELEMENTS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.level_options: list[str] = []
        self.skill_options: list[str] = []
        self.level_selector_count = 0
        self.skill_selector_count = 0
        self._control: str | None = None
        self._option: dict[str, str] | None = None
        self.variants: list[dict[str, str]] = []
        self._variant: dict[str, object] | None = None
        self._variant_depth = 0
        self._capture: dict[str, object] | None = None
        self._capture_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "select":
            selector = "data-study-level-selector" if "data-study-level-selector" in values else (
                "data-study-skill-selector" if "data-study-skill-selector" in values else None
            )
            if selector is not None:
                if self._variant is not None or "disabled" in values or "hidden" in values:
                    raise AdaptiveWidgetValidationError("Adaptive selectors must remain visible and enabled.")
            if "data-study-level-selector" in values:
                if self._control is not None:
                    raise AdaptiveWidgetValidationError("Adaptive selector markup is nested.")
                self.level_selector_count += 1
                self._control = "level"
            elif "data-study-skill-selector" in values:
                if self._control is not None:
                    raise AdaptiveWidgetValidationError("Adaptive selector markup is nested.")
                self.skill_selector_count += 1
                self._control = "skill"
        elif tag == "option" and self._control is not None:
            self._option = {"value": values.get("value") or "", "text": ""}

        if "data-study-adaptation-variant" in values:
            if tag != "article" or self._variant is not None:
                raise AdaptiveWidgetValidationError("Each adaptation variant must be a separate article.")
            self._variant = {
                "level": values.get("data-level") or "",
                "skill": values.get("data-skill") or "",
                "scaffolding": "",
                "challenge": "",
            }
            self._variant_depth = 1
            return

        if self._variant is not None and tag not in self._VOID_ELEMENTS:
            self._variant_depth += 1

        if self._variant is not None:
            if "data-study-scaffolding" in values:
                self._capture = {"kind": "scaffolding", "tag": tag, "text": ""}
                self._capture_depth = self._variant_depth
            elif "data-study-challenge" in values:
                self._capture = {"kind": "challenge", "tag": tag, "text": ""}
                self._capture_depth = self._variant_depth

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._option is not None:
            self._option["text"] += data
        if self._variant is not None and self._capture is not None:
            self._capture["text"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "option" and self._option is not None and self._control is not None:
            value = str(self._option["value"] or self._option["text"]).strip()
            if self._control == "level":
                self.level_options.append(value)
            else:
                self.skill_options.append(value)
            self._option = None
        elif tag == "select" and self._control is not None:
            self._control = None

        if self._variant is None or tag in self._VOID_ELEMENTS:
            return

        if (
            self._capture is not None
            and tag == self._capture["tag"]
            and self._variant_depth == self._capture_depth
        ):
            kind = str(self._capture["kind"])
            self._variant[kind] = str(self._variant[kind]) + str(self._capture["text"])
            self._capture = None

        self._variant_depth -= 1
        if self._variant_depth == 0:
            self.variants.append({key: str(value).strip() for key, value in self._variant.items()})
            self._variant = None
            self._capture = None


_RUNTIME_STYLE = """<style id=\"studytomy-adaptive-runtime-style\">\n[data-study-adaptation-variant]{display:none!important}\n[data-study-adaptation-variant][data-study-active]{display:block!important}\n</style>"""
_RUNTIME_SCRIPT = """<script id=\"studytomy-adaptive-runtime\">\n(() => {\n  const level = document.querySelector('[data-study-level-selector]');\n  const skill = document.querySelector('[data-study-skill-selector]');\n  const variants = [...document.querySelectorAll('[data-study-adaptation-variant]')];\n  if (!level || !skill || variants.length === 0) return;\n  const update = () => {\n    for (const variant of variants) {\n      const active = variant.dataset.level === level.value && variant.dataset.skill === skill.value;\n      variant.toggleAttribute('data-study-active', active);\n      variant.hidden = !active;\n      variant.setAttribute('aria-hidden', String(!active));\n    }\n  };\n  level.addEventListener('change', update);\n  skill.addEventListener('change', update);\n  update();\n})();\n</script>"""


def validate_and_install_adaptive_runtime(
    html: str,
    adaptation: LearnerAdaptationContext,
) -> str:
    """Require a complete differentiated level/skill matrix, then attach the
    trusted switcher that makes the learner controls work without network or
    profile access.
    """
    if not adaptation.enabled:
        return html
    if re.search(
        r"id\s*=\s*([\"'])studytomy-adaptive-runtime(?:-style)?\1",
        html,
        flags=re.IGNORECASE,
    ):
        raise AdaptiveWidgetValidationError("Generated markup contains reserved adaptive runtime identifiers.")

    parser = _AdaptiveWidgetParser()
    try:
        parser.feed(html)
        parser.close()
    except AdaptiveWidgetValidationError:
        raise
    except Exception as exc:
        raise AdaptiveWidgetValidationError("Generated adaptive markup could not be parsed.") from exc

    expected_levels = list(adaptation.levels or DEFAULT_LEARNER_LEVELS)
    if len(expected_levels) < 2:
        raise AdaptiveWidgetValidationError("Adaptive practice needs at least two learner levels.")
    if parser.level_selector_count != 1 or parser.skill_selector_count != 1:
        raise AdaptiveWidgetValidationError("Adaptive practice needs exactly one visible selector for each setting.")
    if parser.level_options != expected_levels:
        raise AdaptiveWidgetValidationError("Generated level choices do not match the configured levels.")

    skills = parser.skill_options
    if (
        len(skills) < 2
        or len(skills) > MAX_TARGET_SKILLS
        or len(set(skills)) != len(skills)
    ):
        raise AdaptiveWidgetValidationError(
            f"Adaptive practice needs between two and {MAX_TARGET_SKILLS} distinct skill choices."
        )
    if not set(adaptation.target_skills).issubset(skills):
        raise AdaptiveWidgetValidationError("Generated skill choices omitted an educator-configured skill.")

    expected_pairs = {(level, skill) for level in expected_levels for skill in skills}
    actual_pairs = [(variant["level"], variant["skill"]) for variant in parser.variants]
    if len(actual_pairs) != len(expected_pairs) or set(actual_pairs) != expected_pairs:
        raise AdaptiveWidgetValidationError("Generated widget is missing one or more level/skill activities.")

    variants = {(variant["level"], variant["skill"]): variant for variant in parser.variants}
    for variant in parser.variants:
        if len(variant["scaffolding"]) < 12 or len(variant["challenge"]) < 12:
            raise AdaptiveWidgetValidationError("Every level/skill activity needs meaningful guidance and a challenge.")

    # Both dimensions must change the actual practice content, not merely the
    # control labels. Compare normalized text to avoid whitespace-only variants.
    def normalize(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().casefold()

    for skill in skills:
        scaffolding = {normalize(variants[(level, skill)]["scaffolding"]) for level in expected_levels}
        challenges = {normalize(variants[(level, skill)]["challenge"]) for level in expected_levels}
        if len(scaffolding) < 2 or len(challenges) < 2:
            raise AdaptiveWidgetValidationError("Changing level must change guidance and challenge content.")
    for level in expected_levels:
        scaffolding = {normalize(variants[(level, skill)]["scaffolding"]) for skill in skills}
        challenges = {normalize(variants[(level, skill)]["challenge"]) for skill in skills}
        if len(scaffolding) < 2 or len(challenges) < 2:
            raise AdaptiveWidgetValidationError("Changing focus skill must change guidance and challenge content.")

    if not re.search(r"</head\s*>", html, flags=re.IGNORECASE) or not re.search(
        r"</body\s*>", html, flags=re.IGNORECASE
    ):
        raise AdaptiveWidgetValidationError("Generated adaptive widget must be a complete HTML document.")
    html = re.sub(
        r"</head\s*>",
        lambda match: f"{_RUNTIME_STYLE}{match.group(0)}",
        html,
        count=1,
        flags=re.IGNORECASE,
    )
    return re.sub(
        r"</body\s*>",
        lambda match: f"{_RUNTIME_SCRIPT}{match.group(0)}",
        html,
        count=1,
        flags=re.IGNORECASE,
    )


def remove_adaptive_runtime(html: str) -> str:
    """Strip only the runtime previously injected by this module before an AI
    iteration, preventing the model from duplicating or rewriting it.
    """
    html = re.sub(
        r'<style\b[^>]*\bid=["\']studytomy-adaptive-runtime-style["\'][^>]*>.*?</style\s*>',
        "",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return re.sub(
        r'<script\b[^>]*\bid=["\']studytomy-adaptive-runtime["\'][^>]*>.*?</script\s*>',
        "",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
