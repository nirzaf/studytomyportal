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
