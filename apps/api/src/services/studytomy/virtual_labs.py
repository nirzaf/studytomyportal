"""Governed virtual science lab descriptor validation for Studytomy releases (T-06).

Reuses existing Activity.content JSON and StudyCourseRelease snapshots without
broadening arbitrary external embeds. Any external Embed activity that does not
carry a strictly valid, pinned virtual_lab descriptor continues to be rejected
with RELEASE_MEDIA_UNSUPPORTED.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from src.services.studytomy.errors import studytomy_http_error

APPROVED_LAB_IDS = frozenset({"conservation-momentum"})
DEFAULT_APPROVED_LAB_ORIGINS = frozenset({"https://labs.studytomy.com"})
_CURATED_MANIFEST_PATHS = (
    Path(__file__).resolve().parents[4] / "labs" / "momentum-pilot" / "manifest.json",
)


def approved_virtual_lab_manifests() -> dict[tuple[str, str], dict[str, Any]]:
    """Load reviewed static-lab manifests trusted by the backend (fail-closed)."""
    manifests: dict[tuple[str, str], dict[str, Any]] = {}
    candidate_paths = list(_CURATED_MANIFEST_PATHS)
    env_file = os.getenv("LEARNHOUSE_STUDYTOMY_APPROVED_LAB_MANIFEST_FILE", "").strip()
    if env_file:
        candidate_paths.append(Path(env_file))
    for path in candidate_paths:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            lab_id = item.get("lab_id")
            build_version = item.get("build_version")
            build_sha256 = item.get("build_sha256")
            if isinstance(lab_id, str) and isinstance(build_version, str) and isinstance(build_sha256, str):
                manifests[(lab_id, build_version)] = item
    raw_json = os.getenv("LEARNHOUSE_STUDYTOMY_APPROVED_LAB_MANIFESTS", "").strip()
    if raw_json:
        try:
            extra = json.loads(raw_json)
            if isinstance(extra, list):
                for item in extra:
                    if isinstance(item, dict) and isinstance(item.get("lab_id"), str) and isinstance(item.get("build_version"), str):
                        manifests[(item["lab_id"], item["build_version"])] = item
        except ValueError:
            pass
    return manifests


def approved_virtual_lab_origins() -> frozenset[str]:
    """Return configured approved static lab origins (scheme://host[:port])."""
    raw = os.getenv("LEARNHOUSE_STUDYTOMY_APPROVED_LAB_ORIGINS", "").strip()
    if not raw:
        return DEFAULT_APPROVED_LAB_ORIGINS
    origins: set[str] = set()
    for part in raw.split(","):
        candidate = part.strip().rstrip("/")
        if not candidate:
            continue
        parsed = urlsplit(candidate)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password:
            continue
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1"}:
            continue
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            continue
        origins.add(f"{parsed.scheme}://{parsed.netloc}")
    return frozenset(origins) if origins else DEFAULT_APPROVED_LAB_ORIGINS


def _validate_entry_url(url: str, *, lab_id: str, build_version: str, expected_entry_path: str | None = None) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ValueError("Invalid virtual lab entry URL.")
    if parsed.username or parsed.password:
        raise ValueError("Virtual lab entry URL must not include credentials.")
    if parsed.query:
        raise ValueError("Virtual lab entry URL must not include query parameters.")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in approved_virtual_lab_origins():
        raise ValueError("Virtual lab entry URL origin is not approved.")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("Non-loopback virtual lab URLs must use HTTPS.")
    required_suffix = f"/{(expected_entry_path or f'{lab_id}/{build_version}/index.html').lstrip('/')}"
    if not parsed.path.endswith(required_suffix):
        raise ValueError("Virtual lab entry URL must use the version-pinned immutable asset path.")
    return url


class GovernedVirtualLabDescriptor(BaseModel):
    """Immutable, pinned simulator build descriptor stored in activity.content['virtual_lab']."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["studytomy_virtual_lab_v1"] = "studytomy_virtual_lab_v1"
    lab_id: Literal["conservation-momentum"]
    experiment_id: Literal["conservation-momentum"]
    build_version: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._-]{1,64}$")]
    build_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    entry_url: Annotated[str, StringConstraints(min_length=12, max_length=512)]
    units_system: Literal["SI"] = "SI"
    source_repository: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)
    ] = "https://github.com/danielw-sudo/agentuto"
    source_commit: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
    linked_assignment_activity_uuid: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ] | None = None
    submission_formats_note: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
    ] | None = None


def validate_governed_virtual_lab_content(
    content: object,
    *,
    snapshot: dict | None = None,
) -> GovernedVirtualLabDescriptor:
    """Validate activity.content for a governed virtual lab activity.

    Raises HTTP 409 RELEASE_MEDIA_UNSUPPORTED when missing, arbitrary, unpinned,
    pointing at an unapproved origin, or referencing an unknown linked assignment.
    """
    if not isinstance(content, dict):
        raise studytomy_http_error(
            409,
            "RELEASE_MEDIA_UNSUPPORTED",
            "External embeds require an approved pinned virtual lab descriptor.",
        )
    raw = content.get("virtual_lab")
    if not isinstance(raw, dict):
        raise studytomy_http_error(
            409,
            "RELEASE_MEDIA_UNSUPPORTED",
            "External embeds require an approved pinned virtual lab descriptor.",
        )
    try:
        descriptor = GovernedVirtualLabDescriptor.model_validate(raw)
        manifests = approved_virtual_lab_manifests()
        pinned = manifests.get((descriptor.lab_id, descriptor.build_version))
        if not isinstance(pinned, dict):
            raise ValueError("Virtual lab version is not registered in the reviewed manifest set.")
        if pinned.get("build_sha256") != descriptor.build_sha256:
            raise ValueError("Virtual lab build_sha256 does not match the reviewed manifest.")
        if pinned.get("experiment_id") != descriptor.experiment_id:
            raise ValueError("Virtual lab experiment_id does not match the reviewed manifest.")
        upstream = pinned.get("upstream_source") if isinstance(pinned.get("upstream_source"), dict) else {}
        if upstream.get("repository") != descriptor.source_repository or upstream.get("commit") != descriptor.source_commit:
            raise ValueError("Virtual lab upstream attribution does not match the reviewed manifest.")
        _validate_entry_url(
            descriptor.entry_url,
            lab_id=descriptor.lab_id,
            build_version=descriptor.build_version,
            expected_entry_path=(
                pinned.get("versioned_entry_path")
                if isinstance(pinned.get("versioned_entry_path"), str)
                else None
            ),
        )
    except (ValidationError, ValueError):
        raise studytomy_http_error(
            409,
            "RELEASE_MEDIA_UNSUPPORTED",
            "The virtual lab descriptor is invalid, unpinned, or uses an unapproved origin.",
        ) from None

    if snapshot is not None and descriptor.linked_assignment_activity_uuid is not None:
        activities = snapshot.get("activities", [])
        assignments = snapshot.get("assignments", [])
        linked_act = next(
            (
                row
                for row in activities
                if isinstance(row, dict)
                and row.get("activity_uuid") == descriptor.linked_assignment_activity_uuid
                and row.get("activity_sub_type") == "SUBTYPE_ASSIGNMENT_ANY"
                and row.get("published") is True
            ),
            None,
        )
        if linked_act is None:
            raise studytomy_http_error(
                409,
                "RELEASE_MEDIA_UNSUPPORTED",
                "Linked practical assignment activity is missing or unpublished in this release.",
            )
        linked_assignment = next(
            (
                row
                for row in assignments
                if isinstance(row, dict) and row.get("activity_id") == linked_act.get("id")
            ),
            None,
        )
        if linked_assignment is None:
            raise studytomy_http_error(
                409,
                "RELEASE_MEDIA_UNSUPPORTED",
                "Linked practical assignment record is missing from this release snapshot.",
            )

    return descriptor
