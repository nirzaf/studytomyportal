"""Prepare the managed Momentum Lab live E2E using synthetic local accounts only.

All product records after the native account seed are created through the
loopback HTTP API. Policy approvals and learner eligibility are test fixtures,
not real educator, safeguarding, commercial, or curriculum decisions.
"""

import argparse
import base64
import copy
import json
import os
import secrets
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
sys.path.insert(0, str(ROOT / "scripts"))

from local_demo_seed import validate_target
from src.db.studytomy.governance import (  # noqa: E402
    POLICY_REQUIRED_DECISION_AREAS,
    StudyPolicyKey,
)
from src.services.studytomy.policies import POLICY_REQUIRED_RESPONSIBILITIES  # noqa: E402
from src.tests.services.rag_helpers import make_pdf  # noqa: E402


API_PREFIX = "/api/v1"
def current_pilot_build(lab_origin: str) -> dict:
    manifest = json.loads((ROOT / "apps/labs/momentum-pilot/manifest.json").read_text())
    keys = ("lab_id", "experiment_id", "build_version", "build_sha256", "versioned_entry_path", "artifact_sha256")
    with httpx.Client(timeout=5, trust_env=False) as static_lab:
        response = static_lab.get(f"{lab_origin}/manifest.json")
        response.raise_for_status()
        serving = response.json()
    if any(serving.get(key) != manifest[key] for key in keys):
        raise ValueError("The local static lab does not serve the checkout's pinned momentum build")
    return {
        **{key: manifest[key] for key in keys[:4]},
        "entry_url": f"{lab_origin}/{manifest['versioned_entry_path']}",
        "source_repository": manifest["upstream_source"]["repository"],
        "source_commit": manifest["upstream_source"]["commit"],
    }


def loopback_origin(raw: str, label: str) -> str:
    parsed = urlsplit(raw)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or not parsed.port
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{label} must be a bare loopback HTTP origin with an explicit port")
    return f"http://{parsed.netloc}"


def private_output(path: Path) -> Path:
    output = path.resolve()
    if output.is_relative_to(ROOT) or not output.parent.is_dir() or path.is_symlink():
        raise ValueError("Output must be a new file outside the checkout in an existing private directory")
    if output.parent.stat().st_mode & 0o077:
        raise ValueError("Output directory must be owner-only because the scenario contains synthetic passwords")
    if output.exists():
        raise ValueError("Output already exists; use a new isolated scenario path")
    return output


def private_credentials(path: Path) -> Path:
    credentials = path.resolve()
    if (
        not credentials.is_file()
        or path.is_symlink()
        or credentials.is_relative_to(ROOT)
        or credentials.stat().st_mode & 0o077
        or credentials.parent.stat().st_mode & 0o077
    ):
        raise ValueError("Credentials must be a regular owner-only file outside the checkout in an owner-only directory")
    return credentials


def synthetic_policy_items(key: StudyPolicyKey, specified: dict) -> dict:
    items = {
        area: {
            "state": "not_applicable",
            "rationale": "Synthetic local E2E fixture only; not an organizational decision.",
            "evidence_reference": f"SYNTHETIC/{key.value}/{area}",
        }
        for area in POLICY_REQUIRED_DECISION_AREAS[key]
    }
    for area, value in specified.items():
        items[area] = {
            "state": "specified",
            "value": value,
            "rationale": "Synthetic local E2E contract only; educator and safeguarding review remain pending.",
            "evidence_reference": f"SYNTHETIC/{key.value}/{area}",
        }
    return items


def run_browser_phase(web_origin: str, phase: str, account: dict, **scenario) -> dict:
    result = subprocess.run(
        ["node", str(ROOT / "apps/e2e/scripts/live-vlab-author-review.mjs")],
        input=json.dumps({
            "web": web_origin, "phase": phase,
            "account": {key: account[key] for key in ("email", "password")},
            **scenario,
        }),
        text=True, capture_output=True, timeout=150,
    )
    if result.returncode:
        message = next(
            (line for line in result.stderr.splitlines() if line.startswith("Synthetic momentum browser failed at:")),
            f"Synthetic browser phase {phase} failed.",
        )
        raise RuntimeError(message)
    return json.loads(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", required=True, help="Bare loopback API origin, e.g. http://127.0.0.1:1342")
    parser.add_argument("--web", required=True, help="Bare loopback web origin")
    parser.add_argument("--lab", required=True, help="Bare loopback static lab origin, e.g. http://127.0.0.1:4173")
    parser.add_argument("--credentials", required=True, type=Path, help="Private native-account manifest")
    parser.add_argument("--output", required=True, type=Path, help="New private E2E scenario JSON path")
    parser.add_argument("--synthetic-only", action="store_true", required=True)
    args = parser.parse_args()

    db_url = validate_target(os.environ["LEARNHOUSE_SQL_CONNECTION_STRING"])
    api_origin = loopback_origin(args.api, "API origin")
    lab_origin = loopback_origin(args.lab, "lab origin")
    web_origin = loopback_origin(args.web, "Web origin")
    pilot_build = current_pilot_build(lab_origin)
    output = private_output(args.output)
    fixture = json.loads(private_credentials(args.credentials).read_text())
    if fixture.get("scenario") != "native-accounts-v1" or fixture.get("database") != db_url.database:
        parser.error("Credentials must come from local_demo_seed.py for the exact disposable database")
    accounts = {item.get("label"): item for item in fixture.get("accounts", [])}
    if not {"admin", "teacher", "reviewer", "learner"}.issubset(accounts):
        parser.error("The four synthetic native seed accounts are required")
    for label, account in accounts.items():
        if account.get("email") != f"demo-{label}@example.com":
            parser.error("Only the exact native synthetic accounts are accepted")

    clients: dict[str, httpx.Client] = {}
    ids: dict[str, dict] = {}

    def call(label: str, method: str, path: str, expected: int = 200, **kwargs):
        response = getattr(clients[label], method)(path, **kwargs)
        if response.status_code != expected:
            try:
                detail = response.json().get("detail", {})
                code = detail.get("code") if isinstance(detail, dict) else "request_failed"
            except ValueError:
                code = "request_failed"
            raise RuntimeError(f"{method.upper()} {path}: expected {expected}, got {response.status_code} ({code})")
        return response.json() if response.content else None

    def post(label: str, path: str, expected: int = 200, **kwargs):
        headers = dict(kwargs.pop("headers", {}))
        headers["Idempotency-Key"] = headers.get("Idempotency-Key", str(uuid4()))
        return call(label, "post", path, expected=expected, headers=headers, **kwargs)

    def scope(owner_uuid: str, capability: str, kind: str, resource_uuid: str) -> None:
        post(
            "admin",
            f"{ids['org_path']}/service-owners/{owner_uuid}/scopes",
            expected=201,
            json={
                "capability": capability,
                "scope_kind": kind,
                "scope_uuid": resource_uuid,
                "evidence_reference": "SYNTHETIC/exact-resource-only",
            },
        )

    def policy(key_text: str, specified: dict) -> dict:
        key = StudyPolicyKey(key_text)
        current = datetime.now(UTC)
        version = post(
            "admin",
            f"{ids['org_path']}/policies",
            expected=201,
            json={
                "policy_key": key_text,
                "body": {"decision_items": synthetic_policy_items(key, specified)},
                "evidence_reference": f"SYNTHETIC/local-vlab/{key_text}",
                "effective_from": (current - timedelta(minutes=2)).isoformat(),
                "effective_to": (current + timedelta(hours=8)).isoformat(),
            },
        )
        for responsibility in sorted(POLICY_REQUIRED_RESPONSIBILITIES[key], key=lambda item: item.value):
            post(
                "reviewer",
                f"{ids['org_path']}/policies/{version['policy_uuid']}/approvals",
                json={
                    "responsibility": responsibility.value,
                    "evidence_reference": f"SYNTHETIC/local-vlab/{key_text}/{responsibility.value}",
                    "rationale": "Distinct synthetic reviewer fixture. Not a production approval.",
                },
            )
        return version

    try:
        for label in ("admin", "teacher", "reviewer", "learner"):
            clients[label] = httpx.Client(
                base_url=api_origin,
                trust_env=False,
                follow_redirects=False,
                timeout=45,
            )
            response = clients[label].post(
                f"{API_PREFIX}/auth/login",
                data={
                    "username": accounts[label]["email"],
                    "password": accounts[label]["password"],
                    "org_slug": fixture["org_slug"],
                },
            )
            if response.status_code != 200:
                raise RuntimeError(f"Synthetic {label} login returned {response.status_code}")
            # The API deliberately marks session cookies Secure outside dev
            # mode. This seed client uses loopback HTTP, so authenticate its
            # own API calls with the normal login response's bearer token.
            access_token = response.json().get("tokens", {}).get("access_token")
            if not access_token:
                raise RuntimeError(f"Synthetic {label} login returned no access token")
            clients[label].headers["Authorization"] = f"Bearer {access_token}"
            session = call(label, "get", f"{API_PREFIX}/users/session")
            if session["user"]["id"] != accounts[label]["user_id"]:
                raise RuntimeError(f"Synthetic {label} identity mismatch")
            ids[label] = session["user"]
            ids[f"{label}_role"] = session["roles"][0]["role"]
            ids[f"{label}_role_assignment"] = session["roles"][0]

        org = ids["admin_role_assignment"]["org"]
        if org["id"] != fixture["org_id"] or org.get("name") != "Synthetic local demo":
            raise ValueError("Exact synthetic seed organization is required")
        ids["org_uuid"] = org["org_uuid"]
        ids["org_path"] = f"{API_PREFIX}/studytomy/orgs/{org['org_uuid']}"
        if call("admin", "get", f"{ids['org_path']}/policies") or call(
            "admin", "get", f"{ids['org_path']}/service-owners"
        ):
            raise ValueError("Fresh synthetic organization required; existing governance records are preserved")

        # Give only the distinct reviewer the exact synthetic staff capabilities used below.
        rights = copy.deepcopy(ids["reviewer_role"]["rights"])
        rights["studytomy"]["learner_eligibility_review"] = True
        rights["studytomy"]["offer_publication"] = True
        rights["studytomy"]["content_release_review"] = True
        rights["studytomy"]["learner_dossier_read"] = True
        role = post(
            "admin",
            f"{API_PREFIX}/roles/org/{fixture['org_id']}",
            json={"name": "Synthetic local lab reviewer", "rights": rights},
        )
        call(
            "admin",
            "put",
            f"{API_PREFIX}/orgs/{fixture['org_id']}/users/{ids['reviewer']['id']}/role/{role['role_uuid']}",
            expected=200,
        )

        # Author creates a private course and lab/assignment activities through native REST routes.
        course = call(
            "teacher",
            "post",
            f"{API_PREFIX}/courses/",
            expected=200,
            params={"org_id": fixture["org_id"]},
            data={
                "name": "Synthetic Grade 12 Physics — Conservation of Momentum",
                "description": "Synthetic local managed lab acceptance fixture.",
                "public": "false",
                "about": "Synthetic test content; no syllabus certification claimed.",
            },
        )
        ids["course_uuid"] = course["course_uuid"]
        ids["course_id"] = course["id"]
        ids["release_name"] = course["name"]
        other = call(
            "teacher",
            "post",
            f"{API_PREFIX}/courses/",
            expected=200,
            params={"org_id": fixture["org_id"]},
            data={
                "name": "Synthetic unrelated course",
                "description": "Exact-scope denial fixture.",
                "public": "false",
                "about": "Synthetic only.",
            },
        )

        roles_effective_from = datetime.now(UTC) - timedelta(minutes=2)
        roles_effective_to = datetime.now(UTC) + timedelta(hours=8)
        responsibilities = {
            responsibility
            for key in (
                StudyPolicyKey.D_01, StudyPolicyKey.D_02, StudyPolicyKey.D_03,
                StudyPolicyKey.D_05, StudyPolicyKey.D_07, StudyPolicyKey.D_08, StudyPolicyKey.D_11,
                StudyPolicyKey.D_09, StudyPolicyKey.D_10, StudyPolicyKey.D_12,
                StudyPolicyKey.D_13, StudyPolicyKey.D_15, StudyPolicyKey.D_16,
                StudyPolicyKey.D_17, StudyPolicyKey.D_18, StudyPolicyKey.D_20,
            )
            for responsibility in POLICY_REQUIRED_RESPONSIBILITIES[key]
        }
        owners = {}
        for responsibility in sorted(responsibilities, key=lambda item: item.value):
            owners[responsibility.value] = post(
                "admin",
                f"{ids['org_path']}/service-owners",
                expected=201,
                json={
                    "user_uuid": ids["reviewer"]["user_uuid"],
                    "responsibility": responsibility.value,
                    "evidence_reference": "SYNTHETIC/local-vlab-owner",
                    "effective_from": roles_effective_from.isoformat(),
                    "effective_to": roles_effective_to.isoformat(),
                },
            )

        scope(owners["education"]["owner_uuid"], "content_release_review", "course", ids["course_uuid"])
        scope(owners["family_services"]["owner_uuid"], "learner_eligibility_review", "function", "learner_intake")

        now = datetime.now(UTC)
        policy_specs = {
            "D-01": {
                "markets": ["LK"],
                "learner_age_limits": {"fixture": "adult synthetic learner only"},
                "independent_participation": {"verification": "synthetic human-review fixture"},
                "independent_purchasing": {"fixture": "synthetic only"},
            },
            "D-02": {
                "subjects": ["Physics"],
                "learner_levels": ["Grade 12 (synthetic fixture)"],
                "languages": ["en"],
                "curriculum_claims": {"status": "not educator validated"},
            },
            "D-03": {
                "guardian_authority": {"verification": "human decision required"},
                "adult_transition": {"review": "human decision required"},
            },
            "D-08": {
                "pass_marks": {"fixture": "server verified numeric task; file task educator marked"},
                "marking_method": {"fixture": "mixed synthetic assessment"},
                "assistance": {"ai_assistance": "disabled"},
                "attempts": {"max_attempts": 1},
                "deadlines": {"fixture": "current assignment far future; negative fixture expired"},
                "appeals": {"fixture": "not exercised"},
            },
            "D-10": {
                "learner_eligibility": {"grades": "synthetic Grade 12 fixture only"},
                "supervision": {"status": "educator and safeguarding sign-off pending"},
                "materials": {"provider_calls": False},
                "usage_limits": {"fixture": "single local run"},
                "review": {"independent": "synthetic reviewer only"},
            },
            "D-11": {
                "community_audiences": {"status": "community features excluded from this pilot"},
                "adult_contact_boundaries": {"status": "no direct staff-to-learner contact in this fixture"},
                "moderation": {"status": "not applicable; no community content enabled"},
            },
            "D-12": {
                "information_uses": {"learning_terms": ["course_release"]},
                "consent": {
                    "notice_versions": {"learning_terms": "synthetic-v1"},
                    "notice_texts": {"learning_terms": "Synthetic local E2E consent notice; not production terms."},
                },
                "parent_report_scope": ["consent_management"],
            },
            "D-13": {
                "retention": {
                    "learner_evidence_retention_category": "synthetic_learner_evidence",
                    "content_evidence_retention_category": "synthetic_course_content_evidence",
                    "course_release_retention_category": "synthetic_course_release",
                },
                "record_access": {"fixture": "assigned reviewer only"},
                "correction": {"fixture": "not exercised"},
                "removal": {"fixture": "not exercised"},
            },
            "D-15": {
                "accessibility_standard": {"fixture": "manual review evidence attached; not a certification"},
                "supported_formats": {"max_bytes": 10_000_000, "media_types": ["application/pdf"]},
                "service_recovery": {"fallback": "draft"},
            },
            "D-16": {
                "content_rights": {"fixture": "synthetic evidence only; no legal clearance claimed"},
                "review_approvals": {"independent_reviewer": "synthetic account"},
                "certificate_presentation": {"none": True},
            },
        }
        policies = {key: policy(key, value) for key, value in policy_specs.items()}

        chapter = post(
            "teacher",
            f"{API_PREFIX}/chapters/",
            json={
                "name": "Synthetic Mechanics: Conservation of Momentum",
                "description": "Synthetic local exercise.",
                "org_id": fixture["org_id"],
                "course_id": course["id"],
            },
        )
        ids["chapter_id"] = chapter["id"]
        asg_activity = post(
            "teacher",
            f"{API_PREFIX}/activities/",
            params={"coursechapter_id": chapter["id"], "org_id": fixture["org_id"]},
            json={
                "name": "Synthetic practical assignment",
                "activity_type": "TYPE_ASSIGNMENT",
                "activity_sub_type": "SUBTYPE_ASSIGNMENT_ANY",
                "chapter_id": chapter["id"],
                "content": {},
                "published": True,
            },
        )
        expired_activity = post(
            "teacher",
            f"{API_PREFIX}/activities/",
            params={"coursechapter_id": chapter["id"], "org_id": fixture["org_id"]},
            json={
                "name": "Synthetic expired deadline check",
                "activity_type": "TYPE_ASSIGNMENT",
                "activity_sub_type": "SUBTYPE_ASSIGNMENT_ANY",
                "chapter_id": chapter["id"],
                "content": {},
                "published": True,
            },
        )
        lab_activity = post(
            "teacher",
            f"{API_PREFIX}/activities/",
            params={"coursechapter_id": chapter["id"], "org_id": fixture["org_id"]},
            json={
                "name": "Virtual Lab: Conservation of Linear Momentum",
                "activity_type": "TYPE_DYNAMIC",
                "activity_sub_type": "SUBTYPE_DYNAMIC_EMBED",
                "chapter_id": chapter["id"],
                "content": {},
                "published": True,
            },
        )
        expired_lab_activity = post(
            "teacher",
            f"{API_PREFIX}/activities/",
            params={"coursechapter_id": chapter["id"], "org_id": fixture["org_id"]},
            json={
                "name": "Virtual Lab: Expired Deadline Guard Fixture",
                "activity_type": "TYPE_DYNAMIC",
                "activity_sub_type": "SUBTYPE_DYNAMIC_EMBED",
                "chapter_id": chapter["id"],
                "content": {
                    "virtual_lab": {
                        "schema_version": "studytomy_virtual_lab_v1",
                        **pilot_build,
                        "units_system": "SI",
                        "linked_assignment_activity_uuid": expired_activity["activity_uuid"],
                        "submission_formats_note": "Synthetic deadline-guard fixture only.",
                    }
                },
                "published": True,
            },
        )
        ids["asg_activity_uuid"] = asg_activity["activity_uuid"]
        ids["expired_asg_activity_uuid"] = expired_activity["activity_uuid"]
        ids["lab_activity_uuid"] = lab_activity["activity_uuid"]
        ids["expired_lab_activity_uuid"] = expired_lab_activity["activity_uuid"]
        policies["D-09"] = policy("D-09", {
            "release_schedule": {
                "mode": "frozen",
                "ungated_lab_activities": {
                    ids["course_uuid"]: [ids["lab_activity_uuid"], ids["expired_lab_activity_uuid"]]
                },
            },
            "completion_prerequisites": {"required": False},
            "mastery_gate": {"separate": True},
        })
        call("reviewer", "put", f"{ids['org_path']}/courses/{ids['course_uuid']}/managed-draft", expected=204)

        authored = run_browser_phase(
            web_origin, "author", accounts["teacher"],
            courseUuid=ids["course_uuid"], activityUuid=ids["lab_activity_uuid"],
            buildVersion=pilot_build["build_version"], labHost=urlsplit(lab_origin).netloc,
        )
        descriptor = authored["content"]["virtual_lab"]
        if any(descriptor.get(key) != value for key, value in pilot_build.items()):
            raise RuntimeError("Browser saved an unexpected momentum lab descriptor")
        # Linked assessment data is fixture setup. The descriptor itself is saved in the browser.
        call("teacher", "put", f"{API_PREFIX}/activities/{ids['lab_activity_uuid']}", json={
            "content": {"virtual_lab": {
                **descriptor,
                "linked_assignment_activity_uuid": asg_activity["activity_uuid"],
                "submission_formats_note": "Synthetic report: export and include the SI CSV in PDF or ZIP.",
            }},
            "expected_current_version": authored["current_version"],
        })

        def assignment(activity: dict, title: str, due_date: str) -> tuple[dict, list[dict]]:
            row = post(
                "teacher",
                f"{API_PREFIX}/assignments/",
                json={
                    "title": title,
                    "description": "Synthetic local momentum exercise.",
                    "due_date": due_date,
                    "published": True,
                    "grading_type": "NUMERIC",
                    "anti_copy_paste": False,
                    "auto_grading": True,
                    "show_correct_answers": False,
                    "allow_retries": False,
                    "max_retries": 0,
                    "ungraded": False,
                    "solution": None,
                    "solution_reveal": "NEVER",
                    "org_id": fixture["org_id"],
                    "course_id": course["id"],
                    "chapter_id": chapter["id"],
                    "activity_id": activity["id"],
                },
            )
            return row, []

        current_assignment, _ = assignment(asg_activity, "Synthetic Momentum Practical Assessment", "2099-12-31")
        expired_assignment, _ = assignment(expired_activity, "Synthetic Expired Deadline Check", "2000-01-01")
        task_num = post(
            "teacher",
            f"{API_PREFIX}/assignments/{current_assignment['assignment_uuid']}/tasks",
            json={
                "title": "Synthetic final velocity",
                "description": "Calculate body 2 final velocity in m/s.",
                "hint": "Use conservation of momentum.",
                "reference_file": None,
                "assignment_type": "NUMBER_ANSWER",
                "max_grade_value": 2,
                "contents": {"prompt": "Find v2f in m/s", "unit": "m/s", "correct_value": 2.5, "tolerance": 0.05},
            },
        )
        task_file = post(
            "teacher",
            f"{API_PREFIX}/assignments/{current_assignment['assignment_uuid']}/tasks",
            json={
                "title": "Synthetic practical report",
                "description": "Upload a PDF or ZIP containing the exported CSV.",
                "hint": "Explain the conservation result.",
                "reference_file": None,
                "assignment_type": "FILE_SUBMISSION",
                "max_grade_value": 8,
                "contents": {},
            },
        )
        expired_task = post(
            "teacher",
            f"{API_PREFIX}/assignments/{expired_assignment['assignment_uuid']}/tasks",
            json={
                "title": "Synthetic expired numeric task",
                "description": "Deadline guard fixture.",
                "hint": "No answer is submitted.",
                "reference_file": None,
                "assignment_type": "NUMBER_ANSWER",
                "max_grade_value": 1,
                "contents": {"prompt": "Deadline guard", "unit": "m/s", "correct_value": 2.5, "tolerance": 0.05},
            },
        )
        for assignment_row in (current_assignment, expired_assignment):
            call("teacher", "put", f"{API_PREFIX}/assignments/{assignment_row['assignment_uuid']}", json={"published": True})

        # The course review is separate from authoring and carries synthetic evidence receipts.
        cp = f"{ids['org_path']}/courses/{ids['course_uuid']}"
        candidate = call("reviewer", "get", cp + "/content-review-candidate")
        fake = f"ev_{uuid4()}"
        self_review = clients["teacher"].post(
            cp + "/content-review-decisions",
            headers={"Idempotency-Key": str(uuid4())},
            json={
                "revision_sha256": candidate["revision_sha256"],
                "rights_evidence_uuid": fake,
                "accessibility_evidence_uuid": f"ev_{uuid4()}",
                "decision": "approved",
                "accessibility_confirmed": True,
                "assessment_confirmed": True,
            },
        )
        if self_review.status_code != 403:
            raise RuntimeError(f"Teacher self-review should be denied (403), got {self_review.status_code}")
        evidence = {}
        evidence_pdf = {}
        for purpose, text in (
            ("content_rights", "Synthetic rights-review fixture only; not a legal clearance."),
            ("content_accessibility", "Synthetic accessibility-review fixture only; not a certification."),
        ):
            evidence_pdf[purpose] = make_pdf([text])
            evidence[purpose] = post(
                "reviewer",
                cp + "/evidence-files",
                expected=201,
                data={"purpose": purpose, "revision_sha256": candidate["revision_sha256"]},
                files={"file": ("synthetic-evidence.pdf", evidence_pdf[purpose], "application/pdf")},
            )["evidence_uuid"]
        reviewed = run_browser_phase(
            web_origin, "review", accounts["reviewer"], courseUuid=ids["course_uuid"],
            rights=evidence["content_rights"], accessibility=evidence["content_accessibility"],
            oldDigest=candidate["revision_sha256"],
            teacher={key: accounts["teacher"][key] for key in ("email", "password")},
            activityUuid=ids["lab_activity_uuid"], buildVersion=pilot_build["build_version"],
            labHost=urlsplit(lab_origin).netloc,
            pdf={purpose: base64.b64encode(data).decode() for purpose, data in evidence_pdf.items()},
        )
        review, release = reviewed["review"], reviewed["release"]
        evidence = reviewed["currentEvidence"]
        review_route = cp + f"/content-review-decisions/{review['uuid']}"
        if call("reviewer", "post", review_route + "/release", expected=201) != release:
            raise RuntimeError("Immutable release replay did not return the same receipt")
        if clients["learner"].post(review_route + "/release").status_code != 403:
            raise RuntimeError("Unauthorized release creation did not return 403")
        ids["release_uuid"] = release["uuid"]
        ids["review_uuid"] = review["uuid"]

        # Open the exact reviewed lab course to a synthetic independent adult learner via supported APIs.
        p = ids["org_path"]
        # Create learner profile through intake; the target identity is the seeded synthetic learner.
        profile = post(
            "reviewer",
            p + "/learners",
            expected=201,
            json={"user_uuid": ids["learner"]["user_uuid"], "time_zone": "Asia/Colombo"},
        )
        learner_uuid = profile["learner_uuid"]
        ids["learner_uuid"] = learner_uuid
        scope(owners["family_services"]["owner_uuid"], "learner_eligibility_review", "learner", learner_uuid)
        scope(owners["education"]["owner_uuid"], "learner_dossier_read", "learner", learner_uuid)
        learning = post(
            "reviewer",
            f"{p}/learners/{learner_uuid}/eligibility-decisions",
            expected=201,
            json={"eligibility_kind": "learning", "outcome": "eligible", "evidence_reference": "SYNTHETIC/learning"},
        )
        independent = post(
            "reviewer",
            f"{p}/learners/{learner_uuid}/eligibility-decisions",
            expected=201,
            json={
                "eligibility_kind": "independent_participation",
                "outcome": "eligible",
                "evidence_reference": "SYNTHETIC/independent-participation",
            },
        )
        profile = call("reviewer", "get", f"{p}/learners/{learner_uuid}")
        activated = post(
            "reviewer",
            f"{p}/learners/{learner_uuid}/activate",
            json={"expected_access_version": profile["access_version"], "reason_code": "SYNTHETIC_E2E"},
        )
        if activated["status"] != "active" or activated["control_mode"] != "independent":
            raise RuntimeError("Synthetic learner activation did not reach active independent status")

        # Draft an explicit zero-price synthetic offer; current D-05/D-17/D-18 decisions open one exact version.
        offer_uuid = str(uuid4())
        access = {"clock": "after_first_access", "duration_days": 30, "starts_at": None, "ends_at": None}
        price = {
            "currency": "LKR",
            "lines": [{"kind": "charge", "label": "Synthetic no-charge test allocation", "amount_minor": 0}],
            "total_minor": 0,
        }
        scope(owners["business"]["owner_uuid"], "offer_publication", "offer", offer_uuid)
        offer = post(
            "reviewer",
            p + "/offers/drafts",
            expected=201,
            json={
                "offer_uuid": offer_uuid,
                "offer_kind": "course",
                "terms": {
                    "title": "Synthetic no-charge Momentum Lab",
                    "summary": "Disposable local E2E entitlement only.",
                    "goals": ["Exercise the synthetic momentum lab journey."],
                    "audience": "Synthetic adult learner; no curriculum outcome asserted.",
                    "formats": ["web", "assignment"],
                    "resources": [{
                        "kind": "course",
                        "resource_uuid": ids["course_uuid"],
                        "release_uuid": ids["release_uuid"],
                        "release_sha256": release["revision_sha256"],
                    }],
                    "prerequisites": [],
                    "accessibility_notes": ["Synthetic review evidence only."],
                    "effort_minutes_min": 20,
                    "effort_minutes_max": 40,
                    "included_services": ["synthetic_local_lab"],
                    "capacity_basis": "one disposable local account",
                    "capacity_is_limited": False,
                    "access": access,
                    "assessment": {"method": "mixed", "description": "Synthetic numeric and manual file tasks."},
                    "award": {"kind": "none"},
                    "price": price,
                    "customer_terms": "Synthetic local E2E only; no payment is collected.",
                },
            },
        )
        offer_version = offer["current_version_uuid"]
        offer_terms = offer["terms"]
        # Store the service-normalized resource snapshot and exact price/access values for policy binding.
        normalized_terms = offer_terms
        policies["D-05"] = policy("D-05", {
            "prices": {offer_uuid: normalized_terms["price"]},
            "access_periods": {offer_uuid: normalized_terms["access"]},
            "learner_allocation": {
                "free_order_offer_versions": {offer_uuid: offer_version},
                "order_line_offer_versions": {offer_uuid: offer_version},
                "substantive_use_rule": "first_content_delivery",
                "correction_reason_codes": ["wrong_learner"],
            },
        })
        policies["D-07"] = policy("D-07", {})
        policies["D-17"] = policy("D-17", {
            "pilot_size": {"order_acceptance_offer_versions": {offer_uuid: offer_version}},
        })
        policies["D-18"] = policy("D-18", {
            "customer_terms": {offer_uuid: normalized_terms["customer_terms"]},
            "selected_market_requirements": {"fixture": "synthetic local only"},
        })
        policies["D-20"] = policy("D-20", {})

        purchase = post(
            "reviewer",
            f"{p}/learners/{learner_uuid}/eligibility-decisions",
            expected=201,
            json={"eligibility_kind": "purchase", "outcome": "eligible", "evidence_reference": "SYNTHETIC/purchase"},
        )

        notice = call("learner", "get", f"{p}/learners/me/consent-notices/learning_terms")
        consent = post(
            "learner",
            f"{p}/learners/me/consent-events",
            expected=201,
            json={
                "purpose": "learning_terms",
                "choice": "accept",
                "notice_version": notice["notice_version"],
                "scope_codes": ["course_release"],
            },
        )
        offer_list = call("learner", "get", f"{p}/checkout/offers")
        selected = next((item for item in offer_list if item["offer_uuid"] == offer_uuid), None)
        if selected is None or selected["offer_version_uuid"] != offer_version:
            raise RuntimeError("The exact approved synthetic offer version is not visible to checkout")
        selection = {
            "offer_uuid": offer_uuid,
            "offer_version_uuid": offer_version,
            "learner_uuids": [learner_uuid],
        }
        quote = call("learner", "post", f"{p}/checkout/quote", json=selection)
        confirmation = {
            **selection,
            "confirmation_sha256": quote["confirmation_sha256"],
            "acknowledged_overlap": False,
        }
        draft = post("learner", f"{p}/checkout/orders", expected=201, json=confirmation)
        call("learner", "post", f"{p}/checkout/orders/{draft['customer_ref']}/accept", json=confirmation)

        # Stranger is an ordinary synthetic User account in the same organization, with no managed grant.
        stranger_password = secrets.token_urlsafe(24) + "Aa1!"
        stranger_email = f"demo-stranger-{uuid4().hex[:12]}@example.com"
        stranger_username = "demo_stranger_" + uuid4().hex[:12]
        stranger = call(
            "admin",
            "post",
            f"{API_PREFIX}/users/{fixture['org_id']}",
            json={
                "email": stranger_email,
                "username": stranger_username,
                "password": stranger_password,
                "first_name": "Synthetic",
                "last_name": "Stranger",
            },
        )

        wrong_org_uuid = f"org_{uuid4()}"
        mismatched_release_uuid = f"course_release_{uuid4()}"
        state = {
            "orgUuid": ids["org_uuid"],
            "wrongOrgUuid": wrong_org_uuid,
            "courseUuid": ids["course_uuid"],
            "releaseUuid": ids["release_uuid"],
            "mismatchedReleaseUuid": mismatched_release_uuid,
            "releaseName": ids["release_name"],
            "labActivityUuid": ids["lab_activity_uuid"],
            "asgActivityUuid": ids["asg_activity_uuid"],
            "assignmentUuid": current_assignment["assignment_uuid"],
            "taskNumUuid": task_num["assignment_task_uuid"],
            "taskFileUuid": task_file["assignment_task_uuid"],
            "expiredReleaseUuid": ids["release_uuid"],
            "expiredAsgActivityUuid": ids["expired_asg_activity_uuid"],
            "expiredTaskNumUuid": expired_task["assignment_task_uuid"],
            "labBaseUrl": lab_origin,
            "teacher": {"email": accounts["teacher"]["email"], "password": accounts["teacher"]["password"]},
            "reviewer": {"email": accounts["reviewer"]["email"], "password": accounts["reviewer"]["password"]},
            "learner": {"email": accounts["learner"]["email"], "password": accounts["learner"]["password"]},
            "stranger": {"email": stranger_email, "password": stranger_password},
            "learnerStorageState": "",
            "consentEventUuid": consent["event_uuid"],
            "authoringReceipts": {
                "managedDraftStatus": 204,
                "browserAuthoredVersion": authored["current_version"],
                "staleReviewStatus": reviewed["staleStatus"],
                "selfReviewDeniedStatus": self_review.status_code,
                "rightsEvidenceUuid": evidence["content_rights"],
                "accessibilityEvidenceUuid": evidence["content_accessibility"],
                "reviewUuid": review["uuid"],
                "revisionSha256": review["revision_sha256"],
                "preflightStatus": 200,
                "releaseStatus": 201,
            },
            "syntheticOnly": True,
            "syntheticPolicyApprovals": len(POLICY_REQUIRED_DECISION_AREAS),
            "syntheticLearnerDecisions": [learning["decision_uuid"], independent["decision_uuid"], purchase["decision_uuid"]],
            "syntheticCourseScopeDenialUuid": other["course_uuid"],
            "syntheticUserUuid": stranger["user_uuid"],
        }
        # Scenario JSON is private because it contains disposable passwords. The browser state is added after
        # normal Chromium form login by the local runner, then the manifest is rewritten owner-only.
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, indent=2)
        print("PASS synthetic local scenario: separate author/reviewer, governed release, activated learner, consent and exact free-order grant")
        print("PASS fixture approvals are explicitly synthetic; educator, safeguarding, legal rights and curriculum sign-off remain pending")
        print(f"Scenario manifest saved privately for database {db_url.database}; passwords were not printed.")
    finally:
        for client in clients.values():
            client.close()


if __name__ == "__main__":
    if "--synthetic-only" not in sys.argv:
        raise SystemExit("Refusing to run without --synthetic-only")
    main()
