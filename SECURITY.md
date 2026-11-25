# Security Policy

Studytomy Portal is a public source repository under active development. Keep credentials, student records, private documents, and vulnerability details out of public issues and discussions.

## Reporting a vulnerability

Submit a [private vulnerability report on GitHub](https://github.com/nirzaf/studytomyportal/security/advisories/new). Describe the affected version or commit, impact, and reproduction steps using synthetic data. If you cannot access the form, contact a maintainer privately to arrange another channel before sharing details.

## Automated checks

The [Repository security checks workflow](.github/workflows/repository-security.yml) uses Gitleaks to scan reachable Git history and the tracked source snapshot separately. OSV-Scanner checks the committed dependency lockfiles. Scanner images and the checkout action are pinned. The workflow uses read-only repository access, redacts secret findings, and keeps scanner reports out of Actions artifacts. GitHub Dependabot alerts, Dependabot security updates, Secret Scanning, and Secret Scanning Push Protection are enabled.

The `main` ruleset requires **Secret history and source scan** and **Dependency vulnerability scan** from GitHub Actions. The secret check fails on any source finding, any unresolved history finding, or any scan error. Maintainers review genuine findings through the private incident process.

On 23 November 2026, the repository owner confirmed rotation of one exposed historical signing key. The history scan still detects that occurrence and records a warning. The check matches only its exact fingerprint, without recording the credential value in the disposition. The legacy commits remain in history.

## Scope and support

No formal release-support window is declared. Report issues against the current `main` source using the private process above. Maintainers review scanner results alongside the reported impact and available evidence.
