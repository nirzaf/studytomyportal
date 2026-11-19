from typing import Annotated, Any, List, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, StringConstraints, field_validator


DEFAULT_LEARNER_LEVELS: tuple[str, ...] = ("Foundation", "Core", "Extension")
MAX_LEARNER_LEVELS = 5
MAX_TARGET_SKILLS = 8
MAX_LABEL_LENGTH = 80


LearnerSettingLabel = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_LABEL_LENGTH,
        pattern=r"^[^\r\n]+$",
    ),
]


def _sanitize_label_list(value: Any, *, max_items: int, field_name: str) -> Any:
    if value is None:
        return []
    if not isinstance(value, list):
        return value
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{field_name} entries must be strings")
        if "\r" in item or "\n" in item:
            raise ValueError(f"{field_name} entries must not contain line breaks")
        trimmed = item.strip()
        if not trimmed:
            continue
        cleaned.append(trimmed)
    if len(cleaned) > max_items:
        raise ValueError(f"{field_name} must contain at most {max_items} items")
    deduped: list[str] = []
    seen: set[str] = set()
    for item in cleaned:
        key = item.casefold()
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


class LearnerAdaptationContext(BaseModel):
    """Educator-authored adaptation settings; contains no learner profile data."""

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = True
    levels: list[LearnerSettingLabel] = Field(
        default_factory=list,
        max_length=MAX_LEARNER_LEVELS,
        validation_alias=AliasChoices("levels", "level_labels"),
    )
    target_skills: list[LearnerSettingLabel] = Field(
        default_factory=list,
        max_length=MAX_TARGET_SKILLS,
        validation_alias=AliasChoices("target_skills", "focus_skills"),
    )

    @field_validator("levels", mode="before")
    @classmethod
    def sanitize_levels(cls, value: Any) -> Any:
        return _sanitize_label_list(value, max_items=MAX_LEARNER_LEVELS, field_name="levels")

    @field_validator("target_skills", mode="before")
    @classmethod
    def sanitize_target_skills(cls, value: Any) -> Any:
        return _sanitize_label_list(value, max_items=MAX_TARGET_SKILLS, field_name="target_skills")


class PlaygroundContext(BaseModel):
    playground_name: str
    playground_description: str
    course_uuid: Optional[str] = None
    course_name: Optional[str] = None
    learner_adaptation: LearnerAdaptationContext = Field(
        default_factory=LearnerAdaptationContext
    )


class StartPlaygroundSession(BaseModel):
    playground_uuid: str
    prompt: str
    context: PlaygroundContext


class SendPlaygroundMessage(BaseModel):
    session_uuid: str
    playground_uuid: str
    message: str
    current_html: Optional[str] = None
    learner_adaptation: Optional[LearnerAdaptationContext] = None


class PlaygroundMessage(BaseModel):
    role: str  # "user" or "model"
    content: str


class PlaygroundSessionResponse(BaseModel):
    session_uuid: str
    iteration_count: int
    max_iterations: int
    html_content: Optional[str]
    message_history: List[PlaygroundMessage]
    learner_adaptation: Optional[LearnerAdaptationContext] = None


class PlaygroundSessionData(BaseModel):
    session_uuid: str
    playground_uuid: str
    owner_user_id: Optional[int] = None
    iteration_count: int
    max_iterations: int
    message_history: List[PlaygroundMessage]
    current_html: Optional[str]
    context: PlaygroundContext
