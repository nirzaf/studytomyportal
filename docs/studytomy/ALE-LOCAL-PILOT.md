# Adaptive Learning Engine local pilot runbook

This runbook is for synthetic local verification. The adaptive pilot remains closed to real learners pending the approvals and acceptance evidence below. The ALE source snapshot is `4bff1a6a4124f93ed516f90378c65f48c1bd2741`. The implementation checkout was inspected at `43f314540404c1c1478744e6bf7a53802d1fb430` with ALE and other project work in progress. See [implementation evidence](ALE-IMPLEMENTATION.md) for the initial inventory, current code references and individual test outcomes.

## Reproduce the API checks

Use PowerShell from `apps/api` after installing the locked dependencies. The test fixture requires an explicit loopback PostgreSQL URL and a disposable database name ending in `_test`. Choose a fresh container name and preserve any existing database.

```powershell
$name = 'studytomy-ale-runbook-pg'
$db = 'studytomy_ale_runbook_test'
docker run -d --rm --name $name --label studytomy.purpose=ale-runbook-tests -e POSTGRES_HOST_AUTH_METHOD=trust -e "POSTGRES_DB=$db" -p 127.0.0.1::5432 pgvector/pgvector:0.8.7-pg19@sha256:89879f41d564f67b0c1462a491d16fcbc6de39ac10db17e7a4bba2138a5e0ba5
if ($LASTEXITCODE) { throw 'Disposable PostgreSQL did not start' }
try {
    $port = docker port $name 5432/tcp
    if ($port -notmatch '^127\.0\.0\.1:\d+$') { throw 'Unsafe test database binding' }
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        docker exec $name pg_isready -q -h 127.0.0.1 -U postgres -d $db
        if ($LASTEXITCODE -eq 0) { break }
        Start-Sleep -Seconds 1
    }
    if ($LASTEXITCODE) { throw 'Disposable PostgreSQL did not become ready' }
    $env:STUDYTOMY_TEST_DATABASE_URL = "postgresql+asyncpg://postgres@${port}/$db"
    uv run --no-sync python -c "import os; from src.core.migrations import ensure_schema; ensure_schema(os.environ['STUDYTOMY_TEST_DATABASE_URL'].replace('+asyncpg', ''))"
    if ($LASTEXITCODE) { throw 'Disposable schema bootstrap failed' }
    uv run --no-sync pytest -q src/tests/studytomy src/tests/services/test_assignment_grading.py src/tests/services/test_assignment_formative_mode.py src/tests/services/test_assignment_manual_grading.py src/tests/services/test_assignment_api_token_access.py src/tests/services/test_assignment_submission_ownership.py src/tests/services/test_ai_authz_and_clamp.py src/tests/security/test_assignment_content_gates.py src/tests/security/test_require_authenticated_non_api_token.py src/tests/routers/test_auth_logout_revocation.py src/tests/routers/test_assignments_retry.py --tb=short
    if ($LASTEXITCODE) { throw 'Focused tests failed' }
} finally {
    Remove-Item Env:STUDYTOMY_TEST_DATABASE_URL -ErrorAction SilentlyContinue
    if ((docker inspect --format '{{index .Config.Labels "studytomy.purpose"}}' $name) -eq 'ale-runbook-tests') {
        docker stop $name
    }
}
```

The suite includes a signed fixture that creates approved synthetic definitions and policies, supplies separate learner, reviewer and recipient credentials, and drives diagnostic attempts, mastery, tutor recovery and reviewed report publication. It also checks denial, expiry, rollback and independent-session races. To run only PostgreSQL-marked cases against a fresh migrated fixture, use `uv run --no-sync pytest -q src/tests/studytomy -m studytomy_postgres --tb=short` before removing its container.

## Browser checks

Follow [local-only operation](../../LOCAL-ONLY.md). Start a fresh PostgreSQL container as above with database `studytomy_ale_browser_test` and label `studytomy.purpose=ale-browser-tests`. Start Redis separately.

```powershell
docker run -d --rm --name studytomy-ale-browser-redis --label studytomy.purpose=ale-browser-tests -p 127.0.0.1::6379 redis:7-alpine
```

In the API shell, set `$pgPort` and `$redisPort` to the outputs of `docker port` for the two containers. Set `LEARNHOUSE_INITIAL_ADMIN_PASSWORD` and `E2E_ADMIN_PASSWORD` in their respective shells to the same newly generated fixture password. Use a separate copy of the browser harness to keep the generated auth fixtures out of the regular working directory.

```powershell
# Run from apps/api.
if ($pgPort -notmatch '^127\.0\.0\.1:\d+$' -or $redisPort -notmatch '^127\.0\.0\.1:\d+$') { throw 'Unsafe browser fixture binding' }
$env:LEARNHOUSE_SQL_CONNECTION_STRING = "postgresql://postgres@${pgPort}/studytomy_ale_browser_test"
$env:LEARNHOUSE_REDIS_CONNECTION_STRING = "redis://$redisPort"
$env:LEARNHOUSE_AUTH_JWT_SECRET_KEY = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
$env:LEARNHOUSE_INITIAL_ADMIN_EMAIL = 'admin@school.dev'
$env:LEARNHOUSE_DOMAIN = 'localhost:8421'
$env:LEARNHOUSE_FRONTEND_DOMAIN = 'localhost:3421'
$env:LEARNHOUSE_DEVELOPMENT_MODE = 'true'
uv run --no-sync uvicorn app:app --host 127.0.0.1 --port 8421
```

```powershell
# Run from apps/web in a separate shell. Use an isolated copy if Next dev already owns this directory.
$env:LEARNHOUSE_DISABLE_EE = '1'
$env:NEXT_PUBLIC_LEARNHOUSE_DOMAIN = 'localhost:3421'
$env:NEXT_PUBLIC_LEARNHOUSE_TOP_DOMAIN = 'localhost'
$env:NEXT_PUBLIC_LEARNHOUSE_PLATFORM_URL = 'http://localhost:3421'
$env:NEXT_PUBLIC_LEARNHOUSE_BACKEND_URL = 'http://localhost:8421/'
$env:NEXT_PUBLIC_LEARNHOUSE_API_URL = 'http://localhost:8421/api/v1/'
$env:NEXT_PUBLIC_LEARNHOUSE_DEFAULT_ORG = 'default'
bun x next dev --webpack --hostname localhost --port 3421
```

Confirm the API health and login page respond. The global setup creates synthetic accounts in the portal. Stop the two local servers and the two labeled disposable containers after the run.

```powershell
# Run from apps/e2e, using the bound ports of the isolated portal.
$env:E2E_BASE_URL = 'http://localhost:3421'
$env:E2E_API_URL = 'http://localhost:8421/api/v1'
bun run typecheck
bun run test features/studytomy/tests/03-lesson-practice.spec.ts features/studytomy/tests/04-adaptive-attempt.spec.ts features/studytomy/tests/05-account-reports.spec.ts features/studytomy/tests/06-staff-reports.spec.ts --project=chromium --reporter=list
```

The browser specs use the real local lesson, dashboard and sign-in routes with intercepted adaptive API responses. The account report spec includes logout and sign-in as a second synthetic account while a report response is held in another tab. The signed API fixture supplies separate server integration evidence. A complete browser journey through the real adaptive endpoints and dependent guardian authority remains unverified.

## Migration and recovery

`uv run --no-sync alembic history -r 9b2c7e4a16d3:head` reports the current graph. The following ALE/publication revisions are a recorded historical checkpoint, not the entire current graph: `472f0b3ca571 → 5e071b3aaf62 → 19c0a82d4fe6 → 7bfc5ca4ad19 → d6b2beaf0c43 → 37e0adb59ca1 → 950ec32a74bf → ef0b241a1c57 → 8617afa045b0 → 91948e1f3a2d → b2460ac8149e → 448cf67b5309 → dfa43c51b792 → dc48e62a7b91 → 8a91ce74f623 → 627b1fd4a981 → 08fd9332b6ac → 7be224c591a6 → b043fde62c98`.

Use the organization configuration value `admin_toggles.studytomy_adaptive_enabled` to keep processing off. Missing, malformed and false values disable adaptive actions. The flag alone does not grant eligibility, consent, staff scope, or recipient authority. Verify signed availability, protected read and write denial, and stopped report delivery for the affected organization after a disable.

For an unconfirmed answer or tutor operation, recover the current attempt or receipt using its existing operation reference before retrying. For an ambiguous provider or email result, preserve the reconciliation state and review the restricted operation record. For stale report evidence, open the staff view and create a replacement draft or withdraw the publication. Confirm authorization before any replacement disclosure.

For a local application rollback, first disable adaptive processing, preserve the database and rehearse against a disposable copy. Normal startup refuses an unknown migration head. The existing compatibility override is an explicit operator decision for a verified additive schema. It must not substitute for the applicable approval or a rollback rehearsal. The report downgrade steps refuse to remove populated correction or review-request records. Preserve the authoritative journal and report history.

## Release evidence

At the recorded checkpoint, the API regression command above passed 494 tests with zero failures or skips on the inspected worktree. PostgreSQL-marked cases used the disposable PostgreSQL 16 database at `dfa43c51b792`. Other cases used their regular SQLite or mock fixtures. The per-check evidence log records earlier browser and migration rehearsals.

| Capability | Current status |
|---|---|
| Diagnostic, scoring and mastery | Implemented locally for synthetic independent learner fixtures. Named curriculum, rubric and interpretation approval remains unverified. |
| Lesson tutor | Implemented for local guided and fallback cases. Approved live provider configuration, transfers and educational evaluation remain unverified. |
| Staff reporting and independent sharing | Implemented locally with signed API and browser fixture coverage. Dependent guardian journeys and an uninterrupted real-endpoint browser run remain unverified. |
| Report notifications and retention | Link-only simulated delivery and ordinary deletion guards are verified locally. Approved live notification and retention handling, including holds and dependent-store removal, remain pending. |

Before any real cohort activation, verify named approvals against current recorded policy bodies, curriculum rights, item-family grouping, rubrics, diagnostic caps, mastery/report wording, exact cohort and purpose authority, consent, provider configuration, and approved retention and notification scope. D-01, D-02, D-09, D-10, D-12 and D-13 apply to educational and guided-output acceptance as scoped. Report routes also enforce applicable family, access, commercial and customer-term policies. Record the exact approved versions and acceptance results. Synthetic policy and educator fixtures confer no authority to activate real-user flows.
