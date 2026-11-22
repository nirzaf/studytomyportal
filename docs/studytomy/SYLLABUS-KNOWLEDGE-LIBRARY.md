# Syllabus Knowledge Library and Explore Topics

## Scope and current status

This feature adds an organization-scoped PDF reference library and a student-facing, source-first topic search. It reuses the existing FastAPI, PostgreSQL/pgvector, curriculum, policy, consent, private file-storage, embedding, and AI-provider paths. It does not create a course or change the existing course/lesson RAG routes.

The database schema is introduced by Alembic revision `d9f74c2a81e6` (down revision `c5d1e8f2a904`). The migration has not been applied to an application database as part of this change. Local API unit checks and offline migration generation are not proof of a successful PostgreSQL upgrade.

No official syllabus PDF or copyright permission is bundled with this implementation. A source is searchable by learners only after it has a nonempty permission evidence reference, a different educator has approved the indexed version, and the source passes the active D-16 content-rights review gate. Educators must confirm source rights before approval. The feature does not make rights determinations or claim that a source is official based on its title or publisher text.

## Lifecycle and storage

Each organization owns logical sources and immutable numbered PDF versions. Versions record jurisdiction, grade, subject, language, edition, effective cohort, publisher, source type/revision, original filename, SHA-256, size, page count, rights evidence, ingestion state and independent review evidence. Rights fields supplied by the uploader remain claims: an independent approving educator must record the license/permission reference, which changes the version to `verified_permitted`. Identical PDF bytes are deduplicated within the organization. The PDF bytes are stored through the existing private file-storage utility; database rows contain only the private storage reference and extracted page text.

Uploads are limited to 4 MiB and 200 pages and are extracted/indexed synchronously using the existing PDF and embedding utilities. Page numbers are the original one-based PDF page numbers. Image-only PDFs, PDFs with any page lacking extractable text, and unreadable PDFs are retained in a failed state with actionable OCR or upload guidance and cannot be approved; this avoids silently indexing incomplete material. Oversized uploads are rejected before PDF parsing. Embedding/provider errors are explicit failed states; a reviewer with source-update permission can retry up to five total attempts (one initial attempt plus four retries). There is no OCR service or background worker added here.

Chunks are indexed in PostgreSQL with the existing 768-dimension embedding space and PostgreSQL full-text search. Retrieval uses full-text and exact-term search first; semantic search is optional and runs only when student AI safeguards permit it. Only ready, permitted, approved versions in the requesting organization can enter student results or AI context. Approval of a new version supersedes the source's prior approved version under an organization lock. Revocation changes its retrieval eligibility immediately and revokes its mappings. PDF bytes and audit evidence remain stored after revocation; physical retention/deletion follows the existing D-13 policy and is not implemented by this feature.

## Review and syllabus coverage

Educators with organization course-create permission can view the source library, inspect extracted pages, upload versions and propose mappings. Course-update permission is required to retry indexing, approve/reject versions or mappings, and revoke versions. Approval requires a different educator from the uploader and an active D-16 policy. Mapping creation records an exact extracted-page quote and requires a published curriculum revision/concept. Only an official `syllabus` source may propose an `official` mapping. A different educator and active D-02 policy are required to approve mappings.

Coverage is derived for each cited PDF page only from approved source versions, approved mappings whose page range includes that page, and published curriculum revisions. An unrelated mapping elsewhere in the same PDF cannot mark the cited page as in-syllabus:

- **Confirmed in selected syllabus**: an approved official-syllabus mapping to the selected curriculum revision.
- **Mapped to another approved syllabus**: an official-syllabus mapping to a published revision other than the selected revision (or when no current revision was selected).
- **Supplementary evidence**: an approved supporting-source mapping; it does not establish official syllabus inclusion.
- **Unverified**: no applicable approved mapping exists, or the current D-02 policy is not active.

A source mention or a search miss never establishes official inclusion or exclusion.

Lesson references reuse the existing `StudyConceptActivity` curriculum links: the student activity page shows approved syllabus-source passages only when the current activity version/digest is linked to a concept in a published curriculum revision. The read endpoint also runs the normal course/activity reader gate, including publication, chapter lock, paid access, and drip checks. No direct association is inferred from course text or source similarity.

## API surface

All endpoints require an authenticated session and organization membership. Educator operations additionally enforce organization course permissions at the service boundary. Responses use private/no-store headers.

| Method and path (under `/api/v1/studytomy`) | Purpose |
|---|---|
| `GET /orgs/{org_uuid}/syllabus-sources?limit=...` | List versions for source managers. |
| `POST /orgs/{org_uuid}/syllabus-sources` | Multipart PDF upload with a JSON `metadata` form field. |
| `GET /orgs/{org_uuid}/syllabus-sources/{version_uuid}/pages` | Inspect extracted pages for source managers. |
| `POST /orgs/{org_uuid}/syllabus-sources/{version_uuid}/review` | Independently approve or reject a version with evidence. |
| `POST /orgs/{org_uuid}/syllabus-sources/{version_uuid}/retry-index` | Retry failed embedding indexing, up to five total attempts. |
| `POST /orgs/{org_uuid}/syllabus-sources/{version_uuid}/revoke` | Revoke a source version with evidence. |
| `GET /orgs/{org_uuid}/syllabus-mappings` | List curriculum mappings for source managers. |
| `POST /orgs/{org_uuid}/syllabus-mappings` | Propose a page-evidence mapping. |
| `POST /orgs/{org_uuid}/syllabus-mappings/{mapping_uuid}/review` | Independently review a mapping. |
| `GET /orgs/{org_uuid}/explore/curricula` | List published curriculum choices. |
| `GET /orgs/{org_uuid}/syllabus-curricula/{curriculum_uuid}/concepts` | List published concepts for mapping. |
| `POST /orgs/{org_uuid}/explore/topics` | Search approved PDFs and optionally request a consent-gated grounded answer. |
| `GET /orgs/{org_uuid}/syllabus-sources/{version_uuid}/pdf` | Read an approved PDF for organization members; page selection is a client-side PDF fragment. |
| `GET /orgs/{org_uuid}/course-activities/{activity_uuid}/syllabus-sources` | Read source passages linked through current published concept-to-activity curriculum mappings, subject to activity access. |

The Explore request accepts a bounded `query`, optional jurisdiction/grade/subject/language/edition, optional selected published curriculum UUID, and up to ten source version UUIDs for follow-up searches. A response carries each citation's source/version IDs, title, publisher, edition, original page, passage, PDF URL and coverage classification. Generated explanations are limited to four concise claim blocks.

## Learner privacy and AI behavior

The Explore page is available to organization members without course enrollment. Source search is PostgreSQL-only. AI and semantic query embedding are attempted only after the learner has a same-organization active learner profile, all required current D-01/D-02/D-10/D-12/D-13/D-16 policies, organization AI enabled, and current purpose-bound `syllabus_exploration` consent under D-12. If the approved D-12 policy does not authorize that exact scope, the learner sees approved excerpts without an AI explanation. No profile or consent is inferred. Provider context contains the question and only retrieved approved excerpts; PDF text is treated as untrusted. Structured answer claims must cite a known excerpt and include a quote present verbatim in both the stored PDF page and chunk. If any generated citation cannot be verified, the entire generated explanation is withheld and the learner receives approved passages instead. No source match produces an explicit insufficient-evidence response without calling the model.

General-knowledge answers are not implemented in this surface. Explore does not write grades, mastery, assessments, or curriculum approvals. AI failures leave approved excerpts visible; quota/rate-control failures may still be returned by the existing AI control path.

## Frontend routes

- Students: `/orgs/{orgslug}/explore` (also shown as **Explore Topics** in the organization menu when courses are enabled).
- Educators: `/orgs/{orgslug}/dash/studytomy/syllabus-library` (linked from the dashboard Courses menu).

The learner page supports optional syllabus/grade/subject/jurisdiction filters, a source-limited follow-up search, visible coverage status and authenticated PDF opening at a cited page. Published lessons show linked approved source passages when the existing curriculum lesson map is current. The educator page supports upload/version creation, extracted-page inspection, indexing retry, independent source review/revocation, and page-quote curriculum mapping/review.

## Migration, verification and rollback

Apply revision `d9f74c2a81e6` only through the approved migration process after verifying the target database backup, `pgvector` extension/version, storage configuration and learner/policy gates. The revision creates four new tables and provenance triggers. Its downgrade drops those tables, indexes and triggers; it does not delete private PDF objects. Before any approved rollback, stop consumers, take a database/storage backup, preserve audit/rights records under D-13, then remove retained private files only through the separately approved retention process.

The implementation adds focused PDF/citation/coverage unit tests and an opt-in PostgreSQL retrieval isolation test. The latter requires `STUDYTOMY_TEST_DATABASE_URL` to point to an already migrated loopback PostgreSQL database ending `_test`; it never migrates or falls back to the application database. See the final task report for checks actually run and limitations. No production migration, official-source import, rights decision, policy approval, or deployment is included.
