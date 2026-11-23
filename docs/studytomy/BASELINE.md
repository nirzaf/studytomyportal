# Studytomy Portal — Implementation Baseline (`TASK-001`)

**Status:** Baseline frozen for Wave A (`TASK-001`). Business decisions `D-01`–`D-20` and production launch/deployment approvals remain **UNAPPROVED** (`APPROVAL PENDING`).

---

## 1. Repository, Branch, and Revision Metadata

| Field | Verified Value |
|---|---|
| **Repository** | `https://github.com/nirzaf/studytomyportal.git` |
| **Target application domain** | `lms.studytomy.com` (independent of `studytomy.com` / `nirzaf/studytomy-react`) |
| **Checked-out branch** | `main` (`origin/main`) |
| **Inspected commit SHA** | `0d3b439436c69553bf32b69067b06dad13011df5` (`ci: remove GitHub Actions workflow files`) |
| **Adopted upstream baseline SHA** | `144c843b` (`Merge pull request #1243 from learnhouse/feat/desktop-api-gaps`) |
| **Alembic migration head** | `f5a8b3d0c2e4 (head)` |
| **Authoritative specification** | `STUDYTOMY-FAMILY-LEARNING-ATOMIC-IMPLEMENTATION-SPEC.md` (SHA-256 `5afe84a7fb3ea1300d0727676ca55a34610304c25c0c8634f4df44af8cf358ea`) |
| **Supplied summary source (D1)** | [D1 — Students and parents summary](https://docs.google.com/document/d/1ICH0jgg57TvirvMPAYgNcu51gdZOzKpF1gOp4HRGzZs) (export SHA-256 `3fb9a1497bd85d807952d25b93b65d3fabfbef03aa5ffe51a7ed94f30cf1b78f`) |
| **Detailed requirements source (D2)** | [D2 — Detailed business requirements, v1.0](https://docs.google.com/document/d/1yNq10yXgs0AO_Da4126RB_pE0ZFRbh99zS4ZkfMOxcE) (export SHA-256 `54897e309257cab24bf8b09bc2ba7f4a3182898720ee4802554ddf8567545d71`) |
| **Scoped `AGENTS.md` files** | `AGENTS.md` (root), `apps/web/AGENTS.md` (Next.js rules) |
| **Baseline check date** | 2026-11-15 |

---

## 2. Architecture and Scope Boundaries

- **Preserved stack:** Next.js `^16.4.5` / React `19.3.1` / TypeScript `6.0.3` / Bun `1.4.4` frontend (`apps/web`); Python `3.14.7` / FastAPI `0.162.0` / SQLModel `0.0.44` / Alembic `1.20.1` / PostgreSQL (`asyncpg` + `pgvector`) / Redis `8.1.0` backend (`apps/api`).
- **Public website separation (`BR-GOV-01`, `AGENTS.md` §5):** `nirzaf/studytomy-react` (`studytomy.com` / `www.studytomy.com`) is completely untouched. No Supabase dependencies, shared sessions, or cross-app database couplings are introduced.
- **OSS / Enterprise boundary (`AGENTS.md` §2):** `LEARNHOUSE_DISABLE_EE=1` in tests; Community Edition paid-access hooks (`src/core/ee_hooks.py`) default to free/no-op in OSS mode. Studytomy family-learning and commerce controls **are planned** (not yet implemented at baseline) as independent, additive modules under `src/db/studytomy/`, `src/services/studytomy/`, `src/routers/studytomy/`, and `src/security/studytomy_access.py` without unlocking private Enterprise modules or altering license checks.
- **Explicit pilot boundary (`D-17`, `BR-GOV-01`, `BR-GOV-06`):**
  - **Course-only staffed pilot (Launch scope):** Eligible families, reviewed course content, verified guardian/consent processes, confirmed paid or approved-free allocations, supported assessments with immutable attempt/result history, recoverable work, support/safeguarding/privacy casework, and any explicitly sold staff-led reporting.
  - **Conditional Launch capabilities:** Membership renewals (`TASK-031`–`TASK-033`), promotions (`TASK-035`), assessed-pass prerequisites (`TASK-039`), interactive/board boundaries (`TASK-046`), AI lesson guidance (`TASK-047`–`TASK-050`), staff-led progress reports (`TASK-051`–`TASK-052`), and course communities (`TASK-055`) remain gated off until their specific decisions, readiness checks, and owners are approved.
  - **Later-only capabilities (deferred):** `BR-AI-07` course-wide tutoring (`TASK-071`), `BR-PAR-06` dedicated parent dashboard / automatic updates (`TASK-072`), and `BR-CAL-05` personalised study reminders (`TASK-073`) are excluded from launch.

---

## 3. Immutable Code Seams Inspected at `0d3b439436c69553bf32b69067b06dad13011df5`

| Ref | Path & Lines | Baseline Behavior Verified |
|---|---|---|
| `[E01]` | `AGENTS.md:1–191` | Engineering rules, public website separation, AGPL-3.0 boundary, and approval gates. |
| `[E02]` | `apps/web/package.json:1–16` | `lint` runs `eslint . \|\| true` (masks exit code); `lint:strict` runs `eslint`; `test` runs `bun test --isolate ./tests`. |
| `[E03]`–`[E04]` | `apps/api/pyproject.toml:1–92` | Python `>=3.14.7,<3.14.8`; pytest `asyncio_mode = "auto"`; coverage omits `src/services/ai/*`, `src/routers/ai/*`, `src/routers/code_execution.py`, `src/routers/stream.py`. |
| `[E05]` | `apps/api/src/db/users.py:15–125` | `User` requires `email`; `PublicUser` and `UserReadAuthor` expose profile fields by default without minor-specific redaction. |
| `[E06]` | `apps/api/src/db/roles.py:35–100` | `Rights` defines resource permission buckets (`courses`, `users`, `usergroups`, `folders`, `media`, `organizations`, `coursechapters`, `activities`, `assignments`, `roles`, `dashboard`, `communities`, `discussions`, `podcasts`, `boards`, `playgrounds`). |
| `[E07]`–`[E08]` | `apps/api/src/core/deployment_mode.py:24–68`, `src/core/ee_hooks.py:126–177` | OSS mode returns free access from `check_paid_activity_access` / `is_course_paid`. |
| `[E09]` | `apps/api/src/db/courses/courses.py:54–115` | `Course` stores `public`, `published`, `learnings`, `tags`, `drip`, `expiration`; no immutable commercial offer snapshot. |
| `[E10]`–`[E13]` | `apps/api/src/db/courses/assignments.py:36–103`, `src/services/courses/activities/assignments.py:2252–3745` | Task answers freeze once `AssignmentUserSubmission` is `SUBMITTED`/`GRADED`; `retry_assignment_submission` deletes `AssignmentTaskSubmission` rows in place, resets status to `PENDING` with `grade=0`, increments `attempt_number`, and revokes any course certificate. |
| `[E14]`–`[E15]` | `apps/api/src/services/audit/audit.py:1–94`, `src/db/user_audit_events.py:24–114` | Learner audit commits in a separate post-action session and logs/swallows failures; admin actions are skipped. |
| `[E16]`–`[E17]` | `apps/api/src/services/courses/activities/access.py:1–217`, `src/services/courses/drip.py:85–218` | Reader access gate checks publication, usergroups, paid hook, and drip date/completion rules. |
| `[E18]` | `apps/api/src/services/courses/certifications.py:420–690` | `revoke_user_certificate` deletes `CertificateUser`; `get_user_certificates_for_course` lets signed-in users read their own certificates even if the course is later unpublished; `are_course_assignments_passed` requires all course assignments to be `GRADED` and passed. |
| `[E19]` | `apps/api/src/services/courses/activities/versioning.py:17–79` | Activity versioning prunes snapshots beyond the latest 20 entries (`MAX_VERSIONS_PER_ACTIVITY = 20`). |
| `[E20]`–`[E21]` | `apps/api/src/services/webhooks/dispatch.py:1–180`, `src/services/courses/drip_notify.py:57–157` | In-process webhook retries and pre-send `drip_notification` claims. |
| `[E29]` | `apps/api/src/core/events/database.py:1–70` | `import_all_models()` walks `src/db` recursively; `TESTING=true` uses in-memory SQLite. |

---

## 4. Baseline Verification Commands and Pre-Modification Results

All pre-modification baseline checks below were executed against commit `0d3b439436c69553bf32b69067b06dad13011df5` on Windows (`Python 3.14.7`, `Bun 1.4.4`):

| Check | Command | Exit Status | Observed Pre-Modification Baseline Result |
|---|---|---|---|
| Git status & HEAD | `git status --short --branch` / `git rev-parse HEAD` | `0` | Clean tracked tree on `main...origin/main` at `0d3b439436c69553bf32b69067b06dad13011df5`. Note: GitHub Actions workflow files were removed in `0d3b4394`; they are intentionally not restored. |
| Alembic head check | `.\.venv\Scripts\python.exe -m alembic heads` (in `apps/api`) | `0` | Single head: `f5a8b3d0c2e4 (head)`. |
| Backend migration suite | `$env:TESTING="true"; .\.venv\Scripts\python.exe -m pytest src/tests/core/test_migrations.py -q` | `0` | `38 passed in 14.30s`. |
| Frontend unit test suite | `bun run test` (`bun test --isolate ./tests` in `apps/web`) | `1` | `494 pass, 19 fail, 17 errors` across 62 files (5.16s). Failures are preserved without suppression and separated below into **Environment Dependency Issues** vs. **Pre-existing Source Defects**. |
| Frontend TypeScript check | `bun ./node_modules/typescript/bin/tsc --noEmit` (in `apps/web`) | `2` | Fails in the pre-existing local checkout due to both **Environment Dependency Issues** (missing packages/types in `apps/web/node_modules`) and **Pre-existing Source Defects** (implicit-`any` and type diagnostics). |
| Frontend strict ESLint | `bun run lint:strict . --max-warnings=0` (in `apps/web`) | `1` | Fails with `bun: command not found: eslint` (**Environment Dependency Issue**: `eslint` binary is absent from `apps/web/node_modules/.bin`; note that `bun run lint` masks this via `\|\| true`). |

### 4.1 Frontend Pre-Modification Failure Classification (Environment vs. Source Defects)

- **Environment Dependency Issues (local `apps/web/node_modules` gaps):**
  - Missing `eslint` binary in `apps/web/node_modules/.bin` causes `bun run lint:strict . --max-warnings=0` to exit with code `1`.
  - Missing installed packages/binaries in `apps/web/node_modules` (`@tailwindcss/oxide-win32-x64-msvc`, `html-parse-stringify`, `happy-dom`, `next/server`, `next/link`, `next/navigation`, `next/headers`, `@phosphor-icons/react`, `server-only`) cause 17 module-resolution errors in `bun test --isolate ./tests` and `TS2307` module-not-found diagnostics in `tsc --noEmit`.
- **Pre-existing Source Defects:**
  - `apps/web/tests/site-search.test.mjs:108–109`: Uses POSIX-only `.replace(/\/page\.search\.ts$/, '')`, which fails 19 assertions on Windows when `metaFiles` paths contain backslashes (`@/app\orgs\[orgslug]\(withmenu)\page.search.ts`).
  - `apps/web/sentry.client.config.ts:28`, `apps/web/sentry.edge.config.ts:15`, `apps/web/sentry.server.config.ts:15`, and `apps/web/app/api/auth/[...path]/route.ts:340`: Pre-existing TypeScript `TS7006` (implicit `any` parameter) and `TS2551` diagnostics reported by `tsc --noEmit`.

---

## 5. Post-Change Verification (`TASK-001` Regression Contract Suite)

The `TASK-001` baseline regression contract suite in `apps/api/src/tests/studytomy/test_existing_contracts.py` (with `studytomy_postgres` marker registered in `apps/api/pyproject.toml`) was added and verified after the pre-modification baseline checks above.

### 5.1 Observed Execution History

| Run | Command | Exit Status | Observed Result & Remediation |
|---|---|---|---|
| Run 1 (Initial draft) | `$env:TESTING="true"; .\.venv\Scripts\python.exe -m pytest src/tests/studytomy -m "not studytomy_postgres" -q` | `1` | `3 failed, 4 passed` (9.78s). Causes: (1) login test patched `src.routers.auth.create_access_token` instead of `src.services.auth.session.create_access_token` used by `issue_session_or_challenge`; (2) `create_assignment_task` positional args swapped `assignment_uuid` and `AssignmentTaskCreate`; (3) `are_course_assignments_passed` test omitted a gradable `AssignmentTask` (`max_grade_value > 0`), causing vacuous pass (`True`). |
| Run 2 (Signature & task fixes) | `$env:TESTING="true"; .\.venv\Scripts\python.exe -m pytest src/tests/studytomy -m "not studytomy_postgres" -v` | `1` | `1 failed, 6 passed` (3.61s). Cause: `grade_assignment_submission` returns `{"message": ..., **computed}` (persisting `GRADED` on `AssignmentUserSubmission` in DB rather than returning a `submission_status` key in the response dict), and `record_audit_event` opened an uninitialized global `_async_session_factory` (`no such table: user_audit_event`). |
| Run 3 (Audit session binding & grade dict fix) | `$env:TESTING="true"; .\.venv\Scripts\python.exe -m pytest src/tests/studytomy -m "not studytomy_postgres" -v` | `1` | `1 failed, 6 passed` (4.68s). Cause: `retry_assignment_submission` returns a dict (`{"message", "attempt_number", "max_retries", "submission"}`), not an ORM instance. |
| Run 4 (Final verified run) | `$env:TESTING="true"; .\.venv\Scripts\python.exe -m pytest src/tests/studytomy -m "not studytomy_postgres" -v` | `0` | `7 passed in 3.28s` (independent verification: `7 passed in 3.71s`, exit `0`). |

### 5.2 Verified Contract Test Cases (`7 passed`)

1. `TestMigrationAndMetadataBaseline::test_alembic_head_revision_is_pinned` — Pins single Alembic head `f5a8b3d0c2e4`.
2. `TestMigrationAndMetadataBaseline::test_core_models_registered_in_metadata` — Verifies core tables (`user`, `organization`, `role`, `course`, `activity`, `assignment`, `assignmenttask`, `assignmentusersubmission`, `assignmenttasksubmission`, `certifications`, `certificateuser`, `user_audit_event`) are registered in `SQLModel.metadata`.
3. `TestExistingLoginContract::test_login_returns_user_and_tokens_shape` — Pins `POST /api/v1/auth/login` response shape (`user` without `password`, `tokens` with `access_token`, `refresh_token`, `expiry`).
4. `TestExistingCourseReadContract::test_course_read_returns_expected_contract` — Pins `GET /api/v1/courses/{course_uuid}` response contract.
5. `TestExistingAssignmentLifecycleContract::test_learner_save_submit_freeze_grade_and_retry_contract` — Pins assignment creation, task creation, learner draft save, final submission (`SUBMITTED`, `attempt_number=1`), post-submit answer freeze (`403`), instructor grading (`grade=100`, `passed=True`, persisted `AssignmentUserSubmission.submission_status == GRADED`, durable `ASSIGNMENT_SUBMITTED` & `ASSIGNMENT_GRADED` rows in `user_audit_event`), and live retry behavior (returns reset dict, resets persisted row to `PENDING` with `attempt_number=2` and `grade=0`, deletes `AssignmentTaskSubmission` rows, and revokes previously issued `CertificateUser`).
6. `TestExistingCertificateReadContract::test_own_certificate_readable_when_course_unpublished` — Pins `get_user_certificates_for_course` and `GET /api/v1/certifications/user/course/{course_uuid}` returning the learner's issued certificate after the course is unpublished and made private.
7. `TestExistingCertificateReadContract::test_assignments_passed_gate_blocks_ungraded_or_failed_work` — Pins `are_course_assignments_passed` with a gradable `AssignmentTask` (`max_grade_value=100`), returning `False` when unsubmitted, `False` when `SUBMITTED` (ungraded), `False` when `GRADED` below pass threshold (`20/100`), and `True` when `GRADED` at or above pass threshold (`80/100`).

### 5.3 Test Environment Limitations

- **Synthetic in-memory SQLite (`TESTING=true`):** Exercises FastAPI routers, service logic, and SQLModel metadata via `sqlite+aiosqlite://` with `JSONB`-to-`JSON` remapping (`apps/api/src/tests/conftest.py`). SQLite ignores `SELECT ... FOR UPDATE` row locks and does not execute PostgreSQL-specific DDL/triggers or Alembic migration scripts directly against a live PostgreSQL instance.
- **Isolated audit session binding:** `src/services/audit/audit.py::record_audit_event` opens its own session from `src.core.events.database._async_session_factory`. In SQLite unit tests, `_bind_audit_session_factory` binds `_async_session_factory` to the per-test SQLite `engine` so `user_audit_event` writes are tested against an initialized schema instead of an uninitialized default in-memory connection.
- **PostgreSQL-only tests (`studytomy_postgres` marker):** Tests requiring a live PostgreSQL instance remain separated under `-m studytomy_postgres` and are not claimed as executed when running `-m "not studytomy_postgres"`.

### 5.4 Rolling Migration Head After Wave 1 / Wave 3 Start

The §1 and §4 records above are the frozen pre-modification baseline. Each Studytomy batch extends the chain additively from it; the pinned assertion in `test_existing_contracts.py` is what makes the extension auditable.

| Chain | Revision | Adds |
|---|---|---|
| Baseline | `f5a8b3d0c2e4` | Pre-modification head (unchanged, §4). |
| M1 | `a1d9f4c8b732` | `st_policy_version`, `st_owner_assignment`, `st_staff_scope`, `st_business_event`, `st_idempotency_record`, `st_outbox_record`. |
| M2 | `b72a11c83e2f` | `st_learner_profile`, `st_eligibility_decision`. |
| M2 | `c91c2b73468f` | `st_evidence_file`. |
| M3 (started) | `d4a7c9e2b1f0` | `st_offer`, `st_offer_version`. Head as recorded at the Wave 3 start; see §7 for the current head. |

### 5.5 Wave 1 Focused Verification (executed)

| Command (in `apps/api`) | Exit | Observed Result |
|---|---|---|
| `$env:TESTING="true"; uv run pytest src/tests/studytomy/test_wave1_operations.py -m "not studytomy_postgres" -q` | `0` | `91 passed in 7.21s` — TASK-004 event/transaction rules, TASK-005 replay + stale-write + lock-order rules, TASK-006 outbox state machine + worker outcomes, TASK-007 evidence policy/constraint gates, TASK-008 readiness blockers. |
| `$env:TESTING="true"; uv run pytest src/tests/studytomy -m "not studytomy_postgres" -q` | `0` | `109 passed in 23.51s` (whole Studytomy suite, including the pinned head assertion at `d4a7c9e2b1f0`). |
| `$env:TESTING="true"; uv run pytest src/tests/routers/test_activities_router_endpoints.py src/tests/services/test_activity_orphan_paths.py src/tests/services/ai_tools/test_content.py -q` | `0` | `80 passed in 10.29s` — the legacy callers of `require_expected_version` (`src/services/courses/activities/activities.py`, `src/services/courses/activities/versioning.py`) still see their original `409` plain-string `STALE_WRITE` detail. |

**SQLite transaction-emulation limitation found while writing these tests.** When the first statement aiosqlite executes is `SAVEPOINT`, that savepoint *is* the database transaction, so releasing it commits. `claim_idempotency()` therefore leaves its `processing` row durable under SQLite after a caller rollback, while on PostgreSQL the outer `BEGIN` is always already open and the guard rolls back with the mutation as designed. `test_event_and_mutation_rollback_together` emits an explicit `BEGIN` to reproduce the PostgreSQL transaction state; that emulation is recorded here rather than presented as proof. The authoritative proof of the flush-only/rollback contract, of `SELECT ... FOR UPDATE`, of `skip_locked` claim single-winner behaviour, of FK `RESTRICT` and of the `st_business_event` immutability triggers belongs to `TASK-066` under `-m studytomy_postgres` and has not been run.

---

## 6. Open Business & Engineering Decision Gates

- **Business decisions `D-01` through `D-20`:** All 20 decisions remain `UNAPPROVED`. No default age threshold, market jurisdiction, price, currency, pass mark, retry rule, AI provider policy, or retention duration may be silently activated in production.
- **Child credential gate (`TASK-010`):** `User.email` is required by the current schema and auth router. Until an approved credential ADR and verification flow are established, learners without an approved individual login model cannot be activated via fabricated emails or shared adult sessions.
- **Approval (`AGENTS.md` §4 Stage D):** Merges and releases require explicit user authorization.

---

## 7. Addendum — baseline pointers re-verified 2026-11-20

`§1`, `§3` and `§4` above remain the frozen 2026-11-15 pre-modification record and were not rewritten. Four of their values no longer describe the live checkout, so they are restated here as dated changes.

| Frozen record | Value at `2026-11-15` | Verified at `ef6b730d` on 2026-11-20 |
|---|---|---|
| Alembic head (`§1`, `§5.4`) | `f5a8b3d0c2e4`, then `d4a7c9e2b1f0` at the Wave 3 start | `uv run alembic heads` reports one head, `b7c2e9a4d1f0`, across 137 revision files. Every revision cited anywhere in these documents (`f5a8b3d0c2e4`, `d4a7c9e2b1f0`, `b91d2f6a08c3`, `0cb1b9a418ed`, `8b53c0d1e6a4`, `4a9f72c6e801`, `9b7d4c2a1e6f`, `08fd9332b6ac`, `b043fde62c98`) is an ancestor of it, so the chain stayed additive and unbranched |
| `test_existing_contracts.py` head pin (`§5.2` item 1) | passing, pinning `f5a8b3d0c2e4` then the Wave 3 head | **failing**: the assertion still pins `b91d2f6a08c3` and receives `b7c2e9a4d1f0`. TASK-001's open "clean-tree refresh" item includes re-pinning this contract, which is the mechanism §5.4 relies on for auditability |
| Authoritative specification digest (`§1`) | SHA-256 `5afe84a7fb3ea1300d0727676ca55a34610304c25c0c8634f4df44af8cf358ea` | the file was condensed to a status checklist on 2026-11-20 (6,248 lines to 209; SHA-256 `738228947778b613105d6a7f923cd0b560ec091d5c1dcfe9d512e344d2343251`). Identifiers `TASK-0NN`, `AC-NN`, `D-NN`, `BR-*` and `V-NN` and their anchors were preserved; the removed prose, evidence register and traceability remain in git history at the frozen digest |
| Web `lint` script (`[E02]`) | `eslint . \|\| true`, masking the exit code | accurate at `0d3b4394`; the mask was removed in `029f1af1`. `lint` is now `eslint .`, so it fails on errors but not on warnings, and `lint:strict . --max-warnings=0` currently reports 0 errors with 975 warnings |

Post-change regression totals for this date, against a migrated disposable PostgreSQL 16 database, are in the [local verification log](LOCAL-VERIFICATION.md#documentation-refresh-verification-sweep-20-november-2026). None of the above changes the `D-01`–`D-20` approval state or authorizes a deployment.
