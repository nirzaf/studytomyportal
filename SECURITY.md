# Security Policy

Studytomy Portal is under local development. Do not include credentials, student records, private documents, or exploit details in public issues or discussions.

## Reporting a vulnerability

Use GitHub's **Report a vulnerability** feature to create a private security advisory when that feature is available for this repository:

<https://github.com/nirzaf/studytomyportal/security/advisories/new>

Private vulnerability reporting has not been confirmed enabled. If GitHub does not offer the private-reporting form, do not post the issue publicly. Ask a repository maintainer to establish a private reporting channel before sharing technical details. No monitored security email or response-time commitment is published here.

Before making the repository public, the owner must enable and verify private vulnerability reporting or publish a monitored security contact.

## Automated checks

The `Repository security checks` workflow scans reachable Git history and tracked source with Gitleaks and scans the checked-in dependency lockfiles with OSV-Scanner. Scanner images and the checkout action are pinned to immutable digests or commits. The workflow uses read-only repository access and no production credentials. It does not upload source files or scanner reports as artifacts.

Workflow failures do not block merges by themselves. Before merging changes or making the repository public, repository maintainers must require both **Secret history and source scan** and **Dependency vulnerability scan** in the `main` branch rules, verify that those rules are enforced, and resolve all findings.

## Scope and support

No formal release-support window is declared. Report issues against the current `main` source using the private process above. Security fixes and public release decisions require maintainer review; a green scanner is not a security certification.
