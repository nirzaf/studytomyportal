# AUD-18 local verification record

## Synthetic managed Virtual Lab Chromium journey (21 November 2026)

The live Playwright case `17-managed-virtual-lab-live.spec.ts` passed once against a fresh synthetic scenario on PostgreSQL `19.0`, pgvector `0.8.7`, Alembic head `b7c2e9a4d1f0`. It used the named-volume loopback service documented in [the PG19 completion record](POSTGRESQL-19-UPGRADE.md), the real FastAPI and Next.js apps, and the static lab build. The synthetic scenario was prepared through ordinary API routes with a separate author/reviewer, course-scoped authority, synthetic policy decisions, review evidence, release, learner consent and first-use access grant.

Chromium signed in normally, opened the released lab and used the mass slider. The exported CSV and calculation HUD matched. The journey then exercised practical assignment access, raw CSV and grade-injection rejection, offline draft retry, ZIP upload/submission, duplicate and late-write denial, cross-organization/stranger/release/deadline denial, teacher file review and grading, mobile layout and consent withdrawal. PostgreSQL confirmed final `GRADED` status, overall grade `9`, a manual file-task grade `7`, and an automatic numeric-task grade `2`; consent acceptance and withdrawal records both persisted. The sandboxed iframe stayed opaque and sandboxed.

The dedicated Playwright config and normal-login state helper were checked separately: one intended test was discovered, the synthetic learner logged in through the local UI, browser state was saved under owner-only permissions, and non-loopback browser requests were blocked. No AI/OpenAI provider call occurred. API logs show an attempted Resend notification to a synthetic reviewer was rejected for invalid provider credentials; no successful delivery was observed. API egress was not blocked in this run, so it was not fully offline. A local RAG indexing task also logged a missing-provider-configuration error without blocking the lab test.

This is synthetic technical acceptance only. Grade 12 Physics educator, safeguarding, content-rights, curriculum and real release approvals remain outstanding. It does not verify AI generation, real learner data, external storage, hosted deployment, a full production-population migration, production rollback or production performance.

## PostgreSQL 19 completion follow-up (21 November 2026)

The work started at `main` commit `99209c77a177dd99f4377f68af007c7ecb7a3dc1` with a clean tree. The local PG19 pin was verified against PostgreSQL's current-version list and Docker Hub's published pgvector PG19 image index digest. PostgreSQL 19.0 is the current supported minor as of this check; the pinned arm64 index is `sha256:89879f41d564f67b0c1462a491d16fcbc6de39ac10db17e7a4bba2138a5e0ba5` ([version policy](https://www.postgresql.org/support/versioning/), [image digest](https://hub.docker.com/layers/pgvector/pgvector/pg19/images/sha256-4544950632b50c7484ce33b906e2eee185c3619f889eeaa7baef11b1dc0162f4)). No production or existing PG16 database was targeted.

### Fresh CLI install and persistent-volume proof

The built CLI created a new install with `setup --ci --no-start --channel dev`. Its Compose project was `learnhouse-9688427a`; the `db` service selected `pgvector/pgvector:0.8.7-pg19@sha256:89879f41d564f67b0c1462a491d16fcbc6de39ac10db17e7a4bba2138a5e0ba5`, published only at `127.0.0.1:49169`, with physical volume `learnhouse-9688427a_learnhouse_db_data_9688427a` mounted at `/var/lib/postgresql`. The server reported PostgreSQL `19.0-1.pgdg12+1`, `PGDATA=/var/lib/postgresql/19/docker`, and pgvector `0.8.7` after `ensure_schema()`.

The new `apps/api/src/tests/core/test_pg19_volume_persistence_postgres.py` probe passed its seed and verify phases. It wrote a synthetic vector row and HNSW index through the API's normal schema-bootstrap/test path, recreated only the `db` service using `up -d --force-recreate --no-deps --wait --pull never db`, then verified the same row, revision, extension and nearest-vector result without running migrations again. Container ID changed from `20d06a8864c17c1922ab0e581e336ebc7e321199ae324e039ec4a456433a04c4` to `d69bd40e7713bc6eaac9faa58f8baa6b95f4b8680130d1ab6c732c55f2b2fe29`; physical volume and mount target were unchanged. The synthetic row returned vector `[1,0,0]`, revision `1`, distance `0.0`, Alembic head `b7c2e9a4d1f0`, and vector `0.8.7`.

### Separate PG16-to-PG19 restore proof

Compose project `stpg19-restore-67e5f25e1c` created two distinct named volumes: `st-pg16-src-67e5f25e1c` at `/var/lib/postgresql/data` and `st-pg19-dst-67e5f25e1c` at `/var/lib/postgresql`. The isolated source was PostgreSQL `16.16`; the target was the pinned PG19 image. Their only published ports were loopback ephemeral ports `50308` and `50307`. Each database name ended in `_test`.

The API's `ensure_schema()` initialized the synthetic source at single Alembic head `b7c2e9a4d1f0`. The source then received one synthetic organization, one immutable business event and one vector/HNSW probe row. PostgreSQL 19 `pg_dump` produced a custom-format, mode-`0600` archive (SHA-256 `52c8626869d3ce85810480cb1f6c9ddc3615179810df3e07a58f60a81218b056`); `pg_restore --exit-on-error --single-transaction` restored it into a new PG19 test database on the separate target volume. PG16 remained intact. Read-only comparison after restore matched all 138 public tables and row counts, 729 structural constraint signatures, 468 index signatures, 1,581 column signatures, 42 non-internal trigger definitions, the Alembic revision, synthetic organization/event and vector lookup. Both source and target used pgvector `0.8.7`; target version was PostgreSQL `19.0`. This is evidence for a small synthetic logical migration only, not the full learner, guardian, finance, file or external-object-store acceptance matrix.

### Test results and remaining gaps

| Check | Result |
|---|---|
| `apps/cli` `bun run typecheck` and `bun run build` | Passed |
| CLI setup/command/unit/EE preflight suites | 586 passed |
| CLI backup/restore and update-EE suites | 67 passed after correcting mock isolation in three restore tests |
| API migration and startup unit tests (`test_migrations.py`, `test_events_database.py`, current-head assertion) | 62 passed |
| PG19 core migration and volume persistence tests | 5 passed |
| Full `apps/api/src/tests/studytomy` on isolated PG19 | 417 passed, 3 failed in 64.61s |
| Actual CLI database-only PG19 backup then `restore --yes` | Succeeded; archive metadata and dump header both reported PostgreSQL `19.0`; persistence probe passed again afterward |

### Fresh-database FastAPI runtime smoke (21 November 2026)

The installed API venv launched the real Uvicorn application and lifespan against a newly created synthetic database on the retained PG19 restore volume. The database bootstrap used `LEARNHOUSE_AUTO_MIGRATE=true`, a generated one-run admin password, a reserved synthetic email, AI disabled and a separate temporary Redis 7 container. FastAPI listened only on `127.0.0.1:1338`; the PostgreSQL bridge listened only on `127.0.0.1:45439`. `GET /api/v1/health` returned HTTP 200 with body `true`. PostgreSQL reported `19.0-1.pgdg12+1`, `PGDATA=/var/lib/postgresql/19/docker`, pgvector `0.8.7`, Alembic revision `b7c2e9a4d1f0`, and exactly one synthetic organization created by the normal auto-install path. The effective Uvicorn command was `apps/api/.venv/bin/uvicorn app:app --host 127.0.0.1 --port 1338 --log-level warning`, from a temporary working directory with the API source on `PYTHONPATH`.

Initial bootstrap attempts halted at the expected local prerequisites: the first lacked the required initial-admin password; the next was rejected because the reserved `.test` email domain is invalid for this validator. Each partially seeded probe database was dropped before retry. The passing run supplied only an ephemeral password and a synthetic `example.com` address; AI was disabled, no provider credentials were inherited, and no email or AI-provider operation was invoked. Afterward the probe database and temporary runtime files were removed, the Redis and bridge containers were stopped, and the PG19 service returned to its prior stopped state. The named PG19 volume was retained. This verifies fresh FastAPI startup and database health; it does not exercise Next.js or satisfy the rights-reviewed Virtual Lab journey.

### PG19 migration interruption recovery rerun (21 November 2026)

From `apps/api`, `./.venv/bin/pytest -q src/tests/core/test_migrations_postgres.py` ran against PostgreSQL `19.0` with pgvector `0.8.7` on the isolated PG19 restore service. Result: **4 passed in 4.59 seconds**. Coverage included concurrent empty-schema bootstrap and integrity guards, a supported prior-revision upgrade, refusal of a lossy downgrade, and a synthetic upgrade interruption. In the interruption case Alembic was made to create a table and raise inside the migration transaction; the test confirmed the DDL rolled back and the original Alembic revision remained. The suite created and dropped only its unique loopback `_test` databases. Its temporary base database and TCP bridge were removed afterward; the PG19 service was stopped and its named volume retained. This verifies migration transaction recovery on the isolated PG19 fixture, not recovery of a populated deployment.

The refreshed local E2E runbook's pinned PG19 image and tmpfs mount syntax passed a Docker CLI check with `--network none --pull=never`; the check ran `/bin/true`, so it validated mount parsing without starting a database. The separate `docs` workspace has no installed `node_modules`, so its Fumadocs/MDX build was not run and no dependencies were installed. `git diff --check` passed.

The three remaining StudyTOMY failures are existing behavior questions, not PG19-specific regressions: two adaptive-retention tests expect `ADAPTIVE_RETENTION_REVIEW_REQUIRED` but receive `MANAGED_ASSESSMENT_SOURCE_RETAINED`; one service-case test expects `assigned` but receives `acknowledged`. The same three expectations failed on an isolated PG16 database. Their owner rulings remain open in the prior verification record. The outdated test pin for Alembic head `b91d2f6a08c3` was updated to current head `b7c2e9a4d1f0`; its focused test passed.

An initial attempt at the actual CLI backup used local filesystem content mode, so `docker compose run` began pulling `ghcr.io/learnhouse/app:dev` for the content archive. I interrupted that command before an app container started. Compose had created only this fresh project's named Redis/content volumes; they were retained. The temporary install was then set to database-only backup mode, and backup/restore passed without another app-image pull. No app UI was booted. After verification, only the three database services created for these tests were stopped; all five named volumes remain, and no volume removal or prune ran. The Playwright live lab journey remains unrun because the checked-in harness needs a teacher/reviewer/learner scenario and an approved rights-reviewed release; Grade 12 Physics educator and safeguarding sign-offs remain pending. No production optimization was applied; the small synthetic profile below is not a representative workload. No production cutover or hosted migration is authorized.

### Continuation: PostgreSQL 19 suite and retrieval profile (21 November 2026)

The PostgreSQL-backed `apps/api/src/tests/studytomy` selection was repeated against the loopback PG19 restore target `studytomy_pg19_restore_67e5f25e1c_test`: **417 passed, 3 failed in 63.30 seconds**. The two adaptive-retention precedence failures and service-case status failure match the PG16 run. The selection includes PostgreSQL grounded-retrieval, authorization, grading, idempotency and concurrency cases; it is an in-process API test, not a live Uvicorn/Next.js workflow.

A fixed-seed, connection-scoped synthetic profile compared PostgreSQL 16.16 and 19.0 with pgvector 0.8.7. It used 3,000 generated 768-dimensional rows and 66 authorized rows in the selected course scope. After two warmups, median `EXPLAIN ANALYZE` execution times over five queries were: exact authorized cosine ranking **0.203 ms / 0.202 ms** (PG16/PG19), explicitly selected HNSW **3.317 ms / 4.422 ms**, and the test-fixture vector/full-text RRF query **0.268 ms / 0.334 ms**. HNSW recall@10 was `[1.0, 1.0, 1.0, 0.8, 1.0]` on PG16 and `[1.0, 0.9, 1.0, 0.9, 1.0]` on PG19, with no underfilled result. Benchmark tables and indexes were temporary and disappeared when each connection closed. These measurements do not establish production performance or justify tuning; full plans and runtime details are in [the PostgreSQL 19 upgrade note](POSTGRESQL-19-UPGRADE.md).

The PG19 database reported libc locale provider `c`, `en_US.utf8` collation/ctype and collation version `2.36`; it had no public `tsvector` columns/indexes and no `pg_trgm` extension. The autovacuum score view exists, but its synthetic schema does not model real maintenance pressure. The REPACK safety procedure and remaining P0 E2E gates are documented in the PostgreSQL 19 upgrade note. The PG16 and PG19 restore-test containers were stopped after the checks; their named volumes were retained.

### Full API suite on the isolated PG19 target (21 November 2026)

After the focused PostgreSQL-backed selection, the full API suite ran against only `studytomy_pg19_restore_67e5f25e1c_test` with PostgreSQL `19.0`, pgvector `0.8.7`, and Alembic head `b7c2e9a4d1f0`:

```sh
./.venv/bin/pytest -q src/tests --tb=short
```

Result: **10,589 passed, 14 failed, 23 skipped, 250 warnings in 259.88 seconds**. The 14 failures were five logout-credential tests still patching/asserting `revoke_user_sessions_before` although the current route uses `advance_session_generation`, five link-preview tests whose mock factory supplied a transport in addition to the production pinned transport, one Bedrock test reaching the optional CRT-backed login provider with no credentials, the same two adaptive-retention precedence expectations, and the same service-case `assigned` versus `acknowledged` expectation documented above. The stale Alembic-head assertion from the earlier full-suite record now passes after the expected head was updated to `b7c2e9a4d1f0`. No test failures were hidden or reclassified as passes. This run improves PG19 API coverage but does not replace the still-pending live Playwright workflow.

### Full API rerun after test-harness corrections (21 November 2026)

The five logout-credential tests were stale against the current route, which calls `advance_session_generation(db, user_id)`, and the link-preview mock factory supplied a second `transport` keyword after the production code began using its pinned SSRF transport. The focused logout and response-cap files now pass: **28 passed**. Only test code changed; production logout and SSRF transport behavior were left intact.

The Bedrock model-construction test also needed deterministic offline credentials: without them, botocore selected its optional CRT-backed login provider before any model call. The test now supplies synthetic environment credentials and disables metadata lookup; the focused test passed without a provider call or dependency change.

The complete API suite then ran against a fresh clone of the synthetic PG19 restore database named `studytomy_pg19_fullapi_67e5f25e1c_test` on the loopback-only isolated service:

```sh
./.venv/bin/pytest -q src/tests --tb=short
```

Result: **10,599 passed, 4 failed, 23 skipped, 251 warnings in 228.79 seconds**. The resolved ten logout/link-preview test failures no longer occur. The remaining failures are the Bedrock model test, whose declared environment lacks `botocore[crt]`, two adaptive-retention precedence expectations, and the service-case `assigned` versus `acknowledged` expectation. The three behavior expectations still need owner rulings; no product semantics were changed to force them green. The fresh API-suite database was a clone on the disposable PG19 volume; its service was stopped after the run and the volume was retained.

After the Bedrock test correction, the full suite ran again against a second fresh clone, `studytomy_pg19_fullapi2_67e5f25e1c_test`, on the same loopback-only PG19 target. Result: **10,600 passed, 3 failed, 23 skipped, 251 warnings in 228.81 seconds**. Only the two adaptive-retention precedence cases and one service-case status expectation remain. The container is stopped; the PG19 named volume remains.

### Globals-only role handoff and frontend checks (21 November 2026)

To exercise the PG16 globals handoff without touching a prior database, the stopped synthetic PG16 source was started read-only and `pg_dumpall --globals-only --no-role-passwords` was piped into a new PostgreSQL 19 container. The destination used `--network none`, tmpfs at `/var/lib/postgresql`, and no named volume. The PG16 custom-format database dump was restored with PostgreSQL 19 `pg_restore --exit-on-error --single-transaction`. The destination database owner was the restored synthetic `st_test` superuser; PostgreSQL `19.0`, pgvector `0.8.7`, Alembic head `b7c2e9a4d1f0`, and 138 public tables were confirmed. The source database received no writes; both containers were stopped, and the separate named PG16/PG19 evidence volumes remain. This is a globals/restore smoke check for one synthetic superuser, not least-privilege role or production-grant verification.

From `apps/web`, `bun ./node_modules/typescript/bin/tsc --noEmit` exited 0 and `bun run test` passed **761 tests across 81 files (2,246 assertions)**. From `apps/e2e`, `bun run typecheck` exited 0. The installed Bun was `1.4.2` while the repository declares `1.4.4`; no runtime was installed. These checks do not constitute the live Playwright educator/reviewer/learner acceptance.

### Continuation: retention contracts and full API suite (21 November 2026)

The three failure records above describe earlier runs. Current code inspection and focused PostgreSQL 19 tests confirmed their expectations were stale: course deletion first reports the retained frozen-release guard, account deletion reports adaptive retention, and narrative safeguarding reports remain acknowledged until independent triage. The test fixtures now assert those existing contracts without changing runtime code. The adaptive tests directly exercise both adaptive-retention guards; the deletion cases verify the frozen task and evidence remain. The safeguarding fixture verifies that a conflicted triager cannot expose the narrative or leave an ownerless case marked assigned.

The focused suite passed **3 tests** on the isolated PostgreSQL 19 target, and the SQLite-backed adaptive test passed separately. `ruff check` passed for both changed test files. The complete API suite then ran on fresh cloned database `studytomy_pg19_completionfull_20261121_test` using `./.venv/bin/pytest -q src/tests --tb=short --show-capture=no`, against PostgreSQL `19.0`, pgvector `0.8.7` and Alembic head `b7c2e9a4d1f0`: **10,603 passed, 0 failed, 23 skipped, 251 warnings in 233.03 seconds** (exit 0). The skips are reported separately, not counted as passes. The test subprocess had AI disabled and external provider-key variables removed; synthetic AWS credentials were supplied only for deterministic offline model-construction coverage.

After verification, the exact isolated PG19 test container was stopped; its named target volume remains intact. No other database service or volume was stopped or removed.

This closes the current full API-suite failures on the disposable PG19 database. It does not close the live Playwright lab acceptance: `E2E_LIVE_VLAB_SCENARIO`, `BASE_URL` and `API_URL` were unset, no supported full-scenario seed utility is present, and Grade 12 Physics educator and safeguarding approvals remain pending. No real learner journey or approved release was created for this run.

## Documentation-refresh verification sweep (20 November 2026)

Reviewed primary `main` at `ef6b730d`. Ten in-flight files were left untouched: nine under `apps/cli/` and `docs/studytomy/POSTGRESQL-19-UPGRADE.md`. The sweep changed no application code; it is the evidence behind the 2026-11-20 documentation corrections.

**Environment.** macOS arm64. Python dependencies from the locked `uv` environment (`--no-sync`). Each JavaScript workspace installed with `bun install --frozen-lockfile`. PostgreSQL checks used a fresh disposable container (`pgvector/pgvector:pg16`, label `studytomy.purpose=docs-refresh-tests`, loopback-only random port, database `studytomy_docsweep2_test`), bootstrapped with `ensure_schema()` and an ephemeral generated `LEARNHOUSE_AUTH_JWT_SECRET_KEY`. The container was stopped by its label afterwards, and the tracked tree stayed byte-identical apart from the documentation edits.

| Check (working directory `apps/api` unless noted) | Exit | Observed result |
|---|---|---|
| `uv run alembic heads` | 0 | single head `b7c2e9a4d1f0` (137 revision files) |
| `uv run --no-sync pytest -q src/tests/studytomy` (migrated disposable PostgreSQL) | 1 | **416 passed, 4 failed** in 68.14s |
| `uv run --no-sync pytest -q src/tests/studytomy -m studytomy_postgres` | 1 | **78 passed, 2 failed**, 340 deselected in 52.59s |
| `uv run --no-sync pytest -q src/tests` (full API suite, same database) | 1 | **10,588 passed, 15 failed**, 22 skipped, 251 warnings in 279.01s |
| `uv run --no-sync pytest -q src/tests/core/test_migrations.py` | 0 | 54 passed in 6.34s |
| `apps/web`: `bun ./node_modules/typescript/bin/tsc --noEmit` | 0 | pass |
| `apps/web`: `bun run test` | 0 | 761 pass, 0 fail across 81 files (2,246 assertions) in 10.51s |
| `apps/web`: `bun run build` | 0 | pass |
| `apps/web`: `bun run lint:strict . --max-warnings=0` | 1 | 0 errors, **975 warnings**; the non-zero exit is the warning budget, not a rule failure. `bun run lint` (`eslint .`) exits 0 on the same tree because warnings alone do not fail it |
| `apps/collab`: `bun run test` | 0 | 48 pass, 0 fail |
| `apps/e2e`: `bun run typecheck` | 0 | pass |
| `packages/sdk`: `bun run test` | 0 | 118 pass, 3 skipped (8 files passed, 1 skipped) |
| `packages/react` / `packages/vue`: `bun run test` | 1 | fail to collect: 3 and 1 test files import `../dist/*`, which requires `bun run build` first |
| `apps/cli`: `bun run typecheck` (dirty worktree) | 1 | one `TS2322` at `src/commands/setup.ts:121` (`PostgresComposeSettings \| null \| undefined`) |
| `apps/cli`: `bun run test` (dirty worktree) | 1 | 83 pass, 36 fail, 25 errors |

The `apps/cli` results describe uncommitted work in progress and are not evidence about the committed CLI. The React/Vue collection failures are an ordering requirement (`build` before `test`), not a renderer defect; the SDK package passed on its own.

### The 15 API failures, classified

All 15 reproduce against the migrated PostgreSQL database and none is a new product regression, but each is open engineering work:

| Count | Test | Cause and evidence |
|---|---|---|
| 5 | `src/tests/security/test_logout_revocation_credentials.py` | patches `src.routers.auth.revoke_user_sessions_before`; `AttributeError` — the symbol lives in `apps/api/src/security/auth.py:289` and is imported lazily, so the module attribute no longer exists to patch |
| 5 | `src/tests/security/test_resource_exhaustion_limits.py::TestLinkPreviewResponseCap` | `TypeError: httpx.AsyncClient() got multiple values for keyword argument 'transport'` — the fixture injects its own `transport` while `services/utils/link_preview.py` now passes `transport=pinned_transport()` |
| 1 | `src/tests/services/test_llm_provider.py::test_build_model_bedrock_needs_no_api_key` | `botocore.exceptions.MissingDependencyException` — `botocore[crt]` is absent from the locked environment |
| 1 | `src/tests/studytomy/test_existing_contracts.py::TestMigrationAndMetadataBaseline::test_alembic_head_revision_is_pinned` | asserts `['b91d2f6a08c3']`; actual `['b7c2e9a4d1f0']`. The pinned revision is an ancestor of the current head, so the pin is stale rather than the chain forked |
| 2 | `src/tests/studytomy/test_adaptive_curriculum_api.py` (`…require_direct_session_exact_scope_and_native_access`, `…round_trip_on_postgres`) | asserts `ADAPTIVE_RETENTION_REVIEW_REQUIRED` on delete and receives `MANAGED_ASSESSMENT_SOURCE_RETAINED`; the managed-assessment guard in `services/studytomy/assignment_adapter.py:878,894,911` now runs before `services/studytomy/adaptive_retention.py:31` |
| 1 | `src/tests/studytomy/test_service_cases_postgres.py::test_report_routing_enforces_persisted_approval_and_exact_case_grant` | `assert 'acknowledged' == 'assigned'` at `:1349` |

Two of these are behavior questions, not assertion hygiene, and were deliberately **not** resolved by editing tests or docs: whether `MANAGED_ASSESSMENT_SOURCE_RETAINED` should preempt the adaptive-retention review on course or account deletion, and whether report routing should accept an `acknowledged` case. The first belongs to TASK-066 PostgreSQL concurrency and retention-scope work. Until someone rules, capability evidence in this record and in [implementation status](IMPLEMENTATION-STATUS.md) should be read as "focused suite has open failures", not as a clean pass.

No browser journey, live AI provider, real notification delivery, production database, deployed-data migration or real learner account was exercised by this sweep. No approval, release or pilot activation is implied.

## Mac managed enrollment and access verification (18 November 2026)

The Mac ran `main` at `8308715b14f7f73bdb74a0c7a7e3819baf98047a`. The isolated PostgreSQL 16/pgvector and Redis 7 containers listened on `127.0.0.1:59158` and `127.0.0.1:59159`. The test database and demo database were separate. The fresh test schema was initialized with `ensure_schema()` at `b91d2f6a08c3`. The demo database used `python cli.py install --short` with generated local credentials. No AI provider was configured for this check.

With the private local demo environment loaded, the API ran from `apps/api` using `.venv/bin/uvicorn app:app --host 127.0.0.1 --port 1318`. From `apps/web`, the working frontend command was:

```sh
NODE_OPTIONS=--dns-result-order=ipv4first \
NEXT_PUBLIC_LEARNHOUSE_API_URL=http://localhost:1318/api/v1/ \
NEXT_PUBLIC_LEARNHOUSE_BACKEND_URL=http://localhost:1318/ \
NEXT_PUBLIC_LEARNHOUSE_DOMAIN=localhost:3018 \
NEXT_PUBLIC_LEARNHOUSE_DEFAULT_ORG=default \
NEXT_PUBLIC_LEARNHOUSE_HTTPS=False \
bun run dev --hostname localhost --port 3018
```

The original web process served `/api/health` with HTTP 200 but logged `ECONNRESET` proxying the tenant rewrite for `/account/learning`. After restarting with the command above, the API root, web health, and learner route all returned HTTP 200. The learner route took 5.8 seconds including its first compilation. The built-in browser opened `/account/learning` and observed the signed-out redirect to `/login?redirect=/account/learning`.

With the dedicated loopback `STUDYTOMY_TEST_DATABASE_URL` loaded, this focused command from `apps/api` reported **12 passed, 19 deselected in 21.22 seconds**:

```sh
.venv/bin/python -m pytest -q -rs --tb=short \
  src/tests/studytomy/test_checkout_router.py \
  src/tests/studytomy/test_content_evidence.py \
  -k 'signed_checkout or grant_first_use'
```

The signed mounted-route tests exercised checkout allocation, the exact learner/release binding, private catalog and lesson delivery, first-use activation, and denial after access was lost. They also checked retained access history under the approved purpose and denials after consent withdrawal. The first-use concurrency test verified that revoking one source preserves an independently valid grant and that the database rejects rewriting a revocation. The route tests use synthetic policies and establish API and persistence behavior in the test database.

### Signed-in browser follow-up

At `9039a92455939cd77b4626efdd616691b05cbd3a`, the same loopback API and web app served the isolated demo database. Two independent learners were created through the native user service. The existing test fixture helpers supplied a private managed course, a reviewed text release, eligibility decisions and explicitly synthetic policy approvals. The entitlement service persisted one exact first-use grant for the first learner. The other learner had eligibility and consent but had no course grant. The first consent attempt correctly returned `409 POLICY_NOT_APPROVED`. Adding the missing synthetic D-05, D-11 and D-18 fixtures let the full run proceed with the authorization checks intact.

- At 390 × 844, Playwright Chromium signed in through the live login page. The learner accepted the synthetic notice through the signed API and opened the course and lesson from **My learning**. The first-use POST returned 200 with `Cache-Control: private, no-store`, and the browser rendered the reviewed fractions text.
- The second learner signed in separately. The live catalog returned an empty list, and the saved lesson URL returned 404. The granted learner also received 404 when requesting the same release and lesson under a different course.
- Signed consent withdrawal returned 201. The saved lesson URL returned 404, and the lesson text disappeared after the browser reloaded. After a fresh consent acceptance, the learner could open the lesson again.
- A fixture update set the exact grant's `revoked_at` in PostgreSQL. The saved lesson URL then returned 404 and the catalog was empty. After reload, **My learning** showed “No reviewed courses are available.” The browser recorded no uncaught page errors or failed Studytomy transport requests. PostgreSQL confirmed first-use activation, revocation before expiry and retained `accept`, `withdraw`, `accept` consent events.

The browser run used the seeded grant and real endpoints without response interception. Checkout allocation is covered by the mounted tests above. Revocation used a fixture update, and consent changes used the signed API. The screenshots capture the reviewed lesson and empty catalog after revocation. The demonstration used synthetic policy records and required no AI provider.

- **Recorded:** 2026-11-18
- **Source revision tested:** `029f1af19d26619a12ccbd162cba4ba1864505df`
- **Git tree:** `7f3e2652fb5ff3942790822a9fc2ee4c1313eadc`
- **Checkout:** primary `main` worktree at `C:\Users\DELL\repos\studytomyportal`; no parallel worktree was used.

The checks below ran against the source tree that was committed as the listed revision. This is a local verification snapshot, not a CI run, browser acceptance, live-provider evaluation, release approval, or deployment evidence. The repository has no configured CI/CD workflow. No production migration or real-learner flow was run.

## Dependent guardian report acceptance checkpoint (18 November 2026)

At `8cd024ecb19ae5b4aef92ca6e8a0f98b4e8fb3ba`, reviewed report creation and published report reads both require an active independent learner. Publication binds each recipient to the exact independent sharing grant and version. The separate family authority resolver checks effective guardian scope and can match a relationship UUID and authority version. Report publication and reads have no dependent guardian recipient binding. An accepted family relationship therefore cannot produce the requested browser success with the current report service.

With the dedicated loopback PostgreSQL test database at `b91d2f6a08c3`, this command from `apps/api` returned **21 passed in 29.25 seconds**:

```sh
.venv/bin/python -m pytest -q -rs --tb=short \
  src/tests/studytomy/test_adaptive_curriculum_api.py \
  src/tests/studytomy/test_family_authority.py \
  src/tests/studytomy/test_family_sharing.py
```

The signed mounted report flow exercised an authorized independent recipient, detail 404 and empty list for a pending guardian, and denial after learner reclassification or sharing withdrawal. The reclassification was a fixture update. Sharing withdrawal used the signed API and advanced the grant's authority version. PostgreSQL rejected mutation and deletion of retained report history. The family service tests exercised exact guardian, purpose and authority-version checks and dispute-based suppression using synthetic policy and staff-authorization fixtures.

Dependent guardian browser acceptance is blocked by the missing report integration. This checkpoint used the existing automated fixtures and made no demo database changes. No browser screenshot was captured. Completing the dependent flow requires a reviewed course-specific disclosure purpose and consent path, an exact report-version recipient binding to current guardian authority, and read-time revalidation after relationship withdrawal. The synthetic decisions confer no real disclosure authority.

The atomic specification's TASK-051 requires a reporting commitment for the applicable offered service. TASK-052 requires authority revalidation during approval, delivery and protected retrieval. D-03 still needs the evidence and permitted actions for guardians, including how competing authority and disputes affect disclosure. D-12 still needs the course-specific report fields and purpose, who can record the dependent learner's consent or other authority, and what withdrawal revokes. D-13 still needs the retention and record-access rules for the disclosure evidence. The current independent consent and sharing contracts do not supply those decisions. A future recipient record must bind the exact report version, learner, course and recipient to the relevant relationship UUID and authority version, then resolve that authority again when reading the report.

The signed report regression now verifies that a pending relationship UUID mixed with a valid independent sharing grant rejects the entire publication with `409 REPORT_RECIPIENT_NOT_AUTHORIZED` and writes no recipient. It also verifies that scoped staff cannot create a new draft after synthetic reclassification to dependent control. The draft route returns a private no-store 404. The existing exact-version independent publication and withdrawal checks still pass. Running the signed adaptive curriculum, family authority and consent-event suites returned **25 passed in 30.58 seconds**, including the real PostgreSQL report workflow. Ruff and `git diff --check` passed. Dependent guardian browser acceptance remains pending.

## Mac provider-free PDF authoring (18 November 2026)

At `5d92da2f5c92dcdc4fe37b7718678d8df773bd9e`, the isolated API on `localhost:1318` ran with `LEARNHOUSE_STUDYTOMY_GROUNDED_DRAFTS_ENABLED=true`. Next.js used `localhost:3018`. The demo fixture contained two private managed courses and three unpublished text lessons. A one-page searchable PDF contained the synthetic sentence “Two quarters equal one half.” No provider key was configured.

1. At 1280 × 900, Playwright Chromium signed in as the synthetic administrator, opened the real lesson editor and uploaded the file through **Lesson sources**. The live multipart request returned 201 with `Cache-Control: private, no-store`. The editor showed one readable page. The first browser harness stalled while reading the multipart response body. A rerun checked the browser response status and read the receipt from the live list endpoint. Reuploading the same bytes returned the identical receipt.
2. The signed API returned empty source lists for the sibling lesson and the other course's lesson. Explicitly selecting the source for preview from either returned 404. Pairing the original lesson ID with the other course also returned 404.
3. The signed API saved a hand-written draft with the exact source UUID, SHA-256, page number and quote. The browser reloaded the editor and rendered the saved citation. This was a provider-free save, with no generated response or intercepted request.
4. The signed revoke endpoint returned 204. Preview with the revoked source returned 404, the active source list was empty and the citation endpoint returned `stale`. On reload the editor marked the citation unavailable and hid the quote. The two screenshots show the current and stale citation panels. Chromium recorded no uncaught page errors on the passing run.
5. A direct PostgreSQL read matched the retained PDF bytes and SHA-256, exact course and lesson, extracted page and citation evidence. The native activity remained unpublished at version 2 and the source had a revocation timestamp.

From `apps/api`, with the dedicated loopback test database configured, `.venv/bin/python -m pytest -q -rs --tb=short src/tests/studytomy/test_grounded_material_postgres.py src/tests/studytomy/test_grounded_retrieval_postgres.py` passed **4 tests in 2.86 seconds**.

**Live provider acceptance remains open.** For OpenAI or an OpenAI-compatible gateway, configure the API process with `LEARNHOUSE_IS_AI_ENABLED=true`, `LEARNHOUSE_STUDYTOMY_GROUNDED_DRAFTS_ENABLED=true`, `LEARNHOUSE_AI_PROVIDER=openai` (or `openai-compatible`), and `LEARNHOUSE_AI_API_KEY` from the approved secret channel. Use `LEARNHOUSE_AI_BASE_URL` for a compatible gateway. Set `LEARNHOUSE_AI_MODEL_STANDARD` to the provider's chat model ID, and `LEARNHOUSE_AI_MODEL_PRO` if the organization resolves to the Pro planning tier. The chosen model must support the structured lesson and quiz output used by the Pydantic AI adapter. The preview path also enforces rate and credit limits. Without a tier override, model resolution falls back to the code's Gemini defaults. The Mac rehearsal made no generation call.

## Mac learner assessment and mastery checkpoint (18 November 2026)

At `51e42912cbccf54d362c546cc8bf455879511f91`, the isolated Mac demo stack ran web on `localhost:3018` and API on `localhost:1318`. The existing native quiz spec created a synthetic ordinary LearnHouse course and used a seeded answer key. With `E2E_BASE_URL=http://localhost:3018` and `E2E_API_URL=http://localhost:1318/api/v1`, run `bun run test features/assignments/tests/02-quiz.spec.ts --reporter=list` from `apps/e2e` with the local demo administrator credential supplied privately. **1 Chromium test passed in 11.1 seconds.** The student selected the answer, saved progress, handed it in and saw 100 in the UI. The live API and a direct PostgreSQL read agreed on 100 and `GRADED`. PostgreSQL confirmed automatic grading with no manual override. No AI generated the question or graded the answer.

The signed adaptive regression now saves the learner answer through the native task-submission PUT in place of a seeded answer row. A forged grade returns 403 and writes no task submission. The valid answer returns 200 with an ungraded stored row. Hand-in and staff finalization exercise the server scoring and evidence journal. The native item with unknown independence increments only the unknown-evidence count. The managed diagnostic uses an exact reviewed release and stores a server-scored, mastery-eligible observation. A different signed actor cannot read its attempt summary and receives a private no-store 404. The PostgreSQL evidence suite exercises append-only integrity, correction lineage and projection watermarks.

With the separate loopback test database configured, this command from `apps/api` returned **15 passed in 39.05 seconds**:

```sh
.venv/bin/python -m pytest -q -rs --tb=short \
  src/tests/studytomy/test_adaptive_curriculum_api.py \
  src/tests/studytomy/test_adaptive_evidence_postgres.py \
  src/tests/studytomy/test_grading_postgres.py
```

Managed assessment and practice browser acceptance remains pending. The exact-release **My learning** viewer renders reviewed lesson content and offers completion where authorized. Its catalog excludes assignments and the lesson view does not mount the native adaptive panel. Learner access through the native draft course route is denied by design. A managed browser journey still needs a release-scoped practice/assessment entry point that preserves exact grant and review checks. The browser result above applies to the ordinary native quiz. The managed release, evidence and mastery results come from signed mounted API and PostgreSQL checks.

### Managed release diagnostic browser follow-up

The managed text lesson now mounts the existing adaptive attempt panel. Options use the exact course and release, recovery uses the release, and starts send the fields accepted by the diagnostic or supported-practice contract. Diagnostic options return an empty list when the supplied course and release do not match. The managed view does not link to native draft pages or render native assignments.

At the working tree based on `1ad9825067ec46cdab3486ad763e0ae6f1f2de9f`, a separate loopback API on `localhost:1320` and Next.js on `localhost:3020` used a disposable PostgreSQL snapshot of the existing signed synthetic diagnostic fixture. Fixture preparation added an ordered chapter before retaining the release so the lesson could appear in the release index. The first fixture omitted that ordering, and the index correctly returned no lessons. The web server was restarted after switching the API to the corrected fixture database to clear the prior tenant state. No provider was configured.

At 390 × 844, Chromium signed in, opened **My learning**, entered the released text lesson and started the mapped diagnostic. The real start returned 201. Reloading and reopening the lesson recovered the same question, and a browser answer submission returned 200. The summary showed zero of one scored responses correct. The question payload exposed no answer key. PostgreSQL confirmed the exact course, release and activity binding, a completed attempt, a mastery-eligible incorrect observation and the matching mastery watermark.

Signed requests returned no options for a wrong course, wrong release or different account. A start against a wrong release returned 404. The other account could not read the learner's summary or open the released course. Revocation was a targeted fixture update in PostgreSQL. After it, the learner's summary returned 404 and options were empty. Reload showed no reviewed courses, while PostgreSQL retained the attempt and evidence. Screenshots captured the completed diagnostic and the empty catalog after revocation. Native managed assignment rendering and a live managed supported-practice browser attempt remain pending. The browser fixture used reviewed synthetic diagnostic items and did not supply a text lesson mapped for supported practice.

The focused signed adaptive curriculum and PostgreSQL evidence suite passed **7 tests in 27.88 seconds**, including the wrong-course option regression. Web TypeScript and three existing adaptive UI tests passed. Scoped ESLint reported no errors and eight existing warnings. Ruff and Git whitespace checks passed.

### Managed supported-practice boundary

At `961e503e0eccf53d7f7fbdcffa3358d8b55a2685`, the isolated Mac API and web app used `localhost:1320` and `localhost:3020`. The entitlement service issued a new fixed-period synthetic grant for the same reviewed release, preserving the previous revoked grant. Chromium at 390 × 844 signed in, opened the entitled text lesson and observed a diagnostic option and no practice panel. The live option route returned 200 with no practice option for the lesson or for the mapped native assignment. The browser session made signed practice-start requests against both sources, a wrong course, a wrong release and the other signed-in account. All returned private no-store 404 responses. After a PostgreSQL fixture update revoked the new grant, the start still returned 404 and the course disappeared on browser reload. A PostgreSQL read found zero release-bound practice attempts.

The native supported-practice resolver requires the item to come from an assignment task on the same activity as the practice mapping. The managed release resolver validates the exact mapped activity through the text or media projector. It excludes assignment activities. A text mapping alone cannot satisfy the item binding, and a native assignment cannot satisfy the managed delivery check. A positive managed practice path needs an explicit reviewed association between a deliverable released activity and a practice item, or a dedicated safe projection for an assignment-backed practice activity. The current contracts do not specify either release binding. No runtime authorization or rendering change was made.

The signed regression now checks that neither source can start managed practice or create an attempt. The adaptive curriculum and PostgreSQL evidence suites passed **7 tests in 28.07 seconds**, including the existing ordinary supported-practice path. Ruff and Git whitespace checks passed. Managed answer submission, recovery, feedback and evidence remain unverified in browser, and full native assignment rendering remains pending. No AI provider was used.

### Live managed supported practice

The managed practice resolver now starts from an explicit `practice` mapping on a deliverable, reviewed lesson. It selects a current D-16-approved practice-only item for the same curriculum concept and reconstructs the item source from the exact frozen release. The retained published assignment, activity and task must agree on organization, course and parent identifiers. The reconstructed source digest and answer must match the reviewed item. Missing or mismatched records leave practice unavailable. The native practice branch still checks the current native source. No migration or new business policy fields were required.

At the working tree based on `349ea047290dcace4b189da8070e7bf8a028b6d6`, a clone of the synthetic PostgreSQL fixture ran through the local API at `localhost:1320` and web at `localhost:3020`. At 390 × 844, Chromium opened the entitled released practice lesson, started the existing practice panel (201), recovered the same question after reload (200), and submitted the numeric answer (200). The server summary showed practice completion without a numeric grade or answer key. PostgreSQL confirmed the exact course, release and lesson, a correct assisted observation excluded from independent mastery, and a matching mastery watermark. No AI provider was used.

Signed negative checks found no options for a wrong course, wrong release or different account. Starting against the wrong release and reading the summary from the other account returned 404. A targeted fixture update revoked the grant. Afterward the summary returned 404, options were empty and the course disappeared on browser reload. PostgreSQL retained the completed attempt and evidence. Full native assignment rendering remains pending.

The focused command from `apps/api` was `.venv/bin/python -m pytest -q --show-capture=no --tb=short src/tests/studytomy/test_adaptive_curriculum_api.py src/tests/studytomy/test_adaptive_evidence_postgres.py`. **7 passed in 29.76 seconds**, including real PostgreSQL. The first fixture retired the item and deleted its source before freezing the release. The passing regression froze the release first and placed the explicit practice mapping on a separate text lesson so the existing prerequisite recommendation fixture retained its intended meaning. Ruff and Git whitespace checks passed.

### Native managed assessment delivery and grading binding

At `7fa1051e59a97ce96d74232a89448be91a8992f3`, the frozen release contains native assignment rows, task content and grading inputs. The existing grading contract can bind an issued attempt to one started release. The managed learner index explicitly excludes assignment activities. Native assignment read, answer-save and hand-in paths still use native activity access, which denies learners access to managed drafts. A pass prerequisite names a result source and does not authorize assessment delivery.

Full managed assignment rendering and submission remain pending. TASK-037 and TASK-039 call for the education lead's D-08 and D-09 decisions. The decision needed for this path is which assessment activities a learner may open and submit for the offered release, and the applicable task formats, assistance, attempts, deadline and answer-reveal rules. The current release schedule covers text and media and does not express that approval. No managed assignment browser run was performed.

The grading binding now requires exactly one retained activity row for the assignment. The PostgreSQL fixture verified that valid issuance retains the exact release identity and digest, and that the binding rejects a synthetic duplicate activity. This check uses a stubbed release resolver for the malformed snapshot. The focused `test_grading_postgres.py` and `test_managed_release_schedule.py` run finished with **38 passed in 5.74 seconds**. An initial test-double error was corrected by supplying the assessment policy ID. Ruff and Git whitespace checks passed.

## Connected Mac demo runbook (18 November 2026)

Use a disposable PostgreSQL/pgvector database and a separate Redis namespace. Start the API on `localhost:1320` and Next.js on `localhost:3020`, using the split-origin setup above with these ports substituted. Confirm both health endpoints and the organization lookup before sign-in. Provision a fresh synthetic learner and grant for each rehearsal so previous attempts and retake rules do not affect the result.

1. Use the signed PostgreSQL adaptive curriculum fixture as the base. Keep its reviewed numeric diagnostic, practice-only item, separate practice mapping and exact release entitlement. Add the native chapter order before capturing the release. Include a separate unpublished text lesson in the synthetic D-09 text schedule for the PDF-authored material.
2. Enable `LEARNHOUSE_STUDYTOMY_GROUNDED_DRAFTS_ENABLED=true` for authoring. With a signed synthetic teacher session, upload a small searchable PDF to that lesson's `/draft-sources` endpoint. Save a hand-written paragraph through `/grounded-lesson-draft` using the actual receipt UUID, SHA-256, page and exact quote. Confirm `/grounded-lesson-sources` reports `current`. In this rehearsal the PDF said “Two quarters equal one half.” Apply the synthetic reviewed release fixture after saving, with the PDF lesson, diagnostic lesson and practice exercise in the same frozen course snapshot. Verify `grounded_source_evidence` contains the PDF lesson and exact citation identity. Use the resulting database only for the loopback demo.
3. Sign in as the synthetic learner and open the reviewed course from **My learning**. Open **Reviewed PDF fractions lesson** and read the retained paragraph. Return to the same release's lesson list, open the mapped diagnostic lesson and submit `0` for the synthetic numeric question. The server summary should show zero of one correct.
4. Return to the same release's **Reviewed practice exercise** and submit `19.25`. The summary should show completion. Open **Concept progress** and verify that supported practice is distinguished from independent mastery. The managed panel sends the exact activity, course and release on its mastery request. Suggestions that link to native pages remain confined to the native viewer.
5. Compare persisted evidence. Both attempts must identify the same learner, course and release. The diagnostic observation should be independent and mastery-eligible. The correct practice observation should be assisted and ineligible for independent mastery. Confirm the progress watermark reflects the latest retained observation. A signed query with an unrelated release UUID should return a private 404.

**Observed on the working tree based on `3baf586ffb88a9cf7156cd3bfd41ff5478e37436`.** The temporary fixture used the signed mounted API for PDF upload (201), draft save (200) and current citation read (200), followed by fixture review and release. The staff authoring and release review steps were fixture preparation. The learner steps ran continuously in one 390 × 844 Chromium session against live endpoints with no interception. Both attempt starts returned 201, both answers returned 200, and the progress read returned 200 with assisted evidence. PostgreSQL matched the retained PDF bytes, source digest, page and frozen lineage, both attempt bindings, and the final mastery watermark. The wrong-release probe returned private no-store 404. No uncaught page errors were observed. TypeScript and three adaptive UI tests passed. Scoped ESLint reported no errors and one existing warning.

The browser evidence covers the released PDF-authored text and concept progress. The synthetic review does not establish staff approval UI acceptance. Dependent guardian disclosure, full managed native assignment delivery, and provider-backed drafting remain outside this rehearsal. For separately authorized provider testing, use the API environment contract in the Mac provider-free PDF authoring section and supply credentials through the approved secret channel.

### Connected demo staff review and release follow-up

The **Content review** workspace now offers **Record reviewed release** for the exact approved candidate. It runs the existing server preflight, then records the release and displays its returned reference. The server rechecks current authority, review, evidence and policy before retaining the snapshot.

On the working tree based on `f174cdd5`, a separate loopback database cloned the connected demo course and PDF lineage. The fixture supplied separate synthetic author and reviewer identities, exact course review scopes, and synthetic D-13 retention and D-15 PDF evidence settings. At 390 × 844, Chromium signed both in against `localhost:3020` and `localhost:1320`, then dismissed the reviewer's first-run welcome modal. The reviewer uploaded separate rights and accessibility PDF attachments in **Content review**, each returning 201. The signed course-author approval request returned 403. The reviewer selected the two attachments, confirmed both findings, recorded approval (201), and used the release control. Preflight returned 200, release returned 201 and the UI showed the receipt. Retrying the record endpoint returned the same receipt. No browser responses were intercepted in this rehearsal and no uncaught page errors were observed.

PostgreSQL confirmed one release for the new review, an exact snapshot digest, distinct author and reviewer, the correct attachment purposes, and the original PDF's UUID, digest and page in the retained citation lineage. The native course stayed private and unpublished. The connected learner rehearsal above used a separate fixture grant. The new staff-recorded release did not receive a learner grant during this check.

The focused content-evidence suite passed **19 tests in 4.71 seconds** with the local PostgreSQL cases enabled. The web review unit test, TypeScript, scoped ESLint and Git whitespace checks passed. The intercepted content-review Chromium regression separately passed in **4.7 seconds**, including the preflight-before-record sequence. Real approval decisions and live provider generation remain outside the demo.

### Staff-recorded release to learner handoff

On the working tree based on `a34b7acd78d247e9dc5e2321cd1775791f90c61f`, the same isolated staff-review database served API `localhost:1320` and web `localhost:3020`. Use the release UUID and review digest returned by **Record reviewed release** in the preceding staff browser run. Resolve that retained review and course, then use `persist_authorized_course_grant` to grant the exact release to a fresh synthetic independent learner with the existing eligibility and consent fixture. Use a stable synthetic source reference so repeating the grant request returns the same row. The grant and learner approvals were fixture setup. The first rehearsal account already had a completed diagnostic for the same blueprint and received the expected retake denial, so a fresh learner was used without changing the rule. The fresh learner also received a grant to the prior release as a revocation control.

When releases on the same catalog page share a course name, **My learning** now displays each release reference. In 390 × 844 Chromium, the learner selected the staff receipt reference, opened its PDF-authored text lesson, completed the diagnostic and supported practice, and opened **Concept progress**. The live requests used the staff-recorded release throughout. The two starts returned 201 and the two answers returned 200. Replaying each start and answer with its original idempotency key succeeded. The diagnostic showed zero of one correct and practice showed completion with assisted evidence separated from independent mastery. No responses were intercepted and no uncaught page errors were observed.

PostgreSQL verified the exact staff review, distinct author and reviewer, the release digest and retained PDF source UUID, digest and page, one exact grant, two completed attempts and one retained evidence row per attempt. The mastery watermark matched the last observation. A targeted PostgreSQL fixture update revoked only the staff-release grant. Its saved lesson, both summaries and its progress endpoint returned private no-store 404. Reloading preserved the separately granted earlier release and cleared the revoked release's progress view.

TypeScript, scoped ESLint and Git whitespace checks passed. The focused mixed-format Chromium spec passed in 3.8 seconds and separately verifies selection by release reference using intercepted catalog responses. Provider-backed generation, full managed native assignments, dependent guardian disclosure and real business approvals remain outside this local rehearsal.

## Environment

- Windows `10.0.26300.0`; PowerShell `7.6.9`; Bash used for the repository shell script.
- Bun `1.4.4`; web ESLint `9.39.5`.
- API virtual environment Python `3.14.7`; pytest `9.2.0`; uv `0.12.10`.
- E2E lockfile-resolved `@playwright/test` / Playwright `1.64.1`; TypeScript `6.0.3`. Chromium build `1248` was present in the local Playwright cache.
- Docker client/server `29.9.3`; disposable loopback PostgreSQL `16.16` with pgvector `0.8.7`.

## Executed checks

| Scope | Command | Result |
|---|---|---|
| Web unit suite | `bun run test` from `apps/web` | **PASS:** 735 tests passed, 0 failed across 75 files; 2,042 expectations. |
| Web editor regression rerun | `bun test --isolate ./tests/dynamic-content.test.mjs` from `apps/web` | **PASS:** 31 passed, 0 failed. |
| E2E TypeScript | `bun run typecheck` from `apps/e2e` | **PASS:** `tsc --noEmit`, exit 0 after frozen-lockfile resolution. |
| Lockfile consistency | `bash scripts/lockfiles.sh --check` from repository root | **PASS:** all Bun and uv lockfiles reported current; no tracked lockfile changes. `.gitattributes` keeps this script LF so Bash can execute it in a Windows checkout. |
| PostgreSQL integration | `python -m pytest -q src/tests/studytomy/test_checkout_router.py -m studytomy_postgres` from `apps/api`, with a temporary `STUDYTOMY_TEST_DATABASE_URL` to the disposable Docker database | **PASS:** 11 passed, 1 deselected in 132.20s. Target was loopback `127.0.0.1:60607`, database `studytomy_aud03_test`; no development or production database was targeted. |
| Lint failure propagation | `bun run lint -- --codex-intentional-invalid-flag` and `bun run lint:fix -- --codex-intentional-invalid-flag` from `apps/web` | **PASS:** both intentionally invalid invocations returned exit 2; the script no longer masks ESLint failures. |
| Full strict lint | `bun run lint:strict` from `apps/web` | **FAILED / existing baseline issues:** exit 1 with 6 errors and 976 warnings. The six errors are in untouched files: two `React` `no-undef` errors in admin layouts, two `no-useless-escape` errors in `PasswordStrengthIndicator.tsx`, and `React` `no-undef` errors in `GeneralWrapper.tsx` and `ClientComp.tsx`. They were not changed to make this tooling task pass. |

The first full web-suite run overlapped with strict lint and PostgreSQL checks: 731 passed and 4 editor tests timed out at 5 seconds. The named editor file passed alone, and a subsequent full run without competing checks passed 735/735. No timeout or assertion was weakened.

## Not run or unverified

- **Next.js production build:** skipped because the local development server was listening on port 3000 and shares `.next`; a concurrent build could collide with its output.
- **Unmocked browser journey:** not run in this checkpoint. The local acceptance path remains unverified; an earlier E2E attempt stopped during setup with `401 INVALID_CREDENTIALS` before scenario assertions. The installed browser binary does not establish application acceptance.
- **Live AI/provider checks:** not run; no live-provider result is inferred from mocked/unit coverage.
- **Production deployment, production migrations, or real learner activation:** not run or authorized by this verification record.

A preliminary PostgreSQL test invocation used the wrong relative path and ran zero tests. The corrected command and its 11-pass result are the only PostgreSQL result counted above.

## Focused audit follow-up — AUD-13 and AUD-15

- **Recorded:** 2026-11-18
- **Source revision tested:** `7d5526d983f09141556d2d3a1b546821fdaca862`
- **Database:** Disposable Docker PostgreSQL 16.16 at loopback `127.0.0.1:60607`, database `studytomy_aud03_test`, already at the current migration head. PostgreSQL fixtures used only `STUDYTOMY_TEST_DATABASE_URL`; no normal development or production database was targeted.

| Scope | Command | Result |
|---|---|---|
| Report scheduler unit | `uv run --no-sync python -m pytest -q src/tests/studytomy/test_report_delivery_scheduler.py` from `apps/api` | **PASS:** 5 passed. |
| API lifecycle wiring | `uv run --no-sync python -m pytest -q src/tests/core/test_core_events.py -k startup_and_shutdown_app` from `apps/api` | **PASS:** 1 passed, 21 deselected. |
| Outbox lease, authority, outcomes and lifecycle | `uv run --no-sync python -m pytest -q src/tests/studytomy/test_concurrency_postgres.py -m studytomy_postgres` from `apps/api` | **PASS:** 8 passed, 5 deselected; includes the scheduler polling a synthetic PostgreSQL outbox row without a manual batch call. Provider and authorization callbacks were synthetic. |
| Outbox retry/reconciliation | `uv run --no-sync python -m pytest -q src/tests/studytomy/test_wave1_operations.py -k outbox` from `apps/api` | **PASS:** 23 passed, 73 deselected. |
| Managed release learner/report workflow | `uv run --no-sync python -m pytest -q src/tests/studytomy/test_adaptive_curriculum_api.py -m studytomy_postgres -k drafts_round_trip_on_postgres` from `apps/api` | **PASS:** 1 passed, 1 deselected. This is an ASGI/PostgreSQL synthetic integration test; report email is mocked. |
| Assessment finalization, freeze, appeal and retry archive | `uv run --no-sync python -m pytest -q src/tests/studytomy/test_grading_postgres.py -m studytomy_postgres` from `apps/api` | **PASS:** 6 passed, including concurrent finalization and late-save serialization. |
| Native retry, manual grading and certificate edge paths | `uv run --no-sync python -m pytest -q src/tests/routers/test_assignments_retry.py src/tests/services/test_assignment_manual_grading.py src/tests/services/test_certification_edge_paths.py` from `apps/api` | **PASS:** 29 passed on the SQLite unit-test fixture. This is not PostgreSQL evidence. |
| Managed historical award retention | `uv run --no-sync python -m pytest -q src/tests/services/test_certifications_service.py -k managed_template_and_automatic_revocation_preserve_award` from `apps/api` | **PASS:** 1 passed, 71 deselected on the SQLite unit-test fixture. |
| API lint | `uv run --no-sync ruff check src/services/studytomy/report_delivery_scheduler.py src/tests/studytomy/test_report_delivery_scheduler.py src/tests/studytomy/test_concurrency_postgres.py` from `apps/api` | **PASS:** all checks passed. |
| Report-review UI checks | `node node_modules/eslint/bin/eslint.js components/Studytomy/StaffReportReview.tsx`; `node node_modules/typescript/bin/tsc --noEmit` from `apps/web` | **PASS:** both exited 0. |

The report scheduler remains default-off, and D-04, D-12, D-13, D-14 and D-18 approval records/dates remain unverified. No email provider call, browser acceptance, full suite, production migration or real-learner activation was run. This focused evidence does not substitute for AUD-21's integrated acceptance gate.

At `d83ac01282dd0f527f8a9c40cabcd7771762b4ee`, a disposable SMTP sink bound to an ephemeral loopback port accepted one synthetic report notification from the existing SMTP adapter in single-attempt mode. The adapter returned the intended recipient and no provider message ID. The sink decoded the message and verified its recipient, subject and sign-in link text. The probe used unauthenticated local SMTP and neither relayed mail nor exercised TLS, an external provider, or an actual outbox dispatch.

At `c60238a865921425a3b31daa95db164394e68354`, the authorized report fixture used the actual scheduler, report revalidation, PostgreSQL outbox and SMTP adapter. With the scheduler explicitly opted in for that process, one queued synthetic report reached `sent` after one loopback SMTP handoff. The sink verified the authenticated report link and the absence of the synthetic learner's name and score in the message body. The probe used the disposable `studytomy_aud03_test` database, a synthetic HTTPS link origin, and a loopback SMTP sink with no external relay. The fixture passed **1 test, 1 deselected** in 77.34 seconds. Two preceding probe attempts failed on a missing import and a fixture-held organization lock. The passing run committed the fixture's direct revalidation transaction before starting the independent scheduler session. Live provider, TLS and approved-pilot delivery remain outside this rehearsal.

## AUD-14 discovery-to-checkout trace

- **Recorded:** 2026-11-18
- **Source revision inspected/tested:** `d49838fa3584f0510112a90e606bddda12d1f8b0`
- **Scope result:** API quote, draft, accept, purchase-list and receipt contracts exist in `apps/api/src/routers/studytomy/commerce.py:146-207`. The OSS `/store` and offer routes re-export Enterprise `StorePage`/`OfferPage`; OSS `OfferCard` and Enterprise purchase stubs render `null`. The custom `StudyTomyPurchases` component reads only the purchaser's order list or receipt; it does not call the quote, draft or accept APIs. No OSS path currently connects a catalog selection to these checkout APIs.
- **Server gates:** `orders.py` requires an exact D-17 pilot offer version, D-18 approved customer terms, and current D-01/D-03/D-05/D-17/D-18/D-20 policy versions at acceptance. Server quotes calculate prices, bind named learners and overlap acknowledgment, and are rechecked before acceptance. Nonzero orders use the staff-led manual collection path; receipt status is `awaiting_collection` or `pending_collection` until confirmed. The checkout API test uses synthetic policy fixtures only.
- **Check:** `uv run --no-sync python -m pytest -q src/tests/studytomy/test_checkout_router.py` from `apps/api` with the dedicated loopback PostgreSQL URL — **PASS:** 12 passed, including the fail-closed missing-approval/client-price case and 11 PostgreSQL checkout/collection scenarios.
- **Boundary:** No checkout UI or catalog endpoint was added. AUD-14 remains approval-gated until the named decision-makers approve the exact offer/version, claims, price/access/allocation, customer terms, discount/sample handling and chosen staff-led or self-service path. The draft course-only guidance is not approval. No browser purchase, actual payment, external provider or learner activation was tested.

## AUD-16 support and safeguarding verification

- **Recorded:** 2026-11-18
- **Source revision tested:** `320b80ebaaefee6682fba09fa436af43af4a82aa` (source files were unchanged by the later documentation entry).
- **Database:** Dedicated loopback Docker PostgreSQL 16.16 at `127.0.0.1:60607`, database `studytomy_aud03_test`; tests used only `STUDYTOMY_TEST_DATABASE_URL`.

| Scope | Command | Result |
|---|---|---|
| Support and safeguarding case persistence, ownership and concurrency | `python -m pytest -q src/tests/studytomy/test_service_cases_postgres.py` from `apps/api` | **PASS:** 5 passed. Covers concurrent safeguarding assignment, persisted approval and exact case-grant routing, support replay/owner handoff/recovery, and restricted safeguarding narrative access using synthetic policies and identities. |
| Support after course access expiry | `python -m pytest -q src/tests/studytomy/test_checkout_router.py -m studytomy_postgres -k signed_checkout_accepts_only_retained_approved_terms` from `apps/api` | **PASS:** 11 passed, 1 deselected. Includes the integration assertion that an assigned support case can be opened/read while the paid lesson remains inaccessible. Synthetic policy and checkout records only. |

- **Verified controls:** The API requires authenticated organization membership for support intake and checks approved policy/staffing at the service boundary. Named case subjects are resolved inside the organization and excluded from owner selection and handoff. Safeguarding narratives require the exact assigned case grant; reporters, excluded subjects and unassigned staff receive not-found responses. Support remains available independently of paid lesson access in the tested integration path.
- **AUD-16 gap:** `StudyServiceCase` persists reporter, owner, topic, status and version; it has no staff-authored next action, correspondence log or minimal external-record reference. The UI's next-step text is a static status description, not a persisted accountable action. No support-case notification or outcome-communication path was found in the inspected routes/services. The repository does not establish an approved external record arrangement, so adding case fields or notifications would be speculative and is deferred.
- **Safeguarding gate:** The tests cover synthetic named-subject exclusion and exact case access; they do not establish guardian relationship policy, confidential routing for an accused guardian/staff member, or notification recipient exclusions for a real service. AUD-02 and the current approved safeguarding/support records remain unresolved; real-user intake stays disabled. No approval, response target, or emergency claim was inferred.
- **Browser limitation:** The existing mobile Playwright case journey was not run. Its local loopback app/API prerequisites were unavailable (ports 3000 and 8000 were closed); its default global setup would otherwise start a separate `learnhouse@latest` instance. Therefore UI/browser acceptance, production support operations, correspondence, outcome delivery, and real-user guardian handling remain unverified.

## AUD-17 grounded-material retrieval verification

- **Recorded:** 2026-11-18
- **Source revision tested:** `beab8002f40d42e87006eb14e94f45e9d344e6d4`.
- **Database:** Dedicated loopback Docker PostgreSQL 16.16 with pgvector 0.8.7 at `127.0.0.1:60607`, database `studytomy_aud03_test`; tests used only `STUDYTOMY_TEST_DATABASE_URL`.

| Scope | Command | Result |
|---|---|---|
| Lesson-source boundaries, later-page excerpts, revocation, stale citations and unsupported extraction | `python -m pytest -q src/tests/studytomy/test_grounded_material_postgres.py src/tests/studytomy/test_grounded_retrieval_postgres.py -m studytomy_postgres` from `apps/api` | **PASS:** 4 passed. Synthetic providers, identities, sources and vectors; no live model call. |
| API lint | `uv run --no-sync ruff check src/services/studytomy/grounding.py src/tests/studytomy/test_grounded_material_postgres.py src/tests/studytomy/test_grounded_retrieval_postgres.py` from `apps/api` | **PASS:** all checks passed after the bounded `beab8002` lint corrections. |
| Grounded-source UI static checks | `node node_modules/eslint/bin/eslint.js components/Studytomy/GroundedLessonSources.tsx`; `node node_modules/typescript/bin/tsc --noEmit` from `apps/web` | **PASS:** both exited 0. |

- **Measured evidence:** The existing synthetic comparison reports mean Recall@2 of 0.75 for exact authorized cosine, 0.50 for English full-text, and 1.00 for reciprocal-rank fusion over four hand-authored queries; the fused and exact lists returned no ineligible rows in that fixture. A separate selective-scope fixture demonstrates underfill from fixed overfetch and filtered HNSW, while exact authorized ranking returns all three eligible lesson rows. These small synthetic vectors test ranking mechanics, not production retrieval quality or latency.
- **Current behavior and deviation:** Full extracted pages are examined through bounded overlapping windows and at most six excerpts are sent to the model. A different-vocabulary request without lexical support returns an actionable insufficient-evidence error and skips the provider; synonym recall is not established. Citation UUID/hash/page/quote checks and access revalidation remain in place. Hybrid RRF is evaluation-only, and the exact small-course runtime option remains off by default; no vector model or runtime retrieval mode was changed in this slice.
- **Limits:** Prompt-like source text is passed as quoted data with a system instruction to ignore embedded instructions, but provider behavior is mocked. No OCR, real-provider factual support, representative educator judgments, browser acceptance, production recall/latency, learner activation, or launch approval was verified. See [the detailed synthetic evaluation](GROUNDED-RETRIEVAL-EVALUATION.md) for fixtures and measured limits.

At `a810bfc3cd6215b37a6c22eca03c00c56fc57a44`, a loopback Chrome component probe mounted the actual grounded lesson draft form and intercepted only the preview API with a synthetic two-page omission. The accessibility snapshot showed the retrieval warning as a status message, alongside the cited quote and unpublished save control. The full Next.js editor spec did not reach its new assertion. The existing local browser session failed to load the editor controls, and a temporary mocked-session probe redirected to sign-in. This verifies warning rendering with a synthetic response and leaves authenticated browser acceptance open.

At `331d6d168c93b73c54692b4bda820e41de785ad1`, the amended editor spec passed in Chromium with **1 passed** in 1.1 minutes. A temporary build directory used the committed Next.js source and a fresh synthetic admin sign-in against the disposable loopback API. The spec verified the omission warning, preserved text after a simulated stale save, and verified reload after a successful simulated save. The original server was serving a build that predated the warning. The first fresh-build run reached the warning but exhausted the existing 15-second expectation during the subsequent editor reload. The rerun used the same readiness allowance as initial navigation. The test intercepts editor and grounded-content API responses, so real provider quality and end-to-end grounded save remain outside this run.

## AUD-05 managed media browser and filesystem checks

The captioned-video checkpoint based on `f5ad821bc5f93c496cda58938a6c571f75c73882` passed the live mobile Chrome spec (**1 passed** in 36.0 seconds). The selected fixture used a synthetic four-second MP4, a retained English WebVTT track, named synthetic policy fixtures and a fresh first-use grant. Chrome used the real Next.js account screen and FastAPI routes, with Docker PostgreSQL and Redis on loopback. After first-use activation through the reviewed PDF, Chrome received the retained captions (200) and ranged video (206), observed the default track showing a cue, played the clip and sought to its midpoint. No browser API responses were intercepted. The synthetic checkout fixture preseeded the release. Independent review, caption mutation rejection and retained-byte verification ran separately in the API workflow suite. The combined checkout, content-evidence and staging suite passed **37 tests** with its PostgreSQL cases enabled, while the independent review workflow used its SQLite fixture.

To include video in the live spec, set `E2E_MANAGED_MEDIA_CAPTION_CODE` to the retained track's language code alongside the existing learner state and release variables. The fixture needs a playable local video and a valid caption track retained in the same release. Current D-15 must authorize both media formats and their sizes.

At `bdd4ddf0bc9a5bc776c921b6bac5ab38125d1a00`, the live mobile Chrome session-loss case passed (**1 passed** in 1.1 minutes). It reused the activated synthetic grant with the real Next.js account screen and FastAPI backed by disposable Docker PostgreSQL and Redis. The PDF, video and caption URLs returned 206 with the learner cookies. After clearing cookies and reloading, the iframe and release button were absent, and each saved URL returned 401. The test cleared client credentials directly. The separate account-switch test exercises the sign-out UI.

At `206be0b3d8698611b3f9b5bca36d0689059e382b`, the live mobile Chrome grant-expiry case passed (**1 passed** in 48.6 seconds). It reused the same persisted synthetic grant and advanced only the managed-access service clock to one second beyond the stored expiry. The browser remained authenticated and no API responses were intercepted. The active catalog omitted the course, approved access history showed **Expired**, and the exact lesson index and saved PDF, video and caption requests returned 404. The test used the stored grant timestamps without changing them.

For the expiry check, provide the expired fixture's saved PDF and video activity IDs via `E2E_MANAGED_MEDIA_EXPIRED_PDF_ACTIVITY` and `E2E_MANAGED_MEDIA_EXPIRED_VIDEO_ACTIVITY`, and select it with `bun run test 14-managed-release-media-live -g 'keeps expired access history'` from `apps/e2e`. Use a signed-in fixture with D-12 access history and D-13 retention approved. The expiry-only case runs separately from the pending-first-use journey.

At `d0b203547a8001947da4167fcd96f1b077f475d0`, the mobile Chrome run of `14-managed-release-media-live.spec.ts` passed (**1 passed** in 32.7 seconds) with no intercepted API requests. The exact revision ran in a temporary Next.js runtime copy alongside the full FastAPI app, retained local PDF, PostgreSQL on `127.0.0.1:51237` and Redis on `127.0.0.1:51238`. The checkout fixture accepted the synthetic free order and consent before the browser received the learner session. Chrome listed the release, saw the pending first-use notice, received 206 on first-use POST and 200 on the iframe PDF GET, then returned to the activated lesson list. Hosted video and unsupported audio remained disabled. An unrelated assessment-integrity request returned 403 for this synthetic session. The successful check covers PDF delivery and iframe visibility. The earlier headed fixture below separately covered PDF viewer rendering.

For a repeat run, point `E2E_BASE_URL` and `E2E_API_URL` at a ready loopback stack. Seed a fresh pending first-use grant using the `free-release-media` PostgreSQL fixture shape, use the fixture organization as the stack's default, and provide its synthetic learner storage state and release title via `E2E_MANAGED_MEDIA_LEARNER_STATE` and `E2E_MANAGED_MEDIA_RELEASE_NAME`. Run `bun run test 14-managed-release-media-live` from `apps/e2e` with the local stack configuration. The targeted rehearsal used a temporary config without shared-suite setup. Wait for the Next.js route to compile before starting the run. Earlier attempts encountered server locks, a loopback hostname mismatch, and a cold compile exceeding the browser budget. Use a fresh grant for each run because the browser activates it.

At `adfc76e1a639c6b63083b2987069d2b90315dd6b`, a temporary Uvicorn fixture served the mounted managed-media route against the disposable PostgreSQL restore and retained local PDF. A synthetic learner used an HTTP-only session cookie. Headed Chrome displayed the PDF in an iframe, with both page thumbnails and the viewer's two-page count visible. The fixture then completed its remaining test assertions with **1 passed, 11 deselected** in 89.09 seconds. The console's single 404 was for the fixture site's favicon. This used a minimal same-origin HTML host after first-use activation through the test API client. The Next.js lesson navigation and accessible video playback remain outside this check.

The `free-release-media` fixture originally returned from the free checkout branch before reaching its detailed media assertions. Earlier results for that selection do not establish execution of the retained PDF, range, corruption, policy-change or staging-lock checks. The corrected fixture runs those assertions directly after free checkout and consent. Its expiry case advances the service clock past the grant end while storage is paused, preserving the database's immutable access window. The focused PostgreSQL rerun passed 2 tests with 10 deselected in 21.08 seconds, covering the corrected media fixture and free re-entry.

- At `d9f57753f8d79d98f407e29378dc7348d1bc93aa`, Playwright type checking and the mobile Chromium spec `13-managed-release-media.spec.ts` passed (**1 passed** in 36.3 seconds). The isolated frontend used real synthetic sign-in against a loopback API with disposable PostgreSQL and Redis. The spec intercepted the release list, lesson index and first-use media response. It verified the mixed-format list, unavailable video and audio states, PDF frame URL without a token, and return to the activated lesson list. Two earlier runs found incorrect fixture assumptions about the organization UUID and the post-activation label.
- At `2e77df3b67a70a47d8981b7bddce4cc364695c0f`, the PostgreSQL `free-release-media` test passed (**1 passed, 11 deselected** in 18.28 seconds). It exercised authenticated PDF delivery and byte ranges using the filesystem adapter with a temporary content root. The delayed and corrupted streams used for fault injection were simulated.
- The earlier PDF-only run left accessible video playback and seek outside its scope. The captioned-video checkpoint above covers those steps.
- At `823967491529d48652fbed341a5c38585687107b`, the mobile Chromium test passed again (**1 passed** in 26.8 seconds), with TypeScript checking also passing. The PDF frame requested the real API using the learner session. The API returned `404 Course release unavailable` because the intercepted release does not exist in the database.
- At `f0067774ec06d2370ae262898842660bcd4177cc`, the media endpoint rejected both direct video reads and first-use video requests before opening storage, matching the lesson index. The checkout and staging selection passed (**7 passed, 11 deselected** in 11.09 seconds) with disposable Docker PostgreSQL and a temporary filesystem for the retained PDF. The immutable release format still lacks retained captions or an approved alternative. An accessible video release needs a reviewed alternative tied to the exact release and evidence that the browser can deliver it.

- At `4d03faf9e1d4c1d3d658f3d1364e75f28abc87d1`, the combined content evidence, checkout and managed media staging selection passed **31 tests** in 71.52 seconds with disposable Docker PostgreSQL. It covers missing PDF recovery without consuming first use and caption dependency rejection at review. The review case injects the caption dependency into the candidate snapshot. The prior combined run failed in free re-entry because a branch-local import shadowed the shared storage fixture import. The corrected run passed both branches.
- At `9c0a7e0ee03841178c6b485b0da7aa5c70e132b5`, Playwright type checking passed and `07-content-review.spec.ts` passed in Chromium. With real local sign-in and simulated review responses, the panel focused the unsupported-media message and showed no approval after rejection. Successful delivery and accessibility for a captioned release remain open.

## AUD-06 managed-media lock and staging verification

- **Recorded:** 2026-11-18
- **Source revision inspected/tested:** `0dc4bdb24e6363d575d59900ef6dbca90a2c3163` (service/test source was unchanged by subsequent verification-only commits).
- **Database isolation:** Dedicated Docker PostgreSQL 16.16 / pgvector 0.8.7 at loopback `127.0.0.1:60607`, database `studytomy_aud03_test`, selected solely through `STUDYTOMY_TEST_DATABASE_URL`.

| Scope | Command | Result |
|---|---|---|
| Media staging timeout, cancellation, oversize and truncation cleanup | `python -m pytest -q src/tests/studytomy/test_managed_media_staging.py` from `apps/api` | **PASS:** 4 passed. Each failure path closes the temporary file; timeout and integrity errors fail with 503. |
| PostgreSQL managed release media/range/lock checks | `python -m pytest -q src/tests/studytomy/test_checkout_router.py -m studytomy_postgres -k signed_checkout_accepts_only_retained_approved_terms` from `apps/api` | **PASS:** 11 passed, 1 deselected. Includes storage staging while another session acquires the organization lock, grant expiry/revocation during staging, policy-version change, first-use concurrency, range edges, repeated ranges and staged-file cleanup. |

The implementation snapshots the reviewed release/media/policy, commits before opening and hashing storage, then reacquires current authority and rechecks the exact grant, release and policy before any bytes are returned. Repeated-range assertions record complete-object bytes read on each range request; no cache was introduced because the suite provides no representative production size/latency evidence to justify a cache. Storage and policy changes were synthetic, and no real S3, slow-network or production workload was exercised. No schema change or production migration was made.

## AUD-01 current-code managed-course characterization

- **Recorded:** 2026-11-18
- **Source revision tested:** `8649e57e9291ab1bc48aa7b89cf7299c8b2a913e`.
- **Database isolation:** Dedicated Docker PostgreSQL 16.16 / pgvector 0.8.7 at loopback `127.0.0.1:60607`, database `studytomy_aud03_test`; `STUDYTOMY_TEST_DATABASE_URL` pointed only to this `_test` database. The fixture guard rejects runtime URL fallback, remote hosts, non-test databases and routing parameters.

| Scope | Command | Result |
|---|---|---|
| PostgreSQL fixture target guards | `python -m pytest -q apps/api/src/tests/studytomy/test_concurrency_postgres.py -k 'runtime_database_variables_are_not_used_as_test_fallbacks or postgres_test_url_rejects_nonlocal_or_non_test_targets'` | **PASS:** 5 passed, 8 deselected. |
| Managed curriculum integration characterization | `python -m pytest -q apps/api/src/tests/studytomy/test_adaptive_curriculum_api.py -m studytomy_postgres -k drafts_round_trip_on_postgres` | **PASS:** 1 passed, 1 deselected in 63.54 seconds. Uses synthetic staff, learner, payer, guardian, policies, private managed course, reviewed frozen release, grant and curriculum/diagnostic data. |

The audit’s older managed-diagnostic denial is no longer current: the existing integration fixture already exercises the exact reviewed-release branch. It verifies the managed course remains `public=False` and `published=False`, the granted learner resolves the exact frozen release and a diagnostic bound to it, incomplete course/release scope returns 404, a lesson outside the release returns 404, and the exact release path can issue an attempt with frozen source provenance. The fixture therefore records current behavior and the intended later integration without restoring the stale denial. Synthetic approvals do not constitute real approval or activate learners. No production migration or real service was used.

### Assignment reconciliation at `7def49a02115f26d198c6311c99e326b017d5eef`

Reviewed the 164,334-byte independent audit with SHA-256 `2e5b37a45491c2f572dc4a22ee88e81214fae7483d138a25b93fea956530c801` and baseline `210cb561`. The only worktree was the primary `main`, with a clean index and working tree. Reused the existing AUD-01 fixture and current lockfiles (`apps/api/uv.lock`, `apps/collab/bun.lock`, `apps/web/bun.lock` and `apps/e2e/bun.lock`).

| Scope | Local command | Result |
|---|---|---|
| AUD-01, AUD-03, AUD-04 and dependent evidence and outbox checks | From `apps/api`, `python -m pytest -q --tb=short src/tests/studytomy/test_adaptive_curriculum_api.py src/tests/studytomy/test_adaptive_evidence_postgres.py src/tests/studytomy/test_concurrency_postgres.py` | 21 passed, 0 failed, 0 skipped in 137.41 seconds. Covers the managed-private database guard, authorized release-scoped adaptive work, immutable evidence, concurrent requests, fixture target validation, and outbox claims. |
| AUD-06 staging cleanup | From `apps/api`, `python -m pytest -q --tb=short src/tests/studytomy/test_managed_media_staging.py` | 6 passed, 0 failed, 0 skipped in 5.55 seconds. Uses synthetic storage and failure injection. |
| AUD-05 and AUD-06 checkout, media and organization lock | From `apps/api`, `python -m pytest -q --tb=short src/tests/studytomy/test_checkout_router.py -m studytomy_postgres -k signed_checkout_accepts_only_retained_approved_terms` | 11 passed, 0 failed, 0 skipped, 1 deselected in 79.70 seconds. Covers storage staging concurrent with a same-organization governance transaction, grant revocation and expiry during staging, policy change and media ranges. |
| AUD-07 and AUD-08 collaboration regression | From `apps/collab`, `bun run test` | 46 passed, 0 failed, 0 skipped across 9 suites. Uses synthetic adapters and local collaboration connections. |

The API suite used the existing disposable Docker database `studytomy_aud19_full_restore_test` at `127.0.0.1:60607`. A read-only probe confirmed PostgreSQL 16.16, pgvector 0.8.7 and database revision `b91d2f6a08c3`, matching the sole source Alembic head.

The [interim task matrix](IMPLEMENTATION-STATUS.md#independent-architecture-audit-checkpoint-2026-11-18-interim) tracks the remaining acceptance work. AUD-02 still needs named, dated decisions on cohort, credentials and recovery, commercial terms and safeguarding. AUD-20 still needs educator sign-off on the exact course, rubrics, accessibility alternatives and AI teaching samples. These runs did not exercise the full browser-to-API pilot journey or a live AI or email provider.

## AUD-07 and AUD-08 collaboration recovery verification

- **Recorded:** 2026-11-18
- **Source revision inspected/tested:** `4cd9097d834d6b7e2c576903ac90f263a6b79d17` (the collaboration source was unchanged by subsequent verification-document commits).

| Scope | Command | Result |
|---|---|---|
| Collaboration fault-injection and queue tests | `bun run test` from `apps/collab` | **PASS:** 43 passed, 0 failed across 9 suites. |
| Collaboration TypeScript build | `bun run build` from `apps/collab` | **PASS:** `tsc`, exit 0. Its generated `dist` outputs were removed after verification; no build outputs remain in the worktree. |

The load tests distinguish an explicitly marked empty board from Redis failure, API timeout, HTTP 503, malformed empty response and empty-present state; failed loads do not create/store a replacement document, and a later recovery loads the original content. The queue tests cover retry after non-2xx, persistence of the last edit without a further edit, per-board write ordering, retained state after bounded shutdown timeout/retry exhaustion, and aggregate memory limits. The shutdown handler was inspected: it logs unresolved board IDs and exits nonzero after its bounded flush deadline. That handler itself was not executed by the unit suite. These tests use synthetic adapters and in-memory Hocuspocus documents: live Redis/API outage recovery, deployment connector compatibility, and end-to-end multi-client recovery remain unverified. Existing authorization rechecks and read-only downgrade were left intact. No schema change or service was added.

### Loopback Redis recovery probe

At `4892e0f708caa033090239033b7a2f504f5d3bce`, a local Bun probe exercised the existing `loadBoardState` with a real Docker Redis connection at `127.0.0.1:51238` and an ephemeral loopback HTTP fixture. All assertions passed. A disconnected Redis client and HTTP 503 denied the load without creating a cache key. A subsequent HTTP 200 restored the exact synthetic Yjs text and warmed Redis. When the fixture returned to HTTP 503, a cache hit served the same bytes without another HTTP request. The probe removed its unique, 60-second test key.

The probe induced client disconnection without stopping Redis. The HTTP fixture served a synthetic snapshot. This verifies the Redis transport and loader recovery branch. Full FastAPI and Hocuspocus WebSocket recovery across multiple clients and a process restart remain open.

### Multi-client WebSocket recovery probe

At `a1804ed2c64b40ef491648f44023745a6497f3a4`, a loopback Node.js 24.19.0 probe used two Hocuspocus providers, the Hocuspocus server and database extension, and the repository's persistence queue and state loader. One client made a final synthetic edit and the other received it. The HTTP fixture returned 503 on the first write. The queue retried without another edit and reached zero pending writes. After both clients disconnected and the server instance was destroyed, a fresh instance and client reloaded the exact edit.

This exercised real WebSockets and HTTP with in-memory fixture storage and synthetic authentication. Both server instances ran in the same Node process. Node emitted a duplicate Yjs import warning from the separate web and collaboration dependency trees. The fixture did not exercise the full collaboration entry point, FastAPI membership, Redis, PostgreSQL or process-level restart.

### Collaboration process restart probe

At `25dc20b40892086de7c78c44495ac5c90f6e43f6`, a Node.js 24.19.0 probe launched the actual collaboration entry point in two successive loopback subprocesses. Two authenticated WebSocket clients shared a synthetic edit. The real Docker Redis instance at `127.0.0.1:51238` received the cache entry. The board HTTP fixture rejected the initial persistence request with 503 and acknowledged the retry without another edit. Both processes exited successfully through the application's SIGTERM handler. Between processes, the probe removed only its unique Redis key and verified that the new process requested the authoritative HTTP snapshot and reloaded the exact text.

The probe signed synthetic JWTs and served membership and board storage from an in-memory HTTP fixture. SIGTERM was emitted inside each child because this was a Windows run. The first run recovered the edit but hit a Windows libuv shutdown assertion while the test IPC channel remained open. Closing the IPC channel before emitting SIGTERM produced the passing run. Real FastAPI membership and database recovery across the collaboration restart remain open.

### PostgreSQL-backed collaboration restart

At `bebf3a731897a82e6a8b89bf8786a116f466c682`, the separate-process probe passed with the mounted FastAPI v1 board storage route served by Uvicorn and the disposable Docker PostgreSQL database `studytomy_aud19_full_restore_test` at `127.0.0.1:60607`. The probe used two WebSocket clients, real Docker Redis, and one transient 503 injected by a loopback proxy before the write reached FastAPI. The retry was acknowledged by the real storage endpoint. After a clean collaboration shutdown and removal of the synthetic Redis cache key, a new collaboration process fetched the persisted snapshot through FastAPI and reloaded the exact final edit. The temporary board and associated synthetic rows were cleaned up by the API fixture.

The membership response still came from the proxy fixture. The test substituted only the API database session dependency to point at the disposable database and used synthetic JWTs. Full membership authorization and abrupt-restart behavior during an unacknowledged write remain outside this probe.

At `08dc6a3e811e581f273c648542df45cc39436d2f`, the follow-up forwarded membership to the real mounted FastAPI route too. The API accepted a signed synthetic session for an organization and board member and denied a signed synthetic session for a nonmember. The two editor WebSockets, injected 503, successful PostgreSQL retry, Redis cache removal and fresh-process reload all passed again. Synthetic records were scoped to the disposable database and cleaned up on exit. This verifies graceful restart after acknowledgement. Abrupt termination during an unacknowledged write was outside the run.

### Abrupt collaboration restart before acknowledgement

At `fcfc821c4ef511bdd6872de97ab9f0bca829f53b`, the authorized two-client probe killed only the disposable collaboration child after Redis cached the edit and the loopback proxy rejected its first PostgreSQL-bound write with 503. No write was acknowledged. A replacement process admitted an authorized client and reloaded the exact edit from the surviving Redis key without a board storage GET. Over the next 6.5 seconds, exceeding the configured five-second flush debounce, the replacement process sent no database write. No further edit occurred. The probe cleaned up the synthetic database rows and Redis key.

This establishes cache-backed recovery for a collaboration-process failure with Redis still available. The Redis entry expires, and a cache load does not reconstruct the pending persistence queue. Durable replay of an unacknowledged edit after cache expiry, Redis loss or another process failure remains open.

The subsequent pending-snapshot change passed the same abrupt restart rehearsal on disposable Redis, FastAPI and PostgreSQL. After the original process was killed before acknowledgement, an authorized reconnect requeued the pending snapshot without another edit. PostgreSQL returned the exact restored Yjs text through the board API, and acknowledgement cleared the pending key. The marker has the existing one-hour cache lifetime. Replay requires a board load before expiration and surviving Redis state.

The concurrent Redis checkpoint based on `55b8a11f992b4e80e61bf5ff5a0fe150b60914ea` prevents one process from replacing another process's unacknowledged edit. Two real Docker Redis clients at `127.0.0.1:51238` were forced to read the same empty pending key before either update. The losing writer reread and merged both Yjs states. The final pending and cache entries matched, and the restored document contained both edits. The probe removed only its two synthetic keys. The collaboration suite passed **47 tests in 9 suites**, and the TypeScript build passed. This specific race probe exercised Redis and the production cache implementation. The PostgreSQL restart evidence above covers the durable handoff separately. Loss of both the process and Redis before PostgreSQL acknowledgement remains outside this recovery guarantee.

### Board load with both storage paths unavailable

At `0acae70484055d1a7df0feaa8425280b92e43305`, the loopback process rehearsal passed an AUD-07 dual outage. An authorized client first used an empty synthetic board, edited it and obtained PostgreSQL acknowledgement. With the test cache key removed, the next collaboration process targeted a closed local Redis port, and the fault proxy returned HTTP 503 for the board storage GET. The client remained unsynchronized and the process sent no board write. The final process used normal Docker Redis connectivity and the mounted FastAPI storage route. It reloaded the exact saved Yjs text from disposable PostgreSQL. All three processes used the real collaboration entry point and FastAPI membership route. The fault injection left the Docker services running and affected only the synthetic board.

## TASK-065 pilot cash dashboard

The checkpoint based on `f1b23051e6f7e6057c62f8886527a9598926df00` passed the live Chrome finance spec (**1 passed** in 1.5 minutes) at widths of 390 and 1440 pixels. It used Next.js, FastAPI and disposable Docker PostgreSQL and Redis on loopback. The paid checkout fixture seeded the offer, exact commercial scope, synthetic policy approvals and retained ledger. Scoped staff chose the offer and UTC reporting period, viewed the confirmed cash, and received a private, no-store response. The purchaser received an empty offer list and HTTP 403 on the same report URL. The test intercepted no API responses. The targeted paid PostgreSQL seed and verification passed with **1 passed, 11 deselected**. Targeted web lint and web and Playwright type checking passed.

To repeat the browser case, provide authenticated synthetic storage states with `E2E_PILOT_CASH_STAFF_STATE` and `E2E_PILOT_CASH_PURCHASER_STATE` on a ready loopback stack. Seed the paid checkout fixture within the report's prior-day window. From `apps/e2e`, run `bun run test 09-finance-review -g 'pilot cash report shows'` with the local stack configuration. This checkpoint covers confirmed ledger cash only. Broader pilot measures and their approval decisions remain open.

The follow-up based on `6a3a43ebf245c7af29f372f2686fc19d09bc45b1` verified the settled refund reason breakdown against the same scoped ledger. Docker PostgreSQL checkout tests passed (**2 passed, 10 deselected**), and live Chrome passed (**1 passed** in 3.4 minutes). The synthetic paid fixture produced two settlements for one recorded reason. Both the API breakdown and the amount by currency reconciled to the existing totals. The purchaser remained excluded. Ruff, targeted web lint, and web and Playwright type checking passed.

### Assessment evidence scope

The internal assessment projection can filter a curriculum revision by the exact course and frozen release recorded on the learning attempt. It counts current logical observations by assistance and outcome, with the number eligible for mastery shown separately. A superseded result contributes only through its current head. Retracted and assisted observations do not contribute to mastery eligibility. This is a source inventory for pilot measurement. It does not yet match starting and follow-up work or report a denominator of eligible learners. D-17 cohort and comparison decisions and AUD-20 educator acceptance remain open.

The checkpoint based on `7fd5fb8c3b8471ecf11c5f9d5ba047f95f9ded61` passed **7 tests** across `test_adaptive_evidence_postgres.py` and `test_adaptive_curriculum_api.py`. The managed release scope assertions ran in disposable Docker PostgreSQL. The SQLite workflow fixture retained its existing application assertions.

### Release access population

The internal projection counts distinct learner profiles with retained grants for the exact course and frozen release at a specified time. Overlapping grants count once. A current grant takes precedence over pending, scheduled, expired and revoked sources. These figures describe access records. Required-work completion still needs a published requirements version and an approved eligible cohort. The earlier course-level completion records do not identify the release requirements a learner met.

At the checkpoint based on `f6c951dd429c428b040dcea48260678a79922108`, all **19 tests** in `test_content_evidence.py` passed. The access population assertions used disposable Docker PostgreSQL and covered pending first use, deduplication across active grants, expiry and an unmatched release.

### Support resolution activity

The internal projection counts support cases first resolved during a half-open period, sums calendar seconds from case creation to those recorded resolutions, and counts reopening transitions and unique reopened cases separately. The source is the retained support transition journal. A case marked resolved without a retained transition contributes no timing evidence. Safeguarding data, service-hour compliance and response targets stay outside the projection.

At the checkpoint based on `17e20ff5cee66fe8f1f2ec5815fa9471b9d24c20`, all **6 tests** in `test_service_cases_postgres.py` passed on disposable Docker PostgreSQL. The report-routing scenario used actual support API transitions and checked the first-resolution boundary and later reopening independently. The backlog fixture checked that a status without journal evidence supplies no first-resolution time.

The operating report endpoint is `GET /studytomy/orgs/{org_uuid}/support/staff/operating-report`. It requires a direct staff session, the default-deny `support_operating_report` capability, an active support owner assignment with an exact `function` scope for `support`, and current D-04, D-15 and D-17 approvals. It returns period activity and the current backlog as aggregates, with no case IDs or narrative. Service-hour compliance measures remain open.

The endpoint checkpoint based on `af0abe17822706b71c1a6f960217c044ee82f48b` passed **115 API tests** across the support, staff-access, generic role-tool, setup role and role-service suites. Disposable Docker PostgreSQL exercised real support transitions and verified that a case assignment alone is insufficient, an exact support report grant returns private aggregates, and the reporter is denied. The role-tool regression also verified that editing a legacy role keeps prior grants and defaults the new permission to false.

The support workspace checkpoint based on `bc7ebe908bf42eda7e593206d16db6e25cc8d116` passed the live Chrome case (**1 passed** in 1.2 minutes) at 390 and 1440 pixels. Next.js and FastAPI ran against disposable loopback Docker PostgreSQL and Redis. Staff saw and refreshed the report alongside the case queue, while a synthetic reporter got 403 and no report panel. No responses were intercepted. The targeted PostgreSQL fixture passed (**1 passed, 5 deselected**), and targeted web lint plus web and Playwright type checking passed. The initial report covers the last seven calendar days as a display default. It does not establish a policy reporting cadence or a service-hour target.

## AUD-18 integrated local verification checkpoint

- **Recorded:** 2026-11-18
- **Full API source revision:** `cc3d88433f909a4114a2620d6b1cd2d5cf1f7395`.
- **Post-correction focused source revision:** `bdb54ab8723f6de7ca1a112e6ce8b8f04646c869`.
- **Database isolation:** API PostgreSQL-marked tests used a dedicated Docker PostgreSQL 16.16 / pgvector 0.8.7 instance bound to loopback `127.0.0.1:60607`, database `studytomy_aud03_test`, through `STUDYTOMY_TEST_DATABASE_URL`. The normal development and production databases were not targeted. The disposable test database/container remains available locally; it was not removed during this checkpoint.

| Scope | Command | Result |
|---|---|---|
| Full API suite | `.venv\\Scripts\\python.exe -m pytest -q src/tests` from `apps/api` at `cc3d884` | **10,470 passed, 14 failed, 22 skipped, 250 warnings** in 1,370.54 seconds. Twelve failures require Windows symlink-creation privilege (`WinError 1314`) in filesystem/archive tests. Two failures were stale assertions: the retention registry assumed every direct organization FK was `RESTRICT`, and the pinned Alembic head expected `a78f2c9d1e64` instead of the current `b91d2f6a08c3`. No assertion was weakened to resolve these. |
| Corrected registry and migration-head tests | `.venv\\Scripts\\python.exe -m pytest -q src/tests/services/test_demo_teardown.py src/tests/studytomy/test_existing_contracts.py` from `apps/api` at `bdb54ab` | **PASS:** 20 passed in 8.88 seconds. The registry now specifically covers retention-protected `RESTRICT` organization FKs; the separate FK contract still requires each organization FK to be explicitly `RESTRICT` or `CASCADE`. `st_guest_support_challenge` is intentionally short-lived and cascades with its organization. The pinned migration head is the verified single head `b91d2f6a08c3`, parent `a78f2c9d1e64`. |
| Migration graph and PostgreSQL migration tests | `uv run --no-sync python -m pytest -q src/tests/core/test_migrations.py src/tests/core/test_migrations_postgres.py` from `apps/api` at `cc3d884` | **PASS:** 53 passed. PostgreSQL migration rehearsal used uniquely named disposable databases with loopback/test-database guards; no production migration ran. |
| Full web unit suite | `bun run test` from `apps/web` at `cc3d884` | **PASS:** 735 passed, 0 failed, 2,042 expectations across 75 files. |
| Full strict web lint | `bun run lint:strict` from `apps/web` at `cc3d884` | **FAIL:** 6 errors and 976 warnings in untouched files: React `no-undef` in admin layouts, `GeneralWrapper.tsx` and `ClientComp.tsx`, plus two `no-useless-escape` findings in `PasswordStrengthIndicator.tsx`. The lint findings were not changed in this test-only checkpoint. |

The API full-suite failure classification is environment-specific for the twelve symlink cases and expectation drift for the other two. The focused rerun verifies the two corrected assertions only; the full API suite was not rerun after the test-only edits. The 22 skipped cases are reported as skipped; this record does not infer their acceptance. Browser acceptance, live AI/provider quality, real learner activation and educator acceptance remain unverified. No CI/CD, deployment, production migration or external service was run.

### Web lint follow-up

At `bfea54db35154cc0764e451905ee03637e58fa11`, Windows Python ran `python -m pytest -q --tb=short src/tests/studytomy src/tests/services/test_mcp_draft_authoring.py` from `apps/api`. The run passed **366 tests in 282.93 seconds** with zero failures or skips. PostgreSQL fixtures used the disposable Docker restore `studytomy_aud19_full_restore_test` at `127.0.0.1:60607`. Other cases used their existing harnesses. This checkpoint covers Studytomy API and MCP regressions across the recent grounded-save, report-delivery, checkout, grading and board persistence changes. It does not rerun the whole LearnHouse API suite or establish browser, provider or pilot acceptance.

At `692fe7a471e0a63e77de789c922cb342a54ce0b1`, the local integration selection passed **53 tests in 72.82 seconds**, with no failures or skips. It included `test_content_evidence.py`, `test_checkout_router.py`, `test_board_persistence_postgres.py`, `test_boards_router.py`, and `test_boards_service.py`. PostgreSQL-marked cases used `studytomy_aud19_full_restore_test` on the disposable loopback Docker instance. This included the corrected free-release media path and the review candidate's unsupported-media warning field. The collaboration suite passed **46 tests across 9 suites**, and `tsc --noEmit` passed for collaboration. This selection did not exercise the Next.js review warning in a browser or the complete learner journey.

At `c5d920eabee7b8a3eca21a2baf7ab3eb46566e27`, `bun run lint:strict --format stylish` from `apps/web` exited 0 with **0 errors and 976 warnings**. The change resolved the six errors in the previous checkpoint. `node node_modules/typescript/bin/tsc --noEmit` also exited 0 on the source that was committed at this revision.

### API symlink follow-up

At `33b40c36d58a786c4121b273d6a7e8571a73fb72`, Python 3.14.7 under Ubuntu 24.04 on WSL ran `python -m pytest -q -rs src/tests -k 'symlink or trimmed_archive'` from the primary checkout's `apps/api` directory. The run exited 0 with **13 passed, 9 skipped during collection and 10,486 deselected** in 56.58 seconds. The collection skips required unavailable Enterprise modules: seven SCORM modules, one Stripe idempotency module and one superadmin API token module. A native Windows symlink probe still failed with error 1314. The WSL run used Linux temporary directories and covers the selected symlink cases.

### Full WSL API follow-up

At `a78f00777859ec6c24f0f61054de624255da0495`, `python -m pytest -q -rs src/tests` ran under Ubuntu 24.04 on WSL from the primary checkout. PostgreSQL fixtures targeted the dedicated Docker `studytomy_aud03_test` database at `127.0.0.1:60607`. The run exited 1 with **10,478 passed, 1 failed, 29 skipped and 251 warnings** in 1,315.61 seconds. No symlink failures were reported. The skips comprised 10 cases requiring Enterprise code, 7 requiring FFmpeg or ffprobe, and 12 requiring a reachable local Ollama service or installed models.

The sole failure was `test_canonical_extensions_and_sniffing`, which expected a conversion rejection warning even when FFmpeg was unavailable. The test fix at `12c758012ea4854918865e2da69428d2c635d390` simulates converter rejection and checks that only `bad.wav` reaches the converter. `python -m pytest -q src/tests/lms_import/test_core_package.py` then passed all **54 tests** in 4.84 seconds under WSL. This was a focused rerun with a simulated converter. Full API revalidation after the fix, real conversion on WSL and browser acceptance remain open.

At `8172a8554318ca8654cb81c02a02c4b1c49edd8e`, the Windows API environment ran `python -m pytest -q -rs src/tests/services/test_hls_transcode_service.py src/tests/services/test_video_processing_service.py src/tests/security/test_hardening_round7b_code.py` with native FFmpeg and ffprobe on `PATH`. All **63 tests passed** with zero skips and two async mock warnings in 15.81 seconds. This covers the seven media tool cases skipped by the WSL run, using the Windows binaries.

At `db8b75f5ca0730e7f0af9d892628480a8a4415dc`, the full WSL command ran again with the same disposable PostgreSQL target. It exited 1 with **10,478 passed, 1 failed, 29 skipped and 250 warnings** in 1,302.62 seconds. The import test passed. `TestSweep.test_edits_after_indexing_are_queued` expected `(1, 0, 0)` and observed `(1, 1, 0)`, indicating one extra course queued. A focused WSL rerun of `python -m pytest -q src/tests/services/test_rag_scheduler.py` passed all **31 tests** in 11.32 seconds using the scheduler's SQLite fixtures and mocked embeddings. The full-run discrepancy remains unresolved.

The follow-up at `0609cdd3b15dfa221cba21bbfb77e5060bebd71c` traced the extra queue entry to the content date hooks. When registered, an ORM activity edit also stamps the parent course. The test now changes the activity timestamp by SQL update to exercise the activity-only sweep and retains the original queue assertions. `python -m pytest -q src/tests/core/test_content_dates.py src/tests/services/test_rag_scheduler.py` passed all **40 tests** in 9.14 seconds on WSL. The content date module registers the hooks during collection, so this check exercised the corrected test with those hooks present. No application behavior changed. The full suite was not rerun after this correction.

The subsequent full run at `5265fe46133711ded39d290796701984dbfbed29` executed `python -m pytest -q -rs src/tests` under Ubuntu 24.04 on WSL with the dedicated Docker PostgreSQL test database at `127.0.0.1:60607`. It exited 0 with **10,479 passed, 0 failed, 29 skipped and 250 warnings** in 1,241.34 seconds. The skips comprised 10 Enterprise cases, 7 cases that require FFmpeg or ffprobe in WSL, and 12 cases that require a reachable Ollama service or installed models. The separate Windows media run exercised the seven media cases. Browser acceptance and live AI provider evaluation remain open.

### Managed certificate browser checkpoint (18 November 2026)

With the primary worktree at `57137506fb39c7eef815ea33b149366f80e35731`, a local headless Chromium run at 390 × 844 signed in through the running Next.js app with the existing synthetic learner fixture. The login POST returned HTTP 200. The run verified that My Learning opens My certificates, issued and corrected cards render on the first page, the next page shows a withdrawn award, and Back to my learning returns to the course view. The learning catalogue and award responses were intercepted with synthetic fixtures. The initial browser with stale session state displayed the sign-in requirement.

This browser check covers learner navigation and status display. Exact award ownership, cursor ownership, and retained evidence are covered separately by the disposable PostgreSQL tests. An end-to-end browser journey against stored award API data, educator acceptance, and live learner activation remain open.

The live follow-up at `bdf83fa240f659e299114b32c78ac5878087781e` first reproduced a navigation gap. With the synthetic award holder signed in, the course catalogue returned HTTP 404 and My certificates was unreachable. The follow-up fix exposes certificate navigation from the unavailable course view without changing any authorization gate.

The fixed Next.js source then ran on `http://localhost:3007` against FastAPI at `http://127.0.0.1:1347`. Headless Chromium at 390 × 844 used two independent synthetic sessions and no intercepted requests. Both learner logins and both award-list GETs returned HTTP 200. The holder saw the one stored certificate with no grades, learner name, or result references in the response. The second learner saw an empty list. The stored award was seeded in the disposable PostgreSQL database `studytomy_award_live_test` at `127.0.0.1:51237`, copied from the isolated browser fixture database at Alembic revision `b91d2f6a08c3`. This exercised browser-to-API discovery and ownership. It did not exercise reviewer issuance in Chromium or activate real learner awards.

### Managed learning integration checkpoint

The primary main worktree at `f7e70034a19a2a749f738d4cb5e06ff55d9114b5` was clean during these local checks.

| Scope | Result |
| --- | --- |
| Studytomy API and MCP draft authoring | `python -m pytest -q -rs --tb=short src/tests/studytomy src/tests/services/test_mcp_draft_authoring.py` from `apps/api` passed all **407 tests** with zero skips in 349.21 seconds. PostgreSQL-backed cases used the disposable Docker database `studytomy_aud19_full_restore_test` on `127.0.0.1:60607`, at Alembic revision `b91d2f6a08c3` with pgvector 0.8.7. |
| Web | `bun run test` from `apps/web` passed **735 tests** with zero failures and 2,042 expectations across 75 files in 17.60 seconds on rerun. The first run passed 732 and timed out on three dynamic-content editor tests at the original 5-second limit. All 31 tests in that file passed in isolation in 2.38 seconds. No code or timeout changed between runs. |
| TypeScript | `node node_modules/typescript/bin/tsc --noEmit --incremental false` from `apps/web` exited 0. |
| Collaboration | `bun run test` from `apps/collab` passed all **47 tests** across nine suites with zero failures or skips. |

This checkpoint covers local automated integration. Browser verification for staff award issuance and guardian report rendering, live provider acceptance, educator acceptance, and recorded business and safeguarding approvals remain open.

### Stored progress report browser checkpoint

Chromium at 390 × 844 signed in as a synthetic recipient and navigated from the account report list to a stored report. The browser used Next.js on loopback port 3008, FastAPI on loopback port 1348, and the dedicated Docker PostgreSQL database `studytomy_guardian_browser_test` at `127.0.0.1:51237` with Alembic revision `b91d2f6a08c3`. The application source was archived from `1f6e20a098747deaae88a7103b494c22f46d1c64`, which has the same application source as browser test checkpoint `5fd029b42a58d91f94cd470b5fd1ead26dfb09ae`.

A temporary copy of the signed PostgreSQL fixture ran through report approval, publication and recipient API assertions, then stopped before its curriculum retirement step. That fixture preparation passed in 47.08 seconds. The browser intercepted no report requests. Login, list and detail each returned HTTP 200. The list contained one stored report, and the detail response and rendered page both showed one reviewed course-release completion. An earlier attempt used the fixture after curriculum retirement and produced an empty list, consistent with the published curriculum requirement. The browser check exercised a learner-chosen sharing grant for a synthetic recipient. Child guardian eligibility, browser withdrawal and staff issuance still require their own end-to-end checks.

A follow-up Chromium run on the same isolated stack changed exactly one synthetic sharing grant's expiry timestamp in the disposable database while the report was open. Before expiry the list showed one item and the detail rendered the reviewed completion count. After reload, the live detail endpoint returned HTTP 404, the page showed the unavailable state, and the count was absent. Returning to the list called the live API, which returned HTTP 200 with zero items, and the empty state rendered. This exercises grant expiry and disclosure revalidation across browser, API and PostgreSQL. The staff withdrawal command and child guardian eligibility still require separate browser journeys.

A separate pass on the same disposable stack restored the synthetic grant's validity and opened the published report in Chromium. While the report was open, a separately authenticated synthetic reviewer submitted the exact published version to the real staff withdrawal endpoint. The API returned a withdrawn receipt for the next version. The recipient's browser then reloaded the same report URL. Detail returned HTTP 404, the completion count disappeared, and Back to reports fetched an empty list from the API with HTTP 200. No report requests were intercepted. The staff action was submitted directly to FastAPI. Staff-dashboard interaction and child guardian eligibility remain open.

### Staff certificate issuance in Chromium

The isolated stack on ports 3008 and 1348 used the same application snapshot and disposable PostgreSQL database described above. A temporary copy of the signed award fixture prepared a synthetic reviewer, reviewed release and retained completion, then stopped before issuance. Fixture preparation passed in 4.63 seconds. The reviewer held the native dashboard permission and the scoped assessment moderation capability. The first browser attempt lacked the fixture dashboard right. After granting that right in the disposable database and evicting its cached session, the workspace opened. The final run used the repository test fixture's dismissed onboarding state.

At 390 × 844, Chromium signed in and opened the course's Managed certificates workspace. The eligibility GET and issuance POST both returned HTTP 200. The POST used the exact reviewed digest from the preview, and the UI rendered the returned award reference. Requests were not intercepted. This verifies synthetic staff issuance. Staff correction and withdrawal in the browser, educator acceptance and real award policy approval remain open.

A mobile Chromium follow-up submitted the staff review form for that stored award using the same authenticated reviewer and exact course, release, learner and award references. The issuance-only synthetic policy returned HTTP 404 with `MANAGED_CERTIFICATE_UNAVAILABLE`. The dashboard rendered an error and showed neither a corrected nor a withdrawn receipt. No request was intercepted. An initial harness attempt omitted the required release and learner fields and correctly made no request. A positive browser correction or withdrawal still requires a synthetic change-enabled policy and a finalized assessment correction linked to the issued award.

### Grounded draft refusal in the authenticated editor

The isolated Playwright run of `12-grounded-lesson-draft.spec.ts` passed in 54.9 seconds. Chromium used a real synthetic editor login and the local Next.js editor. Editor bootstrap and grounding requests were intercepted. The spec supplied the same HTTP 422 message verified in the PostgreSQL service regression at `e413bdba228c2105427170bcf84e28bb23227f57`. The editor displayed that message, hid unpublished save and issued no save request. The same run exercised a successful preview, stale-edit retention and retry with the same request ID after a lost save response. This verifies browser behavior for the specified responses. Live provider generation, real material upload and browser-to-FastAPI save still need end-to-end verification.

### Real PDF intake through the local API

At `ebcbe3c1075585071c1a3dcf3c7eef71eaa3a42a` the primary API source ran on loopback port 1346 against the disposable Docker PostgreSQL database `studytomy_guardian_browser_test` at Alembic revision `b91d2f6a08c3`. A separate Redis namespace and temporary API directory were used. A synthetic teacher signed in through the real API and uploaded the rehearsal's three-page fractions PDF twice. Both responses were HTTP 201 with private no-store caching and the same receipt. Its SHA-256 matched the rehearsal manifest. PostgreSQL contained exactly one version for the lesson, with the stored byte digest matching the recorded SHA-256 and all three pages retained.

The linked lesson listed that version and the sibling lesson listed none. Explicitly selecting the PDF in a sibling-lesson generation request returned HTTP 404. This check used a real PDF and real HTTP requests without a provider key. It exercised upload and lesson scope. Browser upload and provider-backed generation remain separate checks.

An authenticated Chromium follow-up used Next.js on loopback port 3006 and the same live API on port 1346. From the previously empty synthetic sibling lesson, the teacher submitted the actual three-page fractions PDF through the editor. The live upload returned HTTP 201. PostgreSQL stored one version for that lesson with matching bytes and three extracted pages. The first browser runner did not return its final assertion. A separate read-only Chromium run signed in, reopened the same editor and verified that the persisted PDF and three readable pages appeared. The upload and read used no intercepted requests. Provider generation and browser-to-API draft save remain open.

The next Chromium run used the same stack and stored source. The browser intercepted only lesson generation, returning a synthetic preview with a real source UUID, digest and exact page-one quote. A teacher edited the paragraph and clicked Save unpublished lesson. The live FastAPI save returned HTTP 200. The reloaded editor rendered the saved text and exact quote through the saved-citations view. A direct PostgreSQL check matched the evidence to the current native activity version, the exact linked source digest and page one, and confirmed the activity was still unpublished. This verifies the browser-to-API save and citation reload. Provider-generated factual support remains unverified.

A follow-up Chromium run opened the saved quotation, then the authenticated synthetic teacher revoked the linked PDF through the live API. Revocation returned HTTP 204. After reload the editor still displayed the unpublished paragraph, marked the citation stale, hid the quotation and showed an empty active source list. A new preview request naming the revoked source returned HTTP 404. No requests were intercepted. This covers revocation and revalidation of an unpublished draft. It does not withdraw an existing reviewed release.

### Grounded quiz save through the live local API

A synthetic teacher used the isolated API on port 1346 to link the rehearsal PDF to an unpublished native assignment activity. The upload returned HTTP 201. Saving an edited equivalent-fractions quiz and retrying the same command both returned HTTP 201 with the same task reference and teacher-review-required state. The protected citation read returned HTTP 200 and matched the exact linked PDF digest, page one and quote. PostgreSQL showed one native task and one citation evidence row, and the assignment stayed unpublished. The quiz was supplied as a teacher-edited draft for this check. Provider quiz generation and educator acceptance remain open.

At `667a021dc9076ea9a1186b09ef74a2c6a6bfedaf` a focused integration checkpoint ran the grounded material, grounded retrieval, MCP draft PostgreSQL and service suites, retained grading, native assignment router wiring and auth logout revocation. All 68 tests passed in 34.75 seconds. PostgreSQL-marked cases used the disposable loopback Docker database `studytomy_guardian_browser_test` at Alembic revision `b91d2f6a08c3`. Other tests retained their existing unit and API harnesses. No live provider call or operational acceptance was included.

### MCP authentication and authoring checkpoint

At `c0ef8e135f7a491c666f6040374bec985a8d6278`, `python -m pytest -q --tb=short src/tests -k mcp` ran from `apps/api`. It reported 235 passed, 9 skipped and 10,321 deselected in 40.12 seconds. PostgreSQL-marked cases used the disposable loopback Docker database `studytomy_guardian_browser_test` at Alembic revision `b91d2f6a08c3`. The command did not request skip reasons. A separate unauthenticated HTTP request to the running loopback `/mcp` tool list returned HTTP 401 with a Bearer challenge. This covers automated MCP checks and the real transport's unauthenticated response. An authenticated teacher or administrator OAuth session with interactive approval and live draft creation still needs end-to-end verification.

A live follow-up at `c322eaec27d5cbc2b1b750131f78df34192d91b3` used a separate loopback API on port 1345 and the same disposable PostgreSQL database with a separate Redis namespace. A synthetic teacher registered a loopback MCP client, authorized the requested resource via PKCE, consented to read/write access and discovered the enabled draft tools. Both tool calls paused for separate approval, which the test submitted as the signed-in synthetic teacher through the approval API. PostgreSQL verified a private managed course, an unpublished lesson and two corresponding audit events. The test sent revocation requests for the issued tokens. The first attempt was denied when the synthetic organization lacked its normal configuration row, and the passing run supplied that row without relaxing the native create or approval checks. This exercises the teacher transport and approval APIs. Browser consent, administrator live authoring and revoked-token reuse remain separate checks.

A second live run used a synthetic member with the built-in administrator role. PKCE consent, tool discovery and separate course and lesson approvals passed on the isolated loopback transport. PostgreSQL verified the same private/unpublished state and two audit events for the administrator's drafts. The immediate post-revocation probe still returned HTTP 200 during the documented 30-second in-memory authentication cache window. In a separate run the same probe returned HTTP 401 after waiting 32 seconds. The test does not establish immediate revocation or browser consent rendering.

At `e20c516176db4cc5f27e0b8dccccbb7e21c4aa54` a mobile Chromium journey used the local web app on port 3005, the isolated MCP API on port 1344 and the same disposable PostgreSQL database. A synthetic administrator signed in, selected the organization, granted read/write access in the OAuth consent UI, and separately approved the course and lesson writes in the browser. The local test client intercepted only its callback redirect, verified OAuth state, and redeemed the real PKCE authorization code through the live API. PostgreSQL verified one private managed course, one unpublished lesson and two corresponding audit events for the run. After token revocation, the very next transport request returned HTTP 401. This exercises live browser consent and write approval for a synthetic administrator. It does not authorize any real external MCP client.

The same mobile Chromium consent and approval journey passed for a synthetic teacher without the administrator role. The user selected the organization and the read/write scope and separately approved each draft. PostgreSQL confirmed two audit events for the non-administrator actor, a private managed course and an unpublished lesson. The next transport request after revocation returned HTTP 401. An initial browser harness run timed out filling the password field. The passing run located the password input directly. Application code and permissions were unchanged between the two runs.

### Board snapshot stream checkpoint

At `510aac605a6455b8344eba96585a42466d6d65be` the mounted FastAPI board route used an in-process ASGI client and a disposable Docker PostgreSQL session at Alembic revision `b91d2f6a08c3`. With an ephemeral local internal credential, a new synthetic board returned the empty-state marker and ETag. A two-chunk PUT with that ETag returned HTTP 200. A new GET returned the present-state marker, a new ETag and byte-identical payload, matching the row queried directly in PostgreSQL. This checks the actual route and persistence without mocking the write. Node's collaboration fixture separately verifies oversized stream cancellation. The background API launch for a combined live Redis check was blocked by automatic approval review, so this checkpoint does not add fresh Redis, WebSocket or restart evidence.

## Final E2E safety and verification completion (22 November 2026)

**Base source revision:** `86e875e2d4c5e9d8b88de80730e25a850da3b122` (`main`). This verification pass made no application-code changes. It added and executed focused test assertions for the shared cross-language digest, database-port variation, and URL-fragment rejection. Older checkpoints elsewhere in this file do not substitute for these results.

**Environment:** macOS Darwin 27.2.0 arm64, Node.js 26.13.0, Bun 1.4.2. The E2E URL, test-database URL and source author/reviewer credentials were unset at the start. `localhost:3000` and `localhost:8000` did not serve the frontend or API (HTTP connection failure). Docker listed existing development and unrelated project containers, but none was selected as an approved isolated syllabus test database. No container, database, account or source record was changed. PostgreSQL and pgvector versions for this verification are **UNVERIFIED**.

### Findings

| Finding | Status | Evidence and limitation |
| --- | --- | --- |
| DB-01 | **PARTIALLY IMPLEMENTED** | The query-free URL validator, active-session `current_database()` identity digest, and pre-provisioning comparison are present. Runtime-identity and digest tests passed, pinning the same SHA-256 value in Python and TypeScript and covering host, port, database, host/hostaddr overrides, fragments, and digest mismatch. The API was not running, so comparison against a live database and the authenticated HTTP failure contracts remain **UNVERIFIED**. |
| E2E-01 | **BLOCKED** | Both Explore Topics Playwright specs were attempted with loopback URLs. Global setup rejected them before health checks, authentication or account provisioning because `STUDYTOMY_TEST_DATABASE_URL` was unset. The required source author/reviewer accounts, approval policies, synthetic curriculum, private storage and approved embedding provider were also not configured or verified. Neither browser scenario executed. |
| VERIFY-01 | **PARTIALLY IMPLEMENTED** | The available CLI, API unit, web, docs and E2E helper checks below ran. PostgreSQL integration was skipped and the live browser acceptance was blocked. |
| WIN-01 | **UNVERIFIED** | The branding test and POSIX URL conversion passed on macOS. A Node.js 26.13.0 cross-target `fileURLToPath(..., { windows: true })` probe accepted a drive-qualified URL with an encoded space. No Windows operating-system test run occurred; Windows execution remains **UNVERIFIED**. |
| DOC-01 | **IMPLEMENTED** | This revision-specific record distinguishes executed checks from skipped and blocked acceptance. No credentials, connection strings or private source content are recorded. |

### Commands and results

| Check | Actual result |
| --- | --- |
| `cd apps/cli && bun run typecheck` | Passed. |
| `cd apps/cli && bun run build` | Passed. |
| `cd apps/cli && bun run test` | 22 files passed; 854 passed, 1 skipped (855 total). The test script also built the CLI successfully. |
| `cd apps/api && uv run pytest -q src/tests/routers/test_instance_runtime_identity.py src/tests/studytomy/test_syllabus_library_unit.py` | 40 passed. The runtime-identity tests use synthetic connection/session doubles; this is not live PostgreSQL verification. |
| `cd apps/api && uv run ruff check src/routers/instance.py src/tests/routers/test_instance_runtime_identity.py` | Passed. |
| `cd apps/api && uv run pytest -q -m studytomy_postgres src/tests/studytomy/test_syllabus_library_postgres.py` | 4 skipped because `STUDYTOMY_TEST_DATABASE_URL` was unset. |
| `cd apps/e2e && bun test core/testDatabase.test.ts` | 3 passed, including host, port and database identity, host/hostaddr query and fragment rejection, and the credential-free digest contract. |
| `cd apps/e2e && bun run test:identity` | 4 passed, including missing and incorrect database digest handling. |
| `cd apps/e2e && bun run typecheck` | Passed. |
| `cd apps/web && bun run test` | 788 passed; includes 4 Studytomy branding tests. |
| `cd apps/web && bun run lint` | Exit 0; 0 errors and 966 warnings. |
| `cd apps/web && bun run build` | Passed. Next.js reported existing dynamic-filesystem tracing warnings. |
| `cd docs && bun run test:snippets` | 3 passed, including POSIX/Windows path containment cases. |
| `cd docs && bun install --frozen-lockfile` | Installed the already-declared locked documentation dependencies; no dependency versions were changed. |
| `cd docs && bun run build` | Passed, generating 461 static pages. OpenAPI fetching failed and the documented bundled snapshot fallback was used. The generated tracked `docs/public/llms.txt` was restored after the build. |
| Playwright specs `19-explore-topics.spec.ts` and `20-explore-topics-live.spec.ts` | **BLOCKED before test execution** by the missing approved `_test` database URL. No API request or shared learner provisioning occurred. |

The live blocker is environmental, not a test pass: both loopback applications were unavailable, there was no approved test database URL, and no separate source-author/reviewer fixture credentials were supplied. Until an authorized isolated environment with the documented policies, curriculum, storage and embedding prerequisites is available, active API database identity, PostgreSQL source lifecycle, browser search/citation behavior and revocation remain unverified. No production or shared database was used. This is a local verification record, not CI, deployment, educator approval or operational acceptance.

### Runtime-identity failure-path retest

On 22 November 2026, against base commit `cea60982924c247b893d04d9a1e31e8ad7fe57af`, two focused API regression tests were added in the working tree and run without modifying application code. `uv run pytest -q src/tests/routers/test_instance_runtime_identity.py src/tests/studytomy/test_syllabus_library_unit.py` passed **42 tests**. `uv run ruff check src/tests/routers/test_instance_runtime_identity.py` passed. The added cases verify that a failed active-database connection returns the generic HTTP 503 contract and that the mounted runtime-identity route rejects unauthenticated HTTP requests with 401. They use synthetic sessions and do not establish live PostgreSQL behavior. An initial test harness attempt using the shared SQLite fixture failed during schema setup because SQLite does not implement PostgreSQL's `to_tsvector`; the HTTP test was changed to avoid that unrelated fixture path and the focused suite then passed.

## Isolated Explore Topics environment and acceptance (22 November 2026)

**Checkout:** Local `main` started at `159671a8ad29f5c715ed9206ab79e5e894a8cc57`; `origin/main` was `cea60982924c247b893d04d9a1e31e8ad7fe57af`. The local commit was preserved. The isolated acceptance changes in this checkpoint are recorded in the working tree on top of that commit.

**Environment:** A task-created loopback PostgreSQL 19 container with pgvector `0.8.7` served the disposable database `study_explore_20261122_bb2191a976_test` on `127.0.0.1:58205`. Its Alembic head was `f04d9c6a31b8`, matching the source migration head. Redis, private RustFS, and local Ollama services used separate task-created loopback ports; the private RustFS bucket round-tripped test objects. FastAPI ran from this checkout on `127.0.0.1:8000`. The final identity-gated browser run served Next.js on `127.0.0.1:3001` because another project already owned port `3000`; that process was left untouched. No existing development or unrelated container/database was used. API runtime identity, source tree digest, source/database migration heads, and the active database identity were checked against the same query-free loopback `_test` database before provisioning or live browser setup. The test fixture used separate synthetic source-author and reviewer accounts, scoped educational assignments, and synthetic policy evidence; no production authorization or approval gate was disabled. Text generation remained disabled; local Ollama was used only for embeddings, with no paid AI/provider request.

**Executed checks:**

| Check | Result |
| --- | --- |
| Syllabus library backend unit tests | 36 passed, including the curated single-concept retrieval regression. |
| Syllabus library PostgreSQL integration tests | 4 passed against the isolated PG19 database. |
| Ruff for the touched API service, fixture provisioner, and tests | Passed. |
| CLI typecheck and build | Passed. |
| CLI test suite | 22 files passed; 854 passed, 1 skipped. |
| Frontend test suite | 788 passed, 0 failed. |
| Frontend lint | Exit 0; 0 errors and 966 existing warnings. |
| Frontend production build | Passed; Next.js emitted existing dynamic-filesystem tracing warnings. |
| E2E database and runtime-identity helpers | 3 database tests and 4 identity tests passed. |
| E2E typecheck | Passed. |
| Explore Topics browser specs 19 and 20 | 2 passed in headed Chromium against the final application code and real isolated API/database. Spec 19 exercised deterministic mocked UI states; spec 20 used real API upload/review/search/PDF/follow-up/revocation routes and PostgreSQL state. |
| Documentation snippet tests and build | 3 snippet tests passed; build generated 461 pages using the documented bundled OpenAPI snapshot fallback after the remote OpenAPI fetch failed. The build-generated tracked `docs/public/llms.txt` was restored. |

The live browser journey verified that a pending source was unavailable, author self-approval and learner approval were denied, required rights evidence was enforced, an independent reviewer could approve the synthetic source, retrieval returned its approved page citation, authenticated PDF access served the synthetic PDF, follow-up retained the authorized source context, a scope change cleared stale results, unsupported content produced no fabricated citation, and revocation denied later retrieval and PDF access. The fixture's cleanup path revoked the synthetic source while retaining its audit history. The educator-library UI correctly denied the learner. No learner personal data, grades, or mastery records were used.

Headed Chromium was necessary for the built-in PDF viewer: in headless Chromium the popup remained at `about:blank`, while the headed run opened the authenticated PDF at the cited page. This is a browser implementation limitation, not an API failure. The in-app browser could not reach the loopback server in this environment, so interactive verification was performed through the repository's headed Playwright runner; no claim is made that the in-app browser was tested. These results establish local synthetic acceptance only, not educator validation, real learner safeguarding approval, real-provider generation quality, deployment readiness, or production behavior.

The task-created PostgreSQL, Redis, RustFS, Ollama, API, and web processes are disposable and loopback-bound. No named Docker volumes were attached. The final run's synthetic source was revoked by the test cleanup path; exact cleanup of the task-created processes and temporary credentials is recorded in the delivery report. No schema migration was introduced.

## Final isolated verification receipt (22 November 2026)

**Tested revision:** `d9d35b8d90c6a770ac6a324b4d36b46c40b6858e` on `main`, with `origin/main` at the same SHA before this documentation-only update. The source tree digest reported by the live runtime was `39165f4d30b05f6753f2284808c427f0ce0a64dc5e3a041f114051b4a0a1d5cd`; the runtime reported a clean source tree and matching web/API build identity. The API and frontend/test files are unchanged from implementation commit `1146e796b1d905ac299e3e20911fbf9f172387c7` through this tested revision.

**Database identity gate (DB-01):** The E2E runner accepts only a query-free loopback PostgreSQL URL naming a `_test` database. Before provisioning shared authentication state, global setup compares the runner's credential-free host/port/database digest with the authenticated API runtime-identity response. The API reports that digest only after checking its actual PostgreSQL connection and `current_database()` against its configured URL; identity failures return the guarded error path. Query overrides, remote hosts, database mismatch, missing identity configuration and unauthenticated access are covered by focused API and E2E helper tests. No database credentials are returned. The health and organization readiness requests before runtime identity are read-only.

**Executed checks at this revision:**

| Check | Result |
| --- | --- |
| API runtime-identity tests | 7 passed. |
| Syllabus library unit tests | 36 passed. |
| PostgreSQL syllabus integration tests | 4 passed against the disposable loopback PostgreSQL 19 database recorded above; the same API/test source is present at the tested revision. |
| Ruff on the touched API service and test files | Passed. |
| E2E database-guard tests | 3 passed. |
| E2E runtime-identity tests | 4 passed. |
| E2E TypeScript check | Passed. |
| Mocked and live Explore Topics Playwright specs | 2 passed in headed Chromium. The live spec used the real local FastAPI routes and isolated PostgreSQL; the mocked spec remains a separate deterministic UI check. The live flow verified pending-source isolation, self-approval and learner-approval denials, rights evidence, separate reviewer approval, cited retrieval/page, authenticated PDF access, contextual follow-up, scope reset, unsupported-topic fallback, revocation, and revoked-PDF denial. |
| CLI typecheck, build and tests | Passed; 22 test files, 854 passed, 1 skipped. |
| Frontend branding test | 4 passed, including encoded-space file URL handling. |
| Frontend test suite | 788 passed, 0 failed. |
| Frontend lint | Exit 0; 0 errors and 966 warnings. |
| Frontend production build | Passed; Next.js reported existing dynamic-filesystem tracing warnings in Sentry configuration. |
| Documentation snippet tests | 3 passed. |
| Documentation production build | Passed; 461 pages generated. Remote OpenAPI fetches fell back to the bundled snapshot; generated `docs/public/llms.txt` was restored afterward. |

**Portability (WIN-01):** The branding fixture selects a drive-qualified Windows file URL on Windows and a POSIX file URL elsewhere. On this macOS host, the branding test passed and a Node `fileURLToPath(..., { windows: true })` probe accepted the drive-qualified URL and decoded its space. A native Windows test runner was unavailable; Windows execution remains **UNVERIFIED**.

**Limits:** PostgreSQL and browser verification used synthetic records and the isolated database only. The test run used local Ollama for embeddings, with text generation disabled and no paid AI calls. The source fixture cleanup revoked its approved version while retaining audit history; no active approved synthetic source remained. These results do not establish production behavior, educator validation, real-provider answer quality, or deployment readiness. No schema migration or application-code change was made for this receipt.

## Markdown documentation refresh (25 November 2026)

**Source baseline:** `main` and `origin/main` at `997ac953d8e4c7acfcdd666ba8a53cc72422d9b7`; source changes in that merged commit were not altered by the documentation pass. The current guides were aligned with association-backed course ordering, the managed-PDF topic-check entry point and its test boundaries, enum-only tutor action vocabulary, the current family/migration gaps, and the local-only deployment status. Dated records and approval documents remain historical. The refresh was later committed and pushed as documentation-only commit `28eff10ec9eace33cee5024465378307f7a6d425`.

| Command/check | Result |
|---|---|
| `cd docs && bun run test:snippets` | Exit 0; 3 passed, 0 failed, 0 skipped. |
| `cd docs && bun run build` | Exit 0; Next.js compiled and generated 461 static pages. Remote OpenAPI fetch failed and the configured bundled snapshot fallback was used. Sitemap generation was skipped because `NEXT_PUBLIC_DOCS_URL` was not set. |
| Relative-link and docs-route inventory across tracked `.md`/`.mdx` | 191 files inventoried; 281 relative repository targets and 510 root-relative documentation/asset routes resolved; no missing targets. Generated `/reference` routes were checked by the docs build rather than the filesystem mapping. External URLs and every fragment anchor were not checked. |
| `git diff --check` | Passed after the documentation changes. |

GitHub verification is separate: [CI Build run 45660150950](https://github.com/nirzaf/studytomyportal/actions/runs/45660150950) and [Repository Security run 45660150883](https://github.com/nirzaf/studytomyportal/actions/runs/45660150883) passed on the source baseline. No local API/UI/browser test, database operation, migration or deployment was performed as part of the documentation refresh. The managed-PDF live browser spec still requires its own isolated fixture-backed run.

### Git delivery receipt

Implementation and test commit `1146e796b1d905ac299e3e20911fbf9f172387c7` was pushed normally to `origin/main` after the post-commit runtime identity check and both Explore Topics browser specs passed. `git ls-remote origin refs/heads/main` returned the same SHA. This receipt records that earlier delivery; the documentation-only follow-up below is separate.

### Documentation follow-up receipt (26 November 2026)

The follow-up docs audit corrected the stale current-status wording and updated the CI/security receipts. The preceding documentation checkpoint `28eff10ec9eace33cee5024465378307f7a6d425` was pushed to `origin/main`, and both workflows passed on that SHA. No application deployment, migration or live browser scenario was run as part of the documentation work.

| Check on the 26 November documentation follow-up | Result |
|---|---|
| `cd docs && bun run test:snippets` | Exit 0; 3 passed, 0 failed, 0 skipped. |
| `cd docs && bun run build` | Exit 0; compiled and generated 461 static pages. Remote OpenAPI fetch used the bundled snapshot fallback. Sitemap generation was skipped because `NEXT_PUBLIC_DOCS_URL` was unset. |
| `git diff --check` | Exit 0. |

## Local audit continuation (27 November 2026)

**Inspected revision:** local `main` at `b7e57929eba1230577fe23767aac162fefccf80d`, matching `origin/main` at the start of this continuation. The worktree already contained extensive uncommitted audit changes; they were preserved. This continuation made no commit, push, deployment, migration, or live-data change.

| Verification | Result |
|---|---|
| F01 credit settlement, board/playground routes, and MCP OAuth regressions | 48 passed. |
| Full API tests excluding PostgreSQL-marked and Ollama tests | 10,704 passed, 14 skipped, 211 existing warnings. |
| Isolated PostgreSQL 19 StudyTOMY integration suite | 93 passed, 413 deselected; migration tests separately passed 4 with 1 expected skip. Disposable loopback test data only. |
| API Ruff on the changed retention/governance policy paths | Passed. |
| Web unit suite, TypeScript, lint, and production build | 809 passed; typecheck passed; lint exit 0 with 965 warnings and no errors; build passed with 41/41 static pages. Existing Sentry/Turbopack tracing warnings remain. |
| CLI typecheck and test suite | Passed; 22 files, 855 passed, 1 skipped. |
| Documentation snippets and production build | 3 snippet tests passed; build passed and generated 461 pages. Remote OpenAPI fetch used the bundled snapshot; sitemap generation was skipped because no verified `NEXT_PUBLIC_DOCS_URL` was configured. |
| SDK/package synchronization and tests | Passed after regenerating API schema and syncing `@learnhouse/react` dependencies; SDK 118, React 28, Vue 10 tests passed. |
| Momentum Pilot 01 build, verification, and tests | Passed; 8 tests passed. |
| Root and standalone API Dockerfiles under OrbStack | Fresh local builds passed. Smoke checks confirmed runtime UID/GID 1001, `ffmpeg`, the packaged momentum manifest (matching the checkout SHA-256), writable runtime directories, and API module imports. Both healthcheck definitions were inspected; no services were started, so live health transitions remain unverified. |
| E2E typecheck and safety helpers | Passed; identity 4, browser egress 1, database guard 3 tests passed. |
| Ollama-marked API tests | 12 collected; not run because the local loopback Ollama endpoint was unavailable. |
| Local learner browser routes | `/playgrounds` showed the Practice Studio and saved Grade 8 Maths demo; `/explore` showed the approved-source search interface. These read-only observations are not the author/reviewer/publish acceptance journey. |

**Remaining acceptance:** Full signed-in multi-role E2E acceptance was not run. `E2E_BASE_URL`, `E2E_API_URL`, and `STUDYTOMY_TEST_DATABASE_URL` were unset in this execution environment; the open local browser is connected to an existing learner session, not a proven disposable acceptance stack. The F01 Redis refund failure path has no durable idempotent reconciliation record, so a process crash during a failed refund remains an explicitly reported limitation. F08 credential/recovery remains gated on named D-01/D-03/D-12 decisions and the ADR choice. F09 sales, payments, and offer activation remain gated on the applicable named policy decisions and an approved provider. No synthetic fixture was treated as live approval.

### Focused findings follow-up (27 November 2026)

This local-only continuation resumed at the same inspected SHA, `b7e57929eba1230577fe23767aac162fefccf80d`, with a heavily modified worktree. It preserved all earlier uncommitted work and made no commit, push, migration, deployment, or real-recipient send.

| Findings | Verification and current boundary |
|---|---|
| F01 | Existing credit-refund/playground/MCP regression group: **48 passed**. At that checkpoint, ambiguous Redis refunds had no retry. The later F01 follow-up below adds one same-key idempotent retry; durable reconciliation across process crashes remains unavailable. |
| F02–F03 | Managed-course native/RBAC/copy/export and Zapier projection regressions: **54 passed**. |
| F04 | Media/board/stream/calendar focused API regressions: **245 passed**; Collab access recheck suite: **48 passed**. The existing tests cover chunk-stop denial and calendar extension scoping, but this follow-up did not execute a database-backed concurrent stream revocation scenario. |
| F05 | Family, sharing, privacy, consent, record-retention and case API unit group: **47 passed**; focused family/privacy/retention web tests: **16 passed**. The earlier isolated PostgreSQL Studytomy suite passed **93** tests. D-13 classification and exact object-version deletion remain required before real-record/S3 removal. |
| F06 | Added `report-outbox-drain`: default is read-only aggregate preview; execution is bounded to 25, requires exact opt-in, a loopback PostgreSQL `_test` database, and loopback SMTP. Status and drain now both reject non-PostgreSQL, non-loopback, query-routed, or non-`_test` databases before reading or processing. Scheduler/CLI regression file: **17 passed**; separate non-PostgreSQL retention/case/concurrency checks: **14 passed**; Ruff and CLI help passed. The drain was not executed, and no external email was sent. Additional archive/idempotency/overdue-review CLI lifecycle actions remain incomplete or policy-gated. |
| F07–F09 | Focused offer/readiness/authorization API group: **25 passed**; readiness web regression: **1 passed, 11 assertions**. Governance and offers remain private until current evidence and approvals exist. F08 credential/recovery is blocked on named D-01/D-03/D-12 decisions and the ADR choice; F09 sale activation is blocked on D-02/D-04/D-05/D-18/D-20 and an approved provider. |

API test commands used the isolated test environment prefix `PYTHON_DOTENV_DISABLED=true TESTING=true LEARNHOUSE_CONTENT_DELIVERY_TYPE=filesystem LEARNHOUSE_DISABLE_EE=1 LEARNHOUSE_DEMO_ENABLED=0 LEARNHOUSE_REDIS_CONNECTION_STRING=redis://127.0.0.1:1/0`:

```sh
uv run --no-sync pytest -q src/tests/security/test_resource_access.py src/tests/admin/test_admin_api.py src/tests/services/ai_tools/test_course_copy.py src/tests/services/test_export_service.py src/tests/services/test_activities_service.py src/tests/integrations/test_zapier.py -k 'managed_course or native_admin_hides_managed_enrollment or managed or zapier' --tb=short
uv run --no-sync pytest -q src/tests/services/test_media_service_coverage.py src/tests/services/test_boards_service.py src/tests/services/test_calendar.py src/tests/routers/test_content_files_router.py src/tests/routers/test_local_content_router.py src/tests/routers/test_media_serve_router.py src/tests/routers/test_media_authz.py src/tests/studytomy/test_managed_media_staging.py src/tests/security/test_content_access.py src/tests/security/test_hardening_round7_content.py src/tests/security/test_hardening_round7_boards.py --tb=short
uv run --no-sync pytest -q src/tests/studytomy/test_family_authority.py src/tests/studytomy/test_family_sharing.py src/tests/studytomy/test_privacy_projections.py src/tests/studytomy/test_consent_events.py src/tests/studytomy/test_record_retention_postgres.py src/tests/studytomy/test_service_cases_postgres.py -m 'not studytomy_postgres' --tb=short
uv run --no-sync pytest -q src/tests/studytomy/test_report_delivery_scheduler.py src/tests/studytomy/test_record_retention_postgres.py src/tests/studytomy/test_service_cases_postgres.py src/tests/studytomy/test_concurrency_postgres.py -m 'not studytomy_postgres' --tb=short
uv run --no-sync pytest -q src/tests/studytomy/test_report_delivery_scheduler.py --tb=short
uv run --no-sync ruff check cli.py src/tests/studytomy/test_report_delivery_scheduler.py
uv run --no-sync pytest -q src/tests/studytomy/test_offer_readiness.py src/tests/studytomy/test_offer_award_terms.py src/tests/studytomy/test_checkout_router.py src/tests/studytomy/test_studytomy_access.py -m 'not studytomy_postgres' --tb=short
uv run --no-sync python cli.py report-outbox-drain --help
```

`apps/collab`: `bun run test` (**48 passed**). `apps/web`: `bun test --isolate tests/studytomy-readiness.test.mjs` (**1 passed, 11 assertions**). `docs`: `bun run test:snippets` (**3 passed**). Repository root: `git diff --check` passed. Full API, web, CLI, package, Docker build and PostgreSQL results remain as recorded in the preceding 27 November table; they were not rerun after adding the F06 CLI. Full author → independent reviewer → publish → learner → staff browser acceptance remains **NOT RUN** because this continuation did not provision a matching isolated browser/API/database fixture.

### F04 stream and native-access follow-up (27 November 2026)

This follow-up used the existing disposable `studytomy-guidance-audit-test` PostgreSQL container and synthetic records only. No migration, application deployment, commit or push was performed.

| Check | Result |
|---|---|
| API media, boards, calendar and file-access regressions | **285 passed** across the focused media, board, calendar, content-file, local-content, authorization and staging test modules. |
| Collaboration socket and access-recheck regressions | **48 passed**, 0 failed, 0 skipped. Revoked/expired authorization closes or rejects the collaboration connection as expected. |
| PostgreSQL grant revocation while streaming | **1 passed, 12 deselected.** `test_checkout_router.py -m studytomy_postgres -k stream` pauses after the first media chunk, revokes the learner grant in a separate PostgreSQL session, then verifies the stream ends at that chunk, the staged file closes, and a later request is denied. |

The ordinary `/content/...` and local-content paths do not carry an immutable release identifier. They therefore use the managed-course staff authorization gate and do not grant managed learners native-file access. Learner media delivery remains on the release-specific Studytomy route, whose fresh-session revalidation checks the exact release, policy, progression and current grant before each chunk. This is a deliberate fail-closed boundary; do not infer learner release access from a native URL. The managed calendar projection likewise lists the current actor's releases and reads linked assignments under that actor; its focused test checks the released extension, actor binding, denial, and that leftover native enrollment does not supply a managed deadline.

`STUDYTOMY_TEST_DATABASE_URL` was unset in the shell. For the single PostgreSQL test, a URL was assembled in-process from the named test container's configuration and was never printed; the fixture enforced loopback and the `_test` suffix and verified the Alembic schema before running. Two initial `-k` selectors matched no cases and exited 5; the corrected `-k stream` run above executed the intended scenario successfully.

### F05 authority and evidence follow-up (27 November 2026)

| Check | Result |
|---|---|
| Family authority, scoped sharing service, privacy projections, consent, case and retention API regressions | **47 passed, 8 deselected** (`-m 'not studytomy_postgres'`). The checks cover relationship evidence resolution, invalid/unbound case references, default-private work filters, and reviewed record-request evidence. |
| Family/privacy/retention browser-component regressions | **18 passed**, 0 failed; 90 assertions across five existing web tests. |
| Isolated PostgreSQL record-evidence retention journey | **1 passed, 12 deselected** (`test_checkout_router.py -m studytomy_postgres -k reentry`). Synthetic data exercised a current D-13/D-18 decision, expiry, hold blocking and release, stale-manifest rejection, failed filesystem unlink, concurrent same-approval removal/replay, removal receipts, and restore replay. |

The current path validates relationship evidence against an exact verified tenant/learner/relationship record or one uniquely bound learner-reported case attachment. Restriction writes suppress queued/retry notices in the same transaction; leased sends still require the F06 send-time authority check. Existing family/privacy workspaces expose relationship decisions, evidence, eligibility, consent, record requests, holds, reviewed removal and safeguarding retention. Independent sharing remains API/service-only because the applicable D-12 audience/purpose decision is not verified; the default-private filters stay fail-closed.

**F05 remains PARTIAL / approval-gated.** `StudyEvidenceFile` stores an internal storage key but no object version identifier. The case-evidence removal executor explicitly allows only filesystem storage under current D-18 execution policy. It cannot safely perform exact S3/R2 version removal, and no schema change or unapproved object deletion was made. Real-record removal remains unavailable until D-13 retention classification, the selected storage's exact-version deletion capability, and the corresponding D-18 execution authority are verified. Synthetic policy fixtures in the passing test do not constitute those approvals.

### F06 delivery and maintenance follow-up (27 November 2026)

| Check | Result |
|---|---|
| Report scheduler, outbox, generic worker and concurrency unit regressions | **121 passed, 10 deselected** across `test_report_delivery_scheduler.py`, `test_wave1_operations.py` and non-PostgreSQL cases in `test_concurrency_postgres.py`. Covers idempotent enqueue, bounded claims, retry and reconciliation outcomes, provider timeout/unknown handling, recipient revalidation, and scheduler shutdown. |
| Isolated PostgreSQL delivery and worker concurrency cases | **8 passed, 6 deselected** from `test_concurrency_postgres.py -m studytomy_postgres -k 'idempotency or outbox or report_delivery or scheduler'`. Exercises unique idempotency races, `SKIP LOCKED` claims, lease recovery, stale finalization, recipient/policy revalidation, and scheduler polling. The test URL was assembled in-process from the named loopback `_test` container and was not printed. |
| Ruff and local CLI help | **Passed.** `ruff check` covered the CLI, outbox, report dispatcher, scheduler, generic worker, idempotency, archive-retention and learner services plus the exercised tests. `python cli.py report-outbox-status --help` exited 0. |

The existing scheduler is a single default-off lifecycle task. Its current dispatcher is intentionally restricted to `PROGRESS_REPORT`; it re-resolves the published report, recipient, relationship or scoped grant, consent and policy immediately before sending. The email adapter receives the stable outbox idempotency key for supported providers. Provider timeouts/ambiguous outcomes enter reconciliation and are never automatically resent; this is at-least-once-safe handling, not a claim of exactly-once email. The existing aggregate status and bounded drain CLI are local `_test`-database-only; the drain additionally requires explicit opt-in and loopback SMTP.

**F06 remains PARTIAL / policy-gated.** There are allowlisted outbox purposes for required notices, receipts, optional reminders and marketing, but no current dispatchers/producers for those purposes beyond the reviewed progress-report path. Optional reminders, marketing and guardian study-path mail were not enabled. Assessment-archive removal and case-evidence removal remain explicit reviewed operations, not automatic cleanup. Idempotency records carry an engineering replay horizon (`RETENTION_DAYS = 30`) but are not purged; no D-13 retention approval for deleting these records was verified. `review_due_at` currently affects eligibility, but no separately approved staff-escalation channel/purpose was verified. No deletion, policy change, email send, schema migration, commit or push occurred.

### F07 readiness and governance follow-up (27 November 2026)

| Check | Result |
|---|---|
| Readiness, course-evidence and offer API regressions | **7 passed, 114 deselected** across `test_offer_readiness.py`, the readiness selection in `test_wave1_operations.py`, and the readiness selection in `test_content_evidence.py`. This includes a valid exact reviewed release clearing the course-review blocker and a changed course revision restoring that blocker. |
| Readiness/governance UI regression | **1 passed**, 0 failed; 11 assertions. The test confirms that the existing panel calls real governance/draft workflows, communicates that policy drafts are not approvals, and does not render an activation control. |

The exact course-release predicate is implemented in `readiness.py`: it checks the tenant/course, independent active education reviewer, current scoped review assignment, snapshot digest, and current release-policy binding. The governance and private-offer draft flows are wired in the existing course general page through `ReadinessPanel.tsx`; the panel presents policy version/status, effective dates, evidence references and required/recorded approver responsibilities. The current policy contract has no next-review date, and the UI says so explicitly.

**F07 remains PARTIAL.** Source inspection found no `StudyCapacity` staffing/support-capacity record or persisted D-17 pilot-acceptance decision model. `StudyCapacityHold` is a checkout reservation, not evidence of staffed support, and metrics are not pilot acceptance. Readiness therefore retains `SUPPORT_CAPACITY_EVIDENCE_MISSING` and `PILOT_ACCEPTANCE_EVIDENCE_MISSING` even when course review passes; `ready_to_sell` remains false. No evidence or approvals were fabricated, and no schema change was introduced. A future completion requires the approved D-04 capacity evidence contract and D-17 criteria/acceptance record, then tenant-scoped validity predicates and dedicated regressions.

### F08 credentials and F09 sale-gate follow-up (27 November 2026)

**F08 is BLOCKED by required human decisions.** `ADR-LEARNER-CREDENTIALS.md` remains proposed and records D-01, D-03 and D-12 as unapproved, with no selected credential option, cohort, named approver or evidence. The current identity contract remains email-based (`UserBase.email: EmailStr`); the learner profile references an existing user and does not provision credentials. No auth, schema, placeholder-address, guardian-login or recovery change was made. This finding needs the authorized owners to record the exact policy/ADR decision before implementation can continue.

For F09, the current order acceptance checked an exact D-17 offer-version allowlist but passed a literal `sales_open=True` into capacity reservation. The code now requires current F07 readiness for every course in the offer, combines that result with the exact persisted D-17 version binding, hides not-ready offers from checkout, and passes the computed state into bounded reservation. A not-ready acceptance returns `OFFER_SALES_PAUSED` before acceptance or hold writes. Since F07 currently has no approved support-capacity or pilot-acceptance evidence contract, the actual checkout list remains empty and sale acceptance stays closed. Private quotes/drafts remain non-sale operations. No state schema, payment provider or finance behavior was added.

| Check | Result |
|---|---|
| Offer quote/readiness API regressions | **4 passed** (`test_order_quote.py` and `test_offer_readiness.py`). The acceptance regression verifies that an exact D-17 offer still cannot be accepted without F07 readiness and that no `StudyOrderAcceptance` or capacity hold is written. |
| PostgreSQL signed-checkout regressions | **12 passed, 1 deselected** from `test_checkout_router.py -m studytomy_postgres -k signed_checkout_accepts_only_retained_approved_terms`. The real readiness predicate exposed no sale-ready offers; downstream settlement scenarios used an explicit test-only readiness stub after that assertion to keep testing their separate authorization, manual collection, correction and refund boundaries. This is synthetic integration evidence, not proof of live sale authorization. |
| Ruff and `git diff --check` | **Passed.** Ruff covered the changed order service and its API tests. |

**F09 remains PARTIAL / blocked by F07 and policy.** No actual support-capacity or pilot-acceptance records can satisfy the readiness predicate, and no sale-enabled offer state exists beyond the exact D-17 pilot allowlist. The existing award and assessed-pass blockers remain intact; payment collection/refund remains manual. D-02/D-04/D-05/D-17/D-18/D-20 and an approved provider/market contract must be verified before any real sale. The test-only readiness stub above is limited to regression coverage of downstream mechanics and is not stored policy evidence.

### Remaining findings follow-up (27 November 2026)

The source review continued through F10–F21 at `b7e57929eba1230577fe23767aac162fefccf80d`. All verification used local files, synthetic fixtures or the previously recorded disposable PostgreSQL run; no GitHub workflow was triggered, and no application deployment, commit or push occurred.

| Finding | Status and evidence |
|---|---|
| F10 | **Locally verified:** root and API images built under OrbStack in the preceding receipt, with non-root runtime, `ffmpeg`, packaged momentum manifest and writable paths checked. The web image had a separate build/smoke check there. No service health transition was exercised. |
| F11 | **Partial:** the focused guidance, policy, topic-check and learning-surface API set passed **78**, with **3 PostgreSQL cases skipped** because `STUDYTOMY_TEST_DATABASE_URL` was not configured for this invocation. Focused tutor/configuration and learning-surface web tests passed **15** (89 assertions). Policy resolvers are fail-closed for D-10/D-13; real-user activation still requires current policy/consent approvals and a live learner journey. The four-value `LessonTutorAction` enum remains vocabulary only, with no production route/caller. |
| F12 | **Partial / approval-gated:** guest challenge issuance and redemption use hashed single-use tokens, rate limits, D-04/D-12 authorization and a feature opt-in; existing PostgreSQL case tests were included in the earlier isolated StudyTOMY run (**93 passed** across that suite). New case categories, external contact, or sanction/appeal policy remain unavailable until the named decisions exist. No real contact was sent. |
| F13 | **Focused checks passed:** organization help-route and active-navigation tests passed (**3**). Staff workspaces are projected by the authorized API and linked from desktop/mobile organization navigation. No signed-in role-by-role browser acceptance was run. |
| F14 | **Focused checks passed:** edition-route gate, navigation and billing-webhook test group passed (**18**). OSS routes return 404 through the route gate; billing webhook checks its feature guard before payload processing. Paid-edition behavior remains source/test verified, not exercised against a licensed runtime. |
| F15 | **Blocked by policy:** recurring memberships, withdrawal remedies, promotions and managed-learner community remain unavailable pending D-05/D-06/D-07/D-11/D-18/D-19/D-20 and a separately approved provider. No commercial behavior or price terms were invented. |
| F16 | **Partial:** existing source-derived metrics and inventory-only backfill helpers remain present. The audited metric helpers are not exposed through a current staff metrics API/workspace; D-17 measures and TASK-069 service targets are not approved. No write-mode legacy migration or PostgreSQL backup/restore rehearsal was performed. |
| F17 | **Pilot implementation checked:** `bun run build`, `bun run verify`, and `bun run test` in `apps/labs/momentum-pilot` passed; **8 tests**. The workflow now contains a pilot test job. Hosted origin/CSP and qualified educator, safeguarding, curriculum and rights sign-offs remain open; no hosted deployment occurred. |
| F18 | **Not complete:** added a credential-free CI job for E2E typecheck and existing database, browser-egress and runtime-identity tests. Locally, E2E typecheck passed; database guard **3**, browser-egress **1**, identity **4** passed. `actionlint .github/workflows/ci-build.yml` and `git diff --check` passed. This verifies the workflow syntax and helper contracts only. The full author → independent reviewer → publication → learner → staff acceptance and AC-01–34 matrix remain **NOT RUN**; no current isolated live E2E fixture/runtime identity was provisioned in this continuation. Policy-denial tests do not count as a completed business scenario. |
| F19 | **Targeted docs state is accurate:** demo podcast/audio removal is described, local PG/Redis ports are documented as `5433`/`6380`, and the portal guide states generated JavaScript controls are disabled and the momentum lab is separately governed. The local docs snippet suite passed **3**; the previous docs build produced **461 pages**. This is not a fresh exhaustive external-link/fragment audit. |
| F20 | **Verified locally:** workflow action references are immutable and the existing `uv` version is pinned to `0.10.7`; CLI and Compose Redis references both use `redis:8.6.1-alpine`. E2E database-guard tests passed **3**, and `actionlint` passed. Unreferenced helper deletion and a native macOS/PostgreSQL restore rehearsal were not performed. |
| F21 | **Scope preserved:** later exam generation, advanced marking, self-service family updates, new reminders, AI marketing and Momentum Lab P3–P6 remain roadmap/proposal scope; no later feature was promoted to a launch commitment. |

The new local outbox status and drain commands both fail closed unless the configured target is a loopback PostgreSQL database ending in `_test`, with no routing query. The drain remains a bounded opt-in through loopback SMTP; it was not executed. Its regression file now has **17 passing tests**, and Ruff passes. No delivery or real-recipient email was sent.

### F01 credit-settlement follow-up (27 November 2026)

The playground reservation now carries a unique refund operation key. `refund_ai_credit()` can apply that refund atomically with a Redis `SET NX EX` marker, and an ambiguous Redis response gets at most one retry using the same key. The marker uses the existing 30-day engineering replay window; this is not a D-15 retention-policy decision. A replay of the same operation is suppressed while Redis retains its marker. Ordinary callers without an operation key retain the previous refund behavior.

Verification after this change:

| Check | Result |
|---|---|
| `uv run --no-sync pytest -q src/tests/security/test_ai_credit_atomic.py src/tests/security/test_remediation_ai_credit_refund.py --tb=short` | **37 passed**. Covers same-key replay, org scoping, refund retry, successful settlement, setup failure, invalid output, provider error, disconnect and cancellation. |
| Broader F01 credit, board/playground and OAuth regression suite | **215 passed**, 0 failed; 6 warnings from existing test setup invoking async session methods without awaiting them. |
| `uv run --no-sync ruff check src/security/features_utils/usage.py src/routers/playgrounds/credit_stream.py src/tests/security/test_ai_credit_atomic.py src/tests/security/test_remediation_ai_credit_refund.py` | **Passed.** |
| Isolated `redis:8.6.1-alpine` loopback container with the actual Python Redis client and Lua script | **Passed.** Refunding 3 from 8 produced 5; replaying the same key kept the counter at 5; the 30-day marker TTL was present. The temporary container was removed. |

**F01 remains PARTIAL for crash recovery:** no durable record tracks a reserved request whose process exits before it can attempt a refund. The idempotency marker makes an in-process retry safe while Redis retains the marker, but it does not provide a reconciliation worker or exactly-once settlement across process crashes. No blind retry, database schema, or persistent ledger was added.

Resumed verification on 27 November 2026:

| Check | Result |
|---|---|
| F01 credit, board/playground, MCP OAuth and protected-learner regressions (`test_ai_credit_atomic.py`, `test_remediation_ai_credit_refund.py`, `test_ai_chat_router.py`, `test_boards_router.py`, `test_playgrounds_router.py`, `test_mcp_oauth_router.py`, `test_ai_approval_gate.py`) | **81 passed**, 0 failed. |
| Ruff for the F01 routes, refund helper and focused tests | **Passed.** |
| `git diff --check` | **Passed.** |

The refund operation key is generated per request and retained only by the active reservation; Redis makes a same-key retry safe while its bounded marker exists. There is no durable ledger/reconciliation record for a process crash after reservation and before refund confirmation. This remains an explicit recovery limitation; this implementation does not claim exactly-once recovery across process crashes.

### F10 current-image health and mount smoke (27 November 2026)

Using OrbStack and the locally built `studytomy-local-audit-api-20261127:local` and `studytomy-local-audit-root-20261127:local` images, a fresh internal-only Docker network, and disposable PostgreSQL 19 / Redis 8.6.1 containers, the API and combined images completed the existing startup path and became `healthy`. The unauthenticated `/api/v1/health` route returned HTTP 200; the combined image's `/api/health` probe also returned HTTP 200. Synthetic install data and random local-only credentials existed only in the disposable database/environment.

The combined image ran as UID 1001, included `ffmpeg`, and found the momentum manifest at `/app/labs/momentum-pilot/manifest.json`. The API image ran as UID 1001, included `ffmpeg`, and found the manifest at `/labs/momentum-pilot/manifest.json`. A UID-1001 bind-mount write/cleanup check passed against a temporary host directory. The test containers, temporary database data and network were removed; no existing named volume was mounted or modified. One earlier probe failed at the expected empty-database auto-install gate, and a follow-up corrected the disposable initialization inputs before the successful health run; no application source change was needed.

### Final local verification and virtual-lab research reference (27 November 2026)

This verification began on `main` at `b7e57929eba1230577fe23767aac162fefccf80d` with a dirty worktree. During the run, `main` advanced to `bafc499dc5a98a570fbaa49e58bc4c3c6ddd945`, now matching `origin/main`; that commit includes the virtual-lab research reference and the previously modified implementation/test files. I did not create that commit. The checks below ran against the same file contents now represented by that commit. The final worktree retains this appended verification entry and the pre-existing untracked `courses/` directory. I made no push, deployment, shared-data migration, or production change.

| Check | Result |
|---|---|
| Web unit suite (`cd apps/web && bun run test`) | **809 passed**, 0 failed across 93 files; 2,567 assertions. |
| Web route type generation and TypeScript | `bun ./node_modules/next/dist/bin/next typegen` succeeded; `bun ./node_modules/typescript/bin/tsc --noEmit` succeeded. `apps/web/package.json` has no dedicated `typecheck` script. |
| Web lint | `bun run lint` exited 0: **0 errors, 965 warnings**. Warnings are existing repository-wide findings and were not bulk-fixed as unrelated cleanup. |
| Web production build | `bun run build` passed; Next generated all 41 static pages. Existing Turbopack dynamic-filesystem tracing warnings remain. |
| E2E checks | `bun run typecheck` passed; `bun run test:identity` **4 passed**; `bun run test:browser-egress` **1 passed**. These validate harness safety/type contracts, not the live learner journey. |
| Documentation snippets/build after adding the virtual-lab reference | `bun run test:snippets`: **3 passed**. `bun run build` passed and generated 461 pages. OpenAPI fetching returned 404 and used the bundled snapshot; sitemap generation was skipped because no verified `NEXT_PUBLIC_DOCS_URL` was configured. |
| Disposable PostgreSQL 19 | Fresh loopback-only `_test` DB migrated through `python cli.py migrate`; both reported schema heads were `2d6a1b7c9e40`. `test_migrations_postgres.py -m studytomy_postgres`: **4 passed, 1 expected skip**, with the separately provisioned two-phase volume persistence case passing in both seed and verify phases (**1 passed each**). Full `pytest -q src/tests/studytomy -m studytomy_postgres`: **93 passed, 424 deselected**. Temporary container/data were removed. |
| API non-PostgreSQL suite | A broad run reported **10,717 passed, 14 skipped, 1 failed**: `test_adaptive_curriculum_drafts_require_direct_session_exact_scope_and_native_access` returned 401 instead of its expected 403 during the broad parallel run. The complete containing file passed in isolation (**1 passed, 1 skipped**) and the individual test passed in isolation. Treat the broad-run discrepancy as unresolved/intermittent; do not weaken the authorization assertion. |
| API targeted regressions/Ruff | The final F01 credit/refund, AI chat, boards, playground, MCP OAuth and AI approval group passed (**81 passed**); changed test-focused Ruff checks passed. The 93-test focused setup/mocking regressions also passed in their recorded command. |
| Repository diff hygiene | `git diff --check` passed. |

The supplied virtual-laboratory research is recorded in [VIRTUAL-LAB-REPOSITORY-RESEARCH.md](VIRTUAL-LAB-REPOSITORY-RESEARCH.md), linked from the [Studytomy documentation index](README.md) and root [AGENTS.md](../../AGENTS.md). It is a candidate inventory only: repository-level OSS status does not clear third-party code, assets, datasets, or educational content. Each candidate still requires immutable-revision license and provenance checks plus scientific, syllabus, educator, rights, privacy, safety, and safeguarding review. No new laboratory source was copied or integrated.

**Still open:** F01 remains partial because a process crash between reservation and refund confirmation has no durable reconciliation record. The broad API 401/403 discrepancy remains to be root-caused. Full real signed-in multi-role acceptance remains unrun because this continuation did not establish an isolated application stack with verified source identity and pre-provisioned synthetic reviewer/learner data. F08/F09 and other policy-dependent activation remain gated by their named decisions. The successful mocked, unit, and service-level checks are not substitutes for those gates.
