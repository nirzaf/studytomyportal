# Studytomy documentation: start here

Read only the documents needed for the task. Source, mounted routes and tests are authoritative; plans and historical test counts are not current implementation proof.

| Task | Start with | Then inspect |
|---|---|---|
| Any code change | Root `AGENTS.md` and applicable scoped instructions | Current HEAD, worktree/index, actual callers and tests |
| Architecture or ownership | [Agent architecture guide](AGENT-ARCHITECTURE-GUIDE.md) | Referenced models, services and contracts |
| Non-technical client walkthrough | [Client process overview](architecture/client-process-overview.html) | [Implementation status](IMPLEMENTATION-STATUS.md) and [Sri Lankan decision baseline](SRI-LANKA-DECISION-BASELINE.md) |
| Current vs. planned status | [Implementation status](IMPLEMENTATION-STATUS.md) | [Dated documentation review](DOCUMENTATION-REVIEW.md), [architecture inventory](ARCHITECTURE-IMPLEMENTATION-INVENTORY.md), and current source |
| Task or acceptance-checklist state | [Atomic implementation checklist](../../STUDYTOMY-FAMILY-LEARNING-ATOMIC-IMPLEMENTATION-SPEC.md) | [Implementation status](IMPLEMENTATION-STATUS.md) for per-task evidence; the checklist is not acceptance proof |
| Frozen baseline and decision gates | [Baseline record](BASELINE.md) (see its §7 dated addendum) | Current source, migrations and tests |
| Local setup and verification | [Root README](../../README.md), [local-only policy](../../LOCAL-ONLY.md) | [Local verification log](LOCAL-VERIFICATION.md), [end-to-end testing runbook](../LOCAL_END_TO_END_TESTING.md), component manifests and test scripts |
| Learning, mastery, assessments | [Adaptive learning implementation](ALE-IMPLEMENTATION.md), [adaptive pilot runbook](ALE-LOCAL-PILOT.md) | `apps/api/src/routers/studytomy/adaptive.py`, `services/studytomy/adaptive_*`, native assignment callers and focused tests |
| Content review or release | [Content publication paths](CONTENT-PUBLICATION-PATHS.md) | `services/studytomy/publication.py`, `releases.py`, course/activity access paths |
| Curriculum and syllabus scope | [Sri Lankan decision baseline](SRI-LANKA-DECISION-BASELINE.md) | Approved sources, rights records and the open `D-01`/`D-02` decisions |
| Interactive lesson UI | [Generative UI implementation](GENUI-IMPLEMENTATION.md) | AI learning surface contracts and lesson components |
| Grounded authoring or retrieval | [MCP draft authoring](MCP-DRAFT-AUTHORING.md), [grounded retrieval evaluation](GROUNDED-RETRIEVAL-EVALUATION.md) | `services/studytomy/grounding.py`, RAG authorization tests |
| PDF syllabus sources or Explore Topics | [Syllabus Knowledge Library](SYLLABUS-KNOWLEDGE-LIBRARY.md) | `services/studytomy/syllabus_library.py`, `/studytomy` source and Explore routes, source review and consent gates |
| Virtual laboratory work | [Momentum pilot specification](VIRTUAL-LAB-MOMENTUM-PILOT-SPEC.md) | `apps/labs/momentum-pilot`, `services/studytomy/virtual_labs.py`, managed lab specs |
| Support, safeguarding or privacy casework | [Support configuration](SUPPORT-CONFIGURATION.md), [safeguarding configuration](SAFEGUARDING-CONFIGURATION.md) | `services/studytomy/support.py`, `cases.py`, `safeguarding_retention.py` and their policy keys |
| Access-path or entitlement work | [Managed-course access inventory](../../STUDYTOMY-ACCESS-PATH-INVENTORY.md) | `services/studytomy/access_policy.py`, `entitlements.py`, route-level tests |
| Migration, rollback or database version | [Migration and rollback notes](MIGRATION-AND-ROLLBACK.md), [PostgreSQL 19 upgrade note](POSTGRESQL-19-UPGRADE.md) | Actual Alembic graph and schema/bootstrap code; no implied production authorization |
| Operations or incident response | [Operations record](OPERATIONS.md) | Configured schedulers, workers, retention jobs and their enablement flags |
| Upstream security or feature review | [LearnHouse monitoring runbook](UPSTREAM-MONITORING.md) | Stable upstream source at the named commit and this fork's equivalent path. The [PR #1192 review](UPSTREAM-PR-1192-SECURITY-REVIEW.md) is a dated example. |
| Learner login design | [Credential ADR](ADR-LEARNER-CREDENTIALS.md) | Existing identity/auth contracts and explicit decisions |
| SDK or embedded renderer | `packages/sdk/README.md`, `packages/react/README.md`, `packages/vue/README.md` | Local manifests/generated contracts; published upstream packages are not unpublished fork code. Build each package before running its tests |
| Native CLI or E2E | [CLI guide](../../apps/cli/README.md), [E2E guide](../../apps/e2e/README.md) | Verify whether tests use the current local build or a published upstream image |

The [PostgreSQL 19 runbook](POSTGRESQL-19-UPGRADE.md) describes the current CLI-managed database image and retains dated migration rehearsals. For hosted build checks, security scans, and required checks on `main`, see the [local development guide](../../LOCAL-ONLY.md) and [security policy](../../SECURITY.md).

For product requirements and testable delivery order across authoring, learner experience and staff history, start with the [topic learning loop](TOPIC-LEARNING-LOOP.md).

## Working conventions

- Preserve LearnHouse's Next.js/React, FastAPI, PostgreSQL/pgvector and existing authentication/RBAC. No separate LMS, backend or identity store.
- Reuse domain services from HTTP/MCP; enforce current course/lesson and learner/guardian scope server-side. AI output does not grant permissions or final grades.
- Distinguish source-present, tested locally, committed/pushed and deployed. Record exact scope and fixture limitations for each test run; do not sum unrelated runs.
- Keep history, specifications and approval decisions intact. Update the current summary and add dated evidence rather than rewriting a baseline as though it had always contained the new code.
- Check for concurrent edits before writing. Stage only the task's reviewed files. No deployment, production migration or CI/CD is authorized by documentation updates.
