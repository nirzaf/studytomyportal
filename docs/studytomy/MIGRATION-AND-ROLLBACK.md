# StudyTOMY migration and rollback notes

**Status:** local engineering guidance; not production approval.

Business decisions D-13 and D-15 remain unapproved. Do not apply these migrations
to production, backfill learner or family records, or activate StudyTOMY flows
until the applicable decisions and release gates are approved.

## Database paths

- **Fresh database:** use the application's `ensure_schema()` path. It creates
  the current model schema and stamps the current Alembic head. The historical
  Alembic chain starts from an existing schema; running `alembic upgrade head`
  against an empty database is not a supported clean-install path.
- **Existing database:** use the normal application migration path against a
  verified backup and the inspected Alembic revision. It runs under the
  PostgreSQL advisory lock and fails startup if the resulting head is unexpected.
- **StudyTOMY migration rehearsal:** restore or create an isolated test database
  from the actual parent revision, run `ensure_schema()`, verify the head and
  schema, then run the opt-in PostgreSQL tests. Never point
  `STUDYTOMY_TEST_DATABASE_URL` at a runtime or production database.

The current StudyTOMY suffix begins at `f5a8b3d0c2e4` and ends at
`8b53c0d1e6a4`. A disposable PostgreSQL 16 rehearsal created the parent schema
with clean `main`, upgraded it with the working tree, and started the older app
with the explicit rollback option. The old app reported `forward-compatible`,
left the revision at `8b53c0d1e6a4`, and retained all 15 StudyTOMY tables. The
PostgreSQL-enabled StudyTOMY suite then passed 169 tests. A separate local run
downgraded and re-upgraded the suffix against a model-created schema. These are
local schema/startup results; they are not production backup/restore or full
feature-level old-application compatibility acceptance.

## Older-application rollback

An older app does not recognize a newer Alembic revision and therefore fails
startup by default. Keep that fail-closed behavior for normal operation.

For a reviewed rollback to an app version that includes the forward-schema
compatibility change, an operator may explicitly set
`LEARNHOUSE_ALLOW_UNKNOWN_ALEMBIC_HEAD=true`. That mode:

1. accepts only database heads unknown to that app (mixed known/unknown heads
   still fail);
2. skips Alembic migrations and performs only additive reconciliation against
   the older app's models;
3. preserves all unknown revision markers and newer tables; and
4. reports the database as `forward-compatible`.

Use the option only after confirming the newer migrations are additive and the
older app is compatible with the retained schema. It does not reverse or mark
down a migration, remove new tables, approve a business decision, or make a
production rollback safe by itself. Disable it after returning to a version
that recognizes the database head. A deployed older version that predates this
compatibility option still cannot start against an unknown newer Alembic head.

## Remaining verification

- Rehearse the actual deployed pre-migration snapshot and restore process in an
  isolated environment before any production approval.
- Validate the older app's product reads and writes with new StudyTOMY tables
  present; schema startup evidence alone does not establish feature
  compatibility.
- Complete approved retention/backfill decisions before writing historical
  identity, consent, guardian, purchase, or assessment data.
- Keep production activation, destructive recovery, and release authorization as
  separate decisions from code review and local test success.
