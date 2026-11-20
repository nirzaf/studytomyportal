# PostgreSQL 19 local upgrade and recovery

Status: local implementation and disposable verification only. No hosted or production database was inspected or changed.

## Runtime used for new local databases

- Server: `pgvector/pgvector:0.8.7-pg19@sha256:89879f41d564f67b0c1462a491d16fcbc6de39ac10db17e7a4bba2138a5e0ba5`.
- External dump client: `postgres:19@sha256:bb6e70add742cd868fb62cb3b2f67d1dd2bdd2b65a521e778420748753687c60`.
- The server image is multi-architecture (amd64 and arm64). Its PostgreSQL 19 `PGDATA` is below `/var/lib/postgresql/19/docker`, so new CLI-managed local databases mount the parent `/var/lib/postgresql` directory.
- `learnhouse dev` uses the separate `learnhouse-dev-pg19` Compose project, ports 5433/6380, new container names, and new named volumes. The previous `learnhouse-dev` profile remains separate.
- Re-running setup on an existing local install preserves the database image and persistent mount source/target. If the CLI cannot read a local database image or data mount, setup stops before rewriting files. It does not migrate or replace existing database volumes.

The digest was inspected from the registry manifest list and the local arm64 image. PostgreSQL 19.0 and pgvector 0.8.7 were verified by executing `CREATE EXTENSION vector`, creating a 768-dimensional vector column and HNSW cosine index, inserting synthetic vectors, and retrieving the nearest vector. The API's existing drivers passed the targeted migration and retrieval tests; no dependency upgrade was needed. The local builder was Bun 1.4.2 while the repository declares 1.4.4; both versions are recorded in the pilot manifest identity inputs, and release builds should use the declared pin.

## Explicit PostgreSQL 16 to 19 procedure

Major upgrades use a new PG19 cluster and a logical dump/restore. Do not mount the PG16 volume into PG19. Do not use `learnhouse setup` against the existing install directory: create a separate destination directory/project and retain the source install and volume unchanged until acceptance is complete.

1. Schedule a maintenance window and stop application writers. Create a private `learnhouse backup` archive first; it includes the database, `.env`, and local uploaded content when applicable. Keep that archive outside both database volumes.
2. Record the source container image, `SHOW server_version`, installed extensions and versions, database encoding/collation, `alembic_version`, and any additional application roles. Preserve the existing app image and source volume. For nonstandard roles, capture and review `pg_dumpall --globals-only`; do not blindly replay role scripts into a shared cluster.
3. Create a new CLI-managed install in a separate directory so its PG19 `db` service gets a new named volume mounted at `/var/lib/postgresql`. Start only that destination database until restore validation is complete.
4. Use the pinned PostgreSQL 19 client to make a logical dump of the source. `pg_dump` from a newer major can read older servers. Use the source Compose network (for example, the running source API container's network namespace) to reach its `db` service. Keep the dump in a private directory with mode `0600`.
5. Restore into the fresh target cluster using the target's `learnhouse` database role. Recreate any explicitly reviewed extra roles first. Verify extension names/versions, vector dimensions and indexes, all table counts, foreign keys, unique constraints, `alembic_version`, user/guardian consent, review/release history, assignment/grading evidence, financial and idempotency/outbox records, and uploaded content before starting application writers.
6. Start the application image matching the source version against the restored copy, exercise health and representative read/write journeys, then rehearse rollback by returning to the original PG16 project and its original app/content volumes. Keep writes frozen during any later authorized cutover and document the recovery point and expected data-loss window.
7. Retain the PG16 project and backup until the authorized owner accepts the migration. This repository has no deployed environment or approved cutover target; this procedure does not authorize production changes.

CLI restore checks a recorded backup server major and refuses to restore a newer-major dump into an older-major target before stopping services or executing SQL. Older archives without a source-version marker remain readable, but the CLI warns that it cannot prove major-version compatibility. A database-only dump does not include external object storage; S3 recovery remains a provider-specific procedure.

## Configuration and tuning decisions

PostgreSQL 19 defaults observed in the disposable server were `io_method=worker`, `io_min_workers=2`, `io_max_workers=8`, `autovacuum_max_parallel_workers=0`, `jit=off`, `default_toast_compression=lz4`, and `password_encryption=scram-sha-256`. No values were overridden. A small synthetic smoke/restore test is not a representative workload benchmark, so no claim of platform performance improvement is made.

No query or index change was adopted because production-like plans and workload metrics were unavailable. `INSERT ... ON CONFLICT DO SELECT`, parallel autovacuum tuning, `REPACK (CONCURRENTLY)`, plan advice, and JIT/compression overrides remain unelected. They require workload-specific evidence, particularly for concurrent submissions, learner privacy filters, vector recall, table bloat and older-snapshot risk. Do not enable them globally to demonstrate features.

## Verification limits

The disposable PG19 tmpfs container reported PostgreSQL `19.0-1.pgdg12+1` and pgvector `0.8.7`; the application-created vector table/index and nearest-vector lookup succeeded. The API migration, grounded-retrieval and lab-platform audit selection passed 12 tests. A separate concurrent grading finalization and independent reviewer authorization selection passed 5 tests. The momentum pilot build verifier passed and its focused suite passed 8 tests. Chromium exercised the static pilot inside the app's opaque sandboxed iframe at desktop and mobile sizes: start, recording, stop and CSV export succeeded; the exported build identity matched the manifest; the exact hashed JS response had `Access-Control-Allow-Origin: *` without credentials, while HTML did not. No browser errors, failed requests or unexpected external requests were observed.

This tmpfs verification does not prove persistence across service recreation. The disposable persistent-volume Compose proof was rejected before execution by the command runner because its cleanup contained an `rm -rf` operation (`Rejected: rm -f style commands are not permitted`). It was not retried, and no persistent PG19 test project was created. A seeded PG16-to-PG19 dump/restore rehearsal was not performed; PG16 carry-forward remains unverified. The CLI Vitest/typecheck commands could not run because its local test and Node type packages are absent; no packages were installed. The local pilot builder was Bun 1.4.2 while the repository pins Bun 1.4.4; the identity records both versions, so release output should be rebuilt with the repository pin.

The full non-mocked API-backed educator/reviewer/learner journey still needs a local scenario fixture and synthetic user/session setup. No `run_live_vlab_e2e.sh` or exit-code-7 log was present to identify the earlier failing command. Grade 12 Physics educator approval and safeguarding sign-off remain pending.
