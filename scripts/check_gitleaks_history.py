"""Resolve owner-reviewed findings from a complete, redacted history scan."""

import hashlib
import json
import sys
from pathlib import Path


# Digest of one immutable Gitleaks fingerprint, excluding the credential value.
# The repository owner confirmed rotation of this historical signing key on 2026-11-23.
REVOKED_FINGERPRINT = "eced910c92fb423b45fb5aa8a83caa5424e8fc6d9a244197de24e875c758a52e"


def main(report_path: str) -> int:
    try:
        findings = json.loads(Path(report_path).read_text(encoding="utf-8"))
        if not isinstance(findings, list) or not findings:
            raise ValueError("A nonempty list of findings is required")
        fingerprints = [finding["Fingerprint"] for finding in findings]
        if any(not isinstance(value, str) or not value for value in fingerprints):
            raise ValueError("Every finding needs an exact fingerprint")
    except (OSError, ValueError, KeyError, TypeError):
        print("::error::History scan reported findings but its redacted report was missing or invalid.")
        return 1

    if len(findings) == 1 and hashlib.sha256(fingerprints[0].encode()).hexdigest() == REVOKED_FINGERPRINT:
        print("::warning::History scan found one historical signing key. The owner confirmed rotation. No unresolved history findings.")
        return 0

    print("::error::History scan detected findings outside the single owner-reviewed historical disposition.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
