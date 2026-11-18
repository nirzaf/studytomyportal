# Local-only development

Studytomy Portal is currently developed and tested locally. This repository does not configure project deployment or continuous integration/continuous delivery. A local build or test does not certify a live service. Normal source pushes may be separately authorized; a push is not permission to run a deployment command or evidence that a deployment did or did not occur.

## Local verification

Install dependencies with the frozen lockfiles, then run the existing component scripts from their component directories. The root README lists the web, API, collaboration, CLI, browser, and SDK commands. The `scripts/lockfiles.sh --check` command verifies lockfile consistency locally. Run applicable tests before committing; no hosted test workflow is configured here.

See the [AUD-18 local verification record](docs/studytomy/LOCAL-VERIFICATION.md) for a dated command-by-command evidence snapshot and explicit skipped/unverified checks.

Dockerfiles and CLI-generated Compose files remain available for local builds and local service dependencies. The LearnHouse CLI is product tooling; its non-interactive setup mode and user-facing self-hosting commands do not configure deployment or CI/CD for this repository.

## External integrations

Provider-settings observations below are historical, not rechecked at every commit. Repository configuration alone cannot establish external provider state.

Removing repository files does not uninstall GitHub Apps, remove hosting projects, revoke credentials, or disconnect external integrations. GitHub repository settings showed no configured webhooks and listed a Vercel GitHub App installation. The app's repository scope could not be inspected because GitHub required step-up authentication. The settings also list other hosting/build-provider apps; their repository scope was not verified. No external settings or projects were changed.

Treat external auto-build or deployment behavior as **unverified** until the account owner checks the provider settings. Repository configuration cannot establish whether an external provider will build or deploy after a push, and this repository's local-only status does not prove external providers are disconnected. No external provider settings or projects were changed.
