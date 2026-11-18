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
