# Grounded-materials retrieval evaluation

## Draft source intake

For synthetic local testing, set `LEARNHOUSE_STUDYTOMY_GROUNDED_DRAFTS_ENABLED=true` and send a multipart `file` to `POST /api/v1/studytomy/orgs/{org_uuid}/courses/{course_uuid}/lessons/{lesson_uuid}/draft-sources`. It requires an authenticated organization member, native course update permission, and an unpublished lesson in that exact private managed course. The current limit is 4 MiB, 200 PDF pages, and 100,000 extracted characters. Image-only scans need OCR before upload. The receipt lists pages with extracted text and, for newly ingested files, pages without readable text. Both lists preserve the original page numbers. Unreadable pages supply no draft context, and a file with no readable pages is rejected. Earlier immutable source revisions retain only the page metadata captured at intake.

PostgreSQL retains the original bytes, extracted pages, and SHA-256 under a source UUID linked to the exact course and lesson. Repeated upload of the same file for the same lesson reuses that source version. The upload endpoint returns metadata only. The authorized `POST .../draft-sources/{source_uuid}/revoke` operation blocks future retrieval without deleting the retained version.

For a lesson or quiz preview, send `source_uuids` (up to five) and `topic` to `POST .../lessons/{lesson_uuid}/grounded-lesson-preview` or `POST .../lessons/{lesson_uuid}/grounded-quiz-preview`. The service verifies every selected source is active for the authorized course and lesson, ranks bounded page excerpts by matching topic terms, and rechecks scope before returning citations. If no excerpt matches, the provider is skipped. If the provider declines because matching excerpts cannot support the topic, the API returns HTTP 422 without a draft and refunds the reserved credit. Every returned block or question carries a server-verified exact quote, source UUID, digest, and original page number. A quiz uses the native task contents format and keys citations by question UUID. It rejects duplicate choices and requires exactly one correct choice per question. Teachers still need to check whether the quote supports the explanation or answer. Preview routes make no changes to learning content.

To save an edited text lesson, use `POST .../lessons/{lesson_uuid}/grounded-lesson-draft` with the current `expected_current_version`, edited `title`, and `blocks`. Each block contains `text` and a citation with `source_uuid`, `sha256`, `page`, and `quote`. The server verifies active source versions and saves the native unpublished activity plus private, version-bound evidence and audit metadata in one transaction. Stale saves return HTTP 409. Send a `request_id` and reuse it with the same payload to recover the receipt after a lost response.

Authorized editors can read `GET .../lessons/{lesson_uuid}/grounded-lesson-sources`. It returns the current native version and a status of `current`, `stale`, or `none`. Quotations are returned only when the saved evidence still matches the lesson and all cited sources are active and correctly scoped. Stale evidence remains stored for audit.

The native text lesson editor shows **Lesson sources** for eligible private managed drafts when grounded drafting is enabled. The panel uploads searchable PDFs, lists active sources using the lesson-scoped `GET .../draft-sources` route, and loads citations for the saved version. Teachers can select up to five PDFs, generate and edit a lesson preview, then explicitly save it as unpublished. A successful save reloads the editor at the new native version. Quiz drafting currently uses the HTTP or MCP paths described here.

To save a quiz, link sources to the target unpublished assignment activity. Send `request_id`, `assignment_uuid`, `title`, and edited `questions` to `POST .../lessons/{lesson_uuid}/grounded-quiz-draft`. Each question contains `question`, `options` with `text` and `is_correct`, and a citation in the same format as a lesson block. The save creates one native quiz task and retains its question citations and retry receipt in the same transaction. Use the same request ID and payload to retry. The assignment stays unpublished.

Authorized editors can read `GET .../lessons/{lesson_uuid}/assignments/{assignment_uuid}/tasks/{task_uuid}/grounded-quiz-sources`. It returns citations keyed by native question UUID when the task digest and active source links still match. Independent edits and source revocation return `stale` without quotations.

Managed course candidates include current grounded lesson and quiz lineage in the reviewed digest. Review approval and new release creation reject stale or revoked grounding with `GROUNDED_SOURCE_REVIEW_REQUIRED`. The staff review panel shows that condition and leaves **Request changes** available. The candidate records source UUIDs, hashes, page numbers, and quotation hashes. Source revocation changes the candidate and blocks new releases. Handling an already released snapshot continues through the separate withdrawal process.

## Retrieval evaluation

Migration `0cb1b9a418ed` leaves existing embedding provenance unknown. Newly indexed rows record the resolved provider, model, dimensions, extraction/chunking revision, and a vector-space fingerprint. Set `LEARNHOUSE_AI_EMBEDDING_MODEL_REVISION` to a non-secret model revision identifier when the provider deployment has one. Changing that identifier causes a ref to re-embed on the next index job even when the text is unchanged. Search excludes legacy and mismatched vectors, and skips the provider when no compatible rows exist. No automatic fallback serves unknown vectors.

For a local cutover rehearsal, migrate the disposable database, keep indexing and querying processes on the same embedding configuration, and run `uv run python cli.py index-backfill --org <slug> --course <course_uuid> --inline` from `apps/api` against a synthetic unmanaged course. Verify compatible row counts and search after the backfill. Omit `--transcribe` unless explicitly testing that path. Changing the model makes search coverage incomplete until the rebuild runs. Reverting configuration also requires rebuilding or restoring the prior index because the native pipeline replaces each source's rows. The PostgreSQL test verifies query isolation with synthetic vectors. A real provider cutover is unverified.

The existing authorized `GET /api/v1/ai/rag/status?course_uuid=<course_uuid>` endpoint keeps the stored chunk counts and adds compatible counts for the course and each activity. `reindex_required` reports retained incompatible chunks for a native course with a supported embedding dimension. It does not indicate whether entirely absent chunks are missing, nor whether the caller can read every compatible activity.

Retained PDF sources, including revoked versions, require retention review before deleting their linked lesson, course, organization, or uploader account. This local implementation rejects those deletions before native cleanup starts. A reviewed source disposal operation is still pending.

The current course retrieval path uses pgvector cosine distance with organization and course filtering, bounded overfetch, and an activity-level authorization check before snippets reach model context or citations. The evaluation leaves runtime retrieval unchanged. [Current query](../../apps/api/src/services/ai/rag/query_service.py) · [Activity authorization](../../apps/api/src/services/ai/rag/access.py)

The offline comparison used a disposable loopback-only PostgreSQL 16 container with pgvector. A temporary table held six eligible curriculum chunks and three controls from another tenant, a draft, and a revoked source. Four queries covered an exact curriculum code, an exact teaching term, and two conceptual paraphrases. The vectors were hand-authored to isolate ranking behavior. Every candidate set was authorized before ranking.

| Approach | Mean Recall@2 |
| --- | --- |
| Exact authorized cosine distance | 0.75 |
| PostgreSQL English full-text search | 0.50 |
| Reciprocal rank fusion of both lists | 1.00 |

All three approaches returned zero ineligible sources in this fixture. The fused ranking recovered the curriculum code missed by the exact vector baseline. These four synthetic queries establish ranking behavior. They do not establish provider embedding quality, latency, production recall, or launch readiness. Retain the current runtime ranking while assembling a larger evaluation set of representative approved material and judgments. [Evaluation fixture](../../apps/api/src/tests/studytomy/test_grounded_retrieval_postgres.py)

Filtered approximate search can return too few matches because pgvector applies filters after scanning the approximate index. Measure any HNSW candidate against exact authorized results, including underfill, recall, and latency at selective tenant and release filters. Consider bounded iterative scanning only where the larger evaluation shows a need. Keep final authorization before model context and citations for every search mode. [pgvector documentation](https://github.com/pgvector/pgvector#iterative-index-scans)

A PostgreSQL stress fixture adds 96 closer ineligible vectors and three eligible lesson vectors. Fixed twenty-row overfetch returned zero eligible results. A forced filtered HNSW scan at `ef_search=8` with iterative scans disabled also returned zero. Exact ranking after scope and authorization returned all three, and no path returned an ineligible source. This verifies two underfill cases on synthetic data. The fixture does not establish runtime latency or recall at the production index settings. [Stress fixture](../../apps/api/src/tests/studytomy/test_grounded_retrieval_postgres.py)

For local comparison, `LEARNHOUSE_RAG_SMALL_COURSE_EXACT=true` enables an exact path for single-course native RAG requests whose compatible index has at most 1,000 chunks. The existing activity access helper filters candidates before the exact rank, and a final access check still filters the ranked results. Larger and multi-course scopes keep the existing ranker. The default is off pending representative recall and latency checks. This switch does not affect managed lesson drafting or enable hybrid retrieval.

Row-level security remains deferred. Any future local evaluation should use a non-owner role without `BYPASSRLS`, test default denial and cross-tenant access, and account for transaction and connection reuse. No shared roles or policies changed. [PostgreSQL row security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)

### PostgreSQL 19 comparison (21 November 2026)

The same checkout's PostgreSQL-backed retrieval tests passed in the local PG19 API selection. A separate fixed-seed, connection-scoped synthetic benchmark compared exact authorized ranking, an explicitly selected HNSW path, and the existing test-fixture reciprocal-rank-fusion query on PG16.16 and PG19.0. It used 3,000 generated 768-dimensional rows and is a query-path smoke measurement, not a representative curriculum dataset or proof of production latency/recall. Plans, measurements, collation checks and limitations are recorded in the [PostgreSQL 19 upgrade note](POSTGRESQL-19-UPGRADE.md). No runtime hybrid search or index/configuration change was adopted.

The existing MCP OAuth path uses the requested MCP resource in its token lookup and cache partition. The grounded preview tools delegate to the same service as the HTTP routes and keep inbound tokens out of downstream credentials. Teacher review and release approval remain required. [MCP draft authoring](MCP-DRAFT-AUTHORING.md) · [Current MCP middleware](../../apps/api/src/security/mcp_auth.py) · [MCP authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)

## Lesson-source excerpt characterization (AUD-17)

Commit [`2a62e4764e8c1817870b9a4db365e5bba5cc4353`](../../apps/api/src/services/studytomy/grounding.py)
changes transient prompt-context selection for explicit course-and-lesson
sources. The selector now examines overlapping 2,000-character windows (1,800
character stride) across each extracted page, keeps the highest lexical
overlap per page, then selects at most six pages across up to five sources. The
stored PDF bytes, extracted page text, source UUID/hash/page identity and saved
citation format do not change; no schema migration was needed. Saved draft and
release evidence still identifies the exact source revision and quote. The
full provider prompt is not retained, so prior prompt context is not exactly
replayable.

The PostgreSQL synthetic fixture has an irrelevant first page, an unreadable
blank page, a page with photosynthesis evidence and a long fourth page whose
relevant aurora evidence appears after the old 2,000-character prefix. The new
selector returns the exact quote with original page 4 provenance. A
non-matching phrase using different vocabulary returns an actionable 422 and
does not call the provider; retrieval remains lexical and does not expand
synonyms or establish semantic support. The prompt-like sentence in the
selected source remains inside the JSON excerpt data, while the system prompt
instructs the model to ignore embedded source instructions. This uses a mocked
provider and does not establish how a real model responds to adversarial
content.

The same fixture confirms a PDF with no extracted text fails with the existing
OCR-required response and a text file is rejected as an unsupported upload.
The unreadable fixture is a blank text page; it is not a rendered image-only
scan. The extractor has no OCR. A scan with no text layer returns an
OCR-required error instead of being reported as successfully extracted. The
regression also retains the existing cross-organization/course/lesson denial, source
revocation during generation, exact citation validation and stale-evidence
checks. Verification on the dedicated loopback PostgreSQL 16.16 / pgvector
0.8.7 container passed `test_grounded_material_postgres.py` (1 passed) and
`test_grounded_retrieval_postgres.py` (3 passed). The changed source-panel
component passed focused ESLint and the web TypeScript check. Browser acceptance,
real-provider factual support, OCR, representative approved curriculum
judgments and learner/pilot approval remain unverified. No hybrid runtime
search, vector-model change or real-user flow was enabled.
