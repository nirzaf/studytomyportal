# Studytomy web application

LearnHouse-based Next.js/React frontend. Start with [scoped agent instructions](AGENTS.md), [root setup guide](../../README.md) and the [task-oriented documentation index](../../docs/studytomy/README.md). Do not duplicate architecture or implementation ledgers here.

## Local verification

From this directory, use the scripts declared in [package.json](package.json): `bun run dev`, `bun run build`, and `bun run test`. Install with the frozen lockfile. `bun run lint` runs ESLint across the app and fails on errors; warnings alone do not fail it. Use `bun run lint:strict -- <changed paths>` for meaningful scoped failure reporting, adding `--max-warnings=0` when a warning budget is required. A successful unit-test run is not browser acceptance.

Inspect API contracts and server authorization before UI changes. Keep Next.js, native authentication and assignment behavior; do not let client state or generated content determine final grades or grants. Follow the installed Next.js documentation required by AGENTS.md. Studytomy remains local-development-only; see [LOCAL-ONLY.md](../../LOCAL-ONLY.md).
