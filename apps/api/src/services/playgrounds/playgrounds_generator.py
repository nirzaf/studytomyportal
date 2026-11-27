from typing import Optional, AsyncGenerator
from uuid import uuid4
import logging
import redis
import json

from config.config import get_learnhouse_config
from src.services.ai.llm import generate_stream, model_for_tier
from src.services.playgrounds.schemas.playgrounds_generator import (
    DEFAULT_LEARNER_LEVELS,
    PlaygroundContext,
    PlaygroundSessionData,
    PlaygroundMessage,
)
from src.services.playgrounds.adaptive_widget_contract import (
    AdaptiveWidgetValidationError,
    remove_adaptive_runtime,
    validate_and_install_adaptive_runtime,
)

logger = logging.getLogger(__name__)

LH_CONFIG = get_learnhouse_config()

PLAYGROUND_SESSION_KEY = "playground_session:{session_uuid}"
PLAYGROUND_OWNER_KEY = "playground_session_owner:{session_uuid}"
SESSION_TTL = 2160000  # 25 days
MAX_ITERATIONS = 10


class PlaygroundGenerationError(RuntimeError):
    """Raised when streaming Playground HTML fails safely after logging."""


def get_redis_connection():
    redis_conn_string = LH_CONFIG.redis_config.redis_connection_string
    if redis_conn_string:
        try:
            return redis.from_url(redis_conn_string)
        except Exception as e:
            logger.error("Failed to connect to Redis: %s", e, exc_info=True)
    return None


def get_playground_session(session_uuid: str) -> Optional[PlaygroundSessionData]:
    r = get_redis_connection()
    if not r:
        return None
    try:
        key = PLAYGROUND_SESSION_KEY.format(session_uuid=session_uuid)
        session_data = r.get(key)
        if session_data:
            if isinstance(session_data, bytes):
                data = json.loads(session_data.decode("utf-8"))
            else:
                data = json.loads(session_data)
            return PlaygroundSessionData(**data)
    except Exception as e:
        logger.error("Failed to get Playground session: %s", e, exc_info=True)
    return None


def get_playground_session_owner(
    session_uuid: str,
    session: Optional[PlaygroundSessionData] = None,
) -> Optional[int]:
    """Return the user id that created the session, or None if unknown."""
    embedded_owner = getattr(session, "owner_user_id", None) if session is not None else None
    if isinstance(embedded_owner, int):
        return embedded_owner
    r = get_redis_connection()
    if not r:
        return None
    try:
        owner = r.get(PLAYGROUND_OWNER_KEY.format(session_uuid=session_uuid))
        if owner is None:
            return None
        if isinstance(owner, bytes):
            owner = owner.decode("utf-8")
        return int(owner)
    except Exception as e:
        logger.error("Failed to get Playground session owner: %s", e, exc_info=True)
    return None


def create_playground_session(
    playground_uuid: str,
    context: PlaygroundContext,
    user_id: Optional[int] = None,
) -> PlaygroundSessionData:
    session_uuid = f"pg_{uuid4()}"
    session = PlaygroundSessionData(
        session_uuid=session_uuid,
        playground_uuid=playground_uuid,
        owner_user_id=user_id,
        iteration_count=0,
        max_iterations=MAX_ITERATIONS,
        message_history=[],
        current_html=None,
        context=context,
    )
    # Persist owner atomically inside the session payload (and keep the legacy
    # side-key populated for compatibility). Read/iterate fail closed if no owner
    # matches the acting user.
    if user_id is not None:
        r = get_redis_connection()
        if r:
            try:
                r.setex(
                    PLAYGROUND_OWNER_KEY.format(session_uuid=session_uuid),
                    SESSION_TTL,
                    str(user_id),
                )
            except Exception as e:
                logger.error("Failed to save Playground session owner: %s", e, exc_info=True)
    save_playground_session(session)
    return session


def save_playground_session(session: PlaygroundSessionData) -> bool:
    r = get_redis_connection()
    if not r:
        return False
    try:
        key = PLAYGROUND_SESSION_KEY.format(session_uuid=session.session_uuid)
        r.setex(key, SESSION_TTL, json.dumps(session.model_dump()))
        return True
    except Exception as e:
        logger.error("Failed to save Playground session: %s", e, exc_info=True)
        return False


def build_playground_system_prompt(
    context: PlaygroundContext,
    course_context: Optional[str] = None,
) -> str:
    base_prompt = f"""You are an expert interactive content creator for educational playgrounds.

CONTEXT:
- Playground: {context.playground_name}
- Description: {context.playground_description}"""

    adaptation = context.learner_adaptation
    if adaptation.enabled:
        levels = adaptation.levels or list(DEFAULT_LEARNER_LEVELS)
        skills = adaptation.target_skills
        level_labels = "\n".join(f"- {label}" for label in levels)
        skill_labels = (
            "\n".join(f"- {skill}" for skill in skills)
            if skills
            else "- Infer a small set of core focus skills directly from the educator's request and any provided course material."
        )
        base_prompt += f"""

LEARNER-CHOSEN ADAPTATION (enabled by the educator):
- Build the exact machine-readable contract the platform validates:
  - One `<select data-study-level-selector>` with one `<option value="LABEL">LABEL</option>` for every level below, in the supplied order.
  - One `<select data-study-skill-selector>` with one option for every target skill. Include every supplied target skill exactly; if fewer than two were supplied, add at least one closely related, clearly named skill so learners can switch focus.
  - One `<article data-study-adaptation-variant data-level="LEVEL" data-skill="SKILL">` for every level/skill pair.
  - Inside every article, include non-empty `<div data-study-scaffolding>` and `<div data-study-challenge>` content. Vary scaffold detail across levels and challenge content across skills; each pair must have useful, distinct content.
- Do not implement selector event handlers. The platform adds a deterministic, offline switcher after validating every option and level/skill variant.
- Available level labels (plain labels supplied by the educator):
{level_labels}
- Focus skills (plain labels supplied by the educator):
{skill_labels}
- Meaningful pedagogical adaptation — changing the selected level or focus skill MUST visibly and functionally adapt BOTH:
  - Scaffolding: adjust explanation depth, worked examples, step-by-step guidance, hints, conceptual cues, and cognitive load (more structured scaffolding at earlier levels vs. minimal hints at advanced levels) while preserving the same core learning objective.
  - Challenges: adjust problem complexity, multi-step tasks, edge cases, open-ended prompts, and skill-specific practice scenarios tied directly to the chosen focus skills.
- Keep question keys, calculations, and answer checks deterministic and correct; do not ask an LLM to calculate or decide a grade at runtime.
- Strict local & self-selected state boundary: keep the learner's level and skill choices strictly in local component/page memory only. Do not read or claim to use learner history, mastery, identity, or other profile data. Do not persist choices to a backend, send telemetry or analytics, or couple to any server-side adaptive engine or gating/placement logic; the generated widget must not call external APIs or use browser storage.
- Include an accessible reset, keyboard-operable controls, and a brief note that choices are self-selected for this activity and are not a measured mastery result."""

    if course_context and context.course_name:
        base_prompt += f"""

COURSE CONTEXT:
The following content is from the course "{context.course_name}":

{course_context}

Use this content as the foundation for the interactive experience when relevant."""

    base_prompt += """

You create standalone interactive HTML learning widgets: focused, contained interactive environments that teach or reinforce a concept. Think card-based UIs, quiz panels, simulation panels, interactive diagrams, NOT full websites or landing pages.

CRITICAL DESIGN PHILOSOPHY:
- Think WIDGET, not website. Your output is a contained interactive environment embedded in a page.
- Use a clean, card-based or panel-based layout. Everything should feel like a purposeful learning tool.
- NO navigation bars, NO hero sections, NO footers, NO marketing copy, NO "Sign up" buttons.
- Avoid a website-like layout with a top navbar, multiple sections, and a footer; that is NOT what this is.
- The content should fill the iframe naturally with a single focused interactive experience.
- Prefer a neutral or light background (white, gray-50, slate-50) with one or two accent colors max.

DESIGN RULES:
1. Generate a COMPLETE, 100% self-contained, script-free HTML document using semantic HTML, inline <style> CSS, inline SVG diagrams, and native declarative interactivity (<details>/<summary> reveal panels, CSS :checked radio/checkbox tabs and flip cards, labeled SVG callouts, and structured comparison tables).
2. DO NOT rely on JavaScript or <canvas>: the portal preview sandbox intentionally runs with scripts disabled (`script-src 'none'`, empty iframe sandbox) and strips `<script>` tags and inline event handlers (`onclick`, `onload`, etc.). Any content that requires `<script>` or `<canvas>` painting will appear blank or inert.
3. DO NOT load external scripts, external stylesheets, external fonts, external images, iframes, or CDNs: the preview sandbox enforces a strict zero-egress Content Security Policy (`connect-src 'none'`, `script-src 'none'`, no remote assets/forms/popups). Everything must render completely offline inside the single HTML document.
4. Use LIGHT backgrounds, no dark full-page themes.
5. Centered, card-style layout; the learning content and clear declarative controls are the star.
6. Responsive with proper spacing, rounded corners, subtle shadows on cards, and accessible keyboard focus styles.
7. Clear instructions so learners know how to explore each card, hint, worked example, and self-check answer.

GOOD EXAMPLES (build script-free declarative widgets like these):
- Flashcard deck using CSS `:checked` toggles or `<details>/<summary>` flip/reveal cards
- Self-check practice cards with progressive `<details>` hints, worked steps, and model answers
- Labeled inline SVG concept diagrams, timelines, coordinate planes, and comparison matrices
- Step-by-step worked example walkthroughs using CSS radio tabs (Step 1 / Step 2 / Step 3)
- Concept map or structured study guide with expandable `<details>` nodes
- Formula reference card with worked numerical substitutions and common misconception callouts
- Side-by-side comparison panels (e.g., elastic vs. inelastic collisions, mitosis vs. meiosis)
- Vocabulary & definition matcher with expandable self-check key
- Structured case-study / branching explanation cards using CSS tabs

BAD EXAMPLES (do NOT build these):
- Anything requiring `<script>`, `onclick`, `oninput`, or `<canvas>` rendering (will be stripped/inert in the script-free sandbox)
- Scientifically validated physics or chemistry simulators (those belong in curated Virtual Labs, not generated HTML)
- A website with a hero, sections and a footer, landing page, or multi-page app with routing

CONTAINMENT & NETWORK SAFETY:
- Never include `<script>`, inline event handlers (`on*`), `<canvas>`, external `<link href=...>`, remote `<img>`, `fetch()`, `XMLHttpRequest`, `navigator.sendBeacon()`, `WebSocket`, `<form action=...>`, `window.open()`, or top-level navigation.
- Render all visuals and interactions strictly with semantic HTML, inline `<style>` CSS (including `:checked` / `<details>` / `<summary>`), inline SVG, and Unicode/HTML math formatting. Host-provided learner-level and focus-skill selectors switch among the declarative `data-study-adaptation-variant` blocks without executing widget scripts.

OUTPUT FORMAT:
Return ONLY the HTML code, starting with <!DOCTYPE html> and ending with </html>.
Do not include any explanations, markdown code blocks, or other text outside the HTML."""

    return base_prompt


async def generate_playground_stream(
    prompt: str,
    session: PlaygroundSessionData,
    model_name: str = "",
    current_html: Optional[str] = None,
    course_context: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    try:
        system_prompt = build_playground_system_prompt(session.context, course_context)

        history = [
            {"role": msg.role, "content": msg.content}
            for msg in session.message_history
        ]

        if current_html and current_html.strip():
            current_html = remove_adaptive_runtime(current_html)
            user_prompt = f"""The user wants to modify the existing interactive element.

CURRENT HTML CODE:
```html
{current_html}
```

USER REQUEST:
{prompt}

Please modify the HTML code above according to the user's request. Output ONLY the complete updated HTML code, starting with <!DOCTYPE html> and ending with </html>. Do not include any explanations."""
        else:
            user_prompt = prompt

        full_response = ""
        async for chunk in generate_stream(
            model_name=model_name or model_for_tier("fast"),
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            history=history,
            timeout=300.0,
        ):
            full_response += chunk
            if not session.context.learner_adaptation.enabled:
                yield chunk

        generated_html = extract_html_from_response(full_response)
        if not generated_html:
            raise ValueError("Playground generation returned no content")
        if session.context.learner_adaptation.enabled:
            generated_html = validate_and_install_adaptive_runtime(
                generated_html,
                session.context.learner_adaptation,
            )
            # Adaptive content is held until the complete matrix is validated;
            # never present a partial or non-adaptive widget as successful.
            yield generated_html

        session.message_history.append(
            PlaygroundMessage(role="user", content=prompt)
        )
        session.message_history.append(
            PlaygroundMessage(role="model", content=full_response)
        )
        session.current_html = generated_html
        session.iteration_count += 1

        if len(session.message_history) > 12:
            session.message_history = session.message_history[-12:]

        if not save_playground_session(session):
            raise RuntimeError("Could not persist generated playground")

    except AdaptiveWidgetValidationError:
        # This is a safe, user-correctable output-contract failure. Let the SSE
        # layer return its message without storing a broken adaptive result.
        raise
    except Exception as exc:
        # Never yield raw error text as a content chunk or emit a subsequent
        # done event; propagate a terminal failure to the SSE stream handler.
        logger.warning("Playground generation stream failed")
        raise PlaygroundGenerationError(
            "An internal error occurred while generating content."
        ) from exc


def extract_html_from_response(response: str) -> str:
    if "```html" in response:
        start = response.find("```html") + 7
        end = response.find("```", start)
        if end != -1:
            return response[start:end].strip()
    if "```" in response:
        start = response.find("```") + 3
        end = response.find("```", start)
        if end != -1:
            return response[start:end].strip()
    return response.strip()
