# Studytomy Portal E2E: UI acceptance tests

**Studytomy working-tree acceptance:** the default CLI runtime image is the existing upstream LearnHouse image, not a verified Studytomy build. Point `E2E_BASE_URL` and `E2E_API_URL` at the intended local build; `core/instance.ts` supports an existing instance and `E2E_SKIP_BOOT`. Record the tested revision. Local `features/studytomy/` test files are not evidence of a passing browser run. See [documentation review](../../docs/studytomy/DOCUMENTATION-REVIEW.md). This does not enable repository CI/CD.

Browser-driven acceptance tests for Studytomy Portal. Generic upstream tests may
boot a CLI-managed self-host; Studytomy tests run against the local web and API
build whose identity matches this checkout. A published LearnHouse image does
not contain unpublished changes from this repository.

It is organized as a **modular, multi-feature** harness: shared infrastructure
lives in `core/`, and each product area is a self-contained module under
`features/<area>/`. Assignments is the first module; adding more (courses,
communities, …) is a copy-the-pattern affair (see below).

## Architecture

```
apps/e2e/
  core/                      # generic, feature-agnostic infrastructure
    instance.ts              # env-driven config (base URL, admin creds, makeStudent)
    client.ts                # generic REST client: req, login, getOrg, createStudent
    auth.ts                  # uiLogin / uiLogout (drives the real login form)
    fixtures.ts              # the `test` export (suppresses first-run onboarding)
    sharedAuth.ts            # storageState paths + the reusable shared student
  features/
    assignments/             # one feature module
      api.ts                 # assignment seeding/grading API (builds on core/client)
      verify.ts              # API read-backs for assertions
      scenario.ts            # one-call scenario setup (course→assignment→student)
      pages/                 # page objects
        student.ts           # AssignmentPage (student activity view)
        teacher.ts           # TeacherSubmissionsPage / AssignmentEditorPage / GradingModal
      fixtures/              # static test assets (e.g. an upload sample)
      tests/                 # *.spec.ts for this feature
  global-setup.ts            # boot self-host + create shared admin/student sessions
  global-teardown.ts         # docker compose down -v on the generated compose file
  playwright.config.ts       # testDir: './features' (auto-discovers every module)
```

**Import rules:** specs and feature code import shared things from `core/` and
feature-local things relatively. Specs never reach into another feature.

## Adding a new feature module

1. `mkdir -p features/<area>/{pages,tests,fixtures}`.
2. Add `features/<area>/api.ts` for that feature's API seeding. `import { req, login, getOrg, createStudent } from '../../core/client'` and build feature calls on top.
3. Add page objects under `features/<area>/pages/` (import `BASE_URL` from `../../../core/instance`).
4. Optionally add a `scenario.ts` for one-call setup.
5. Write specs in `features/<area>/tests/*.spec.ts`:
   - `import { test, expect } from '../../../core/fixtures'` (onboarding-suppressed).
   - Reuse the shared sessions: `import { ADMIN_STATE, STUDENT_STATE } from '../../../core/sharedAuth'` then `test.use({ storageState: ADMIN_STATE })` (teacher) or `STUDENT_STATE` (student). This keeps the suite under the login rate limit.
6. `playwright.config.ts` (`testDir: './features'`) discovers the new specs automatically.

## Running locally

Requires Docker running, plus Node 22+ / bun (the default `E2E_CLI` is the published `learnhouse` image, which declares `engines.node >= 22` — `apps/cli/package.json:55-57`).

```bash
cd apps/e2e
bun install
bunx playwright install chromium
bun run test                              # boots a self-host, runs all features, tears it down
E2E_BASE_URL=http://localhost:8080 bun run test   # reuse a running instance (fast iteration)
bun run test:headed                       # watch it drive the UI
bun run report                            # open the HTML report after a run
```

### Studytomy tests against this fork

Studytomy specs never boot the default published `learnhouse@latest` install.
Run them against loopback web and API processes built from this checkout. In
each server shell, from the repository root, export the current commit and full
tracked/untracked source digest before starting the process:

```bash
eval "$(bun apps/e2e/scripts/studytomy-build-identity.ts)"
# Start the local API or web process from this shell using its existing command.
```

Then run the scoped browser suite from `apps/e2e` with `E2E_BASE_URL` and, when
the API is on a separate origin, `E2E_API_URL` set to those loopback services:

```bash
cd apps/e2e
E2E_BASE_URL=http://localhost:3000 E2E_API_URL=http://localhost:8000/api/v1 bun run test:studytomy
```

Global setup compares both served source identities to the current checkout,
checks that the API database migration heads match the API source, and logs the
commit, source digest, dirty-worktree flag and database heads. Missing or
mismatched identity stops the run before test setup. Explicit upstream-only
feature paths keep the published self-host harness. Run `bun run test:identity`
to verify the guard itself. The opt-in PostgreSQL fixtures remain separate:
they read only `STUDYTOMY_TEST_DATABASE_URL`, reject non-PostgreSQL/non-loopback
or non-`_test` databases, and report an explicit skip when the variable is
absent.

#### Explore Topics: mocked UI versus live backend

`19-explore-topics.spec.ts` is a deterministic browser-interaction test. It
intercepts syllabus, topic, PDF, and educator-access responses; it does not prove
FastAPI, PostgreSQL, private PDF storage, indexing, or authorization behavior.
Global setup still provisions a shared synthetic learner, so the test-database
guard applies to this mocked UI test as well as the live API test.

`20-explore-topics-live.spec.ts` drives the learner UI and calls the real
`/api/v1/studytomy` routes. It requires all of the following before it creates a
source record:

* Loopback `E2E_BASE_URL` and `E2E_API_URL` served by the same local checkout;
  global setup verifies build identities and database migration heads.
* The local API must use the exact same database endpoint as
  `STUDYTOMY_TEST_DATABASE_URL` passed to the E2E process. The URL must be
  query-free PostgreSQL on loopback and its database name must end in `_test`;
  host-routing query overrides are rejected. Before provisioning shared
  accounts, global setup compares the E2E endpoint digest with an authenticated,
  opt-in API runtime identity. The API derives its digest from its active
  connection URL and `current_database()`; credentials and the connection URL
  are never returned. Never point it at shared or production data.
* One pre-provisioned synthetic organization member with syllabus author scope
  and a separate educational reviewer with review scope, an education owner
  assignment, and active source-approval policy. The test checks those
  capabilities through the API and does not seed or bypass them.
* At least one published synthetic curriculum in the isolated organization.
  The shared learner is created by existing global setup.
* Private storage and a test-approved embedding provider available to the API.
  Upload runs the normal ingestion and indexing path. Do not enable a chargeable
  provider for this run unless that use is approved.

Set the four credentials only in a local test shell; never commit them:

```bash
export E2E_STUDYTOMY_SOURCE_AUTHOR_EMAIL='source-author@example.test'
export E2E_STUDYTOMY_SOURCE_AUTHOR_PASSWORD='...'
export E2E_STUDYTOMY_SOURCE_REVIEWER_EMAIL='source-reviewer@example.test'
export E2E_STUDYTOMY_SOURCE_REVIEWER_PASSWORD='...'
```

Run the categories separately and retain their results as separate evidence:

```bash
cd apps/e2e
bunx playwright test features/studytomy/tests/19-explore-topics.spec.ts
bunx playwright test features/studytomy/tests/20-explore-topics-live.spec.ts
```

The live test uses unique original synthetic content. It rejects or revokes its
source in `finally` after failures; if cleanup fails, it logs the HTTP status so
the isolated database can be inspected. A passing mocked test does not
substitute for this live acceptance test or the separate PostgreSQL service
tests.

Run a single module or spec:

```bash
bunx playwright test features/assignments         # one feature
bunx playwright test features/assignments/tests/06-manual-grading.spec.ts
```

### Configuration (env)

| Var | Default | Purpose |
| --- | --- | --- |
| `E2E_BASE_URL` | unset | Use an existing instance; skips boot + teardown. |
| `E2E_PORT` | `8080` | HTTP port for the self-host. |
| `E2E_ADMIN_EMAIL` / `E2E_ADMIN_PASSWORD` | `admin@school.dev` / `E2eTestAdmin!234` | Bootstrapped admin. |
| `E2E_ORG_SLUG` | `default` | Bootstrapped org slug. |
| `E2E_CLI` | `npx --yes learnhouse@latest` | CLI used to boot (override for a local build). |
| `E2E_SKIP_BOOT` | unset | `1` to skip booting (assumes instance already up). |
| `E2E_KEEP` | unset | `1` to keep the instance running after the suite. |
| `STUDYTOMY_TEST_RUNTIME_IDENTITY` | unset | Local-only opt-in for exact API/web build identity endpoints. |
| `STUDYTOMY_BUILD_SHA` | unset | Full Git commit SHA embedded in the local API and web processes. |
| `STUDYTOMY_BUILD_TREE_SHA256` | unset | Digest of tracked and non-ignored untracked checkout files. |
| `STUDYTOMY_TEST_DATABASE_URL` | unset | Loopback PostgreSQL test database ending in `_test`; for the live Explore Topics spec the API must use this same database. |
| `E2E_STUDYTOMY_SOURCE_AUTHOR_EMAIL/PASSWORD` | unset | Pre-provisioned synthetic source author credentials for the live Explore Topics spec. |
| `E2E_STUDYTOMY_SOURCE_REVIEWER_EMAIL/PASSWORD` | unset | Separate educational reviewer credentials with active approval policy assignments. |

### Reliability notes

- `core/auth.ts` saves shared admin + student browser sessions in `global-setup`;
  specs reuse them via `test.use({ storageState })`, and `core/client.login`
  caches tokens per email + retries on 429. A full run does only a handful of
  logins (the API rate-limits logins to **30 / 5 min / IP**).

## Feature coverage: assignments (34 tests across 24 specs)

Driven through the UI, verified against the REST API (`features/assignments/verify.ts`):

| Spec | What it proves |
| --- | --- |
| `01-lifecycle` | Student completes an all-task-types assignment; auto-graded; visible to teacher |
| `02-quiz` / `03-short-answer` / `04-number` / `05-form` | Each auto-gradable task type: answer → save → submit → auto-grade 100 |
| `06-manual-grading` | Teacher grades a submission (Full / feedback / Finalize & Complete) |
| `07-reject` | Teacher rejects a submission (it is removed) |
| `08-submissions-dashboard` | Stats, status filters, search |
| `09-retry` | Student retries a graded submission; it resets to a fresh attempt |
| `10-analytics` | Analytics KPIs + charts render for graded submissions |
| `11-show-correct-answers` / `15-show-answers-off` | Reveal setting on/off behaves correctly |
| `12-editor` | Edit modal (grading type), publish toggle, add a task |
| `13-file-submission` | Student uploads a file; teacher grades it manually |
| `14-grading-edge` | Partial quiz credit (50) and a number outside tolerance (0) |
| `16-assignments-list` | List page: search + Published/Drafts filters + Editor/Submissions links |
| `17-author-tasks` | Author task content via the editor; delete a task |
| `18-submissions-sort` | Sorting submissions by Grade reorders rows |
| `19-per-task-override` | Per-task grade override: custom numeric, Half, Zero |
| `20-editor-toggles` | Auto-grading / anti-paste / retries toggles persist |
| `21-grading-display` | Student grade display per type: PASS_FAIL→Pass, ALPHABET→A, GPA→4.0 |
| `22-student-aux` | Hint reveal, reference-document link, due date, attempt counter |
| `23-anti-copy-paste` | Anti-copy-paste deterrent blocks pasting into a form blank |
| `24-formative` | Formative mode: handing in a document unlocks the model answer without grading, and a retry re-locks it until the next hand-in |

Complemented by 367 assignment-behavior tests in the ten `apps/api/src/tests/services/test_assignment*.py` modules (grading math, matching modes, tolerance, server-verify dispatch, CODE grading, retry caps, due dates, permissions); the directory as a whole collects 5,459 tests.

### Bugs found & fixed

1. **Manual grades discarded for auto-gradable task types** (backend): server-side
   re-verification overwrote a teacher's explicit per-task grade on finalize.
   Fixed in `apps/api/.../assignments.py` (gate re-verification to the auto-grade
   path). Covered by `test_assignment_manual_grading.py` + `06-manual-grading`.
2. **Save button hidden on retries** (frontend): the student "Save your progress"
   control was gated on submission-row existence (a retry keeps the row), so
   students couldn't save new answers. Fixed in `apps/web/.../AssignmentBoxUI.tsx`
   (gate on submission status).

> The two fixes are not in the published `:latest` image yet, so the
> manual-grading grade assertion requires this branch's code in the running
> instance (it passes against `learnhouse dev` / a branch-built image).
> CODE task type is covered by backend tests only (Judge0 isn't available on a
> self-host).
