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
