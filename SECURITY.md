# Security Policy

Studytomy Portal is a public source repository under active development. Keep credentials, student records, private documents, and vulnerability details out of public issues and discussions.

## Reporting a vulnerability

Submit a [private vulnerability report on GitHub](https://github.com/nirzaf/studytomyportal/security/advisories/new). Describe the affected version or commit, impact, and reproduction steps using synthetic data. If you cannot access the form, contact a maintainer privately to arrange another channel before sharing details.

## Automated checks

The [Repository security checks workflow](.github/workflows/repository-security.yml) uses Gitleaks to scan reachable Git history and the tracked source snapshot separately. OSV-Scanner checks the committed dependency lockfiles. Scanner images and the checkout action are pinned. The workflow uses read-only repository access, redacts secret findings, and keeps scanner reports out of Actions artifacts. GitHub Dependabot alerts, Dependabot security updates, Secret Scanning, and Secret Scanning Push Protection are enabled.

The `main` ruleset requires **Secret history and source scan** and **Dependency vulnerability scan** from GitHub Actions. The secret check fails if either scan finds a secret or cannot complete. A clean tracked source scan can coexist with an unresolved historical finding. Maintainers must resolve genuine findings through the private incident process.

## Scope and support

No formal release-support window is declared. Report issues against the current `main` source using the private process above. Maintainers review scanner results alongside the reported impact and available evidence.
