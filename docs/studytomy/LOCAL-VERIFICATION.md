# AUD-18 local verification record

- **Recorded:** 2026-11-18
- **Source revision tested:** `029f1af19d26619a12ccbd162cba4ba1864505df`
- **Git tree:** `7f3e2652fb5ff3942790822a9fc2ee4c1313eadc`
- **Checkout:** primary `main` worktree at `C:\Users\DELL\repos\studytomyportal`; no parallel worktree was used.

The checks below ran against the source tree that was committed as the listed revision. This is a local verification snapshot, not a CI run, browser acceptance, live-provider evaluation, release approval, or deployment evidence. The repository has no configured CI/CD workflow. No production migration or real-learner flow was run.

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

## AUD-05 managed media browser and filesystem checks

- At `d9f57753f8d79d98f407e29378dc7348d1bc93aa`, Playwright type checking and the mobile Chromium spec `13-managed-release-media.spec.ts` passed (**1 passed** in 36.3 seconds). The isolated frontend used real synthetic sign-in against a loopback API with disposable PostgreSQL and Redis. The spec intercepted the release list, lesson index and first-use media response. It verified the mixed-format list, unavailable video and audio states, PDF frame URL without a token, and return to the activated lesson list. Two earlier runs found incorrect fixture assumptions about the organization UUID and the post-activation label.
- At `2e77df3b67a70a47d8981b7bddce4cc364695c0f`, the PostgreSQL `free-release-media` test passed (**1 passed, 11 deselected** in 18.28 seconds). It exercised authenticated PDF delivery and byte ranges using the filesystem adapter with a temporary content root. The delayed and corrupted streams used for fault injection were simulated.
- A combined browser journey through real release and media endpoints is still required to verify actual PDF rendering, video playback and accessibility.
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

## AUD-07 and AUD-08 collaboration recovery verification

- **Recorded:** 2026-11-18
- **Source revision inspected/tested:** `4cd9097d834d6b7e2c576903ac90f263a6b79d17` (the collaboration source was unchanged by subsequent verification-document commits).

| Scope | Command | Result |
|---|---|---|
| Collaboration fault-injection and queue tests | `bun run test` from `apps/collab` | **PASS:** 43 passed, 0 failed across 9 suites. |
| Collaboration TypeScript build | `bun run build` from `apps/collab` | **PASS:** `tsc`, exit 0. Its generated `dist` outputs were removed after verification; no build outputs remain in the worktree. |

The load tests distinguish an explicitly marked empty board from Redis failure, API timeout, HTTP 503, malformed empty response and empty-present state; failed loads do not create/store a replacement document, and a later recovery loads the original content. The queue tests cover retry after non-2xx, persistence of the last edit without a further edit, per-board write ordering, retained state after bounded shutdown timeout/retry exhaustion, and aggregate memory limits. The shutdown handler was inspected: it logs unresolved board IDs and exits nonzero after its bounded flush deadline. That handler itself was not executed by the unit suite. These tests use synthetic adapters and in-memory Hocuspocus documents: live Redis/API outage recovery, deployment connector compatibility, and end-to-end multi-client recovery remain unverified. Existing authorization rechecks and read-only downgrade were left intact. No schema change or service was added.

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
