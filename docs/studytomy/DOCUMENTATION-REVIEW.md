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

## Current engineering boundaries

The observed diagnostic, mastery, tutoring and report services are real local source, not merely a roadmap. They still require dependency-complete tests, browser acceptance and applicable education/safeguarding approvals. MCP has an existing authenticated native service (`apps/api/src/services/mcp/handlers.py`); a complete newly requested teacher/admin authoring and tightly course/lesson-grounded generation workflow was not established by this review. Do not create a redundant server or claim that workflow complete.

No application tests, migrations or browser journey were run in this documentation pass. Historical test counts in evidence logs remain scoped records, not fresh test results. Never infer production readiness, approved curriculum, or real-user activation from this document.

## Coverage and preservation decisions

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
