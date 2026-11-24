# Local end-to-end testing on Mac

Runbook refreshed 21 November 2026 against `99209c77a177dd99f4377f68af007c7ecb7a3dc1`. The verification notes below preserve the historical 19 November checkpoint at `bc7b70027e08769b044a59e534c63ad8cc5d90ec`.
This is a **local-only** operator runbook, not deployment or real-learner approval.
Do not push, open a PR, deploy, configure CI/CD, or use a VPS as part of it.

## Evidence and limits

For new integration verification, follow the [same-version topic-loop acceptance cases](studytomy/TOPIC-LEARNING-LOOP.md#acceptance-on-the-same-published-version), including wrong answer, cited reteaching, distinct recheck, reload and allowed/denied staff history. Historical receipts below do not establish this full sequence.

Historical verification recorded on 19 November:
- Supported `python cli.py migrate` on a new disposable PostgreSQL 16 database: `schema empty: none -> 9b7d4c2a1e6f, 0 reconciled`.
- Current-source Luna/provider unit tests plus PostgreSQL grounded material/retrieval tests: **40 passed, 1 deselected in 3.69 seconds**. Bedrock was excluded because the existing environment lacks its optional `botocore[crt]` dependency.
- No new model request or browser acceptance run was performed for this document.
- Initial setup attempts exposed a startup-readiness race and an incorrect assumed database username. The fresh successful run used readiness polling, the actual configured username and a fail-fast shell. Only that final fresh run is bootstrap evidence.

Historical synthetic browser evidence at parent revision `dc607ab3`: same-release PDF-authored text → diagnostic → assisted practice → concept progress passed. Review/release was fixture setup, not a new staff UI acceptance run. The later Luna change affects structured model settings, not those flows, but this is **not** a fresh browser pass at `bc7b7002`.

See [the detailed verification record](studytomy/LOCAL-VERIFICATION.md), [architecture guide](studytomy/AGENT-ARCHITECTURE-GUIDE.md), and [documentation index](studytomy/README.md). Their dated results are evidence, not permission to enable real users.

## 1. Isolate the checkout, dependencies and storage

Prerequisites: Docker Desktop running, Git, Python/API dependencies matching `apps/api/uv.lock`, Bun/web dependencies matching the committed lockfile, and installed Playwright Chromium for browser tests. Do not update dependencies to fix an unrelated setup issue.

Inspect the main checkout and applicable instructions first. Never reset or clean someone else's changes. Use a git archive so build output and filesystem media cannot collide with an active development server:

```sh
set -eu
REPO=/Users/qts/Repos/studytomyportal
cd "$REPO"
git status --short
git rev-parse HEAD
RUN=$(mktemp -d /tmp/studytomy-local-e2e.XXXXXX)
git archive HEAD | tar -x -C "$RUN"
# Existing installed dependencies are reused read-only; do not install through these links.
ln -s "$REPO/apps/api/.venv" "$RUN/apps/api/.venv"
ln -s "$REPO/apps/web/node_modules" "$RUN/apps/web/node_modules"
printf '%s\n' "$RUN"
```

Keep `$RUN` in a local shell. Never copy `.env`, API credentials, real uploads or real database dumps into the archive. Filesystem content delivery writes inside the isolated API working directory. It needs no shared S3 bucket or additional object-storage service for this rehearsal. This does not verify S3 backup/recovery.

## 2. Disposable Docker PostgreSQL and Redis

Example reserved ports: PostgreSQL `59458`, Redis `59459`, API `1342`, web `3042`. First use `lsof -nP -iTCP:<port> -sTCP:LISTEN` for each; choose other unused ports consistently if occupied. Do not stop an unrelated listener.

The current disposable database command below uses the same pinned PostgreSQL 19 image as new CLI installs. This temporary test container has no persistent PostgreSQL volume; the PG19 named-volume layout is exercised by the separate CLI persistence acceptance test.

Use unique container names and local synthetic secrets. These example commands create only disposable resources:

```sh
set -eu
TAG=$(basename "$RUN")
PG="$TAG-pg"
REDIS="$TAG-redis"
umask 077
python3 -c 'import secrets; print("POSTGRES_USER=demo"); print("POSTGRES_DB=studytomy_demo_accounts_test"); print("POSTGRES_PASSWORD="+secrets.token_hex(24))' > "$RUN/postgres.env"
docker run -d --name "$PG" --env-file "$RUN/postgres.env" \
  --mount type=tmpfs,destination=/var/lib/postgresql \
  -p 127.0.0.1:59458:5432 \
  pgvector/pgvector:0.8.7-pg19@sha256:89879f41d564f67b0c1462a491d16fcbc6de39ac10db17e7a4bba2138a5e0ba5
docker run -d --name "$REDIS" -p 127.0.0.1:59459:6379 redis:7-alpine
for i in $(seq 1 30); do
  docker exec "$PG" pg_isready -U demo -d studytomy_demo_accounts_test >/dev/null 2>&1 && break
  sleep 1
done
docker exec "$PG" pg_isready -U demo -d studytomy_demo_accounts_test
set -a
. "$RUN/postgres.env"
set +a
export LEARNHOUSE_SQL_CONNECTION_STRING="postgresql://demo:${POSTGRES_PASSWORD}@127.0.0.1:59458/studytomy_demo_accounts_test"
export LEARNHOUSE_REDIS_CONNECTION_STRING=redis://127.0.0.1:59459/0
export LEARNHOUSE_AUTH_JWT_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
export LEARNHOUSE_CONTENT_DELIVERY_TYPE=filesystem
export LEARNHOUSE_DOMAIN=localhost:1342
export LEARNHOUSE_FRONTEND_DOMAIN=localhost:3042
export LEARNHOUSE_TENANCY=single
export LEARNHOUSE_AUTO_MIGRATE=false
export LEARNHOUSE_IS_AI_ENABLED=false
export LEARNHOUSE_STUDYTOMY_GROUNDED_DRAFTS_ENABLED=true
cd "$RUN/apps/api"
.venv/bin/python cli.py migrate
.venv/bin/python cli.py migrate --status
```

Do not use raw Alembic alone to bootstrap an empty database: the supported CLI coordinates initial model/schema creation. The historical 19 November run expected revision `9b7d4c2a1e6f`; this checkout's current single head is `b7c2e9a4d1f0`. Reread the current migration head after any code change. The command above uses the pinned `pgvector/pgvector:0.8.7-pg19` image. The API migration/startup tests and the synthetic live lab journey passed on isolated PG19; qualified educator/safeguarding/content-rights approval and hosted acceptance remain open in [the PostgreSQL 19 upgrade notes](studytomy/POSTGRESQL-19-UPGRADE.md).

## 3. Accounts and repeatable baseline

Choose **one** baseline below. Do not run both against the same database. For an administrator-only baseline, use the supported CLI:

```sh
export LEARNHOUSE_INITIAL_ADMIN_EMAIL=admin@example.invalid
# zsh: enter a unique local-only password without echo; never reuse a real password.
read -s 'LEARNHOUSE_INITIAL_ADMIN_PASSWORD?Local demo password: '
export LEARNHOUSE_INITIAL_ADMIN_PASSWORD
printf '\n'
.venv/bin/python cli.py install --short
unset LEARNHOUSE_INITIAL_ADMIN_PASSWORD
```

### Persistent four-account baseline (verified locally)

Instead of `cli.py install --short`, use the freshly **migrated but unpopulated** database. The committed `scripts/local_demo_seed.py` and `scripts/local_demo_smoke.py` are included in the `git archive` checkout.

The credential output must be outside the archive checkout in a private directory:

```sh
DEMO_PRIVATE=$(mktemp -d /tmp/studytomy-demo-private.XXXXXX)
chmod 700 "$DEMO_PRIVATE"
cd "$RUN/apps/api"
.venv/bin/python ../../scripts/local_demo_seed.py --credentials "$DEMO_PRIVATE/accounts.json"
```

The seed fails closed unless the URL is loopback PostgreSQL, has no routing query, and the database name matches `studytomy_demo_<name>_test`. It refuses any pre-existing user/organization and any existing credential output. It never updates existing accounts or publishes content. Credentials are random per fresh run, not default passwords. Labels and native role assignments are deterministic:

| Account | Native role | Boundary |
|---|---|---|
| `demo-admin@example.com` | Admin | Organization admin, not platform superadmin |
| `demo-teacher@example.com` | Instructor | Native teaching permissions |
| `demo-reviewer@example.com` | Instructor | Separate identity; **no** business release-review scope or fabricated approval |
| `demo-learner@example.com` | User | Native learner, no managed entitlement automatically granted |

Addresses are synthetic and no notifications are sent. Read passwords privately from the owner-only generated manifest when manually signing in. Do not paste the manifest into chats, Git or screenshots. The seed uses existing setup helpers and native password hashing; runtime login, authorization and licensing checks remain active.

After launching the isolated API, run the actual authentication smoke:

```sh
cd "$RUN/apps/api"
.venv/bin/python ../../scripts/local_demo_smoke.py \
  --credentials "$DEMO_PRIVATE/accounts.json" --api http://127.0.0.1:1342
```

Verified on 19 November at `bc7b7002` plus these local scripts: all four accounts signed in through `/api/v1/auth/login` (200), `/auth/me` and `/users/session` (200), with exact expected native roles and organization, no platform superadmin, and anonymous identity requests denied (401). Four unsafe database targets were rejected by the guard. This is real HTTP authentication/session verification, not a browser or learning-flow pass.

**Resume:** reuse the same private manifest and stopped containers; do not reseed. **Reset:** stop only the run's owned services/containers and choose a new unique container/database/private directory, rerunning the setup. Leave old stopped resources until deliberately disposed of; the script offers no destructive reset. If setup fails partway, use a new disposable namespace rather than modifying partially seeded history.

The default organization slug is `default`. Preserve authentication; do not add an anonymous bypass. Native admin installation does **not** provision the complete managed adaptive learning scenario.

For repeatable automated role/access cases, use the synthetic actors and explicit permissions in:
- `apps/api/src/tests/studytomy/test_grounded_material_postgres.py`: teacher, learner, organization/course boundaries and source receipts.
- `apps/api/src/tests/studytomy/test_adaptive_curriculum_api.py`: independent learner, staff, exact review/release, policy/consent/grant and diagnostic/practice/report workflow.
- `apps/api/src/tests/studytomy/test_content_evidence.py`: review authority and retained release evidence.

These are tests, **not a supported persistent demo-account seeder**. They include destructive-to-fixture lifecycle assertions such as retirement and revocation. Do not run the full test and assume its leftover records are a usable demonstration. Reset by creating a new isolated database/container and reinstalling, not by weakening immutable-history triggers or clearing a shared database.

The four-account seed above provides persistent native identities. `scripts/local_virtual_lab_e2e_scenario.py` now prepares a single synthetic managed Momentum Lab journey for the live browser test. It uses ordinary local API routes and records synthetic-only policy, rights, consent, eligibility and release decisions; it is not a real approval or a general managed-course seeder. The adaptive diagnostic/practice/progress and guardian/report workflows remain separate. Never send invitations to real addresses or copy synthetic decisions into an organization.

## 4. Launch API and web

In the shell with the API environment loaded, from the isolated API directory:

```sh
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 1342 --log-level warning
```

In another local terminal, set `RUN` to the same archive and launch:

```sh
cd "$RUN/apps/web"
NODE_OPTIONS=--dns-result-order=ipv4first \
NEXT_PUBLIC_LEARNHOUSE_API_URL=http://localhost:1342/api/v1/ \
NEXT_PUBLIC_LEARNHOUSE_BACKEND_URL=http://localhost:1342/ \
LEARNHOUSE_INTERNAL_API_URL=http://127.0.0.1:1342/ \
NEXT_PUBLIC_LEARNHOUSE_DOMAIN=localhost:3042 \
NEXT_PUBLIC_LEARNHOUSE_DEFAULT_ORG=default \
NEXT_PUBLIC_LEARNHOUSE_HTTPS=False \
LEARNHOUSE_DISABLE_EE=1 \
node node_modules/next/dist/bin/next dev --webpack --hostname localhost --port 3042
```

Use `localhost` for browser/web host consistently. A previous isolated run bound Next to `127.0.0.1` while routing with `localhost`; it recursively rewrote `/orgs/default`. Correct host/config alignment fixed that test runtime without source changes.

Check `http://localhost:1342/`, `http://localhost:3042/api/health`, then sign in at `http://localhost:3042/login`. Learner entry: `http://localhost:3042/account/learning`. A 200 health response alone proves neither login nor entitlement.

## 5. Focused tests before browser rehearsal

Use a **separate** migrated test database for test suites, not the database holding browser fixtures. Set `STUDYTOMY_TEST_DATABASE_URL` explicitly to its loopback PostgreSQL URL; the name must end in `_test`. The test harness never falls back to the app database and does not migrate it.

```sh
cd "$RUN/apps/api"
.venv/bin/python -m pytest -q -p no:cacheprovider \
  src/tests/services/test_llm_luna_settings.py \
  src/tests/services/test_llm_provider.py \
  src/tests/studytomy/test_grounded_material_postgres.py \
  src/tests/studytomy/test_grounded_retrieval_postgres.py \
  -k 'not bedrock'
```

Current measured result is above. This covers offline model-setting boundaries and real PostgreSQL source retention/retrieval controls; it does not call a model or establish browser acceptance. For adaptive work, run the affected scoring/mastery/evidence suites separately. A known retained API fixture tries to decrease `evidence_watermark`; the database correctly rejects that. Do not disable its monotonic guard. A test-only alternative drift fixture was validated in isolation but is not committed in this revision.

## 6. Teacher-to-learner rehearsal and expected results

1. **Teacher preparation:** create a new unpublished text lesson in a synthetic course; add native chapter placement. Upload a tiny searchable, original PDF containing “Two quarters equal one half.” Retain actual receipt UUID, SHA-256, page and exact quote from the signed draft-source API. Expected upload 201; duplicate bytes reuse the receipt. Sibling lesson/other course source selection must return 404.
2. **Cited draft:** without a provider, save a hand-authored paragraph through the grounded-lesson draft path with the exact citation. Current citation status must be `current`. This is manual grounded authoring, not AI generation. Revoked sources must become unavailable/stale.
3. **Review and release:** use existing Content review and server preflight, distinct authorized reviewer, required synthetic rights/accessibility evidence and exact policy schedule. Record a new immutable release containing the PDF lesson, diagnostic and practice. Do not mutate old releases or publish without the review/authorization checks. Confirm frozen source lineage equals the receipt. Staff UI acceptance is a separate check, not inferred from fixture records.
4. **Learner entitlement:** grant that exact release to a fresh independent synthetic learner under the intended synthetic eligibility and consent. Open My learning and select the release reference, not just the course name. Unentitled/wrong-release requests must be private, no-store 404.
5. **Diagnostic:** open the mapped diagnostic and submit the scenario's known incorrect answer. For the historical numeric fixture, `0` produced 0/1. Verify server-owned grading and independent evidence, not a browser-computed grade.
6. **Practice:** open supported practice on the same release. The historical fixture accepted `19.25`; inspect current seeded item before using it. Expect completed assisted practice, not independent mastery credit.
7. **Progress:** open Concept progress; confirm exact learner/course/release binding, assisted observations distinct from independent mastery, and evidence watermark. Replay only existing idempotency keys to check duplicate protection. Withdrawal/revocation must clear access rather than leaking retained content.

Do not count these instructions as executed results. Capture revision, sanitized statuses, screenshot names, exact fixture version and observed results for every new rehearsal.

## 7. Optional Luna configuration, no secret copying

Default rehearsal is provider-free. Only after separate authorization for billed synthetic calls, supply the key to the **API process only** via an approved private secret file outside the checkout (owner-only) or no-echo prompt. Never put keys in `NEXT_PUBLIC_*`, Git, screenshots, shell command arguments or documentation. Do not read an existing key merely to repeat a test.

Required non-secret settings:

```sh
export LEARNHOUSE_IS_AI_ENABLED=true
export LEARNHOUSE_STUDYTOMY_GROUNDED_DRAFTS_ENABLED=true
export LEARNHOUSE_AI_PROVIDER=openai
export LEARNHOUSE_AI_BASE_URL=https://api.openai.com/v1
export LEARNHOUSE_AI_MODEL_STANDARD=gpt-6-luna
export LEARNHOUSE_AI_MODEL_PRO=gpt-6-luna
# LEARNHOUSE_AI_API_KEY is supplied privately to this process, never committed.
```

The current `client.generate` sets `openai_reasoning_effort=none` only for structured output on official OpenAI-hosted exact `gpt-6-luna`. Plain text, other models/providers and compatible gateways remain unchanged. Offline lesson and quiz schema cases pass. A historical single synthetic provider preview returned `teacher_review_required`; that is not saved, approved or published content. Rate/credit and source/citation controls remain required. Stop on failure and sanitize error type/status; do not dump provider bodies or credentials.

## 8. Guardian/report limits and cleanup

Dependent-child guardian report acceptance is **not implemented end-to-end** in the inspected report pathway. Independent learner-selected report recipients are a different feature. Do not label them guardians or invent consent. Current report lookup also requires the native course to be published; converting a retained test course to private managed delivery can yield an empty recipient list despite a valid sharing grant. This needs a separate supported scenario/integration review, not a bypass.

Stop only API/web processes started for this run, then stop the exact owned containers:

```sh
docker stop "$PG" "$REDIS"
```

Keep stopped disposable data only while actively investigating. Do not run broad Docker prune, shared database reset or process-name kill. Clear secret variables when finished. No production rollout, Git commit/push, external email, real-user activation, model call or backup certification follows automatically from this runbook.

**Next priority:** obtain qualified educator, safeguarding, rights and curriculum decisions before adapting the synthetic Momentum Lab to real teaching. Separately verify diagnostic/practice/progress and guardian/report journeys against their own approved scenarios; the lab fixture does not cover them.

## 9. Synthetic managed Virtual Lab browser journey

Use a fresh disposable database, native account manifest and local API, web and static-lab servers from sections 2–4. Keep the manifest, scenario JSON, browser state and Playwright artifacts in an owner-only directory outside the checkout. The scenario helper refuses a non-loopback API/lab origin, a database outside its `*_test` guard, a reused output path, or credentials/output with permissive file or directory modes.

The browser runner blocks non-loopback browser requests, but it does not control network access from the separate API process. Clear email-provider settings and block API outbound network access before a repeat; otherwise notification code may attempt external delivery during author/reviewer setup. Do not treat invalid provider credentials as an egress control.

```sh
DEMO_PRIVATE=/tmp/studytomy-demo-private.<unique>
mkdir -m 700 -p "$DEMO_PRIVATE/vlab-output"
chmod 700 "$DEMO_PRIVATE"

python3 scripts/local_virtual_lab_e2e_scenario.py \
  --api http://127.0.0.1:1342 \
  --lab http://127.0.0.1:4173 \
  --credentials "$DEMO_PRIVATE/accounts.json" \
  --output "$DEMO_PRIVATE/vlab-scenario.json" \
  --synthetic-only

export E2E_BASE_URL=http://localhost:3042
export E2E_API_URL=http://127.0.0.1:1342/api/v1
export E2E_LIVE_VLAB_SCENARIO="$DEMO_PRIVATE/vlab-scenario.json"
export E2E_LIVE_VLAB_OUTPUT_DIR="$DEMO_PRIVATE/vlab-output"
node apps/e2e/scripts/live-vlab-browser-state.mjs \
  --scenario "$E2E_LIVE_VLAB_SCENARIO" \
  --base "$E2E_BASE_URL" \
  --state "$DEMO_PRIVATE/learner-storage-state.json"
apps/e2e/node_modules/.bin/playwright test \
  --config apps/e2e/playwright.vlab-live.config.ts --project chromium
```

Set `LEARNHOUSE_SQL_CONNECTION_STRING` to the same disposable database already migrated and seeded by `scripts/local_demo_seed.py`. The browser-state helper performs normal login and aborts non-loopback requests. The live Playwright case also enforces loopback-only HTTP and WebSocket traffic in its desktop and mobile contexts, blocks service workers, and fails if an external browser request is attempted. Neither guard restricts network access from the separate API process. The dedicated Playwright config runs only `17-managed-virtual-lab-live.spec.ts`, uses one worker, and keeps artifacts outside the checkout. All four accounts and product records are synthetic. The fixture grants synthetic approvals only to reach the product workflow; it does not satisfy educator, safeguarding, content-rights, curriculum or real release approval. Do not enable an AI provider for this run.

The loopback egress guard has a standalone synthetic Chromium check: `cd apps/e2e && bun run test:browser-egress`. It verifies external HTTP and WebSocket attempts are intercepted and recorded without contacting those hosts. This does not verify or restrict API-process egress.

Verified on 21 November 2026: the full Chromium case passed against a named-volume PostgreSQL 19 service and real local FastAPI/Next.js/static-lab stack. It covered release authoring/review, first-use lab activity, CSV evidence, practical assignment upload/submission, teacher grading/feedback, negative authorization/deadline cases, mobile layout and consent withdrawal. PostgreSQL readback confirmed the final state. The full-run browser artifacts and scenario credentials remain under the private local temp directory; never commit or attach them. See [the completion record](studytomy/POSTGRESQL-19-UPGRADE.md) for exact bounds.

## Persistent-account vertical-slice attempt (19 November 2026)

Against `bc7b7002` with the local account seed, the real loopback API accepted a teacher-created private, unpublished course (HTTP 200). Teacher, separate reviewer and organization admin each received HTTP 403 `CAPABILITY_DENIED` from both managed-draft designation and content-review candidate lookup. The response requests the required Studytomy role capability from an authorized owner. This is expected default-deny behavior: native Instructor/Admin permissions do not confer `content_release_review` or the effective scoped owner/policy authority.

No role capability, owner, policy approval, release or learner grant was inserted to bypass that result. Source checks: `services/studytomy/publication.py:96–122` delegates designation through `_course_scope`; `services/studytomy/content_evidence.py:107–122` requires scoped `CONTENT_RELEASE_REVIEW`; `services/studytomy/grounding.py:292–311` requires a private managed unpublished course/lesson before source authoring. Consequently this freshly seeded scenario cannot yet reach the PDF→review→learner flow. It is not a failed model call, and it is not evidence that the historical fixture journey now passes with the native seed.

The independently passing current PostgreSQL PDF/retrieval tests remain separate evidence. The next necessary scenario setup is a supported, explicitly synthetic owner/capability/scope/policy onboarding path, followed by real review operations. Do not add broad Admin privileges or fabricate approval rows merely to complete the demo. No paid provider, publication, push or deployment occurred in this attempt.

### Supported authority onboarding follow-up

A fresh disposable `studytomy_demo_authority_test` database demonstrated that authority bootstrap is supported; no product fix or direct approval-row seed was needed. Four generated accounts authenticated normally. The teacher created a private draft course. The administrator then used only existing authenticated HTTP routes:

1. `POST /api/v1/roles/org/{org_id}`: clone native Instructor rights into a new organization role, set only `studytomy.content_release_review=true`, and use a synthetic-only name/description. **200**. Never modify a global role.
2. `PUT /api/v1/orgs/{org_id}/users/{reviewer_id}/role/{role_uuid}`: assign only the separate synthetic reviewer. **200**.
3. `POST /api/v1/studytomy/orgs/{org_uuid}/service-owners`, with a unique `Idempotency-Key`: reviewer `user_uuid`, `responsibility="education"`, explicit synthetic evidence reference, `effective_from=now`, `effective_to=now+2h`. **201**. This was a real service operation in the disposable scenario, not an inserted approval record.
4. `POST /api/v1/studytomy/orgs/{org_uuid}/service-owners/{owner_uuid}/scopes`, with a unique idempotency key: `capability="content_release_review"`, `scope_kind="course"`, exact new course UUID, `is_exclusion=false`, explicit synthetic evidence reference. **201**. No organization-wide review scope was granted.
5. Retry the review-candidate read with the reviewer's own session. It advanced from capability denial to **409 `POLICY_NOT_APPROVED`**. Teacher and learner remained **403 `CAPABILITY_DENIED`**. The separate maker did not gain checker privileges.

This confirms role + time-limited owner + exact course-scope onboarding works through the product. It does not establish approved content or learner entitlement. Required policy proposal/approval routes also exist (`POST .../policies`, `POST .../policies/{policy_uuid}/approvals`); use their actual typed contracts and responsibility owners rather than writing policy/approval rows directly. A complete explicitly synthetic policy scenario with valid decision bodies is the next prerequisite, not a reason to weaken the checks. The content-evidence layer additionally requires D-02, D-10, D-12, D-13, D-15 and D-16. No policy approvals, content release or real-user publication were created in this follow-up.

### Real API synthetic policy → PDF → review → release milestone

A later bounded rehearsal used the existing persistent synthetic identities on `studytomy_demo_authority_test`, with no direct policy/approval rows:

- Administrator proposed D-02/D-10/D-12/D-13/D-15/D-16 via `POST .../policies` (201 each). The distinct synthetic reviewer, assigned each required time-limited owner responsibility through `POST .../service-owners`, approved each via `POST .../policies/{policy_uuid}/approvals` (200). Every evidence/rationale explicitly said disposable synthetic testing, not production policy. Unused decision areas were explicitly marked not applicable only for this no-real-participant fixture; this is not a reusable production decision template.
- D-13 specified synthetic content/release retention categories; D-15 allowed only PDF evidence with a 4096-byte limit. No broad storage/provider permissions were enabled.
- Reviewer managed-draft designation returned 204 and candidate read 200.
- Teacher created a chapter and unpublished text activity through native APIs, uploaded the original fractions PDF (201), saved a manually authored exact cited paragraph (200), and read citation status `current` (200). Learner source retrieval was denied (403). No model output was generated or simulated.
- Distinct reviewer uploaded two original, explicitly synthetic rights/accessibility evidence PDFs (201 each) against the exact candidate digest, then recorded review through `POST .../content-review-decisions` (201). Teacher was not the checker. This is synthetic scenario acceptance, not professional educational/accessibility certification.
- Release preflight correctly returned 409 for missing D-08. Added D-08 (no assessments in this text-only fixture) and D-09 (exact course/activity ungated text schedule) through the same proposal/owner-approval APIs. Preflight then returned 200; recording the local immutable release returned 201. A repeat returned the same receipt.

**Not yet covered by this new persistent scenario:** learner eligibility/consent/exact-release grant, a published diagnostic blueprint, assessed practice, mastery/progress, guardian report, browser interaction or AI generation. The text-only release milestone must not be described as the full learning vertical slice. Adding those stages requires explicit synthetic contracts and real product operations, not repurposing historical test leftovers or automatically approving production policy. No real-user access, Git publication or deployment occurred.

### Supported learner onboarding leg

Continuing the same retained disposable scenario through real APIs:

- Admin proposed expiring, explicitly synthetic adult-only D-01/D-03 contracts; separate required owner approvals succeeded.
- Admin added `learner_eligibility_review` only to the existing synthetic organization reviewer role, then granted `function/learner_intake` and the exact newly created learner scope under the existing two-hour family-services owner assignment.
- `POST .../learners` returned **201**, creating a pending profile for the synthetic learner. `POST .../learners/{learner_uuid}/eligibility-decisions` with `eligibility_kind=learning` returned **201**.
- Independent-participation decision returned **409 `POLICY_DECISION_UNRESOLVED`**: the earlier text-only D-12 fixture deliberately marked consent/information/report areas not applicable. Adult independent participation correctly requires explicit D-01, D-03 and D-12 choices. No control-mode field was written directly.
- Learner's actual `GET .../learners/me/consent-notices/learning_terms` returned **409 `POLICY_NOT_APPROVED`**, identifying missing D-05. A reviewed text release alone does not provide consent or entitlement.

No grant, diagnostic response, practice or progress pass is claimed for this new scenario. Next, author a coherent **synthetic-only** D-12 notice/information/report policy version and the other consent-required contracts using the existing approval/supersession APIs, then use the supported enrollment/allocation route. Do not manufacture a grant or reuse a test's retained profile to skip this flow. Preserve the earlier immutable release and policy history; re-review where policy-binding rules require it.

### Supported adult consent continuation (2026-11-19, current local bc7b7002)

On the same disposable `studytomy_demo_authority_test` scenario, actual authenticated
API calls approved D-05/D-18 and superseded D-12 with an explicit synthetic adult
information-use/notice contract. Admin proposed; the distinct scoped reviewer
approved through the existing owner endpoints. These are sandbox choices, not
production business/legal decisions. D-12 uses the required nonempty scope-code
list (`progress_summary`); this does not create a recipient or sharing grant.
An initial object-shaped scope was correctly rejected with `POLICY_SCOPE_INVALID`
and corrected by a new immutable policy supersession, not a database overwrite.

- Independent-participation eligibility now returns **201**.
- Read-only database verification shows **pending / independent / access_version 3**.
- The actual learner notice request still returns **403 CONSENT_ACTOR_NOT_AUTHORIZED**.
- Actual checkout learner choices return **200 with zero choices**.
- Actual learner access to the private native course remains **403**.
- Reviewer profile-read is separately denied **403 CAPABILITY_DENIED** because its
  eligibility-review capability is not the distinct profile-read capability.

Concrete remaining onboarding gap: `services/studytomy/learners.py:472` creates
`pending`; its eligibility handler (`:677–683`) updates control mode but not status.
The inspected family router exposes creation/read/eligibility operations, not an
activation operation. `services/studytomy/consents.py:168` and
`services/studytomy/orders.py:136` require an **active** profile. No supported status
transition was found in the Studytomy service/router search. Do not set status
manually to make the demo pass. A designed, authorized, audited activation path is
needed before this API-only newly onboarded learner can consent/enroll.

Consequently this run did **not** grant consent, allocate a course, start a
new diagnostic, run practice, or establish learner progress. Earlier isolated
fixture-based learning tests are not evidence that this onboarding gap is solved.
No paid model calls, real people, production publication, commits, or deployments
were used. Sanitized local evidence: `api-activation-check-result.json` beside the
private isolated rehearsal workspace; it contains no account credentials.

### Activation design review: explicit credential decision remains unresolved

Before implementing the missing transition, read
[`ADR-LEARNER-CREDENTIALS.md`](studytomy/ADR-LEARNER-CREDENTIALS.md).
It is explicitly **proposed, not approved**, with no selected credential option,
permitted cohort, named approval/evidence or effective/review dates. The source
specification ([`TASK-010`](../STUDYTOMY-FAMILY-LEARNING-ATOMIC-IMPLEMENTATION-SPEC.md#task-010),
blocked on the unapproved `D-01`/`D-03`/`D-12` credential decisions; the file was
condensed to a checklist on 2026-11-20, so the `:276,305` line numbers cited by this
2026-11-19 run no longer resolve)
requires an approved credential model before profile activation. Existing
`learner_eligibility_review` permits scoped eligibility decisions; it does not
itself define an activation decision or approved credential-model binding.

Therefore the observed pending state is not safely resolved by merely adding
`profile.status = active` to eligibility. The engineering gap and unresolved
activation contract must be addressed together. Next design input is an explicit
approved **independent-adult, existing-per-user-login** branch (if selected), with
scope/evidence and the authorized activation actor. Implementation can then
validate exact current learning/independent eligibility and credential decision,
lock the organization/profile, accept only pending→active, preserve idempotency
and audit evidence, and reject dependent/expired/wrong-org/self-escalating paths.
It must not grant course access, consent, purchase eligibility or guardian rights.
Synthetic test approvals above do not resolve the production credential ADR.
No activation endpoint or state-setting workaround was added during this review.

### Retained authoring/release regression command

The local `scripts/local_release_regression.py` reuses the prior **API-created**
synthetic teacher PDF/cited draft/distinct reviewer/immutable release state. It
never seeds policy or review approvals and does not invoke a model provider.
Start only the existing disposable authority-test API described above, then:

```sh
apps/api/.venv/bin/python scripts/local_release_regression.py \
  --api http://127.0.0.1:1343 \
  --credentials /tmp/studytomy-t24.JyJN7b/authority-demo-credentials.json \
  --state /tmp/studytomy-t24.JyJN7b/api-released-state.json \
  --synthetic-only
```

These private files must already exist from the supported API rehearsal; the
command does not reconstruct or forge missing state. Credentials stay outside
Git. Only loopback HTTP and synthetic `example.com` accounts are accepted.

Verified at `bc7b7002` plus this untracked local harness:
- Teacher current cited-source read **200**.
- Learner source read **403** and release attempt **403**.
- Wrong-course/lesson route **404** (a nonexistent mismatched course, not a second
  real populated course; do not overstate this as full cross-course coverage).
- Reviewer repeated release **201, 201**, both exactly matching the prior
  immutable receipt, including UUID and revision hash.

This is **retained-state regression**, not a fresh complete authoring seed.
Original PDF creation, review-evidence upload, separate reviewer approval and
release creation were verified in preceding API legs. A complete one-command
fresh scenario orchestrator is still pending. Stop owned runtime/containers after
use; retain their disposable data if another bounded rehearsal is planned.

### Fresh API-only authoring scenario (verified 2026-11-19)

`scripts/local_authoring_scenario.py` now extracts the earlier staged workflow
into a single fresh-organization API run. Use a new explicitly disposable
`studytomy_demo_<name>_test` database, migrate it, run `local_demo_seed.py`, and
start the isolated API against that **same** database with AI disabled and the
existing local grounded-drafts flag enabled. Use private, fresh manifest/output
paths outside the checkout; do not point the API at shared data.

```sh
# After sourcing the private local runtime configuration and choosing a new DB:
export LEARNHOUSE_SQL_CONNECTION_STRING="${LEARNHOUSE_SQL_CONNECTION_STRING%/*}/studytomy_demo_authoring_fresh_test"
# Create the new empty DB on the owned Docker PostgreSQL (never drop/reuse shared DBs).
cd apps/api
.venv/bin/python cli.py migrate
.venv/bin/python ../../scripts/local_demo_seed.py \
  --credentials /private/demo-authoring-accounts.json
# Start the loopback API with this exact DB, isolated media working directory,
# LEARNHOUSE_IS_AI_ENABLED=false and LEARNHOUSE_STUDYTOMY_GROUNDED_DRAFTS_ENABLED=true.
cd ../..
apps/api/.venv/bin/python scripts/local_authoring_scenario.py \
  --api http://127.0.0.1:1343 --synthetic-only \
  --credentials /private/demo-authoring-accounts.json \
  --output /private/demo-authoring-state.json
```

Replace `/private/...` with a private directory you own. The harness checks the
manifest database against the explicit loopback DB URL, synthetic organization
and account names, fresh absence of prior policies/owners, and no output overwrite.
It is not a production setup command. Admin proposes test policies; the distinct
synthetic reviewer approves through actual scoped owner APIs. No approval rows
are inserted directly. The original one-sentence PDF is generated locally.

Actual fresh run at `bc7b7002`: CLI migration empty→`9b7d4c2a1e6f`, four native
accounts, two real private courses, teacher PDF upload/manual cited draft/current
source verification, learner-source denial, mismatched **existing second-course**
source denial, reviewer denial outside its exact course scope, review evidence
upload, distinct review, preflight, immutable release and identical release retry
all passed. Unauthorized release also denied. Two PASS lines identify these groups.
The later manifest/database and organization-name preflight hardening was inspected
but not rerun against a second fresh database in this slice.

This supersedes the previous limitation that no fresh scenario orchestrator exists.
It does **not** activate learners, create enrollment, exercise adaptive learning,
claim AI-generated content, or establish any real commercial/education policy.
No live provider credentials were loaded. Owned services were stopped after the run.

Final-byte confirmation: a second fresh `studytomy_demo_authoring_final_test`
run passed the complete authoring scenario after all manifest/database and
organization-name guards were added. Both PASS groups above succeeded again.
Script SHA-256: `1cf5f000560292be96a8680aa9cb6d5ea77e6ff816a189d4221d808be6eb8f84`.
Three lightweight negative checks also passed: nonloopback API origin,
manifest/database mismatch, and existing output path each exited with status 2
before performing the workflow. No negative-test output file was created.
This supersedes the preceding unrerun-preflight caveat. Owned API and Docker
containers were stopped again; the two disposable evidence databases are retained.

### Independent MCP loopback HTTP verification

An independent local run at `bc7b7002` verified native account login, OAuth client
registration, PKCE authorization with consent, tool-call approval, and MCP draft
lesson creation over loopback HTTP. PostgreSQL readback confirmed the lesson was
**unpublished** and exactly one creation audit event persisted. This used synthetic
identities and disposable PostgreSQL/Redis, not production or a model provider.

Scope: an isolated router/Uvicorn harness exercising actual HTTP. It did **not**
verify browser consent-page rendering, an external desktop MCP application, or
complete application startup. Do not treat transient `/tmp` evidence as a durable
one-command reproducer. Relevant supported source paths:

- `apps/api/src/routers/mcp_oauth.py`
- `apps/api/src/services/mcp/oauth.py` and `approvals.py`
- `apps/api/src/services/mcp/handlers.py` and `draft_authoring.py`

Durable regression references are
`apps/api/src/tests/routers/test_mcp_oauth_router.py`,
`apps/api/src/tests/services/test_mcp_oauth.py`,
`apps/api/src/tests/services/test_mcp_approvals.py`, and
`apps/api/src/tests/studytomy/test_mcp_draft_postgres.py`
(`test_native_mcp_drafts_are_private_scoped_and_retryable`). These cover distinct
layers and are not themselves a claim that the entire loopback HTTP harness is
saved in the repository. MCP authoring remains draft-only and does not replace
the teacher/reviewer publication workflow or learner eligibility/consent gates.

### Authenticated MCP browser continuation (same `bc7b7002`)

The later independent continuation supersedes the rendered-browser limitation
above: genuine isolated local FastAPI/Next development runtimes plus Chromium
completed native login, the OAuth **Allow** screen, PKCE callback/exchange, and
both actual tool-approval buttons for draft-course and draft-lesson creation.
Independent persistence verification confirmed the resulting lesson remained
**unpublished**, with its corresponding audit event. No external AI was invoked.

A preceding attempt encountered a stale organization slug after changing the
synthetic PostgreSQL fixture. A fresh dedicated Redis instance isolated the new
scenario; shared Redis was not flushed. The corrected test used genuine session
and approval flows, not injected authentication or mocked consent controls.

Relevant frontend references: `apps/web/app/oauth/consent/consent.tsx`,
`apps/web/app/oauth/approve/approve.tsx`, and
`apps/web/services/mcp/mcp.ts`; backend boundaries are the OAuth/MCP files listed
above. This is a **local development browser** result, not an external desktop
MCP client integration, production build/deployment, learner activation, or content
publication. Temporary independent evidence is not a durable repository test
command. No application code was changed for this documentation update.

### Adaptive Playground browser slice (20 November 2026)

Tested the dirty local candidate at base HEAD `a2a637142d5aff02ba588259be4624e0f3f70ea6`.
The tracked application diff SHA-256 was
`dad60c07f70efc6eee1d5be47abc5ef51077857e1d64c846108a6e9f826013b7`;
the two untracked source files included in the candidate were
`adaptive_widget_contract.py` SHA-256
`26b0d69f4d8094d48943ea5c69826f798a6ac90e9e6d086f279edb2da634453c` and
`SafePlaygroundFrame.tsx` SHA-256
`68f2c54adb63d4a35e4163a82f73f3725bdddd162c375e36af1e7176c1c14ea3`.
The tested candidate was assembled from that base plus the dirty tracked diff
and those untracked source files; it was not a clean-archive-only test. The
video artifact in the worktree was not part of this test candidate. This
documentation entry was added after the browser run and is not part of the
tested diff.

This evidence comes from separate local runs:

- **Teacher browser run:** the synthetic teacher signed in through the normal
  form, opened the Playground editor, previewed the Foundation variant, saved
  and read back the adaptation settings, then published through the editor's
  native publish control. The page and API readback both confirmed the saved
  state. Playground creation/content setup was synthetic fixture preparation;
  teacher authentication and the create/save/publish requests returned 200.
  This was not a real teacher account or external provider call.
- **Learner route-handoff run:** a fresh disposable database was migrated with
  the supported CLI and populated with `scripts/local_demo_seed.py`. A synthetic
  authenticated Playground was prepared through the teacher API. The learner
  then signed in through the normal browser form. `/api/v1/auth/me` identified
  `demo-learner`, and the authenticated Playground API read returned 200. The
  supported user-facing URL `/playground/{uuid}` returned 200, displayed the
  synthetic Playground and both selectors, and switching level and focus skill
  changed the displayed variants. The browser runner reported
  `authStatus: 200`, `apiPlaygroundStatus: 200`,
  `learnerPageStatus: 200`, `adaptationSelectors: 2`,
  `adaptationWorked: true`, and `pageErrorCount: 0`. The API root and web health
  checks each returned 200. API logs showed the server-side session and
  authorized Playground read succeeding. An anonymous metadata lookup returned
  401 before the authorized read; this did not change the learner access
  decision.
- **Harness correction:** opening the internal Next.js route
  `/orgs/default/playground/{uuid}` directly returned 404 in the browser. In
  single-tenant mode, `apps/web/proxy.ts` resolves the default organization and
  rewrites user-facing paths into `/orgs/{slug}/...`; that internal route is not
  the public browser entry point. Use `/playground/{uuid}` (the path generated by
  `apps/web/components/Playground/PlaygroundCard.tsx`) or follow the card in the
  application. The direct-internal-route 404 was a harness path error, not
  evidence of a Playground rendering or authorization defect.
- **Component tests:** no standalone component-test command was run for this
  browser slice. `apps/web/tests/playground-learner-adaptation.test.mjs` and
  `apps/api/src/tests/services/test_playgrounds_generator_service.py` are
  durable regression-test references, not results from this run.

To repeat the local browser slice, follow sections 2–4 above with a fresh
loopback database, AI disabled, and the four-account synthetic seed. Prepare
an authenticated Playground whose HTML satisfies the contract in
`apps/api/src/services/playgrounds/adaptive_widget_contract.py`; sign in as
the synthetic teacher, use the editor preview/save/publish controls, then sign
out and sign in as the synthetic learner. Open `/playground/{uuid}` or its
Playground card and change both selectors. Do not enter through
`/orgs/default/playground/{uuid}`. The account seed does not create this
Playground fixture, so its creation is a separate supported API setup step via
`POST /api/v1/playgrounds/?org_id={synthetic_org_id}` with the teacher's normal
authenticated session; save through the editor's native controls, which use
`PUT /api/v1/playgrounds/{uuid}`. Keep `access_type` authenticated, content
synthetic, and use the native authorization and publication checks. The
relevant route implementation is `apps/api/src/routers/playgrounds/playgrounds.py`.

Scope and limits: this was local-only Chromium verification against the dirty
candidate, using synthetic accounts/content and disposable loopback PostgreSQL
and Redis. AI was disabled and the browser runner aborted non-loopback requests;
no external provider call, real account/data, production release, or production
acceptance was involved. The reviewer path was not exercised in this ungoverned
synthetic organization; this run does not qualify review policy or governed
content publication. Only resources created for these checks were removed.

### Governed Playground reviewer browser slice (20 November 2026)

Verified against a clean source archive of commit
`8961a925423354fa7fd32d73f9256a1ebe364823`, using real local Chromium, a
native reviewer login, and disposable loopback PostgreSQL and Redis. A
separate synthetic teacher created and edited the Playground through
authenticated API requests; teacher editing was not a browser assertion.

**Synthetic setup through supported product APIs**

1. Migrate and provision fresh native test accounts as in sections 2–4.
   Sign in normally. Use only the generated identities in this fixture.
2. As the teacher, create a private synthetic course. As the administrator,
   create an organization-local Instructor-derived role with
   `studytomy.content_release_review=true` via `POST /api/v1/roles/org/{org_id}`
   and assign it only to the distinct reviewer with
   `PUT /api/v1/orgs/{org_id}/users/{reviewer_id}/role/{role_uuid}`.
   Under `/api/v1/studytomy/orgs/{org_uuid}`, create the time-limited Education
   service-owner assignment and add a `content_release_review` scope with
   `scope_kind=course` and the exact synthetic course UUID. Do not grant
   organization-wide review scope.
3. Use the native `POST .../policies` and
   `POST .../policies/{policy_uuid}/approvals` operations and their typed
   decision areas, effective periods and idempotency keys. The synthetic
   administrator proposes; the fixture's Education owner approves
   D-02 and D-16, Education and Service approve D-15, and Education and
   Safeguarding approve D-10. For D-10 the Safeguarding owner was a different
   synthetic account, created and signed in through native user APIs and
   assigned a short-lived responsibility without a resource scope. Use
   expressly synthetic evidence and rationales and do not copy those test
   decisions into a real organization.
4. As the teacher, create an authenticated Playground linked to that exact
   course with `POST /api/v1/playgrounds/?org_id={org_id}`. As the reviewer,
   read `GET .../playgrounds/{uuid}/content-review-status`; require HTTP 200
   with `governed=true` and `can_review=true`. Record the initial rejection for
   the returned digest through `POST .../playgrounds/{uuid}/content-review-decisions`
   using the separate reviewer's session. The observed response was HTTP 201.

The initial attempt had approved only D-15 and D-16. Teacher readback was 200
with a governed draft; reviewer login, organization role, and exact course
scope were confirmed, but the reviewer draft and review-status reads returned
404. The shared [staff authorization check](../apps/api/src/security/studytomy_access.py)
also requires D-02 and D-10 for `content_release_review`. With those two
missing fixture policies independently approved through the native workflow,
the same reviewer could read the draft and record the rejection. The initial
404 was not evidence of a rendering defect or a reason to widen reviewer access.

**Observed in the signed-in reviewer browser**

- The rejected revision showed **Review rejected**. After the teacher changed
  the saved revision, the reviewer saw **Review required**, not a rejection of
  the new digest.
- **Approve revision** was disabled with none and with only two of the
  rights, accessibility, and age-suitability confirmations checked. It became
  available with all three.
- The teacher changed the revision again while the reviewer held the previous
  digest. Native submission produced the stale-review conflict, cleared all
  three confirmations, and disabled approval. **Reload revision** did not
  carry assent forward; fresh confirmations were still required.

The [review routes](../apps/api/src/routers/studytomy/files.py) and
[editor](../apps/web/components/Playground/PlaygroundEditor.tsx) were unchanged
for this check. No learner journey, real account or data, provider generation,
successful publication, production policy, or deployment was tested. The
owned API, Next.js, PostgreSQL and Redis services were stopped.

### Managed Virtual Lab full-stack acceptance on PostgreSQL 19 (21 November 2026)

The single-use live scenario at `apps/e2e/features/studytomy/tests/17-managed-virtual-lab-live.spec.ts` passed in real Chromium against local FastAPI, Next.js, the curated static lab, PostgreSQL 19 and pgvector 0.8.7: **1 passed, 0 skipped**. The one-run synthetic scenario was prepared by `scripts/local_virtual_lab_e2e_scenario.py` from a fresh restored test database; it created separate author/reviewer identities, synthetic policy decisions and release evidence, learner consent, and a free first-use grant through normal APIs. Educator, safeguarding, legal-rights and curriculum approvals remain pending.

The browser completed the initial lab open, mass adjustment and computed momentum check, CSV export, linked practical assignment, offline draft retry, ZIP upload and submit, teacher file review and grading, and learner feedback view at desktop and mobile widths. It also checked the iframe sandbox, raw CSV and grade-injection rejection, duplicate submission, locked post-submit upload, wrong-organization and stranger access, release mismatch, expired deadline, consent withdrawal, mobile overflow, and zero browser egress violations. The relevant APIs were real; the full suite did not mock critical business operations.

For a repeat, use a **fresh disposable database**: the journey starts first-use access and withdraws the learner's consent, so an earlier scenario is not reusable for the same initial-state assertions. Keep its scenario JSON, credentials, browser state and Playwright output outside the checkout in owner-only paths. Run the app and browser inside a loopback-only OS sandbox, leave AI-provider credentials unset, and direct any required email to a local sink. A background RAG job may log a missing Gemini key and stop before any provider request. The lab's existing CSP allows the local frontend origin `http://localhost:3000`; run the browser there rather than weakening `frame-ancestors`. The opaque iframe and its `allow-scripts allow-downloads` sandbox remain in force.

The run used the dedicated Playwright config `apps/e2e/playwright.vlab-live.config.ts`; from `apps/e2e`, invoke `bun node_modules/@playwright/test/cli.js test --config=playwright.vlab-live.config.ts` with `E2E_BASE_URL=http://localhost:3000`, the real loopback `E2E_API_URL`, and owner-only `E2E_LIVE_VLAB_SCENARIO` / `E2E_LIVE_VLAB_OUTPUT_DIR`. The normal learner browser state was generated with `scripts/live-vlab-browser-state.mjs`. `bun run test:browser-egress` passed 1 test, `bun run typecheck` passed, and the Web single-tenancy proxy tests passed 8 cases. The `apps/web` typecheck passed; focused ESLint found no errors and one pre-existing warning at unchanged `proxy.ts:128`.

This is local synthetic acceptance only. It does not approve the lab's scientific model, Grade 12 syllabus mapping, educator-facing rubric, learner safeguarding policy or content rights, and it is not hosted or production acceptance.

**Migrated-data follow-up (21 November 2026):** the same full Chromium journey also passed once after restoring the synthetic author/reviewer/release/consent/grant/order/assignment scenario from a separate PG16.16 source into a dedicated PG19.0 volume. The import matched all 137 public-table row fingerprints before the browser run, and the source PG16 snapshot remained unchanged. The original two evidence PDFs were copied with matching SHA-256 values into the PG19 filesystem-content directory. This confirms the local pilot-shaped migration path; guardian relationships, settled payments, populated outbox history, object storage and production rollback remain unverified. See the [PostgreSQL 19 migration record](studytomy/POSTGRESQL-19-UPGRADE.md) for image digests, archive hash, schema counts, egress limits and exact acceptance bounds.
