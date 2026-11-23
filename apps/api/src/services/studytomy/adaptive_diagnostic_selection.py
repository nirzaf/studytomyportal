"""Deterministic selection from an approved, immutable diagnostic manifest."""

from collections import Counter
from dataclasses import dataclass
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

SELECTOR_VERSION = "adaptive_coverage_v1"


class DiagnosticLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    total_items: Annotated[int, Field(ge=1, le=100)]
    items_per_concept: Annotated[int, Field(ge=1, le=20)]
    minimum_scored_per_concept: Annotated[int, Field(ge=1, le=20)]
    expires_after_minutes: Annotated[int, Field(ge=1, le=10_080)]

    @model_validator(mode="after")
    def check_limits(self) -> Self:
        if self.minimum_scored_per_concept > min(
            self.total_items, self.items_per_concept
        ):
            raise ValueError("The scoring target must fit both item caps.")
        return self


class DiagnosticRules(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    allow_help: Literal[False]
    reveal: Literal["never", "after_close"]
    max_attempts: Annotated[int, Field(ge=1, le=20)]
    retake_wait_minutes: Annotated[int, Field(ge=0, le=43_200)]


class DiagnosticPoolItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    uuid: str
    concept_uuid: str
    family_uuid: str
    difficulty: Annotated[int, Field(ge=1, le=5)]
    content_digest: str


class DiagnosticManifest(BaseModel):
    """Stable ordering is reviewed and persisted. This selector uses no randomness."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    required_concepts: tuple[str, ...]
    prerequisites: dict[str, tuple[str, ...]]
    pool: tuple[DiagnosticPoolItem, ...]
    limits: DiagnosticLimits
    rules: DiagnosticRules
    mastery_policy_digest: str


@dataclass(frozen=True, slots=True)
class DiagnosticObservation:
    item_uuid: str
    outcome: Literal["correct", "incorrect", "skip", "not_scored"]


@dataclass(frozen=True, slots=True)
class DiagnosticSelection:
    item_uuid: str | None
    stop_reason: Literal["coverage_met", "item_limit", "insufficient_items"] | None = (
        None
    )


def select_diagnostic_item(
    manifest: DiagnosticManifest,
    history: tuple[DiagnosticObservation, ...],
    exposed_families: frozenset[str],
) -> DiagnosticSelection:
    """Pick one candidate from server state, without reading raw answers or accepting a client score."""
    lookup = {item.uuid: item for item in manifest.pool}
    visited: Counter[str] = Counter()
    scored: Counter[str] = Counter()
    latest: dict[str, tuple[DiagnosticPoolItem, str]] = {}
    excluded = set(exposed_families)
    observed_families: set[str] = set()
    for observation in history:
        item = lookup[observation.item_uuid]
        excluded.add(item.family_uuid)
        visited[item.concept_uuid] += 1
        if item.family_uuid not in observed_families and observation.outcome in {
            "correct",
            "incorrect",
        }:
            scored[item.concept_uuid] += 1
            latest[item.concept_uuid] = item, observation.outcome
        observed_families.add(item.family_uuid)

    need = [
        concept
        for concept in manifest.required_concepts
        if scored[concept] < manifest.limits.minimum_scored_per_concept
    ]
    if not need:
        return DiagnosticSelection(None, "coverage_met")
    if len(history) >= manifest.limits.total_items:
        return DiagnosticSelection(None, "item_limit")

    candidates = {concept: [] for concept in need}
    for item in manifest.pool:
        if (
            item.concept_uuid in candidates
            and item.family_uuid not in excluded
            and visited[item.concept_uuid] < manifest.limits.items_per_concept
        ):
            candidates[item.concept_uuid].append(item)

    for concept in need:
        if not visited[concept] and candidates[concept]:
            return DiagnosticSelection(candidates[concept][0].uuid)
    for concept in need:
        prior = latest.get(concept)
        if prior is None:
            if candidates[concept]:
                return DiagnosticSelection(candidates[concept][0].uuid)
            continue
        previous, outcome = prior
        if outcome == "incorrect":
            for prerequisite in manifest.prerequisites.get(concept, ()):
                if candidates.get(prerequisite):
                    return DiagnosticSelection(candidates[prerequisite][0].uuid)
        preferred = (
            min(previous.difficulty + 1, 5)
            if outcome == "correct"
            else max(previous.difficulty - 1, 1)
        )
        fallback = previous.difficulty
        for difficulty in (preferred, fallback):
            for item in candidates[concept]:
                if item.difficulty == difficulty:
                    return DiagnosticSelection(item.uuid)
    return DiagnosticSelection(None, "insufficient_items")
