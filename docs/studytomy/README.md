# Studytomy documentation: start here

Read only the documents needed for the task. Source, mounted routes and tests are authoritative; plans and historical test counts are not current implementation proof.

| Task | Start with | Then inspect |
|---|---|---|
| Any code change | Root `AGENTS.md` and applicable scoped instructions | Current HEAD, worktree/index, actual callers and tests |
| Architecture or ownership | [Agent architecture guide](AGENT-ARCHITECTURE-GUIDE.md) | Referenced models, services and contracts |
| Current-vs-planned status | [Documentation review](DOCUMENTATION-REVIEW.md) | Current local `IMPLEMENTATION-STATUS.md`, then source; do not read every historical checkpoint |
| Local setup and verification | [Root README](../../README.md), [local-only policy](../../LOCAL-ONLY.md) | Component manifests and test scripts |
| Learning, mastery, assessments | Local `ALE-IMPLEMENTATION.md` and `ALE-LOCAL-PILOT.md`, when present | `apps/api/src/routers/studytomy/adaptive.py`, `services/studytomy/adaptive_*`, native assignment callers and focused tests |
| Content review or release | Local `CONTENT-PUBLICATION-PATHS.md`, when present | `services/studytomy/publication.py`, `releases.py`, course/activity access paths |
| Interactive lesson UI | Local `GENUI-IMPLEMENTATION.md`, when present | AI learning surface contracts and lesson components |
| Migration or rollback | [Migration and rollback notes](MIGRATION-AND-ROLLBACK.md) | Actual Alembic graph and schema/bootstrap code; no implied production authorization |
| Learner login design | [Credential ADR](ADR-LEARNER-CREDENTIALS.md) | Existing identity/auth contracts and explicit decisions |
| SDK or embedded renderer | `packages/sdk/README.md`, `packages/react/README.md`, `packages/vue/README.md` | Local manifests/generated contracts; published upstream packages are not unpublished fork code |
| Native CLI or E2E | [CLI guide](../../apps/cli/README.md), [E2E guide](../../apps/e2e/README.md) | Verify whether tests use the current local build or a published upstream image |

Some evidence documents and application changes remain unpublished in the development worktree. Plain-code filenames above intentionally avoid broken GitHub links until those documents are published. Do not manufacture missing files or treat absence from the remote as absence from local work.

## Working conventions

- Preserve LearnHouse's Next.js/React, FastAPI, PostgreSQL/pgvector and existing authentication/RBAC. No separate LMS, backend or identity store.
- Reuse domain services from HTTP/MCP; enforce current course/lesson and learner/guardian scope server-side. AI output does not grant permissions or final grades.
- Distinguish source-present, tested locally, committed/pushed and deployed. Record exact scope and fixture limitations for each test run; do not sum unrelated runs.
- Keep history, specifications and approval decisions intact. Update the current summary and add dated evidence rather than rewriting a baseline as though it had always contained the new code.
- Check for concurrent edits before writing. Stage only the task's reviewed files. No deployment, production migration or CI/CD is authorized by documentation updates.
