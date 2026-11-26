# Documentation review: 17 November 2026

## Scope and provenance

This is a bounded independent documentation reconciliation, not a full application audit or release certification.

- Inspected development checkout: `main`, HEAD `4f3346af99b516bab2d422217905d078096d55ac`, with substantial staged, unstaged and untracked implementation. The original index/worktree was not edited by this documentation task.
- Publishing base: remote `main` at `d34ebb50b7191730a37452fe3324679b2f5e7a47`, in an isolated checkout. Documentation-only publication does not publish the local implementation described below.
- The inventory contained 30 tracked/indexed Markdown files and four untracked Studytomy evidence documents. Review depth varies as recorded below. MDX pages, generated/vendor documentation and complete application behavior are outside this pass.

## Corrected current claims

| Finding | Evidence inspected in development worktree | Correction |
|---|---|---|
| Root instructions labelled all Studytomy-specific work unimplemented | `apps/api/src/router.py:105` mounts adaptive routes; `services/studytomy/adaptive_attempts.py:545` starts diagnostics | Replace blanket label with mixed local implementation/planned scope; preserve all safety/approval rules |
| README diagnostic and progress rows lagged local implementation | `adaptive_mastery.py:117,163` evaluates policy-bound evidence and reduces mastery; `adaptive_reports.py:385,546,752,1047,1199` implements draft, review, publish, read and withdrawal/supersession paths | Mark these areas partial/local, not completed or deployed |
| Access inventory's old absence claim looked current | `apps/api/src/db/courses/courses.py:69-77` constrains managed-course visibility; `services/studytomy/access_policy.py:80-180` checks eligibility/release access | Preserve historical table with prominent scope/date warning; no assertion every access path is fixed |
| Documentation site claimed Nextra and a nonexistent MIT license file | `docs/package.json:16-19` declares Fumadocs/Next.js; `docs/LICENSE` absent; root LICENSE begins AGPL v3 | Correct stack and license reference; retain upstream attribution |
| Contributor instructions cloned upstream rather than this fork | Actual authorized project is `nirzaf/studytomyportal` | Correct setup target and label upstream contribution procedure |
| Portal/E2E guides could be read as current deployment or local acceptance evidence | `apps/e2e/global-setup.ts:1-8`, `core/instance.ts:14-20` distinguish published-image boot from explicit existing instance | Add local-only and test-target applicability notes |

Paths in shortened evidence cells are beneath `apps/api/src/services/studytomy/`. Line numbers are inspection-time references in a changing dirty worktree, not permalinks to remote source.

The empty web README now provides scoped navigation and warns that the convenience lint command masks exit failures (`apps/web/package.json:11-14`). The CLI README retains useful upstream commands while clearly separating them from Studytomy deployment authorization.

> [!NOTE]
> **2026-11-20:** that masking no longer exists. `apps/web/package.json` declares `"lint": "eslint ."`, the `|| true` suffix having been removed in `029f1af1`. The web README and the root README's testing section were corrected the same day; `lint:strict` remains the scoped gate, and an unweighted whole-app run currently reports 0 errors with 975 warnings.

## Current engineering boundaries

The observed diagnostic, mastery, tutoring and report services are real local source, not merely a roadmap. They still require dependency-complete tests, browser acceptance and applicable education/safeguarding approvals. MCP has an existing authenticated native service (`apps/api/src/services/mcp/handlers.py`); a complete newly requested teacher/admin authoring and tightly course/lesson-grounded generation workflow was not established by this review. Do not create a redundant server or claim that workflow complete.

No application tests, migrations or browser journey were run in this documentation pass. Historical test counts in evidence logs remain scoped records, not fresh test results. Never infer production readiness, approved curriculum, or real-user activation from this document.

## Coverage and preservation decisions

The table below records the first checkpoint. The extended pass following it supersedes the classification-only rows where stated.

| Documents | Review depth / disposition |
|---|---|
| `AGENTS.md`, root `README.md`, `CONTRIBUTING.md`, `LOCAL-ONLY.md`, `PORTAL_GUIDE.md`, `docs/README.md`, `apps/e2e/README.md`, `apps/cli/README.md`, `apps/web/README.md`, `STUDYTOMY-ACCESS-PATH-INVENTORY.md` | Targeted source/contract reconciliation and corrections above; not every command and feature re-executed |
| `docs/studytomy/AGENT-ARCHITECTURE-GUIDE.md` | Inspected recent architecture, stack and evidence framing; retained its dated snapshot and diagrams rather than duplicating them |
| `docs/studytomy/IMPLEMENTATION-STATUS.md`, `MIGRATION-AND-ROLLBACK.md` | Inspected current headings/status; actively edited by implementation writer, left untouched to avoid collisions. Not fully revalidated |
| `docs/studytomy/ALE-IMPLEMENTATION.md`, `ALE-LOCAL-PILOT.md`, `CONTENT-PUBLICATION-PATHS.md`, `GENUI-IMPLEMENTATION.md` | Local evidence/history navigation reviewed; not every historical assertion replayed. Untracked files not silently added to this commit |
| `docs/studytomy/BASELINE.md`, `UPSTREAM-PR-1192-SECURITY-REVIEW.md`, `apps/cli/TEST_AUDIT.md` | Historical/baseline classification; retain original dated facts, not a current blanket status |
| `STUDYTOMY-FAMILY-LEARNING-ATOMIC-IMPLEMENTATION-SPEC.md`, `VIRTUAL_SCIENCE_LAB_PROPOSAL.md`, `docs/studytomy/ADR-LEARNER-CREDENTIALS.md` | Specification/proposal/decision classification; preserve requirements and unapproved decisions, not rewrite as delivered features |
| `DEMO_STACK.md`, `apps/api/src/tests/security/README.md`, `apps/e2e/features/rtl/README.md`, `apps/e2e/features/scorm/README.md`, `apps/web/AGENTS.md`, `apps/web/ee-stub/README.md`, `docs/notes/multi-tenancy.md` | Component-purpose classification and selected context review only; full runtime/command validation outstanding |
| `apps/api/src/services/demo/bundle/LICENSES.md`, `packages/react/README.md`, `packages/sdk/README.md`, `packages/vue/README.md` | Provenance/package-purpose classification only; no new licensing or package-conformance claims |

No file was proven wholly redundant. Consolidation is by the short [task-oriented index](README.md), not deletion of unique requirements, historical evidence or licensing notices. Future updates should extend the authoritative owner document rather than add another overlapping overview.

## Verification

Documentation diff/whitespace and local links in changed files are checked before publication. References to unpublished local source are explicitly marked as local. The application writer's index and changes remain untouched. Publishing this pass must not be reported as a complete audit of every Markdown assertion.

### Inspected local-source fingerprints

| File | SHA-256 at inspection |
|---|---|
| `apps/api/src/router.py` | `d51731f61591c90e4f53d364825c9586326f3c76973a489062e6ab642cbc12fa` |
| `apps/api/src/services/studytomy/adaptive_mastery.py` | `bbcfec4c7d10668f52292912a654c9fd0f4474bde9d49e345f007080cc274eb9` |
| `apps/api/src/services/studytomy/adaptive_reports.py` | `6410466bbbe91f45bfe7ce52d76b6d610a9f8ddcc863b8bf81147565bac6bc54` |
| `apps/api/src/services/studytomy/adaptive_attempts.py` | `61a60528f2574e32641357890e075bbd3a147f5a2d6bdba5e8ed9b9760909676` |
| `apps/api/src/db/courses/courses.py` | `e4fb6472e669bdc38c38205a2c2cb5e9e269db78c86cba6dd14445aa58e3be07` |

## Extended active-document pass

All repository-owned Markdown files in the tracked inventory were considered for purpose and currency, including component/package guides, tenancy, demo operation, licensing/proposals and the active local evidence logs. This is documentation coverage, not exhaustive verification of every historical test result or every application behavior. MDX pages and vendored/generated dependency docs remain excluded.

| Additional area | Source inspected and resulting correction |
|---|---|
| Demo operation | `apps/api/run_demo_api.sh`, `apps/collab/run_demo_collab.sh`, `apps/web/services/config/config.ts:155`, `apps/api/src/routers/instance.py:44-84` and `services/orgs/cache.py`: distinguish generated Compose prerequisites, separate terminals and explicit web API config; remove destructive Redis-wide cache advice; label fixture-only SaaS/verification behavior |
| Security test guide | Actual test inventory and `apps/api/src/tests/conftest.py:14-52`: replace stale six-file/mock-only/CI claims with focused test navigation, correct API working directory and local fixture limitations |
| E2E subguides | `apps/e2e/core/instance.ts:13-36`, SCORM fixture generator: correct relative fixture destination and split API origin; retain EE requirements and local disposable scope |
| EE stubs | `apps/web/next.config.js:8-35`: document known-entrypoint detection and forced-off flags, not directory-exists-only behavior |
| Tenancy | `apps/api/config/config.py:308-397,637-660`, web proxy/auth and resource authorization paths: qualify EE/SaaS selection and remove blanket all-endpoints-use-one-membership-helper claim |
| Migration notes | Actual local revision files continue beyond the old foundation head; mark old 169-test rehearsal historical and require inspection of actual graph/target DB before operations |
| SDK/renderers | Package manifests, SDK HTTP/auth/generator sources, React custom-element and Vue exports: preserve upstream API docs; clarify published-package/local-fork distinction and Windows Python resolution; keep unresolved licensing decisions intact |
| Architecture evidence links | Nineteen references led to unpublished local source, not files in published main. Convert to explicit local path:line references without losing evidence or claiming code was published |
| Historical CLI audit | Retain unique mutation results but prominently label them as historical, not freshly re-executed evidence |

The credential ADR remains a pending decision: local `UserBase.email` and native authentication still require email, and profile creation is not credential provisioning. The virtual lab document is already a proposal pinned to source revisions, so it is retained. Demo provenance and license notices are retained without relicensing or asserting fresh provenance certification. The scoped web AGENTS block is generated Next.js guidance and remains intact.

### Active writer-owned evidence logs

Read-only cross-checks covered the current summaries, historical checkpoint structure, command paths and material permission/migration assertions in `IMPLEMENTATION-STATUS.md`, `CONTENT-PUBLICATION-PATHS.md`, `GENUI-IMPLEMENTATION.md`, `ALE-IMPLEMENTATION.md` and `ALE-LOCAL-PILOT.md`. Their local source references were checked against managed-course access, publication, report and practice services. Their dated counts were not rerun.

- The current implementation summary supersedes early “Still open” claims: managed markers/grants, refund and retention mechanisms now exist locally. The old sections must not be treated as current absence claims.
- Content-publication and ALE “missing” tables are initial baselines; later checkpoints and actual source supersede them.
- GENUI HTTP routes exist, but `learning_practice.py:60-69` still denies practice pending approved policies. Do not confuse route presence with activation.
- GENUI's retained `61b26e413fa2`, ALE's `b043fde62c98` chain and 494-test count are historical checkpoints. Inspect current graph and preserve actual target revision during rollback.

After the active writer explicitly reserved these five files, minimal history headings/reading-order notes and the two stale revision/count phrasings were corrected locally. All five content hashes were checked before editing; the existing Git index hash was unchanged afterward. The reservation was then released. These evidence files remain with their staged/untracked implementation work; this documentation publication does not copy or stage the entire large logs. The local-only edits will accompany their owner’s future reviewed publication.

Final published-tree check: 29 tracked Markdown files, 77 relative file links checked, zero missing targets. This checks file existence, not external URLs or every heading anchor. Documentation whitespace validation passed. No application tests, production operations or deployment were run for this audit.

## Updated-source documentation sweep (25 November 2026)

**Source baseline:** `main` and `origin/main` at `997ac953d8e4c7acfcdd666ba8a53cc72422d9b7`, clean before the documentation sweep. The merged source adds deterministic ordering through `CourseChapter` and `ChapterActivity` association rows, a managed-PDF topic-check UI/API path, and Playwright specs 21 and 22. The documentation changes from that sweep were subsequently committed and pushed in `28eff10ec9eace33cee5024465378307f7a6d425`; no application code, migration or deployment was included.

The tracked inventory contains **53 `.md` files and 138 `.mdx` files (191 Markdown-family files total)**. The current-facing pages below were reconciled against source and the latest exact-SHA checks. Date-stamped baselines, implementation ledgers, proposals, approval records, package docs, licensing notices, generated agent guidance, and old test receipts were retained as historical/contracts; their old result counts were not relabeled as current.

| Updated current-facing documents | Reconciled contract |
|---|---|
| `README.md`, `AGENTS.md`, `LOCAL-ONLY.md` | Project scope, current source vs approval status, association-backed order, latest hosted verification, and no-deployment boundary. |
| `docs/studytomy/IMPLEMENTATION-STATUS.md`, `TOPIC-LEARNING-LOOP.md`, `GENUI-IMPLEMENTATION.md`, `README.md` | Managed-PDF topic-check status and acceptance limits; tutor enum-only status; guardian welcome/study-path and legacy-migration gaps; precise lesson-ordering contract. |
| `apps/e2e/README.md`, `docs/LOCAL_END_TO_END_TESTING.md` | Specs 21/22 guarantees, fixture and global-setup prerequisites, topic-check runner commands, and Docker Engine context options including OrbStack. |
| `docs/content/developers/migration/index.mdx`, `programmatic.mdx` | Course, chapter and activity hierarchy and the actual association-owned ordering/tie-break behavior. |
| `docs/content/self-hosting/installation/requirements.mdx`, `docs/content/developers/contributing/dev-environment.mdx` | Engine-neutral Compose/runtime requirements and resource-setting wording. |
| `packages/sdk/README.md` | Removed an obsolete MIT-license placeholder; it now links the existing package license and matches `packages/sdk/package.json`. |

The current source check confirms `LessonTutorAction` is only defined in `learning_surface.py` and used by its enum unit tests, not a production route. `families/page.tsx` mounts the family workspace, but no guardian welcome/study-path email flow was found. `studytomy_backfill.py` is read-only inventory, not a data migration. These gaps are recorded explicitly; no launch/business approval was inferred.

**Source-checkpoint CI evidence:** [CI Build run 45660150950](https://github.com/nirzaf/studytomyportal/actions/runs/45660150950) passed all nine build/test jobs on `997ac953`. [Repository Security run 45660150883](https://github.com/nirzaf/studytomyportal/actions/runs/45660150883) passed both secret-history/source and dependency-scan jobs. These workflows do not execute the live Playwright topic-check scenario or deploy the application.

**Documentation validation:** 281 relative repository links and 510 root-relative docs/asset routes were checked with zero missing targets; generated `/reference` routes were excluded from the filesystem check. The Fumadocs production build validates the generated documentation routes. External URL reachability and every fragment anchor were not tested. Dated acceptance logs remain scoped to their recorded environment and SHA.

## Main-branch verification receipt (26 November 2026)

At this verification point, `main` was `28eff10ec9eace33cee5024465378307f7a6d425`. [CI Build run 45666724422](https://github.com/nirzaf/studytomyportal/actions/runs/45666724422) passed all nine jobs; [Repository Security run 45666724252](https://github.com/nirzaf/studytomyportal/actions/runs/45666724252) passed both jobs. The follow-up corrected the current implementation summary and replaced stale “local-only/no commit” wording with the actual publication receipt. It did not deploy the application or run the fixture-backed topic-check browser scenario.
