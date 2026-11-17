# Studytomy migration and rollback notes

**Status:** local engineering guidance; not production approval.

Business decisions D-13 and D-15 remain unapproved. Do not apply these migrations
to production, backfill learner or family records, or activate Studytomy flows
until the applicable decisions and release gates are approved.

## Database paths

- **Fresh database:** use the application's `ensure_schema()` path. It creates
  the current model schema and stamps the current Alembic head. The historical
  Alembic chain starts from an existing schema; running `alembic upgrade head`
  against an empty database is not a supported clean-install path.
- **Existing database:** use the normal application migration path against a
  verified backup and the inspected Alembic revision. It runs under the
  PostgreSQL advisory lock and fails startup if the resulting head is unexpected.
- **Studytomy migration rehearsal:** restore or create an isolated test database
  from the actual parent revision, run `ensure_schema()`, verify the head and
  schema, then run the opt-in PostgreSQL tests. Never point
  `STUDYTOMY_TEST_DATABASE_URL` at a runtime or production database.

## Historical foundation rehearsal

The foundation checkpoint described here began at `f5a8b3d0c2e4` and ended at
`8b53c0d1e6a4`. A disposable PostgreSQL 16 rehearsal created the parent schema
with clean `main`, upgraded it with the working tree, and started the older app
with the explicit rollback option. The old app reported `forward-compatible`,
left the revision at `8b53c0d1e6a4`, and retained all 15 Studytomy tables. The
PostgreSQL-enabled Studytomy suite then passed 169 tests. A separate local run
downgraded and re-upgraded the suffix against a model-created schema. These are
local schema/startup results; they are not production backup/restore or full
feature-level old-application compatibility acceptance.

## Local upgrade rehearsal on 17 November 2026

| Evidence | Observed result |
| --- | --- |
| Starting code and schema | `4f3346af99b516bab2d422217905d078096d55ac` bootstrapped a synthetic database to `f5a8b3d0c2e4` using `ensure_schema()`. |
| Target code | `f29a31aed8e28697ab820141d0ec90c1c200bb8c` applied the published Alembic chain with `alembic upgrade head`. |
| Isolation | Disposable Docker PostgreSQL 16 with pgvector, an empty synthetic database, and a loopback-only ephemeral port. |
| Result | Direct inspection of `alembic_version` confirmed the target revision `61e73f9b4c20`. The test container was removed. |

A follow-up run at `2124b593516a546bff11c4b95527ba69a5c425b2` seeded a
synthetic organization, user and public published course at `f5a8b3d0c2e4`.
After upgrade to `61e73f9b4c20`, all three rows were present, the course still
belonged to the same organization, and the new managed-course flag was false.
A custom-format PostgreSQL dump taken before the upgrade restored into a
separate database in the disposable container. The restored sample and parent
revision matched. This verifies one local backup-and-restore path. Historical
business-data backfill, production snapshot restore, and older application
behavior at this target revision still require separate evidence.

## Determine the current graph before changing a database

Published family, commerce, adaptive and publication revisions extend the
foundation rehearsal's historical head. Inspect staged, unstaged
and untracked migration files. From `apps/api`, run `uv run --no-sync alembic heads`
and `uv run --no-sync alembic history`; compare with the target database revision
using read-only inspection. Do not stamp or downgrade a database merely to match
a number copied from this document. Local `ALE-LOCAL-PILOT.md` and
`ALE-IMPLEMENTATION.md`, when present, hold later dated rehearsal records; their
counts/heads are also checkpoint evidence, not universal current facts.

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
- Validate the older app's product reads and writes with new Studytomy tables
  present; schema startup evidence alone does not establish feature
  compatibility.
- Complete approved retention/backfill decisions before writing historical
  identity, consent, guardian, purchase, or assessment data.
- Keep production activation, destructive recovery, and release authorization as
  separate decisions from code review and local test success.
