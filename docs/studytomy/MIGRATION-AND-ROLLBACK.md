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

## Synthetic PostgreSQL migration and recovery rehearsal on 18 November 2026

The opt-in migration test suite added in commit
[`ec0cebbe373f1a201b30cebea5820a401705e8b6`](../../apps/api/src/tests/core/test_migrations_postgres.py)
uses a loopback PostgreSQL URL ending in `_test`. Each test creates a unique
database from `template0` and drops only that exact database afterward. The
rehearsal used the dedicated Docker PostgreSQL 16.16 server with pgvector 0.8.7;
it did not use the normal development database.

| Check | Result |
| --- | --- |
| Concurrent empty-database `ensure_schema()` calls, current tables, integrity triggers/constraints, vector extension and immutable business-event update rejection | 3 PostgreSQL migration tests passed overall |
| Interrupted startup migration and transactional DDL rollback | Passed; the synthetic probe table was absent and the revision remained unchanged |
| Parent-to-current application startup path | Passed from `a78f2c9d1e64` to `b91d2f6a08c3` through `ensure_schema()`; a synthetic business-event row survived |
| Core migration and graph tests | 52 passed |
| Synthetic evidence hold, tombstone and restore replay regression (`test_checkout_router.py -m studytomy_postgres -k free-reentry`) | 1 passed, 11 deselected |

The parent-state rehearsal was constructed in a fresh synthetic database by
downgrading the current head's migration one step. The affected attempt and
tutor-session tables were empty before doing so. It verifies the current
migration's parent revision and the application upgrade path, but it is not a
restore or upgrade of an independently captured historical database.

A separate manual check used a custom-format, schema-scoped `pg_dump` of a
synthetic schema and `pg_restore` into a second disposable database. The target
had pgvector installed in `public` before restore. The restored synthetic
business event, migration head, immutable-event trigger and relevant constraints
matched; the synthetic row digest was
`46ea1bc69b322efcb2cec8a3e88c12deed7569ceaccd37813dd900056addf6d1`. This was
not a full database backup restore or a production-shaped snapshot. Temporary
rehearsal databases and dump files were removed after verification.

### Full synthetic database restore follow-up

At repository revision `85686f3cd7fea3b9234f34f1fa7515d742caae59`, a custom-format `pg_dump` of `studytomy_aud03_test` was restored with `pg_restore --exit-on-error --single-transaction` into a fresh `template0` database named `studytomy_aud19_full_restore_test`. Both databases were inside the same disposable Docker PostgreSQL instance bound to `127.0.0.1:60607`. The restore completed successfully. The source and restored database matched on Alembic head `b91d2f6a08c3`, pgvector `0.8.7`, row counts for 137 public tables and definitions for 42 public user triggers. The compared counts included 3,041 business events, 253 consent events, 205 course releases and 229 learning evidence records.

The follow-up compared counts and order-independent fingerprints of JSON-serialized rows for all 137 public tables in read-only transactions. The fingerprints matched. On the restored database, a rollback-protected probe also confirmed that the business-event trigger rejected both an update and a delete of a retained synthetic event.

At `a088b7ef66c34228117ede0dc6bf33c58a620c97`, `python -m pytest -q src/tests/studytomy/test_checkout_router.py -m studytomy_postgres -k 'free-reentry or free-release-media'` targeted only `studytomy_aud19_full_restore_test` and passed **2 tests, 10 deselected** in 29.13 seconds. The API exercised re-entry, retention replay and authenticated media delivery with newly seeded synthetic records and temporary files on the restored schema. Behavior for pre-existing restored records through the application, external object recovery and restore from a production snapshot remain unverified. The local dump and restored test database were retained for follow-up.

At `0c8d16e77231c3ad888a3cd834f97fef8ffad361`, a read-only application service probe resolved one already-present synthetic grant from the source database and called `read_managed_release_overview` for the same learner, course and release in both databases. The authorized response matched exactly. This exercised current eligibility, consent, policy and grant checks using an actor resolved from the fixture. HTTP authentication and recovery of the original media objects were outside this probe.

The retention/replay check covers synthetic database evidence and local test
fixtures only. It does not verify deletion, versioning or restoration in the
configured external object store. A production-shaped pre-migration backup,
independent historical schema/data upgrade, object-store retention/recovery,
and older-application product reads/writes remain unverified. No production
migration, downgrade or destructive recovery was run.

## Remaining verification

An older-code probe used an archived copy of `69e2e562baf7ce0c741d3d04cf565a11ddc8fd49`, the parent of the commit that added `b91d2f6a08c3`. On a separate disposable clone of the synthetic restore, `ensure_schema` with the explicit compatibility option reported `forward-compatible`, preserved `b91d2f6a08c3`, and reconciled zero items. The older `Course` model read an existing course, flushed and refreshed a synthetic description edit, and rolled the edit back. The initial probe stopped after schema startup because it read the wrong report attribute. The corrected probe checked `state` and `after` and passed. This covers one native ORM write path. HTTP authorization and broader product compatibility still need rehearsal.

- Rehearse the actual deployed pre-migration snapshot and restore process in an
  isolated environment before any production approval.
- Validate the older app's product reads and writes with new Studytomy tables
  present; schema startup evidence alone does not establish feature
  compatibility.
- Complete approved retention/backfill decisions before writing historical
  identity, consent, guardian, purchase, or assessment data.
- Keep production activation, destructive recovery, and release authorization as
  separate decisions from code review and local test success.
