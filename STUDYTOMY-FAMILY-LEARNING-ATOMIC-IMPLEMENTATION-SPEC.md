# Studytomy Portal — Family Learning Service — Implementation Checklist

**Status: in progress. 17/73 tasks have verified implementation, 0 are accepted for launch, 20/20 business decisions are unapproved, 0/34 acceptance scenarios have been executed end to end.**

Re-verified 2026-11-20 at `ef6b730d`: the focused Studytomy suite passes 416 of 420 and the full API suite 10,588 of 10,603 against a migrated disposable PostgreSQL database. The [verification log](docs/studytomy/LOCAL-VERIFICATION.md#documentation-refresh-verification-sweep-20-november-2026) lists every open failure and its cause. `[x]` below means implementation plus focused evidence — never acceptance — and where a focused check now fails, the task says so.

Source: business requirements draft D2 (`BR-*`, `AC-*`, `D-*` identifiers preserved). Prose removed from this file — the detailed specification, data contracts, API inventory, rule traceability and evidence register remain in this file's git history. Current status detail: [IMPLEMENTATION-STATUS](docs/studytomy/IMPLEMENTATION-STATUS.md), [CONTENT-PUBLICATION-PATHS](docs/studytomy/CONTENT-PUBLICATION-PATHS.md), [LOCAL-VERIFICATION](docs/studytomy/LOCAL-VERIFICATION.md).

**Checkbox meaning**
- `[x]` — implementation present and verified by focused tests or local evidence in the records above. Not acceptance, approval, or launch readiness.
- `[ ]` — incomplete. The note states what is missing.
- `· cond:` — applies only if that service is sold or enabled. `· Later:` — not launch scope.

## 1. Boundaries still in force

- No new framework, backend, database, broker or microservice; no TanStack Start, Workers/D1, Supabase or ORM/identity-provider replacement. Reuse the existing Next.js/React and FastAPI/PostgreSQL stack, Studytomy Portal auth and storage.
- `studytomy-react` (`studytomy.com`) stays separate: no shared session, database, API coupling, credential migration or Supabase dependency.
- No automatic Enterprise enablement: no licensing-safeguard changes, no private code, no treating public stubs as features.
- Payment never creates guardianship; a staff profile draft is not an active learner account; eligibility for learning, participation and purchasing are separate decisions.
- Expired, refunded or disputed money does not delete learning evidence or invalidate a valid award; revoking one grant never revokes unrelated grants or siblings.
- Submitted work and its question/rubric/assistance policy survive later edits and retries; corrections append a version rather than overwrite.
- A request, attempted payment, local draft or approved refund is not proof of collection, receipt or returned money; critical mutations commit with their required business evidence.
- No client boolean, route choice, role-switch control or AI output may set authority, settled money, final sanction or approved publication. AI drafts and suggests; it does not approve guardianship, misconduct, refunds, restrictions, safeguarding or publication.
- Ended or restricted authority stops future access and sends; already-downloaded material cannot be recalled — treat prior disclosure as an incident.
- Completion, assessed result and demonstrated understanding stay separate fields; a reported adult (including admin or guardian) is excluded from their own case's evidence and notifications.
- Simulation values come from validated scientific models, not the LLM; educator review is required for curriculum mapping and rubrics; no secrets or personal data in logs, reports or evidence bundles.
- Commits, pushes, merges, releases and migrations happen only on explicit user instruction.

## 2. Business decisions — all open

- [ ] <a id="d-01"></a>**D-01** Markets, learner ages, independent participation and purchasing — business and safeguarding leads
- [ ] <a id="d-02"></a>**D-02** Initial subjects, learner level, language, curriculum claims — education lead
- [ ] <a id="d-03"></a>**D-03** Guardian authority, multiple guardians, disputes, adult transition — family services lead
- [ ] <a id="d-04"></a>**D-04** Support routes, monitored hours, response targets, staffing — support and education leads
- [ ] <a id="d-05"></a>**D-05** Prices, currencies, charges, access periods, learner allocation — commercial lead
- [ ] <a id="d-06"></a>**D-06** Membership period, renewal, retries, grace, recovered-payment periods — commercial lead
- [ ] <a id="d-07"></a>**D-07** Refunds, cancellation, disputes, interruption and withdrawal remedies — commercial lead
- [ ] <a id="d-08"></a>**D-08** Pass marks, marking method, assistance, attempts, deadlines, appeals — education lead
- [ ] <a id="d-09"></a>**D-09** Release schedule, completion prerequisites, any mastery gate — education lead
- [ ] <a id="d-10"></a>**D-10** AI learner eligibility, supervision, materials, limits, review — education and AI leads
- [ ] <a id="d-11"></a>**D-11** Community audiences, adult contact boundaries, moderation — safeguarding lead
- [ ] <a id="d-12"></a>**D-12** Information use, consent, parent report scope, publicity — privacy lead
- [ ] <a id="d-13"></a>**D-13** Retention and record access, correction, removal, incident handling — privacy lead
- [ ] <a id="d-14"></a>**D-14** Reminder frequency, quiet periods, contacts, marketing choices — communications lead
- [ ] <a id="d-15"></a>**D-15** Accessibility standard, supported formats, service recovery — operations lead
- [ ] <a id="d-16"></a>**D-16** Content rights, review approvals, certificate presentation — education lead
- [ ] <a id="d-17"></a>**D-17** Pilot size, duration, comparison method, measures, success thresholds — business lead
- [ ] <a id="d-18"></a>**D-18** Customer terms and requirements for the selected market — legal/commercial lead
- [ ] <a id="d-19"></a>**D-19** Staff authority, refund limits, exceptions, financial review frequency — finance lead
- [ ] <a id="d-20"></a>**D-20** Discounts, bundles, referrals, trial rules, optional additions — commercial lead

Approval record per decision: scope, selected policy, rationale, effective date, named approver, evidence, next review. Synthetic fixtures are not approvals.

## 3. Tasks

### Foundations

- [ ] <a id="task-001"></a>**TASK-001** Freeze the implementation baseline and regression contracts — partial: the pinned head contract still asserts `b91d2f6a08c3` while `alembic heads` reports `b7c2e9a4d1f0`, so the regression test fails; D-17 sign-off open
- [x] <a id="task-002"></a>**TASK-002** Persist approved decisions and accountable service owners
- [x] <a id="task-003"></a>**TASK-003** Extend staff capabilities without changing learner authority
- [x] <a id="task-004"></a>**TASK-004** Record mandatory business decisions in the same transaction
- [x] <a id="task-005"></a>**TASK-005** Implement request replay and stale-write protection
- [x] <a id="task-006"></a>**TASK-006** Build durable delivery with recipient revalidation — no provider, schedule or worker configured yet
- [ ] <a id="task-007"></a>**TASK-007** Extend private-file handling for evidence and reports — partial: case/report files, retention execution
- [x] <a id="task-008"></a>**TASK-008** Make each commercial offer pass explicit launch gates — readiness stays false on dependent evidence

### Learners, family authority and offers

- [x] <a id="task-009"></a>**TASK-009** Add individual learner profiles and eligibility decisions
- [ ] <a id="task-010"></a>**TASK-010** Resolve child credentials and harden recovery — blocked: ADR only, D-01/D-03/D-12
- [ ] <a id="task-011"></a>**TASK-011** Implement verified guardian authority with scoped permissions — partial: evidence references unresolved, no read-path integration
- [ ] <a id="task-012"></a>**TASK-012** Handle disputed, restricted and ended relationships — partial: no case-evidence link, report suppression or recheck path
- [ ] <a id="task-013"></a>**TASK-013** Separate independent-student sharing from guardianship — partial: recipient UI, delivery, due-review worker; migration unrun
- [ ] <a id="task-014"></a>**TASK-014** Provide the launch family-service staff workspace — partial: no decision controls, browser/accessibility run
- [ ] <a id="task-015"></a>**TASK-015** Prevent learner confusion and stale-session disclosure — partial: local draft persistence, collaborative teardown
- [ ] <a id="task-016"></a>**TASK-016** Add child-safe public and community projections — partial: publicity consent control, guardian/dependent consent
- [x] <a id="task-017"></a>**TASK-017** Create immutable offer versions and customer-term snapshots
- [ ] <a id="task-018"></a>**TASK-018** Expose comparable offers and truly free samples — not started: blocked on D-02/D-05/D-18/D-20
- [ ] <a id="task-019"></a>**TASK-019** Enforce available places and deliverable human support — partial: reservation not wired to sale state

### Orders, money, entitlements and access

- [x] <a id="task-020"></a>**TASK-020** Record purchaser approval and named learner allocations
- [ ] <a id="task-021"></a>**TASK-021** Normalise confirmed collections without unlocking Enterprise — partial: provider event intake
- [x] <a id="task-022"></a>**TASK-022** Reconcile duplicate, late and uncertain payments
- [x] <a id="task-023"></a>**TASK-023** Issue privacy-safe receipts and purchase-management views
- [ ] <a id="task-024"></a>**TASK-024** Persist independent course-access grants — partial: sponsorship and provisional grants
- [ ] <a id="task-025"></a>**TASK-025** Compose family-service access with the existing reader gate — completion criteria unmet
- [ ] <a id="task-026"></a>**TASK-026** Close alternate content and submission access paths — unverified: route/service-level tests outstanding
- [ ] <a id="task-027"></a>**TASK-027** Correct allocation without moving learning evidence — partial: used-allocation exception path
- [ ] <a id="task-028"></a>**TASK-028** Preserve re-entry and retained-history rights — partial: retained awards, period-specific metrics
- [ ] <a id="task-029"></a>**TASK-029** Approve and reserve refunds against actual money — partial: settlement of provider returns
- [ ] <a id="task-030"></a>**TASK-030** Settle returns and apply scoped access effects — partial: chargeback dispute resolution
- [ ] <a id="task-031"></a>**TASK-031** Create explicit recurring contracts only when offered · cond:membership — not started
- [ ] <a id="task-032"></a>**TASK-032** Serialise membership renewal and cancellation · cond:membership — not started
- [ ] <a id="task-033"></a>**TASK-033** Handle failed collection and grace without harming other grants · cond:membership — not started
- [ ] <a id="task-034"></a>**TASK-034** Manage offer withdrawal, interruption and material change — not started
- [ ] <a id="task-035"></a>**TASK-035** Validate discounts, bundles and optional additions · cond:promotion — not started

### Content, learning, grading and awards

- [x] <a id="task-036"></a>**TASK-036** Add owner/reviewer approval and distribution-rights evidence
- [ ] <a id="task-037"></a>**TASK-037** Preserve committed content and assessment-policy revisions — partial: assessment-policy binding
- [ ] <a id="task-038"></a>**TASK-038** Show goals, accessible formats and the first learning step — not started
- [x] <a id="task-039"></a>**TASK-039** Add explicit assessed-pass prerequisites and reliable dates · cond:gated offer
- [ ] <a id="task-040"></a>**TASK-040** Make draft saving and submission acknowledgments truthful — unverified
- [ ] <a id="task-041"></a>**TASK-041** Keep immutable submitted attempts while retaining current API projections — unverified
- [ ] <a id="task-042"></a>**TASK-042** Version grading outcomes and preserve educator judgement — unverified
- [ ] <a id="task-043"></a>**TASK-043** Implement fair adjustments, misconduct review and appeals — not started
- [ ] <a id="task-044"></a>**TASK-044** Record assistance and individual evidence explicitly — unverified
- [x] <a id="task-045"></a>**TASK-045** Preserve award evidence and private verification
- [ ] <a id="task-046"></a>**TASK-046** Keep practice, scenarios and boards separate from grading · cond:interactive offer — not started

### AI assistance

- [ ] <a id="task-047"></a>**TASK-047** Apply per-learner AI eligibility and assistance policy · cond:AI — partial: supervision, material and limit enforcement
- [ ] <a id="task-048"></a>**TASK-048** Deliver bounded lesson guidance and an error-review route · cond:AI — not started
- [ ] <a id="task-049"></a>**TASK-049** Make AI allowance and outage handling explicit · cond:AI — not started
- [ ] <a id="task-050"></a>**TASK-050** Keep AI-generated teaching content in reviewed drafts · cond:AI authoring — not started

### Reporting, communication and community

- [x] <a id="task-051"></a>**TASK-051** Produce reviewed staff-led parent progress reports · cond:included report
- [x] <a id="task-052"></a>**TASK-052** Revalidate recipients and correct wrong disclosures · cond:included report — focused report-routing check open at `ef6b730d` (case `acknowledged` where `assigned` is asserted)
- [ ] <a id="task-053"></a>**TASK-053** Show reliable deadlines and material date changes — not started
- [ ] <a id="task-054"></a>**TASK-054** Separate service messages, optional reminders and marketing — not started
- [ ] <a id="task-055"></a>**TASK-055** Constrain course spaces, membership and adult contact · cond:community — not started

### Cases, safeguarding, privacy and operations

- [ ] <a id="task-056"></a>**TASK-056** Create purpose-scoped service cases and confidential ACLs — not started
- [ ] <a id="task-057"></a>**TASK-057** Provide a safe reporting route even when learning is restricted · cond:child participation — not started
- [ ] <a id="task-058"></a>**TASK-058** Review restrictions, appeals and safe re-entry · cond:community — not started
- [ ] <a id="task-059"></a>**TASK-059** Offer support independently of learning access — not started
- [ ] <a id="task-060"></a>**TASK-060** Review essential access and assessment adjustments before sale — not started
- [ ] <a id="task-061"></a>**TASK-061** Separate necessary terms and optional purposes — partial: guardian consent, downstream purpose enforcement
- [ ] <a id="task-062"></a>**TASK-062** Implement authority-checked record access and correction — partial: dependent/commercial cases, reviewed correction
- [ ] <a id="task-063"></a>**TASK-063** Apply purpose-specific retention and reviewed holds — partial: other record classes, versioned object storage
- [ ] <a id="task-064"></a>**TASK-064** Link quality, privacy and outage incidents to remediation — not started
- [x] <a id="task-065"></a>**TASK-065** Define source-faithful pilot and operating measures
- [ ] <a id="task-066"></a>**TASK-066** Verify real PostgreSQL constraints and transaction races — partial: races await dependent workflows; delete-time guard precedence (`MANAGED_ASSESSMENT_SOURCE_RETAINED` ahead of `ADAPTIVE_RETENTION_REVIEW_REQUIRED`) needs a ruling before the retention checks can pass
- [ ] <a id="task-067"></a>**TASK-067** Exercise family, educator and commerce journeys end to end — not started
- [ ] <a id="task-068"></a>**TASK-068** Rehearse additive migration and non-destructive backfill — partial: deployed restore, backfill
- [ ] <a id="task-069"></a>**TASK-069** Demonstrate delivery recovery and service operating targets — blocked: no approved availability/response targets
- [ ] <a id="task-070"></a>**TASK-070** Approve a bounded pilot with reconciled evidence — blocked: pilot acceptance open

### Later — not launch scope

- [ ] <a id="task-071"></a>**TASK-071** Expand lesson help to approved course-wide tutoring · Later
- [ ] <a id="task-072"></a>**TASK-072** Add dedicated parent self-service and automatic updates · Later
- [ ] <a id="task-073"></a>**TASK-073** Offer evidence-based personalised reminders · Later

## 4. Acceptance scenarios — none executed end to end

Task-level tests do not satisfy a scenario. Status values: `NOT_RUN`, `PASS`, `FAIL`, `BLOCKED`, `NOT_APPLICABLE` (approved reason required).

- [ ] <a id="ac-01"></a>**AC-01** Parent purchases for a child — NOT_RUN
- [ ] <a id="ac-02"></a>**AC-02** Payer has no guardian authority — NOT_RUN
- [ ] <a id="ac-03"></a>**AC-03** Two children in one family — NOT_RUN
- [ ] <a id="ac-04"></a>**AC-04** Unresolved or disputed guardian authority — NOT_RUN
- [ ] <a id="ac-05"></a>**AC-05** Relationship ends or learner becomes independent — NOT_RUN
- [ ] <a id="ac-06"></a>**AC-06** Wrong child selected — NOT_RUN
- [ ] <a id="ac-07"></a>**AC-07** Sample and truthful offer — NOT_RUN
- [ ] <a id="ac-08"></a>**AC-08** Unconfirmed or failed payment — NOT_RUN
- [ ] <a id="ac-09"></a>**AC-09** Duplicate or late collection — NOT_RUN
- [ ] <a id="ac-10"></a>**AC-10** Membership cancellation — NOT_RUN
- [ ] <a id="ac-11"></a>**AC-11** Failed renewal and overlapping access — NOT_RUN
- [ ] <a id="ac-12"></a>**AC-12** Refund and repeated request — NOT_RUN
- [ ] <a id="ac-13"></a>**AC-13** Price or benefit changes — NOT_RUN
- [ ] <a id="ac-14"></a>**AC-14** Scheduled and prerequisite restrictions — NOT_RUN
- [ ] <a id="ac-15"></a>**AC-15** Completion is not mastery — NOT_RUN
- [ ] <a id="ac-16"></a>**AC-16** Assisted and group work — NOT_RUN
- [ ] <a id="ac-17"></a>**AC-17** AI lacks sufficient course material — NOT_RUN
- [ ] <a id="ac-18"></a>**AC-18** AI unavailable or restricted assessment — NOT_RUN
- [ ] <a id="ac-19"></a>**AC-19** Practice and graded attempts stay distinct — NOT_RUN
- [ ] <a id="ac-20"></a>**AC-20** Interrupted submission and fair adjustment — NOT_RUN
- [ ] <a id="ac-21"></a>**AC-21** Mark or certificate correction — NOT_RUN
- [ ] <a id="ac-22"></a>**AC-22** Useful parent progress report — NOT_RUN
- [ ] <a id="ac-23"></a>**AC-23** Wrong report or inaccurate result — NOT_RUN
- [ ] <a id="ac-24"></a>**AC-24** Reminder choices and completion — NOT_RUN
- [ ] <a id="ac-25"></a>**AC-25** Harmful contact involving an adult — NOT_RUN
- [ ] <a id="ac-26"></a>**AC-26** Community membership and misconduct — NOT_RUN
- [ ] <a id="ac-27"></a>**AC-27** Unreviewed or incorrect lesson — NOT_RUN
- [ ] <a id="ac-28"></a>**AC-28** Support access and complaint — NOT_RUN
- [ ] <a id="ac-29"></a>**AC-29** Required learning adjustment — NOT_RUN
- [ ] <a id="ac-30"></a>**AC-30** Optional consent and public work — NOT_RUN
- [ ] <a id="ac-31"></a>**AC-31** Record request and leaving — NOT_RUN
- [ ] <a id="ac-32"></a>**AC-32** Launch and expansion control — NOT_RUN
- [ ] <a id="ac-33"></a>**AC-33** Recover access and change learners — NOT_RUN
- [ ] <a id="ac-34"></a>**AC-34** Find suitable support and begin — NOT_RUN

## 5. Verification

Each task records the command run, exit status and test IDs, plus failures and unverified areas. "Tests exist" is not "tests passed".

```bash
# Frontend — `lint` reports errors only; the strict script with a warning budget is the evidence gate.
cd apps/web && bun install --frozen-lockfile
bun run lint:strict . --max-warnings=0
bun ./node_modules/typescript/bin/tsc --noEmit
bun run test && bun run build

# Backend — existing suite on configured test databases.
cd apps/api && uv sync --frozen
TESTING=true uv run pytest src/tests/studytomy -m "not studytomy_postgres"

# PostgreSQL races and constraints — opt-in disposable loopback database only.
STUDYTOMY_TEST_DATABASE_URL=... uv run pytest src/tests/studytomy -m studytomy_postgres
```

Measured on 2026-11-20 at `ef6b730d`: web typecheck, 761 unit tests and the production build pass; `lint:strict . --max-warnings=0` exits non-zero on 975 pre-existing warnings with 0 errors, so treat it as a changed-file gate; the opt-in PostgreSQL Studytomy run ends 416 passed / 4 failed and the full API run 10,588 passed / 15 failed. `packages/react` and `packages/vue` require `bun run build` before their tests collect.

Invariants V-01 to V-20 (baseline compatibility, authority, staff separation, offers, money, concurrency, migrations, assessment evidence, awards, AI, reports, casework, notifications, retention, accessibility, performance, website separation, publication truth, metrics, pilot gate) stay open until the affected capability is exercised. A pilot is approved only when applicable AC scenarios pass or carry an approved non-applicability reason, every listed decision is approved, and unrelated Later capabilities remain disabled.
