#!/usr/bin/env python3
"""Produce a GitHub job summary for a read-only LearnHouse stable-branch review."""

import argparse
import html
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from fnmatch import fnmatchcase


UPSTREAM = "https://github.com/learnhouse/learnhouse"
STUDYTOMY = "https://github.com/nirzaf/studytomyportal"
BASELINE = "144c843b4a7910a3340b73e17f0f576afec4cb87"
STABLE_REF = "refs/remotes/upstream/main"
RELEASE_REFS = "refs/upstream/learnhouse/tags/"
BASELINE_REF = "refs/upstream/learnhouse/baseline"
LIMIT = 25
REVIEW_AREAS = {
    "Authentication": (
        "apps/api/src/security/auth*", "apps/api/src/routers/auth*",
        "apps/api/src/services/auth*", "apps/api/src/services/users/*",
        "apps/web/*/auth/*", "apps/web/*auth*", "apps/web/middleware.*",
    ),
    "Authorization and managed content": (
        "apps/api/src/security/*", "apps/api/src/services/studytomy/access*",
        "apps/api/src/services/studytomy/publication*", "apps/api/src/routers/courses/*",
        "apps/api/src/services/courses/*",
    ),
    "PostgreSQL, Alembic, and persistence": ("apps/api/migrations/*", "apps/api/src/db/*"),
    "Dependencies, images, and build": (
        "*/bun.lock", "*/package.json", "*/uv.lock", "*/pyproject.toml",
        "Dockerfile", "*/Dockerfile", ".bun-version", ".github/workflows/*",
    ),
    "Frontend components and routes": ("apps/web/components/*", "apps/web/app/*"),
    "Adaptive learning and AI": (
        "apps/api/src/services/studytomy/adaptive*", "apps/api/src/routers/studytomy/adaptive*",
        "apps/api/src/services/ai/*", "apps/web/components/objects/activities/*",
    ),
    "Privacy, learners, and curriculum": (
        "apps/api/src/services/studytomy/*", "apps/api/src/routers/studytomy/*",
        "apps/api/src/db/studytomy/*", "apps/web/components/studytomy/*",
        "apps/api/src/*privacy*", "apps/api/src/*retention*", "apps/api/src/*consent*",
        "apps/web/*privacy*", "apps/web/*consent*",
    ),
    "Licensing and attribution": ("license*", "notice*", "*/license*", "*/notice*"),
}


def git(*args, optional=False):
    result = subprocess.run(["git", *args], capture_output=True, check=False)
    if result.returncode:
        if optional and result.returncode in (1, 128):
            return ""
        raise RuntimeError(f"Git {args[0]} failed (exit {result.returncode}).")
    return result.stdout.decode("utf-8", errors="backslashreplace")


def commit(ref, optional=False):
    return git("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}", optional=optional).strip()


def changed_paths(old, new):
    paths = git("diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--name-only", "-z", old, new, "--")
    return {path for path in paths.split("\0") if path}


def details(title, values, limit=LIMIT):
    if not values:
        print(f"\n**{title}:** None.")
        return
    print(f"\n<details><summary>{title} ({len(values)}, showing up to {limit})</summary>")
    formatted = "\n".join(json.dumps(value, ensure_ascii=True) for value in values[:limit])
    print("<pre>" + html.escape(formatted) + "</pre>")
    print("</details>")


def fetch_upstream():
    # Keep upstream objects outside pushable branches and tags. Preserve FETCH_HEAD
    # in developer checkouts so monitoring cannot alter a pending manual review.
    args = (
        "fetch", "--atomic", "--no-tags", "--no-prune", "--no-prune-tags",
        "--no-write-fetch-head", "--no-recurse-submodules", "--refmap=", UPSTREAM + ".git",
    )
    git(*args, f"+refs/heads/main:{STABLE_REF}", f"+refs/tags/*:{RELEASE_REFS}*")
    if not commit(BASELINE, optional=True):
        # A clean Studytomy history may lack the pinned adoption commit.
        git(*args, f"{BASELINE}:{BASELINE_REF}", optional=True)


def report(studytomy_ref):
    stable = commit(STABLE_REF)
    studytomy = commit(studytomy_ref)
    baseline = commit(BASELINE, optional=True)
    print("# LearnHouse stable upstream review\n")
    print(f"- Checked at {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    print(f"- LearnHouse stable `main` at [`{stable}`]({UPSTREAM}/commit/{stable})")
    print(f"- Studytomy comparison tree at [`{studytomy}`]({STUDYTOMY}/commit/{studytomy})")
    tags = git("for-each-ref", "--format=%(refname)", f"--merged={stable}", RELEASE_REFS).splitlines()
    names = (ref.removeprefix(RELEASE_REFS) for ref in tags)
    releases = [name for name in names if re.fullmatch(r"\d+\.\d+\.\d+", name)]
    if releases:
        latest = max(releases, key=lambda tag: tuple(map(int, tag.split("."))))
        print(f"- Highest application release tag reachable from stable: [`{latest}`]({UPSTREAM}/releases/tag/{latest})")
    if not baseline:
        raise RuntimeError("BASELINE UNKNOWN. Verify the recorded adoption commit before assessing compatibility.")

    stable_base = git("merge-base", baseline, stable, optional=True).strip()
    if not stable_base:
        raise RuntimeError("BASELINE UNKNOWN. No common Git ancestor joins the adoption commit and stable branch.")
    shared = git("merge-base", stable, studytomy, optional=True).strip()
    print(f"- Recorded adoption commit: `{baseline}`")
    print(f"- Shared ancestry between stable and Studytomy: `{shared}`" if shared else
          "- Stable and Studytomy have no Git merge base. Comparing trees is still possible. A standard merge is unavailable.")
    if stable_base != baseline:
        print(f"- Adoption commit diverges from stable. Stable path changes use their common ancestor `{stable_base}`.")
    incoming = int(git("rev-list", "--count", f"{baseline}..{stable}"))
    stable_paths = changed_paths(stable_base, stable)
    fork_paths = changed_paths(baseline, studytomy)
    overlap = stable_paths & fork_paths
    print(f"\nStable contains **{incoming} commits** absent from the recorded adoption history. "
          f"Stable changed **{len(stable_paths)} paths** since its comparison base. "
          f"Studytomy changed **{len(fork_paths)} paths** since adoption. "
          f"**{len(overlap)} paths** appear in both sets.")
    if stable_paths:
        print("\n| Review area | Stable paths | Overlapping paths |\n| --- | ---: | ---: |")
        area_overlaps = {}
        for label, patterns in REVIEW_AREAS.items():
            paths = {path for path in stable_paths if any(fnmatchcase(path.lower(), glob) for glob in patterns)}
            area_overlaps[label] = paths & overlap
            print(f"| {label} | {len(paths)} | {len(area_overlaps[label])} |")
        for label, paths in area_overlaps.items():
            if paths:
                details(f"{label} overlap", sorted(paths), limit=8)
    details("Overlapping paths for review", sorted(overlap))
    recent = git("log", f"--max-count={LIMIT}", "--format=%h %s", f"{baseline}..{stable}").splitlines()
    details("Most recent incoming commit subjects", recent)
    print("\n**Integration assessment: UNVERIFIED.** Path overlap flags review areas. "
          "It does not establish a merge conflict, compatibility, or release readiness. "
          "This monitor fetches Git objects and reads metadata. Application tests and migrations are separate approval gates.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="Fetch stable LearnHouse main and namespaced tags first.")
    parser.add_argument("--studytomy-ref", default="HEAD", help="Studytomy commit or ref to compare.")
    args = parser.parse_args()
    try:
        if args.fetch:
            fetch_upstream()
        report(args.studytomy_ref)
    except RuntimeError as exc:
        print(f"\n**Monitor incomplete.** {exc}")
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
