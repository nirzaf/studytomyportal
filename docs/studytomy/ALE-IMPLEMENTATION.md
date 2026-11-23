# Adaptive Learning Engine implementation evidence

**Reading order:** chronological checkpoint evidence. ALE-001 is a historical baseline, not current absence of the features added below. Use current source and the local pilot runbook first; preserve each dated test scope rather than summing counts or treating a recorded migration head as current.

## ALE-001 and CASE-01

Read the two local ALE source documents in full. The atomic specification references `4bff1a6a4124f93ed516f90378c65f48c1bd2741`. The inspected local `main` and the reported `origin/main` at `https://github.com/nirzaf/studytomyportal.git` were both `43f314540404c1c1478744e6bf7a53802d1fb430`. The checkout included 120 staged paths, 18 paths with unstaged modifications (14 also staged), and 22 untracked paths before ALE edits. These include family learning, GENUI and course publication evidence. Existing index and worktree contents were preserved.

Read the root and web `AGENTS.md` and relevant README scope. The existing frontend uses Next.js and Bun 1.4.4. The installed API Python is 3.14.7 and satisfies `apps/api/pyproject.toml`. The existing web instructions require consulting installed Next.js documentation before frontend edits.

| Responsibility | Observed state and code | Ownership decision |
|---|---|---|
| Policy and approval | **Implemented** in working source. `db/studytomy/governance.py` owns policy versions and approvals. `services/studytomy/learners.py:_active_policy` checks body validation, current named-owner approval and membership. Governance is mounted in `src/router.py`. Real approval evidence is **unverified**. | Reuse these records for ALE eligibility, consent and retention prerequisites. |
| Learner identity and scoped staff | **Partial** for ALE. `db/studytomy/family.py` binds a private learner profile to an existing organization member and records eligibility decisions. `security/studytomy_access.py:evaluate_studytomy_staff_access` checks capabilities, assignments, scopes and exclusions. The mounted family routes already manage learner and staff workflows. | Reuse profiles, user accounts and scopes. Verify each eventual adaptive action's subject and resource scope. |
| Guardian and purpose authority | **Partial**. `services/studytomy/authority.py:has_active_guardian_scope`, `consents.py:has_current_consent_scope` and `sharing.py:has_active_independent_sharing_scope` provide current-purpose resolution. No ALE report retrieval currently binds these decisions to exact recipient projections and course coverage. | Reuse relationship, sharing and consent ledgers. Extend the current resolvers where precise report coverage is required. |
| Curriculum, reviewed items and diagnostics (DB-01–06) | **Missing**. Native course content and assignments exist. `services/studytomy/content_evidence.py` binds private review evidence to a course snapshot. That draft evidence does not freeze adaptive concepts, reviewed items or diagnostic selection. | Adaptive definitions will own versioned curriculum, concept/edge/map, item and blueprint records. Reference the existing course/activity and rights evidence. |
| Attempts, response journal and mastery (DB-07–10) | **Missing** for ALE. Native task submissions are mutable. `services/courses/activities/assignments.py:retry_assignment_submission` clears task results and advances the round. `_apply_grade_and_finalize` commits native grade changes and serves multiple callers, including recalculation. Existing course completion and SCORM scores have different purposes. | Keep native grade and completion ownership. Add an adaptive attempt/issued-item owner and one append-only evidence journal feeding a derived mastery projection. Bind native evidence at its transaction, before retry or deletion can discard source state. |
| Guided teaching (DB-11–12) | **Partial**. `services/ai/ai.py` owns activity access and ordinary lesson chat, with Redis history in `base.py`. Mounted AI routers use `require_studytomy_learner_ai_policy`. GENUI uses `db/studytomy/practice.py`, `learning_practice.py`, `learning_generation.py` and `learning_actions.py` to bind deterministic practice to lessons. The React lesson already uses `AIActivityAsk` and `ActivityPracticePanel`. | Reuse lesson UI, course access, provider and validated surface contracts. Guided sessions and turn recovery still need a durable authority and provenance owner. Keep GENUI practice separate from graded evidence. |
| Reviewed progress reports (DB-13–14) | **Missing**. Family workspace and private evidence files exist. Source search found no reviewed report snapshots, releases or exact-version recipients. | A single adaptive report snapshot owner and one recipient projection ledger. Reference current guardian authority on every disclosure. |
| Events, idempotency and delivery | **Implemented** in working source for shared operations. `business_events.py` writes transactional records. `idempotency.py` and `outbox.py` own replay and durable delivery in `db/studytomy/operations.py`. Existing audit writes use a separate session. | Reuse the shared ledgers. Optional report notification stays conditional on an approved service scope. |
| Availability (API-01) | **Missing** at baseline. GENUI availability is activity-specific and deliberately approval-gated. | ALE-002 adds a session-authenticated organization availability contract with default-off config and independent prerequisite decisions. |

Validated with `git status --short --branch`, `git rev-parse HEAD`, `git ls-remote origin refs/heads/main`, source and caller searches, and `uv run --no-sync alembic heads` from `apps/api`. The worktree migration head was `9b2c7e4a16d3` (course evidence). ALE-001 neither ran a migration nor changed runtime behavior. The pre-existing index has whitespace warnings in the family specification, `services/studytomy/privacy.py` and `test_private_community_work.py`.

The first runtime slice is ALE-002. Check API-01 with the current shared governance resolver, no provider calls, ordinary assignment and AI regressions, and current membership and session policy. The baseline SQLite suite covers route behavior. The opt-in PostgreSQL fixtures in `src/tests/studytomy/conftest.py` require an explicit loopback disposable database ending in `_test`. Native assignment tests include `test_assignments_retry.py`, `test_assignment_grading.py`, `test_assignment_manual_grading.py` and `test_assignment_api_token_access.py`. The shared authority and AI guard tests live under `src/tests/studytomy`.

## ALE-002 and API-01

`GET /api/v1/studytomy/adaptive/orgs/{org_uuid}/availability` uses existing session authentication and organization session policy. Organization membership is required, including for administrative users. Responses exposing eligibility use `Cache-Control: private, no-store`. A missing, malformed or ambiguous config stays off. Only a JSON boolean at `admin_toggles.studytomy_adaptive_enabled` can enable the availability flag.

The endpoint distinguishes the organization flag, the caller's own current learning eligibility and the policy prerequisites for diagnostic, guided AI and report actions. It bases eligibility on an existing private active profile and a current human decision. Policy version UUIDs, approval evidence and learner records stay server-side. Course and learner authorization, AI purpose consent and exact-recipient authority still require checks at their respective resource actions. Until those checks and actions exist, the allowed-action list stays empty. Ordinary LearnHouse behavior and the independent managed-learner AI restrictions are preserved. This slice makes no schema change.

| Check | Environment | Result |
|---|---|---|
| API-01 and mounted AI policy tests | `uv run --no-sync pytest -q src/tests/studytomy/test_adaptive_access.py src/tests/studytomy/test_ai_approval_gate.py`. PostgreSQL case was added after this focused run. | 10 passed |
| Integrated Studytomy, native assignment, RAG authorization and migration tests | Full `src/tests/studytomy` directory plus `test_assignments_retry.py`, `test_assignment_grading.py`, `test_assignment_manual_grading.py`, `test_assignment_api_token_access.py`, `test_assignment_submission_ownership.py`, `test_rag_access_scope.py`, `test_migration_graph.py` and `test_migrations.py`. | 377 passed, 0 failures or skips. One ALE availability test and seven existing marked tests used PostgreSQL. Other tests used their normal fixtures. |
| Authentication, MFA and AI authorization regressions | `test_auth_runtime.py`, `test_auth_policy.py`, `test_mfa_org_policy.py`, `test_require_authenticated_non_api_token.py`, `test_superadmin_api_tokens_auth.py` and `test_ai_authz_and_clamp.py` with `-q -rs`. | 124 passed, 1 skipped because the Enterprise module is unavailable in this build. |
| Lint and diff | Ruff on the new route, service and test. Git whitespace check on edited existing files. | Passed. |

The PostgreSQL run used a dedicated disposable `pgvector/pgvector:pg16` container with a loopback-only ephemeral port and `studytomy_ale_test`. `ensure_schema()` received only the explicit test URL and used the supported fresh bootstrap/stamp path to `9b2c7e4a16d3`. The existing PostgreSQL tests use independent real PostgreSQL connections for shared operations. The new availability test is a read check. There is no ALE mutation, new Alembic revision, parent upgrade, or ALE-specific concurrency result at this checkpoint. No external provider ran.

ALE-001 and ALE-002 are complete for this disabled implementation. ALE-003 has a self-bound eligibility read; resource-scoped access and its negative tests remain open. Existing guardian consent resolution does not by itself prove ALE-004 course-level recipient decisions. Continue the staff and learner scope checks and the independent curriculum-definition schema work. Real learner activation, report disclosure, approved curriculum and educational acceptance still require verified decisions. No ALE work is published.

## ALE-006 and DB-01–DB-04

The additive revision `472f0b3ca571` follows `9b2c7e4a16d3`. `db/studytomy/adaptive.py` owns curriculum revisions, concepts, prerequisite edges and lesson mappings. Composite foreign keys bind children to the same organization and curriculum revision. The PostgreSQL guards prevent mutation of published or retired definitions and their children. A published revision can be retired while preserving its reviewed fields.

Lesson maps store a historical identifier, version and digest snapshot. The PostgreSQL write guard checks the live activity, course, organization and version before accepting a mapping. A mapping confers no native course permission. Keeping native identifiers as validated snapshots allows a removed lesson to remain identifiable in evidence without requiring a native schema change. ALE-007 still needs to compute the digest server-side, check rights and approval, and revalidate that a mapped lesson remains current at publication.

Parent-row writes serialize competing prerequisite edits. At read committed the second writer sees the first edge and the cycle fails. At repeatable read PostgreSQL rejects the stale writer with a serialization failure. The database guards execute on PostgreSQL. SQLite provides the schema-level foreign key definitions and check constraints for existing fixtures.

| Check | Result |
|---|---|
| Fresh PostgreSQL 16 bootstrap with `ensure_schema(explicit_test_url)` | Stamped `472f0b3ca571` and installed guards. |
| Separate populated parent rehearsal using an explicit Alembic connection | Upgraded `9b2c7e4a16d3` to `472f0b3ca571`. Retained the synthetic organization record. Four triggers, model column names and named constraints matched. |
| `test_adaptive_curriculum_postgres.py` on the upgraded database | 5 passed. Covered ownership, uniqueness, immutable review state, wrong and stale native source references, lesson removal and concurrent graph edits under two isolation levels. |
| Populated downgrade rehearsal | Refused a downgrade with an existing synthetic curriculum draft and retained the head and row. |
| Regressions on the freshly bootstrapped PostgreSQL database and normal test fixtures | 352 passed, 0 failed, 0 skipped, with two unawaited mock warnings in existing organization-auth tests. Ran all Studytomy tests plus the migration graph and migration suites, native assignment retry and grading, and organization authorization. |
| Ruff and Git whitespace checks | Passed on the new Python files and edited tracked paths, respectively. |

Both PostgreSQL databases were disposable and exposed only on an ephemeral loopback port. No learner data, approved curriculum, curriculum API or production migration was added. CASE-10 and CASE-11 have database evidence. CASE-44 has fresh and populated migration evidence for this revision. ALE-003 staff scope and ALE-007 review and publication remain open.

## ALE-003 and ALE-007 authoring checkpoint

Adaptive routes now check the signed session's authentication provenance. They accept a direct password, self-service magic login, Google or SSO session. Raw API tokens, API-token-origin sessions (including admin-issued sign-in links), external LMS launches and sessions without direct provenance are refused on these routes. Existing LearnHouse session behavior outside ALE is unchanged. Learner availability still resolves only the authenticated account's private profile.

The API-02–04 and API-07 staff paths use both native course update permission and the explicit default-deny `adaptive_curriculum_author` capability with a current education owner assignment and exact course grant. The publishing capability is separate. Drafts created by the API have an immutable owner course UUID. Added the owner course field to the unpublished `472f0b3ca571` migration and kept it nullable for unbound records. The API refuses to edit an unbound revision. Mappings also require grant and native edit permission for every involved course, including courses from mappings being removed. Their lesson digests are calculated on the server.

Creation uses the existing idempotency receipt and business event in one transaction. A replay checks current scope and returns the original safe receipt. Metadata, concept graph and mapping replacements require the current draft version. Cycles, wrong parents, stale sources and attempts to remove a mapped concept are rejected before any committed change.

The current checkpoint covers staff authoring and self-bound learner eligibility. ALE-003 still needs scoped academic learner operations as their routes are added. ALE-007 still needs exact-definition review, current policy and rights evidence, publication and retirement. No curriculum or learner flow was activated.

- **API integration:** 375 passed, 0 failed, 0 skipped. Ran the complete `src/tests/studytomy` directory with PostgreSQL enabled, both migration suites, native assignment retry and grading, session provenance, and AI-tool People and organization permission regressions.
- **Migration and PostgreSQL:** Fresh bootstrap stamped `472f0b3ca571`. A separate loopback-only disposable PostgreSQL 16 database upgraded a populated `9b2c7e4a16d3` parent. The synthetic organization remained intact and the named constraints, model columns and four triggers matched. On the upgraded database, 6 PostgreSQL-selected tests passed and 1 SQLite test was deselected. The HTTP authoring test uses signed synthetic sessions, native RBAC and the real PostgreSQL tables. Its outer test transaction rolls back all fixture data.
- **Web:** 719 Bun tests passed, 0 failed. The pinned local compiler passed `tsc.exe --noEmit`. The web change is the new capability type and its default-deny value.
- **Scope:** Changed the ALE route, access and curriculum services, curriculum storage and migration, staff capability declarations, and focused tests. No ALE commit or push. Real approvals remain unverified.
- **Cleanup:** Docker Desktop briefly stopped responding after the PostgreSQL checks. When the daemon responded again, it confirmed that the disposable authoring container no longer existed.

## ALE-007 curriculum review and publication

The authoring API now provides a private review snapshot under `GET /curriculum-revisions/{uuid}/review`. Its versioned SHA-256 binds the draft identity and version, educational fields, rights and source references, ordered concepts and prerequisites, and lesson mapping snapshots. An authorized curriculum author or content reviewer needs scope for the owning course and each mapped course. The response includes the manifest and digest for the reviewer to check.

Curriculum publish and retire use API-09 and API-10 with `kind=curriculum`, an idempotency key and the expected version. Publication requires the existing `content_release_review` scope, current named-owner policies D-02, D-09, D-10, D-15 and D-16, and a current owner course. The reviewer submits the expected manifest digest and the specific review evidence reference.

- D-02 must specify list-valued `subjects`, `learner_levels` and `languages` covering the draft. Its `curriculum_claims.adaptive_curriculum_keys` must cover the curriculum key.
- D-16 must approve the rights reference in `content_rights.adaptive_curriculum_rights`. Its `review_approvals.adaptive_curriculum_reviews` must include one exact entry binding the revision UUID, digest, rights reference and review evidence reference. These structures define the software contract. Actual approval values come from named owners.
- Each mapped lesson is verified against the current course, activity revision and content digest in the publishing transaction. The revision's digest, reviewer and policy versions are frozen. Publication and retirement both record a business event and idempotency receipt in the same transaction. Retirement retains the published snapshot and requires current reviewer scope and policies.

The curriculum definition workflow is implemented locally through retirement. Item and diagnostic definition kinds are still pending, as are academic learner routes and full educational and real-user acceptance. Only synthetic policies and evidence were used for tests. No real curriculum was published.

- **Focused:** 7 passed, 0 failed or skipped across the curriculum HTTP and PostgreSQL model suites. The signed HTTP workflow ran on both SQLite and disposable loopback PostgreSQL 16 and checked missing approval, stale digest, changed evidence, changed lesson content, publish replay and immutable retirement.
- **Integration:** 293 passed, 0 failed or skipped. Ran all Studytomy tests with PostgreSQL enabled, the migration graph, assignment retry and grading, and session provenance. Fresh bootstrap used `472f0b3ca571`; this slice added no schema change. Ruff and the worktree whitespace checks passed.

## ALE-008 native item draft snapshots

Added DB-05 in `5e071b3aaf62` after `472f0b3ca571`. A composite key pins each item to one concept in the same organization and curriculum revision. Native assignment, task and question identifiers are preserved as provenance. The PostgreSQL guard prevents any change to the captured source, public question or private answer. Updating a native question creates a new item revision within the same family. A draft can still receive a versioned difficulty, hint ladder and reviewed distractor-code mapping.

The staff-only source preview and API-05 resolve the native task through its actual assignment activity, chapter, course and organization. Authoring requires current native course edit permission and an explicit adaptive author grant for the source and concept's owning course. API-06 updates draft metadata using the expected version. Staff responses use the public question schema plus staff-only provenance and configuration, with no answer key. Future learner delivery can reuse the question schema without sending the staff wrapper, source digest or hints.

Supported imports are native plain-text single-choice questions with exactly one explicit correct choice and bounded finite numeric questions. Native questions with answer reveal configured are marked `possible_native_reveal`. Other native questions are marked `unverified_native_history`; current access settings cannot establish historical non-exposure. Both are ineligible for independent diagnostics. The current draft API deliberately has no item publication or learner delivery action. Item review/publication, original standalone items where justified, and their approved exposure evidence are still open.

- **Focused:** 20 passed, 0 failed or skipped. Covers allowlisted question fields, malformed keys and numerics, signed-session staff scope, draft edit and replay, source re-revision, and PostgreSQL refusal of a direct key rewrite.
- **Integration:** 365 passed, 0 failed or skipped across Studytomy, migration, native assignment grading and retry, and assignment-content authorization suites. PostgreSQL-marked checks used real PostgreSQL 16.
- **Migration:** Fresh loopback Docker bootstrapped at `5e071b3aaf62`. A separate populated disposable database upgraded from `472f0b3ca571`. The curriculum record, new column set and guard were verified. One PostgreSQL HTTP workflow passed on that upgraded database, with one SQLite case deselected. The retained-item downgrade rehearsal refused removal and preserved the row and head.
- **Local scope:** Ruff and whitespace checks passed. No learner delivery, production migration, ALE commit or push.

## ALE-008 item review and ALE-009 objective scoring

The private item review endpoint returns the candidate and an exact digest. The digest binds the version, native provenance, evaluator, answer snapshot, rights, and reviewed hints and error codes. The answer snapshot is excluded from the response. API-09 and API-10 now accept `kind=item` alongside curriculum. Review authority is checked for both the source course and the curriculum owner course, independent of authoring permissions.

The current D-16 policy must list the exact item UUID, digest, rights reference, evidence reference and `allowed_use: practice_only` under `review_approvals.adaptive_item_reviews`. It must also continue to approve the published parent curriculum. Current D-08, D-02, D-09, D-10, D-15 and D-16 named-owner approvals are checked. Publication reloads and locks the native task, checks source identity and the answer snapshot, and persists the state change with a business event and an idempotent response. Retirement keeps the approved snapshot. Both native exposure classifications remain excluded from independent diagnostics even after review.

The pure `adaptive_objective_v1` evaluator accepts one exact issued choice or one bounded decimal string. It rejects unknown choices, wrong response types, malformed numbers, attached units and caller-supplied grading fields. Decimal subtraction and tolerance comparisons stay exact within the documented bounds. An absent or unsupported rubric returns `not_scored`. The evaluator returns reviewed misconception codes for valid incorrect responses and has no path to mutate a native grade, consume an attempt, or award mastery. Native grading was used as a parity reference where numerical semantics agree. ALE's exact Decimal comparison intentionally avoids floating-point boundary drift.

Private original items and approved exposure evidence for an independent diagnostic pool are outstanding ALE-008 work. The learner attempt routes still need to call the scorer using a reviewed issued snapshot and persist the result transactionally. The current review changes require no new migration and use synthetic policy data only.

- **Focused:** 13 passed with no skips across the item source tests and signed HTTP review workflow on SQLite and Docker PostgreSQL 16. The workflow exercises review without author permissions, stale digest and native source, wrong evidence, practice-only publication, replay and retirement.
- **Integration:** 334 passed with no failures or skips across Studytomy, native assignment scoring and retries, assignment content authorization and the migration graph. PostgreSQL bootstrapped against `5e071b3aaf62` in a disposable loopback-only container. Ruff and whitespace checks passed.

## ALE-008 private original items

API-05 accepts a bounded original single-choice or numeric question and a separate typed answer rubric. Scoped course editors use the existing draft, revision, review and retirement workflow. The API stores the answer privately and returns only the staff question projection and review digest. Native imports and private originals cannot share an item family. Equivalent decimal spellings use the same source hash, while the original bounded rubric strings remain available for exact scoring.

`19c0a82d4fe6` follows `5e071b3aaf62` and adds a database provenance check. Native rows keep non-null assignment and task identifiers. Private originals require those native identifiers to be absent. The existing immutable-source guard still applies. Downgrade refuses to remove the schema support when an original item is retained.

The exact D-16 item approval must specify `allowed_use: independent_diagnostic`, with a nonblank `exposure_evidence_reference`. `content_rights.adaptive_original_item_rights` must cover the original item's declared rights reference. The published parent curriculum and its own current approval are checked independently. Native items still require `practice_only`. A published original is eligible for blueprint consideration. Learner-specific exposure and authorization must still be checked when items are issued.

- **Focused:** 39 passed, 0 failed or skipped on the source, scoring, contract and signed HTTP suites. HTTP tests ran on SQLite and real PostgreSQL 16. Covered the signed staff boundary, private rubric response, mixed-family denial, approval with evidence, and denial when exposure evidence is absent.
- **Migration:** Fresh Docker PostgreSQL bootstrapped at `19c0a82d4fe6`. A separate disposable loopback database upgraded the populated `5e071b3aaf62` parent, retaining native provenance and its immutable key. A transaction with a retained original verified downgrade refusal and preserved the row and head before test rollback.
- **Integration:** 384 passed, 0 failed or skipped across Studytomy, migration graph and runtime, native assignment grading and retries, and assignment-content access. PostgreSQL cases used the fresh disposable loopback container. Ruff and whitespace checks passed.

All rights, reviews and exposure records in tests were synthetic. There is no learner issuance or activation in this slice. Next are ALE-010 blueprint publication and bounded selection.

## ALE-010 diagnostic blueprints

API-08 creates a scoped diagnostic draft, and the review routes now accept `kind=diagnostic` for exact-version publication and retirement. The blueprint stores the reviewed concept order, prerequisite graph within scope, independent item UUIDs and digests, distinct families, selector version, explicit caps and help/reveal/retake rules. The author selects already published private originals from the same curriculum revision. Native practice imports, retired item revisions, missing approvals, insufficient families and caps too low to meet coverage are rejected.

D-08 must specify diagnostic reveal modes and prohibit help under `assistance.adaptive_diagnostic`. Its `attempts.adaptive_diagnostic` must allow the requested number of attempts and retake interval. The D-09 `mastery_gate.adaptive_mastery_policies` entry supplies a named policy UUID, supported reducer version, family and window bounds, success threshold, minimum sessions, separation and review interval. The draft binds the policy's hash. D-16 `review_approvals.adaptive_diagnostic_reviews` must name the exact diagnostic revision UUID, digest and review evidence reference before publication. The publication transaction checks current review scope, curriculum, item approvals and policies, revalidates the manifest, and records the command and receipt. This path uses only synthetic approvals in tests.

The deterministic selector uses the reviewed order with no random seed. It visits every eligible required concept before probing, then uses one-band difficulty adjustments or explicit prerequisite edges. It excludes previously exposed families, never converts a skipped or duplicate family into scored coverage, and distinguishes coverage met, cap reached and insufficient items. It is available for use by the future attempt service. No learner issuance, grading persistence or activation was added.

The additive `7bfc5ca4ad19` revision follows `19c0a82d4fe6`. A separate populated disposable PostgreSQL 16 rehearsal preserved an existing original item and verified the new table and trigger. Downgrade refused to remove a retained diagnostic definition and kept the row and revision marker intact before test rollback. The same guard is installed on fresh bootstrap.

- **Focused:** 14 passed across signed HTTP, deterministic selection and migration contracts. The HTTP pathway exercised SQLite and PostgreSQL 16.
- **Final integration:** 389 passed, 0 failed or skipped across Studytomy, migration graph and runtime, native assignment grading and retries, and assignment content authorization. This run included the assessment-policy compatibility and stale item pool cases. Ruff and whitespace checks passed. The test database was disposable and loopback-only.

## ALE-012 attempt and evidence storage

`d6b2beaf0c43` follows `7bfc5ca4ad19` and adds DB-07–DB-10. Composite foreign keys tie attempts, issued items, evidence and mastery projections to the same organization, learner and curriculum. Native assignment locators retain assignment UUID, submission UUID, native attempt number and a stable round identity. A recreated submission has a different round identity even if its native attempt counter resets. Native submissions remain mutable and remain responsible for the native gradebook. There is no adapter or historical backfill in this migration.

`st_attempt_item` is the only durable owner of the normalized response. It references the immutable reviewed question and rubric by item revision, so neither evidence results nor mastery projections duplicate the answer or key. The PostgreSQL guards require a current reviewed manifest at diagnostic start and issuance, limit active diagnostics and outstanding items, reject repeat families within a diagnostic, and prevent edits to accepted responses. Retraction and correction append new journal versions to the same issued response. The PostgreSQL journal serializes sequence allocation on the learner profile. Write services should acquire that profile lock before locking attempts or their items. Projection watermarks cannot move backward or exceed the learner's committed journal.

PostgreSQL verification used a disposable loopback-only PostgreSQL 16 container. Fresh bootstrap reached `d6b2beaf0c43`. A separate populated rehearsal applied the migration from `7bfc5ca4ad19`, verified the retained learner, parent composite keys and all four new guards, and confirmed that downgrade refuses to remove a retained round. The focused evidence and contract suite passed 12 tests. Two independent-session races exercised the unique active-diagnostic boundary and ordered evidence writes across different rounds. Other focused checks included tenant boundaries, native round uniqueness, immutable accepted responses, correction ordering and watermark protection.

The final integrated suite passed **394 tests** with zero failures or skips across Studytomy, migrations, native assignment grades and retries, and assignment content authorization. The existing curriculum test now scopes row-count and key-snapshot assertions to its synthetic tenant and item family so committed race fixtures in the same disposable database do not interfere. Ruff and whitespace checks passed.

The migration creates durable storage and guards only. Learner attempt APIs, the deterministic mastery reducer, and native assignment capture still need to write to it under their policy and authorization gates.

## ALE-013 mastery reducer and ALE-014 evidence transactions

The shared D-09 mastery-policy contract now serves both blueprint validation and the mastery reducer. The reducer resolves the latest result for each logical observation at a fixed watermark, applies retractions, excludes ineligible, assisted and unknown observations from independent scores, deduplicates by item family and applies the reviewed evidence window. It uses UTC session starts for spacing and exact integer comparisons for thresholds. No eligible evidence yields a null score. Freshness can change without changing the demonstrated state or observed score.

The internal objective evidence writer re-evaluates the accepted immutable item on the server, checks learner ownership and the diagnostic's pinned mastery policy, and appends the result and its mastery projection in the caller's transaction. The bounded SQL projector resolves observation and family ranks at the captured learner watermark, counts assisted and unknown observations, and reads at most the approved window of independent families into the reducer. It serializes on the learner profile. Exact result replays return the existing journal version, while changes under the same version return conflict.

The scoped integrity operation computes a projection without writing. Explicit repair requires current course and learner moderation scopes and the applicable policy records. It rebuilds only the derived projection and records an immutable audit event. No HTTP endpoints for learner attempts, moderation or repair were mounted in this slice. The native gradebook and completion paths have no new writers.

The first focused run passed 27 tests, including golden reducer cases, native objective scoring parity, injected projection rollback, actual independent PostgreSQL sessions contending on the same learner, and scoped repair. The PostgreSQL test target was disposable and loopback-only. This slice uses `d6b2beaf0c43` with no new schema change.

The final integration suite passed 402 tests with zero failures or skips across Studytomy, migration graph/runtime, native assignment grades and retries, and assignment content authorization. It covered exact correction replay, conflict on a changed command for the same revision, and retraction replay. Ruff and whitespace checks passed. All evidence and named approvals used in these tests were synthetic.

## ALE-015 diagnostic lifecycle and ALE-016 responses

API-11–API-15 now use the existing Studytomy organization router and direct signed sessions. Diagnostic start requires an active self-bound independent learner, current eligibility and consent for adaptive diagnostics, a current reviewed blueprint and mastery policy, and native read access to the exact published course activity mapped by the curriculum. The service checks the activity's version and content digest against the reviewed mapping. Dependent-learner and standalone delivery remain gated until their authorization paths are implemented.

Each start binds a diagnostic to its reviewed manifest and issues one public question. The active-round uniqueness rule applies to the learner and blueprint family. A conflicting start can identify that learner's current attempt. Reads return only the outstanding question. An expired attempt's retake interval is measured from its expiry deadline. A closed diagnostic reports why it stopped and whether coverage was met. Counts reflecting scores are available only after completion when the reviewed reveal policy allows them.

A response requires the exact issued-item identifier, attempt version, and operation key. The server validates the input and stores the accepted response, assistance classification, immutable result, mastery projection, next state, and receipt in a single transaction. Skips and assisted responses do not contribute to independent coverage. An exact replay returns the stored receipt. If a later action has advanced or closed the attempt, a replay omits the prior next question from that receipt. No scoring provider or assignment gradebook write occurs in this flow.

The signed HTTP workflow passed on SQLite and Docker PostgreSQL. On PostgreSQL, separate request sessions raced same-key and different-key starts, then raced same-key and different-key answers. Both races produced one state change and gave each request either the winning replay or a conflict. The workflow also verified the feature switch and missing approval gates, unrelated learner and source denial, unissued question and stale-version denial, invalid-response rollback, key reuse conflict, answer-key exclusion, and exactly one evidence result.

The integrated checkpoint passed 412 tests with zero failures or skips. It covered the Studytomy suites including guardian-access regressions, migrations, native assignment grading and retries, assignment content authorization, and session provenance. After the final receipt-link fix, both signed HTTP tests passed again, including following the actual summary link and the PostgreSQL races. Targeted Ruff lint and formatting checks passed.

This closes the local diagnostic portion of ALE-015 and the ALE-016 response implementation. API-16 practice, dependent/guardian authority, learner UI, timing-specific expiry and abandonment coverage, and end-to-end release acceptance remain open. Tests used synthetic approvals and a disposable PostgreSQL 16 database bound to loopback at Alembic head `d6b2beaf0c43`. This slice added no migration.

## ALE-015 supported practice

API-16 now starts a one-item supported practice round with a concept UUID, source activity UUID, mode and idempotency key. It uses the same signed-session attempt, response, abandon and summary routes as diagnostics. The learner needs current self-bound eligibility and the separate `adaptive_practice` consent scope. D-08 must specify `assistance.adaptive_practice` with `mode: supported`, `expires_after_minutes` and an approved D-09 `mastery_policy_uuid`. Those are policy contract fields. The implementation does not supply approved business values.

Issuance requires an exact published `practice` mapping, native assignment read authorization, a current D-16 `practice_only` item review and parent curriculum approval, and a native task that still matches the reviewed snapshot. The candidate must come from the mapped assignment activity. The attempt pins the item, curriculum, mode rules and mastery policy digest. Reads and response replays recheck present authority, including consent. The public payload excludes scoring details and private source material. A scored practice answer contributes an assisted observation, excluded from independent mastery and diagnostic coverage. The service never writes native assignment grades, submissions, completion or certificates.

The local supported-practice path uses existing attempt tables at Alembic head `d6b2beaf0c43`. No migration, provider call, learner activation or ALE publication was part of this slice. Remaining work includes the lesson UI, dependent/guardian authority, reviewed practice feedback and the wider ALE release checks.

Both signed HTTP cases passed on SQLite and disposable loopback-only Docker PostgreSQL 16. They verified source mismatch and stale-source rejection, same-key replay with one supported observation, consent withdrawal, abandon without evidence, and expiry without question disclosure or response acceptance. The integration suite then passed 412 tests with zero failures or skips across Studytomy, the migration graph/runtime, native assignment grading/retries/content authorization and session provenance. Targeted Ruff lint and formatting passed.

## ALE-011 educator configuration

The existing course dashboard has an **Adaptive learning** tab for a staff member with access to the dashboard and a current course-scoped author or reviewer grant. Its new signed, private, no-store configuration endpoint lists the course's curriculum revisions, concept names, reviewed question prompts, and diagnostic revisions. Only authors receive the course activity picker. The endpoint omits answer keys, raw assignment task contents and authoring activities from reviewer-only views. All authoring, publication and retirement commands still use the existing server gates, exact digests, current policy, idempotency and expected-version checks.

The tab supports creating a curriculum draft, adding concepts and prerequisites, mapping existing lessons to concepts, previewing a native task by identifier, and creating a private original numeric item. Authors can set required concepts, pick published original items, and enter diagnostic limits and policy references. Reviewers can inspect the curriculum or question, check diagnostic pool coverage, and submit the exact approval reference or a permitted retirement reason. The mapping form identifies an activity version change and the server independently rejects changed source content. The UI uses existing input primitives, labels and translation hooks with English fallback text.

Both signed API workflows passed on SQLite and disposable loopback PostgreSQL 16. They checked author-only, reviewer-only, missing policy, delegated session denial, foreign course denial, revocation, and absence of answer keys. The integration suite passed 412 tests with zero failures or skips at Alembic head `d6b2beaf0c43`. No migration or real policy approval was written. In Chromium at 390 × 844, a synthetic API fixture exercised the real component's keyboard disclosure, labeled controls, stale mapping indicator, role-specific controls and publication denial with focus moved to the alert. The signed-in dashboard journey and RTL acceptance remain open.

The pinned TypeScript compiler passed and the final Bun run passed 719 tests with zero failures. The initial run had a timeout in an existing dynamic-content test during parallel validation. The final run used a 60-second per-test timeout. The two new workspace tests also passed independently.

## ALE-017 active attempt recovery

The lesson client fetches the signed learner's most recently started active diagnostic or practice attempt for the exact lesson and purpose through `GET /me/attempts/current`. The endpoint checks current learner authority, consent, the reviewed definition and native access before returning the existing public question projection. Expired or absent attempts return a null result. The response is private and uncached.

The lesson's standard and focus layouts render an active recovered attempt, with numeric and single-choice input, assistance declaration, skip, an explicit confirmation to end the attempt, saved state, and reviewed completion feedback. An ambiguous start, save or end request can be retried with the original operation key during the current interaction. Account or lesson changes discard the in-memory state and abort outstanding requests.

When recovery finds no active attempt, the lesson can offer reviewed diagnostics and supported practice for that activity. The signed, uncached options endpoint applies current learner consent, source access and definition checks and returns only purpose, identifier and label. Start still enforces current authority and the diagnostic active-round and retake rules. Signed-in browser acceptance remains open.

An isolated Chromium component fixture at 390 × 844 verified starting practice, recovering the question after refresh with no saved draft, repeating an unconfirmed save with the same key, and moving keyboard focus between the end confirmation and cancel controls in RTL layout. The fixture used a synthetic signed-in session context and simulated adaptive responses. The checked-in Playwright scenario exercises the real lesson route with a signed-in test student and simulated adaptive transport once a disposable local portal fixture is available.

The checked-in learner scenario subsequently passed on the real lesson route with a signed-in synthetic student and simulated adaptive responses. The disposable portal used loopback-only Docker PostgreSQL 16 at `d6b2beaf0c43` and Redis. A separate signed browser visit without adaptive transport substitutions verified that the disabled backend returns an empty option list and denies recovery. Approved backend issuance through the browser remains open.

Cold-start testing exposed a native GET submission of the initial password form before hydration. The form now renders a native POST method and disables credential entry and submission until hydration. The server-rendered markup and working synthetic logins were verified. Browser setup now waits for DOM readiness and the lesson test waits for the actual lesson heading before asserting adaptive controls.

## ALE-018 native assignment source qualification

The native assignment adapter resolves the exact reviewed task or quiz question, its concept and published practice mapping, and the immutable item revision. It requires current D-08 opt-in under `attempts.adaptive_native_assignments` binding assignment UUID, mapping UUID, item UUID, item digest and assistance rule. It independently checks current D-16 approvals and compares the native task and activity against the reviewed snapshots. Changed, missing or ambiguous source history yields no qualifying snapshot. The resolver does not make an eligibility decision or authorize the caller. The native route must establish learner authority and actor provenance before using it.

Round identity is derived from the organization, actual native submission UUID and attempt number. Repeating the same request preserves that identity and retries or recreated native submissions change it.

The authorized native task read paths now capture first exposure for current self-bound learners with the separate `adaptive_native_assignments` consent scope. For an opted-in assignment the first qualifying exposure reserves a native pending submission, records the reviewed round and issued items, and journals the exact mapping, snapshot digest and reviewed assistance rule. It commits before returning the task payload. An existing answer prevents retrospective source capture. Instructor previews, delegated or token callers, already handed-in rounds, and unmet review or consent produce no exposure record. First exposure deliberately captures all qualified items for the round so a single-task read cannot create an incomplete per-task round.

Eligible reads initially reserve a native pending submission. Native hand-in now freezes the exact normalized objective answer from an already captured round, under the native row lock and in the same transaction as hand-in. It excludes delegated hand-in, missing consent, source drift and unverified answers. Accepted native answers retain an exposure marker because existing task saves do not identify who wrote them. No mastery result is generated at this stage. The signed fixture verifies that clearing the mutable native answer leaves the frozen answer intact.

The focused run passed 113 tests across adaptive authoring and signed task hand-in, PostgreSQL evidence guards, assignment grading, retries and access controls. The database was disposable Docker PostgreSQL at `d6b2beaf0c43`. No schema changed. Save-level actor provenance and final-grade evidence remain open.

## ALE-019 native grade journal

The shared native grade finalizer derives one outcome per frozen reviewed item and appends it before the same commit that stores the native grade. It uses the frozen response and rubric, and it never distributes assignment percentages across concepts. Save-level authorship remains unknown, so these results are ineligible for independent mastery. An unchanged recomputation reuses the evidence revision. A manual task override produces a superseding `not_scored` result instead of treating a task-level mark as question-level evidence. The journal checks current consent and preserves prior retractions.

No schema changed. Other direct grade/status update paths, native save provenance, policy-pinned projection updates, and adaptive retry or correction handling remain open.

## ALE-020 native submission rejection

The authorized native rejection path locks the learner and rechecks the exact native round before deleting the mutable submission. In the same transaction it appends one retraction per latest item result for that round, while retaining frozen answers and earlier revisions. It invalidates an open captured round and closes outstanding items with the rejection reason. An already retracted result is not retracted again. Native task answers and the existing trail and certificate handling retain their ordinary rejection behavior.

Native retry now checks that a captured round is frozen before clearing its mutable task answers. A successful retry retains the evidence for the previous round and continues using the native submission UUID with an incremented attempt number. The learner's next authorized task read captures a distinct round. Rejecting that later round only retracts evidence for the later round.

Native task, assignment, activity, chapter and course deletion paths now check for affected open adaptive rounds before deleting database rows or storage. A protected source returns `409 ADAPTIVE_SOURCE_IN_USE` with an instruction to finish or reject the round. Closed snapshots remain independent of native source rows. The organization lock serializes the checks with qualifying first exposure. Report staleness and deletion paths outside these native course operations remain open. No schema changed.

## ALE-019 / ALE-020 policy-pinned native projections

A native opt-in can explicitly select a D-09 mastery policy UUID. First exposure pins the selected policy's digest and the D-09 version alongside the reviewed item and assistance rule. Native grade evidence, overrides and rejection retractions then use that immutable version and the existing mastery reducer within their transaction. Native results remain excluded from independent mastery while save-level authorship is unknown. Historical rounds without a pinned selection continue to journal evidence without guessing a projection policy.

## ALE-021 learner mastery read

API-17 evaluates the signed-in learner's own concept progress using the bounded reducer and one evidence watermark per page. It requires current `adaptive_mastery` consent, native course read access, a published curriculum with current D-16 review, and an approved D-09 policy selected under `mastery_gate.adaptive_curriculum_policies` for the exact curriculum UUID. It returns concept states, evidence counts, review freshness, policy version and a null score when no independent evidence exists. The read does not write projections or expose raw evidence. Activity recommendations, scoped staff access and the learner progress UI remain open.

## ALE-021 next-activity recommendations

API-18 resolves the signed-in learner's current published lesson against exact reviewed mappings. It ranks unresolved lesson prerequisites, developing concepts, unassessed concepts and review-due concepts in that order, with ties following curriculum sequence. Each candidate must remain published, match its approved mapping digest and pass native resource access. The response returns up to three distinct lessons and reason codes. If no candidate qualifies, it returns `no_accessible_activity` and includes `return_to_lesson` only when approved under D-09 `mastery_gate.adaptive_recommendation_support`. A lesson mapped to multiple current curriculum revisions gets the safe empty outcome because there is no authoritative curriculum selection for that request. No schema changed.

Focused validation passed 15 tests with fresh loopback-only Docker PostgreSQL 16/pgvector at Alembic revision `d6b2beaf0c43`, including evidence concurrency and signed recommendation checks for unpublished candidates and unrelated users. Native grading, assignment token access, authentication and family-sharing fixtures passed 126 tests, with 18 warnings in the auth test fixture for unawaited mocked session methods. Immediate course-access revocation and the full priority matrix still need direct API regressions. Scoped staff mastery, learner progress presentation and reviewed guardian report acceptance remain open. The disposable database was removed.

## ALE-022 learner concept progress

The existing lesson now offers a signed learner a concept-progress disclosure after a successful mastery read. The lesson-scoped API derives the exact reviewed curriculum and mapped concepts from the readable lesson, so the browser never chooses a curriculum on the learner's behalf. Recommendations carry the authorized native course and activity identifiers and reviewed activity title. The UI uses explicit state labels, evidence counts, supported-practice limitations and a policy-controlled fallback without exposing answers, projecting a zero grade, or changing course completion.

The focused API run passed seven tests, including signed lesson scoping and unrelated-user denial in the SQLite and PostgreSQL fixtures, as well as PostgreSQL immutable-evidence and concurrency checks. Fresh schema initialization reached Alembic revision `d6b2beaf0c43` in Docker PostgreSQL 16 on loopback. Nearby web tests passed nine cases. TypeScript, Ruff, and the new component's strict ESLint check passed. No schema migration was added.

Signed Chromium passed the 390 × 844 lesson scenario with keyboard operation, explicit unknown and insufficient-evidence labels, assisted-practice wording, a valid lesson URL, RTL overflow coverage, and link removal after a no-access response. The original browser setup timed out during interactive login. The successful run used a temporary copy of the harness that created genuine cookies through the local Next.js auth endpoint and left the repository's setup unchanged. The learner used the real lesson and auth services with synthetic adaptive response fixtures. Desktop viewport acceptance and a browser run against approved real adaptive data remain open. The disposable PostgreSQL and Redis instances were stopped and removed.

## ALE-021 scoped staff mastery

API-20 reuses the learner mastery reducer, versioned curriculum review and one evidence watermark. A direct staff session must hold assessment-moderation capability and explicit grants for both the curriculum's owning course and the selected learner. It must also pass native course update authority. Access requires an active, eligible independent learner and current acceptance of the `adaptive_staff_mastery` scope under the approved learning-terms notice. The dedicated scope keeps the learner's own `adaptive_mastery` consent separate from staff disclosure. This checkpoint adds no approved policy values or dependent-learner release mechanism.

The route returns the same minimized concept projection with private no-store headers. Missing records and unauthorized records have the same 404 body. The signed fixtures cover course-only denial, authorized scope, learner denial, consent withdrawal while learner self-read stays enabled, and immediate course-grant revocation. No schema changed.

Validation passed 25 focused tests across signed curriculum and mastery calls, PostgreSQL evidence concurrency and integrity, and the staff authorization suite. A final rerun after normalizing missing-curriculum responses passed both signed curriculum tests, one SQLite and one PostgreSQL. Fresh loopback-only Docker PostgreSQL 16 initialized at Alembic revision `d6b2beaf0c43` and was removed after testing. Ruff and tracked Git whitespace checks passed. Guided sessions and reviewed guardian reports remain open.

## ALE-023 durable tutor sessions and bounded context

API-22 opens a PostgreSQL-backed session for the signed-in learner, an actual readable lesson, one mapped concept and an optional supported-practice attempt. API-25 closes the caller's own session using its expected version. Session rows pin course, lesson digest, curriculum, concept, eligibility decision and active policy versions. Changing the lesson or authority causes context reload to fail closed. The service accepts no chat UUID and stores no transcript. A fresh load rebuilds context directly from PostgreSQL and the reviewed lesson.

Guidance requires current learning-terms and AI-assistance consent for `adaptive_guidance`, approved D-08 assistance rules and exact curriculum review. Active diagnostic rounds and active captured native assessments on the same activity block guidance. Assignment content is excluded even for authorized supported practice. The context contains bounded public lesson prose, the reviewed concept objective and a minimized mastery summary. It excludes interactive and staff-only blocks, answer keys, learner identifiers and raw evidence. The current prose extractor supports reviewed dynamic lessons. Supported-practice bindings must match the learner, curriculum, activity and an issued mapped concept.

Migration `37e0adb59ca1` adds DB-11. A second disposable database verified downgrade to `d6b2beaf0c43` removed the tutor table and upgrade back to `37e0adb59ca1` restored it. DB-12 turn storage, provider dispatch and validated teaching responses remain open with ALE-024/ALE-025.

Validation passed 15 focused tests across signed curriculum and guidance flows, context minimization, PostgreSQL evidence safeguards and existing contracts. Both signed curriculum tests passed again after adding the close-race assertion. The PostgreSQL run sent two closes with the same expected version from separate transactions and observed one 200 and one 409. The independent SQLite run exercised the sequential contract. Ruff passed, and the loopback-only Docker PostgreSQL 16 container was removed.

## ALE-024 structured teaching and reviewed hints

The teaching adapter uses the existing non-streaming generation client with a typed plain-text response, the current lesson source alias, a fixed output and time budget, and zero model-validation retries. It rejects unknown fields, incorrect actions, fabricated sources, executable text and unsafe links. Rejected responses fall back to reviewed material or a return-to-lesson action. Transport timeouts propagate to the future turn-reconciliation layer. Context truncation is surfaced explicitly. Provider input is limited to the reviewed lesson/goal and requested action. Linked supported practice uses the reviewed hint path and bypasses provider generation.

In the caller's transaction, the reviewed hint helper revalidates the signed session and current issued practice item, loads the exact reviewed item revision, and advances its assistance counter before returning a hint. Once the reviewed ladder is exhausted it returns the lesson action without changing the counter. The adapter changes no grades, publication or mastery directly. These helpers are internal pending durable turn routing, reservation and post-call reauthorization in ALE-025. Live provider and educator acceptance remain gated.

Validation passed 19 focused tests, including malicious or malformed output, transport timeout propagation, assistance counters and existing evidence protections on loopback-only Docker PostgreSQL 16 at Alembic revision `37e0adb59ca1`. The existing provider and quiz unit suites passed another 35 tests. The disposable database was removed. Scoped Ruff and Git whitespace checks passed. The shared LLM client retains its 18 pre-existing Ruff findings, confirmed against HEAD.

## ALE-025 durable tutor turn reservation and recovery

API-23 reserves one logical operation by session and client UUID, hashes the request for conflict detection, and enforces the approved rolling learner usage limit. Reviewed static hints and local fallback finalize within the same transaction as the turn and session step. A provider-backed turn commits its reservation before crossing to the existing AI credit store, records the observed credit counter, reauthorizes, releases the database transaction before dispatch, and reauthorizes again before storing or releasing the validated result. A repeat operation reads the existing receipt. A timeout, ambiguous credit-store failure or abandoned running marker becomes `reconciliation_required` without retrying the call or claiming a refund.

API-24 returns the same private, current-authorized receipt. Expired result text is cleared during later authorized turn activity while operation status is retained. Background erasure for dormant sessions and an operator reconciliation workflow remain open. Learner free text contributes to the operation digest and does not enter the provider request or durable result. Exact provider, model and alternate endpoint approval are required to dispatch. Provider and credit outcomes in the test suite are simulated. Live provider acceptance and retention operations remain gated.

Migration `950ec32a74bf` adds the tenant-scoped tutor turn table and operation uniqueness. The local PostgreSQL rehearsal verified downgrade to `37e0adb59ca1` removed the table and re-upgrade restored both the table and tenant foreign key.

Final verification passed 26 focused tests with PostgreSQL 16 on loopback. The API integration exercised parallel duplicate submission, stable authorized replay, conflicting payload, synthetic provider timeout, ambiguous quota-store receipt, and expired result removal. An intermediate rerun used a database name without the required `_test` suffix and hit six fixture setup errors. A fresh correctly named database passed every scoped test without changing that safety check. Scoped Ruff and Git whitespace checks passed. All disposable test databases were removed with their container.

## ALE-026 managed learner AI entry points

The existing managed-learner policy gate remains on every mounted legacy AI router and the playground generation router. The same guard now runs inside the shared activity chat and editor authorization services and direct RAG opener, before content retrieval, credit reservation or model work. Activity chat start, resume and both streaming paths reuse that check. The gate looks up the actor's current profile link directly, including a delegated token's creator, so a cached privacy projection cannot admit a newly linked learner. The adaptive UI toggle has no effect on this check.

Embedded MCP now refuses managed actors before target name resolution, tool execution or resource exposure. Its list and resource reads use the same policy even when an external client supplies the model. Generic tools retain their existing guard. Staff authoring uses existing native permissions on unmanaged author accounts. Approved learner use remains on the separate lesson-scoped tutor API, which enforces current consent and assessment restrictions.

No schema changed. Tests use local fixtures and simulated provider calls. Browser integration into the lesson AI panel remains open under ALE-027.

Final verification passed 133 tests covering signed mounted start/send/stream and RAG requests, direct activity/editor/RAG guards, MCP member and token denial, unmanaged compatibility, and the signed adaptive flow on disposable loopback PostgreSQL 16 at `950ec32a74bf`. The first mounted HTTP test tried to override an auth dependency that the parent router called directly and received 401. Using a real synthetic signed session exercised the intended 409 denial. Scoped test Ruff and tracked Git whitespace checks passed. The disposable database was removed.

## ALE-027 guided mode in the lesson AI panel

The private tutor-options endpoint classifies ordinary chat separately from managed guidance, checks the actual lesson and current authority, and returns a narrow action list. Managed learners start guided sessions through the existing lesson AI panel in its hover or side presentation. Supported practice exposes only the reviewed hint. Restricted assessment and unavailable guidance expose no teaching actions. The legacy composer renders only for the server's legacy mode.

The client renders typed receipts as text, labels reviewed fallback and recorded hints, and uses the operation status endpoint after an interrupted request. Only session and operation metadata is stored in session storage, keyed by learner, organization, lesson and optional practice. The panel can retry a missing receipt with the same operation UUID. Adaptive attempt changes invalidate the guidance options and clear rendered teaching when the assessment becomes restricted. No schema changed.

Verification passed 19 focused API tests including signed options, restricted diagnostics, supported practice and real PostgreSQL evidence concurrency at revision `950ec32a74bf`. Nine nearby web tests, TypeScript, scoped Ruff and strict lint for the new component passed. Signed Chromium passed at 390 × 844 and 1440 × 900 using the real lesson route and auth cookies, with synthetic adaptive transport. The first browser run timed out on a lesson bundle containing a non-UTF-8 source file. After correcting the encoding and verifying the rebuilt route, both viewport runs passed. Disposable PostgreSQL and Redis were removed. The protected learner feedback contract (API-26) and acceptance with approved live guidance remain open.

## API-26 protected tutor feedback

The signed feedback endpoint accepts one issue report for the caller's completed, unexpired turn in an authorized open tutor session. Issue codes are limited to inaccurate, unclear or unsafe. An optional single-line note is limited to 300 characters and requires explicit D-13 approval for feedback notes. Matching retries return the original receipt. A changed submission returns a conflict. The receipt excludes the note, and existing turn reads and provider calls exclude all feedback fields.

Feedback notes expire with the turn result and are removed on subsequent authorized turn maintenance. The issue code and receipt time are retained separately from the note for review. The learner control, staff review workflow and dormant-session retention sweep remain open. Migration `ef0b241a1c57` was rehearsed down to `950ec32a74bf` and up again on a disposable PostgreSQL database.

Final verification passed 14 focused tests, including signed feedback, replay using concurrent PostgreSQL transactions, cross-user denial, write conflicts, and expired-note removal. The initial test collection caught an escaped control character written as a literal null byte in source. The corrected rerun passed. Scoped Ruff and Git whitespace checks passed. The loopback-only PostgreSQL 16 container and both test databases were removed.

### API-26 learner feedback control

Completed, authorized guidance results now show an issue-report form in the existing lesson AI panel. The form sends only the current turn and one allowlisted issue code. A confirmed receipt gives visible and announced feedback. An unconfirmed send preserves the selected issue and retries the identical request. No feedback text or receipt is written to browser storage. Optional notes remain available only through the separately policy-gated API. Staff review and dormant-session retention remain open.

The signed Chromium scenario passed at 390 × 844 on the real lesson route using synthetic adaptive transport and a disposable loopback PostgreSQL/Redis portal at `ef0b241a1c57`. It verified keyboard opening, required issue selection, receipt loss, unchanged retry payload and confirmed feedback. The first browser run reached successful feedback and then failed because its closing assertion ambiguously matched two live status messages. The exact closing-text locator passed on rerun. TypeScript, strict lint for the panel, nine nearby web tests and Git whitespace checks passed. The disposable services were removed.

## ALE-028 progress report drafts

API-27 creates a staff-only, single-learner draft for an explicit completed half-open reporting period and published curriculum. It requires current report-review grants for both the learner and owning course, native course update authority, learner eligibility, current report consent and reviewed policy versions. The staff receipt exposes only whitelisted counts and labels. The snapshot stores its source references and policy versions privately, with an evidence watermark and digest. An identical operation replays the same draft.

Independent correct/incorrect outcomes, supported practice, unknown and pending evidence have separate fields. Evidence is selected at the captured watermark, taking the latest result revision for each observation and placing it in the period of first observation. Native completion is a separate, explicitly labeled snapshot of steps currently marked complete with a last update in the period. It contributes nothing to mastery. Drafts remain private and support independently consenting learners. Recipient records, review and publication, dependent-learner authority, and stale-report suppression remain open.

Migration `8617afa045b0` adds the draft table. A separate disposable PostgreSQL database verified that downgrading to `ef0b241a1c57` removed the table and upgrading restored it.

Verification passed 14 focused tests, including signed report scope and consent, matching draft replay from concurrent PostgreSQL transactions, period filtering and private evidence projection, plus existing evidence safeguards. Scoped Ruff and Git whitespace checks passed. The disposable loopback PostgreSQL 16 container was removed.

## ALE-029 staff report edits and approval

API-28 creates a new draft version with bounded human commentary. The summary and source list are carried forward from the server snapshot. After an edit, the previous approval remains historical and the new version requires review.

API-29 uses the same named and scoped staff authority, native course permission, learner eligibility and approved consent as draft creation. It requires the latest draft version and exact digest, rechecks the current policies and source snapshot, then stores the named reviewer and approval time on a new version. A changed source snapshot requires a fresh draft. Concurrent reviewers cannot both approve the same expected version.

Publication, recipient records and guardian read access remain open. Migration `91948e1f3a2d` adds review fields. Downgrade must preserve reviewed rows through a separate approved rollback plan once review versions exist.

Verification passed 14 focused tests with disposable loopback PostgreSQL 16, including wrong digest, learner denial, history-preserving edits, and one successful approval from two concurrent requests. The first downgrade rehearsal found that fresh-schema initialization and Alembic gave the reviewer foreign key different names. After making the model name explicit, a fresh rehearsal downgraded to `8617afa045b0` and reapplied `91948e1f3a2d` successfully. Scoped Ruff and Git whitespace checks passed. The test container was removed.

## ALE-029 explicit recipient publication

API-30 publishes only the latest approved version after rechecking the scoped publisher, policy, evidence and exact recipient grants. Requests name existing sharing-grant UUIDs explicitly. Each grant must belong to the report learner and still authorize the summary projection under current policy. Unknown, expired, withdrawn, ambiguous and out-of-scope grants reject the entire request. Report and recipient rows commit together, preserving the approved digest, named reviewer, grant IDs, grant versions and availability time.

The implemented authority path is independent learner sharing. The checkpoint adds no dependent guardian publication path and sends no notification. Recipient reads and revocation rechecks remain open. Migration `b2460ac8149e` adds version-specific recipient records.

Verification passed 18 focused tests, including signed publication for a current grant, atomic rejection of a mixed valid/expired recipient set, read-side sharing regression, and approval concurrency on PostgreSQL. Scoped Ruff and Git whitespace checks passed. The disposable PostgreSQL 16 database verified downgrade to `91948e1f3a2d` removed the recipient table and upgrading to `b2460ac8149e` restored it. The container was removed.

## ALE-031 protected report detail and listing

API-31 rechecks the current subject, exact recipient grant and version, or scoped staff authority on every read. Publication alone grants no enduring access. Withdrawn grants and stale evidence suppress the report at its existing URL. Recipient reads contain only aggregate sections and the learner first name, while reviewed commentary is available to the subject and scoped staff. Raw keys, source references, private evidence and recipient lists stay internal.

API-32 filters candidates to the signed subject or recorded recipient, revalidates each candidate and returns only period, curriculum/course labels and report version. Pagination uses the descending creation timestamp and report UUID. Both routes use private no-store responses. The account viewer and dependent guardian authority remain open. No migration changed.

Verification passed 18 focused tests including signed learner, staff and recipient access, private responses, and immediate withdrawal invalidating both an existing link and recipient list membership. The learner's own access remained valid. SQLite and disposable loopback PostgreSQL 16 ran the signed workflow at Alembic revision `b2460ac8149e`. Scoped Ruff and Git whitespace checks passed. The PostgreSQL container was removed.

### ALE-031 account report viewer

The existing account navigation now includes Progress reports on desktop and mobile. The page browses paginated private summaries and opens a selected report through the protected detail endpoint. Every report view is keyed by the authenticated account, organization and report UUID. Route changes and unmounts abort outstanding requests. Visibility and history restoration clear the current view and re-fetch. Report data is kept in component state and is absent from browser storage, analytics payloads and server-rendered account props.

The report separates native completion from observed independent responses and supported practice. It explicitly labels missing or pending evidence. An unavailable report clears the detail and offers a route back to the list. No schema changed.

Verification passed five nearby web tests, TypeScript, strict lint for the viewer, and signed Chromium at 390 × 844 and 1440 × 900. The browser scenarios used the real account route and auth cookies with synthetic report responses, checking keyboard opening, evidence wording, a denied old link and an empty list after withdrawal. The first desktop run exceeded the removed-report assertion window while the page was still blank after reload. The retry allowed the authentication and view to load and passed. The disposable loopback PostgreSQL/Redis portal at `b2460ac8149e` was removed.

## ALE-032 optional report notification

API-33 queues a recipient-specific notice only for a current published report under explicit electronic-notification decisions. The approved offer terms must include notification, D-12 must approve email and the sharing scope, and D-13 must approve an authenticated link-only projection. The exact recipient row, sharing grant and version, verified email, learner consent and current report evidence are checked before queueing and before dispatch. A semantic outbox dedupe key binds the report version and recipient record.

The worker reuses the durable outbox with a report-purpose filter. It renders a minimal sign-in link using the trusted organization URL helper and invokes the shared email transport in single-attempt mode. A provider acceptance is reported as accepted. An uncertain outcome is recorded for reconciliation. A revoked or otherwise invalid recipient is suppressed before provider work. No report content is attached and no address or message body is written to the outbox. No scheduler was added. Real notification inclusion, provider configuration and live delivery acceptance remain unverified.

Verification passed 121 local tests for signed queueing, deduplicated replay, verified-contact denial, link-only rendering, withdrawal before dispatch, shared outbox and mail regressions. A separate single-attempt transport test passed with one provider invocation after an ambiguous timeout. PostgreSQL 16 ran on disposable loopback Docker at `b2460ac8149e` and was removed. Provider transport was simulated. Scoped Ruff and Git whitespace checks passed. No schema migration changed.

## ALE-033 report withdrawal and replacement draft

API-34 appends a withdrawal version under current scoped reviewer authority and an expected-version check. Existing links and listings stop serving historical published versions. Queued and retryable notices are suppressed in the same transaction. Active leases still go through the existing authority check before dispatch. Prior report versions and delivery outcomes are retained. A withdrawal receipt describes future access and queued notices only.

API-35 accepts the current published or withdrawn version and creates a fresh evidence snapshot for review. The draft records the correction reason and actor, clears prior approval and commentary, and requires fresh review and recipient selection. The existing evidence revalidation remains active on report access. The staff course workspace surfaces the correction controls described below.

Migration `448cf67b5309` adds the actor, reason, and lifecycle constraints. An empty disposable PostgreSQL database successfully downgraded to `b2460ac8149e` and upgraded to `448cf67b5309`. The downgrade refuses to run once correction or withdrawal records exist, preserving their history.

Verification passed 14 focused tests, including the SQLite signed workflow, the equivalent PostgreSQL 16 workflow, native assignment contract checks and PostgreSQL immutable-evidence and concurrency cases. Two simultaneous withdrawals produced one HTTP 200 and one HTTP 409, with one successor version. The migration refused downgrade with two synthetic correction records present and preserved both records. Ruff and Git whitespace checks passed.

### ALE-033 report review-request API

API-36 accepts a fixed reason and the displayed report version. The current subject or current recorded recipient can submit a request. The authorization check runs again before returning any replay receipt, so revoked recipients cannot replay a prior request. The organization lock serializes concurrent requests. Each subject or recipient gets one receipt per report version and reason. Requests contain no free text, and the endpoint does not alter the report, native grades, mastery, or sharing grants.

Migration `dfa43c51b792` adds the protected request records and refuses downgrade while requests exist. The staff course workspace surfaces grouped follow-up signals.

Verification passed 14 focused tests across the signed SQLite and disposable loopback PostgreSQL 16 workflows, native assignment contracts, and PostgreSQL immutable-evidence checks. Concurrent duplicate submissions shared one receipt. The subject and recipient had separate records. The migration rehearsed `dfa43c51b792 → 448cf67b5309 → dfa43c51b792` on an empty database. A downgrade against populated synthetic data was refused and retained both requests at the current revision. Ruff and Git whitespace checks passed.

### ALE-033 account review-request form

The account report viewer shows the form when the report response confirms the signed user is the subject or a currently authorized recipient. Staff who can view a report solely through staff authority are not offered the form. The form submits a predefined reason with the displayed version, acknowledges receipt, reloads on access loss, and offers an explicit reload after a stale-version response. Pending submissions are scoped to the mounted report and aborted on exit. No request text or receipt is stored in browser storage.

Verification passed both signed API cases, one each on SQLite and Docker PostgreSQL 16, plus 12 nearby web tests, TypeScript, scoped Ruff and strict ESLint. Mobile and desktop Chromium each passed the signed account journey against an isolated local portal, with synthetic report responses covering the submitted version, stale reload, receipt and withdrawn link. An initial Chromium setup timed out at login while Next.js was proxying localhost to a server bound only to IPv4. The loopback hostname correction and warmed login route resolved it. Both disposable containers were removed.

### ALE staff report follow-up API

The scoped staff report detail endpoint returns the latest report version and up to 20 recent version/reason aggregates for review requests, including requests about withdrawn or replaced versions. It enforces the existing report-review capability for the learner and course plus native course access. It omits requester identifiers and leaves all report and request versions intact. The staff course workspace displays this response for an opened report.

Both signed workflow tests passed, one on SQLite and one on disposable loopback PostgreSQL 16 at Alembic revision `dfa43c51b792`. The tests confirmed staff can read the two-request aggregate after withdrawal and that neither the learner nor the recipient can access the staff view. Scoped Ruff and Git whitespace checks passed. No migration changed.

### ALE-033 report evidence status

The scoped staff report response exposes a live review state: current, stale, correction pending, or withdrawn. It reuses the source and aggregate comparison enforced for approval, publication and disclosure. A published report whose evidence differs from its saved snapshot is marked correction pending for reviewers and blocked from ordinary disclosure. It retains the original reviewed version. Unpublished versions with changed evidence are marked stale. The staff course workspace displays these states for an opened report.

Seven tests passed across the signed API workflow and PostgreSQL immutable-evidence suite. The signed workflow inserts a completion inside the report period, verifies correction-pending status and blocked learner retrieval, and compares the saved projection before and after. Disposable loopback PostgreSQL 16 ran at `dfa43c51b792`. Scoped Ruff and Git whitespace checks passed. No migration changed.

### ALE-030 staff report review panel

The existing course adaptive workspace can open an authorized report by reference. It shows the evidence cutoff, approval date, evidence limitations, live status and grouped review requests. Reviewers can save commentary, approve the exact saved version, withdraw publication or request a replacement draft. Version conflicts clear the loaded report and require a fresh read. Reports outside the selected course are not displayed. The scoped report directory and draft creation are documented below. The recipient preview and publication workflow is documented below.

Verification passed 10 web tests, TypeScript and strict lint for the new panel. The existing adaptive configuration file still produces nine unused-argument lint warnings. The signed API workflow passed once on SQLite and once on disposable loopback PostgreSQL 16. The signed browser fixture results are recorded below.

### ALE-030 recipient preview and publication

The staff workspace accepts up to 20 sharing grant references for an approved current version. It previews the actual recipient projection for each reference before adding it to the publication selection. A reviewer can inspect each projection and remove recipients. The preview uses the same exact-scope grant, report version, evidence and policy checks as publication and the same projection as the recipient read. Publication revalidates the complete selection in its own transaction. Preview and publication use existing authorization and no-store responses. The recipient picker is documented below. The signed browser fixture results are recorded below.

Verification passed seven focused API tests on the signed SQLite and isolated Docker PostgreSQL 16 workflows, including preview denial for stale versions, expired grants and learner callers. The signed recipient response matched the preview apart from the published version number. Ten focused web tests, TypeScript, zero-warning ESLint for the panel, Ruff and Git whitespace checks passed. The PostgreSQL container was removed. The existing migration head was unchanged.

### ALE-030 staff report directory and draft creation

The course workspace lists report versions after the reviewer supplies a learner reference and selects a published curriculum. The API returns one latest version per report, including withdrawals, paginates newest first and exposes only references, version, lifecycle status and reporting dates. The directory requires the same current native course rights and learner/course report-review scopes as the detail and creation endpoints. Draft creation uses explicit UTC boundaries and reuses the operation reference on in-session retries with the same inputs. The browser still requires a learner reference obtained through authorized staff workflows.

Two signed API workflows passed, one each on SQLite and disposable loopback Docker PostgreSQL 16. They verified pagination, absence of historical duplicates, projection minimization and denial to a learner caller. Ten focused web tests, TypeScript, zero-warning panel ESLint, Ruff and Git whitespace checks passed. No migration changed. The signed browser fixture results are recorded below.

### ALE-030 authorized recipient picker

The staff report workspace can discover current sharing grants for the selected approved report. It returns only a first name and grant reference after resolving current membership, eligibility policy and the exact report-summary sharing scope. Each page checks a bounded set of active candidates. A candidate can be added to the publication selection only after its recipient-specific preview loads. Publication revalidates all selected grants under the organization lock. No schema changed.

Both signed API workflows passed on SQLite and disposable loopback Docker PostgreSQL 16. The tests confirmed that only the current grant was listed, the response was private no-store, and stale or unauthorized calls were rejected. Ten web tests, TypeScript, zero-warning panel ESLint, Ruff and Git whitespace checks passed. The PostgreSQL container was removed. The signed browser fixture results are recorded below.

### ALE-030 signed staff browser workflow

Chromium passed the staff flow at 390 × 844 and 1440 × 900 using the real course dashboard and signed local admin session, with synthetic report API responses. Both runs created a draft from the scoped directory, saved commentary, approved the exact version and previewed the selected recipient without staff-only commentary. They also verified that a stale publication response removed the submit control until the report was reopened and re-previewed, then published and withdrew the report. The withdrawn view retained the grouped review request. The initial test URL was an internal organization-prefixed path that returned 404. Switching the test to the public dashboard URL resolved it. E2E TypeScript and Git whitespace checks passed. Disposable PostgreSQL 16 and Redis were removed. Full browser acceptance against real report endpoints is still outstanding.

### ALE-034 adaptive request isolation

The course workspace requires an authenticated session and matches the organization context to the App Router tenant before fetching the adaptive catalog. Only a response from the current API client can enable the adaptive tab. Tutor options use the authenticated account, organization, lesson and current client as their SWR partition. The old request is aborted when that client changes, and attempt changes revalidate only matching tutor options. Explicit logout and server-confirmed session invalidation clear tutor recovery references from session storage. The existing recovery flow keeps opaque session and operation references during an active session.

Eleven focused web tests passed, including recovery-reference cleanup and tenant resolution under a browser rewrite. TypeScript and zero-warning lint for the dashboard, tutor panel and scope helper passed. The broader lint run continued to report existing AuthContext and attempt-panel warnings. Browser verification of a live account switch with a pending response remains open. No API or database changes were required.

### ALE-035 protected deletion and retention inventory

Ordinary course deletion checks for retained adaptive reports under the organization lock before removing storage. Account deletion checks direct adaptive reviewer, actor, report recipient and review-request references. Organization deletion, including the sole-admin account path, detects the adaptive curriculum root under the organization lock before legacy cleanup. Affected callers receive `ADAPTIVE_RETENTION_REVIEW_REQUIRED` and can route the resource for retention review. Existing native assignment-round protection continues to run.

| Record | Purpose | Dependent data to review |
|---|---|---|
| Curriculum, items and diagnostics | Approved instructional definitions and provenance | Concept/activity links and content references |
| Attempts, issued items and evidence | Accepted answers, corrections and academic provenance | Mastery projection and any lesson views using the evidence |
| Tutor sessions and turns | Bounded educational guidance and operation recovery | Persisted result material and feedback, provider references and browser recovery metadata |
| Reports, recipients and review requests | Reviewed disclosure, authority and correction history | Derived views, sharing grants and report notice outbox intents |
| Pending and delivered notices | Delivery state and reconciliation | Pending send suppression and restricted delivery history |

Retention review must apply the current approved purpose, schedule and holds to each dependency. A disclosure withdrawal takes effect independently from retention review. The worktree contains no verified approval for executing a purge, pseudonymization, or dependent-store removal. No purge or schema change was made for this checkpoint.

Twenty-five focused tests passed across signed SQLite and disposable loopback Docker PostgreSQL 16, covering course and account deletion conflicts with retained records, immutable evidence, adaptive concurrency, and existing organization and account governance protections. Scoped Ruff and Git whitespace checks passed. The PostgreSQL container was removed.

### ALE-036 cross-entry authorization checkpoint

The report route matrix exercised eight GET and POST endpoints as anonymous, API-token and authenticated nonmember identities. It verified HTTP 401, 403 and indistinguishable private no-store 404 responses respectively. The broader checkpoint passed 478 tests with no failures or skips across the Studytomy suite, native grading/manual/formative assignment paths, assignment ownership and token gates, AI authorization, and auth logout. The new matrix passed separately with 8 tests deselected. PostgreSQL-marked cases used disposable loopback Docker PostgreSQL 16 at `dfa43c51b792`; SQLite and mock cases retain their original fixture types. The container was removed. Browser account-switch, complete payer/guardian cross-entry and dependent retention acceptance remain open.

### ALE-037 populated PostgreSQL migration rehearsal

The disposable PostgreSQL 16 service was exposed on a random IPv4 loopback port. The application migration runner bootstrapped a fresh database to `dfa43c51b792`. The rehearsal then used Alembic to reach `b2460ac8149e` while empty, inserted a synthetic published report and its selected recipient using the parent schema, and ran the application migration runner back to head. Both parent rows compared equal, including their version, digest, approval, scope and recipient link. An explicit rollback of the two additive revisions preserved both rows, and re-upgrade succeeded. With the rollback compatibility override disabled, an unknown revision was refused without changing the fixture data or the unknown revision. The initial seed was rolled back because it omitted the required synthetic organization email. The corrected seed and migration assertions passed. No migration files changed.

A separate fresh database in the disposable service ran every `studytomy_postgres` case. All 24 passed, with 234 nonselected tests deselected. This covered the available independent-session races and immutable-evidence checks. Direct publication-versus-grant-revocation evidence is documented below. Direct grade-versus-retry evidence is documented below. The service was removed after both databases completed. Git whitespace checks passed.

The signed PostgreSQL workflow now races replacement publication against the learner's grant withdrawal through separate connections. It checks both valid serial outcomes and denial of recipient detail and listing after revocation. The observed run had withdrawal win. Publication returned `REPORT_RECIPIENT_NOT_AUTHORIZED` and left version 9 approved with no version 10. The signed run passed with one SQLite case deselected. Ruff and Git whitespace checks passed. The disposable PostgreSQL 16 service was removed.

The instructor grade endpoint now locks and refreshes the authoritative submission row before checking its status or reading answers. It shares that lock with retry, so retry can reopen the next attempt without a stale grade marking it complete. The signed PostgreSQL test races the two HTTP endpoints and verifies one new pending attempt with both prior journal versions still intact. The focused suite passed 133 tests, including manual, formative and retry regressions and the existing journal rollback check. Scoped Ruff and Git whitespace checks passed. Full Ruff on the legacy assignment service reported 43 existing findings outside the edited block. The disposable PostgreSQL 16 instance was removed. Bulk recomputation racing retry is verified below.

Bulk grading now snapshots only the submission ID and attempt number. Before recomputing, it locks and refreshes the row only if that attempt is still graded. The signed PostgreSQL workflow pauses a task-update regrade after candidate discovery, lets the learner retry commit, then resumes bulk grading while the direct grade endpoint also contends. The new attempt stayed pending and both prior evidence versions survived. The focused suite passed 133 tests. Scoped Ruff and Git whitespace checks passed, and the disposable PostgreSQL 16 instance was removed.

### ALE-040 local acceptance and recovery runbook

[ALE local pilot runbook](ALE-LOCAL-PILOT.md) records the checked-out source, exact local API and Chromium commands, schema lineage, availability flag, recovery and rollback procedures, and release gates. The current regression command passed 494 tests with no failures or skips. All five PowerShell command blocks parsed and the documented local links resolve. The disposable PostgreSQL 16 test container was removed. The runbook remains provisional for real endpoint browser acceptance, educator approval and unapproved retention, provider and guardian workflows.

The signed report workflow now verifies that a same-organization adult with a pending guardian relationship and no sharing grant cannot open the published report, list it, or submit a review request. Both signed workflows passed on SQLite and disposable loopback PostgreSQL 16. This fixture verifies pending-relationship denial. The worktree has no payer ledger fixture, and payer-only and approved dependent guardian acceptance remain unverified. Ruff and Git whitespace checks passed, and the container was removed.

The shared guardian purpose check supports an optional exact relationship UUID and authority-version binding. It first requires one unambiguous current purpose grant, then compares the binding, so an old version or a different relationship cannot satisfy a frozen disclosure reference. Sixteen local family authority and sharing tests passed. This is a relationship-level primitive. Dependent reports still require an authorized course-specific projection binding.

The shared self-consent resolver now rejects dependent, inactive and cross-organization profiles before consulting consent records. A stored acceptance from an independent learner cannot satisfy the check after reclassification to dependent. This protects consumers such as guided tutoring and progress reports while a separate reviewed dependent-consent workflow is pending. The focused consent, guardian authority and adaptive access suite passed 28 tests with one PostgreSQL-only case deselected. No database schema or external state changed.

The family authority regression now accepts two synthetic guardians for different purposes, verifies that neither can use the other's purpose or relationship version, then disputes the progress-summary relationship. The separate assessment-feedback grant remains effective. All 16 focused family authority and sharing tests passed using the local fixture database. This verifies purpose isolation in the shared relationship ledger. Dependent course-report disclosure remains open.

The adaptive availability response distinguishes an academically eligible dependent learner from approval to use adaptive actions. With organizational policy current, the response gives `DEPENDENT_AUTHORITY_REQUIRED`. Missing action-level policy still takes precedence. Attempt authorization checks academic eligibility on the already locked independent profile. Twenty-two focused availability and consent tests passed, including the opt-in disposable PostgreSQL availability case. The initial test collection exposed a stale import after the helper rename. The caller was updated and the rerun passed. The container was removed.

The signed PostgreSQL suite also verified publication-first ordering by pausing publication while it held the organization lock and starting withdrawal on a separate connection. Publication created version 10, then withdrawal removed access to its detail and listing and suppressed notification. An earlier notice queued while authority was valid was suppressed by the worker after signed withdrawal with no sender invocation. Both signed SQLite and PostgreSQL workflows passed. Ruff and Git whitespace checks passed. The disposable instance was removed.

Guidance fallback now returns only the reviewed objective or a fixed instruction to return to the lesson. It never echoes the provider's lesson excerpt when the objective is empty, unsafe or too long. Synthetic tests checked that injected lesson instructions remained structured request data and were not displayed after rejecting a forged hint level. Eighteen guided-output and AI approval tests passed with a simulated provider. Named educational approval and live model evaluation remain open.

A signed feedback request against an expired completed turn now clears its saved result and feedback note under the existing turn lock before returning 404. The operation receipt and session version remain recoverable without the content. Sixteen focused tests passed, including the signed SQLite and disposable PostgreSQL 16 workflows and simulated provider output tests. The test verifies the content is cleared before a subsequent read can trigger maintenance. The container was removed. This is access-triggered expiry under an existing recorded deadline. Tenant-wide retention execution remains pending its approved policy and holds.

Tutor receipt serialization now checks the recorded content deadline at the point of response construction, including replay and completion paths that can run independently of maintenance. Late provider finalization enters reconciliation rather than saving an expired result or advancing the teaching step. Sixteen focused guided-output tests passed, including receipt checks with and without a current content deadline. No live provider or database schema change was involved.

The signed tutor workflow now simulates the provider returning a valid result after the stored deadline. Both SQLite and PostgreSQL preserve the original session version and store no late result. Replaying the operation returns the same reconciliation receipt without repeating the provider call. Eighteen focused tests passed including the output contract suite. Ruff and Git whitespace checks passed. Disposable loopback PostgreSQL 16 was removed.

Demo teardown now includes all direct organization-owned adaptive tables in its retention preflight. It refuses teardown before storage or row cleanup when a demo organization holds a curriculum revision, including when it has no learner profile. The registry test checks exact coverage against model metadata. All 13 focused demo teardown tests passed using isolated test fixtures. No existing demo organization was torn down.

### Reviewed report history integrity

Migration `dc48e62a7b91` protects report versions, selected recipients and review requests from row updates and deletions. Fresh schema bootstrap installs the same guards. The signed PostgreSQL workflow rejected all six attempted mutations and continued through the normal withdrawal flow. Nine focused tests passed with zero failures or skips, including the signed SQLite and PostgreSQL workflows and migration-head contract. On a separate disposable database, the Alembic round trip `dc48e62a7b91 → dfa43c51b792 → dc48e62a7b91` changed the PostgreSQL trigger count from three to zero and back to three. Both databases used one loopback-only PostgreSQL 16 Docker service, which was removed. Retention execution still requires approved policy.

### ALE-034 / ALE-038 report account-switch browser evidence

The desktop account report test holds the first synthetic account's detail response in an open tab, signs out in a second tab, signs into a second account, and releases the old response. The first tab reflects the new identity without displaying the old payload. Reopening the private URL returns the unavailable state, and neither browser storage contains the report identifier. Both account report Chromium tests passed with no skips, including the existing mobile withdrawal test. The initial run timed out at a hidden desktop menu in the second tab because it inherited the mobile viewport. Setting both tabs to the desktop viewport resolved the fixture error. E2E TypeScript passed in the repository. Sign-in and logout used the real local API backed by disposable loopback PostgreSQL 16 and Redis. Report traffic was intercepted with synthetic data. The services and containers were stopped after the run.

### ALE-032 delivery lease recovery

Outbox claim, dispatch and completion refresh their locked rows so an earlier copy in the SQLAlchemy session cannot override a newer disposition. The PostgreSQL regression reproduced a stale worker incorrectly finalizing a delivery after an independent recovery session moved it to reconciliation. After the fix, completion returns 409 and the unknown outcome remains recorded. The focused outbox, PostgreSQL concurrency, email transport and signed adaptive API suite passed 133 tests with zero failures or skips at Alembic head `dc48e62a7b91`. Scoped Ruff and Git whitespace checks passed. The disposable loopback PostgreSQL 16 container was removed. Tests simulated provider responses.

### ALE-039 educator review record — draft v1

Approval status is pending. The fixtures are synthetic and the guided-output tests use a mocked provider with model name `synthetic`. Keep the concepts, item-family assignments, rubric digests, diagnostic caps and exact mastery and report wording with the signed review record. For the configured provider evaluation, record provider and model revision, prompt revision, retention and transfer policy versions, and observed outputs from synthetic inputs.

| Cases | Engineering evidence | Educator check to record |
| --- | --- | --- |
| CASE-15–20 | Diagnostic selection tests cover wrong baseline answers, prerequisite probes, skips, repeat families and exhausted pools. Objective-scoring tests cover unknown choices, malformed input and exact decimal tolerances. | Review a wrong answer that treats two unequal pieces as halves. Confirm the rubric distinguishes this misconception from malformed input and the probe addresses equal-sized parts. Confirm equivalent item variants share a family and each cap is defensible. |
| CASE-23–27 | Mastery tests distinguish absent, insufficient and measured evidence. They cover assistance, spacing, family deduplication, correction, retraction and freshness. | Review a hinted correct answer, followed by one unassisted correct answer from the same family. Confirm the learner-facing explanation describes the evidence and its limits. Check that an incorrect counterexample remains visible in the interpretation. |
| CASE-36–39 | Guided-context and output tests keep linked practice away from generated item help, validate action and source identifiers, and fall back when structured output is invalid. | Ask for the exact issued answer. Offer the claim that any two pieces are halves. Check that help avoids disclosing the answer, corrects the claim using the reviewed lesson, and assigns it no score. A schema-valid explanation that teaches the claim is an evaluation failure. |
| CASE-47, CASE-52 | Signed report workflow evidence above covers stale approval and version handling. | Review the exact report text for distinctions among activity completion, supported practice and independent evidence. Record any unsupported mastery, confidence or learning-effectiveness claim as a failure. |

The five focused engineering suites passed 46 tests with zero failures or skips. These establish software behavior with synthetic fixtures. The named reviewer, review date, exact artifact versions, case decisions and evidence links, limitations, and sign-off are pending. No live model or curriculum claim has been validated. D-01, D-02, D-09, D-10, D-12 and D-13 remain applicable to the approved scope.

### PostgreSQL regression checkpoint at `b043fde62c98`

Fresh application bootstrap stamped the current single Alembic head
`b043fde62c98` on a disposable PostgreSQL 16 Docker database named
`studytomy_ale_test`, exposed only at `127.0.0.1:55039`. The PostgreSQL-marked
Studytomy suite passed 29 tests with 250 deselected, zero failures and zero
skips. It includes the signed adaptive workflow, independent-session
concurrency and evidence integrity checks. The test container was removed.
This run exercised fresh bootstrap. The populated rehearsal above records
its own, earlier migration range.

The subsequent populated rehearsal covered `dc48e62a7b91 → b043fde62c98
→ dc48e62a7b91 → b043fde62c98` on disposable loopback PostgreSQL 16. The
application runner upgraded a synthetic published native course and private
draft without changing their IDs, names, organization or visibility. Both
rows received the default unmanaged marker. After the final upgrade, marking
the draft as managed caused PostgreSQL to reject attempts to set it public
or published. Alembic refused to downgrade while the managed draft existed,
and the row and revision remained intact. The first seed attempt omitted
the required `organization.is_demo` column. It rolled back, and the corrected
rehearsal passed on a fresh container. Both containers were removed.

### ALE-036 payer-only report authorization

The signed adaptive workflow now gives a same-organization purchaser a
sealed, accepted order with an allocation to the learner whose report is
published. The purchaser has no family relationship or sharing grant.
Report detail and review-request submission both returned 404, and the
purchaser's report list was empty. The first run exposed missing cache
headers on the detail denial in both SQLite and PostgreSQL. The adaptive
router now attaches private no-store headers to HTTP errors while
preserving their original status and detail.

The rerun passed all 12 tests across the signed adaptive workflow, adaptive
availability and order integrity, with zero failures or skips. The
PostgreSQL cases used a fresh disposable pgvector/PostgreSQL 16 database
on loopback at Alembic head `425be90c73a1`. The instance was removed.
A separate 106-test run passed for authentication, native grading, manual
grading, assignment submission ownership and migration graph checks.
Ruff and scoped Git whitespace checks passed. This fixture covers a
purchaser without family authority. Dependent guardian course-report
disclosure and its approved end-to-end acceptance remain open.

### ALE-037 PostgreSQL regression checkpoint at `425be90c73a1`

Fresh bootstrap on disposable loopback-only pgvector/PostgreSQL 16 stamped
`425be90c73a1`. The full `studytomy_postgres` selection passed 34 tests
with zero failures or skips and 255 deselected. It exercised signed adaptive
workflows, independent-session concurrency, immutable evidence, and shared
commerce and authorization cases. The container was removed. This run
exercised fresh bootstrap. The earlier populated rehearsals cover their
respective migration ranges.
