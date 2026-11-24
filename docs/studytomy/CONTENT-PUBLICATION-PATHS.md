# Studytomy content publication paths

For the first topic-level tutoring slice, see the [topic learning loop](TOPIC-LEARNING-LOOP.md). Authoring media for playback is separate from explicitly authorizing immutable source revisions for the learner tutor.

**Reading order:** this is a chronological implementation ledger. The opening inventory is historical; later checkpoints and current source supersede its missing-feature statements. Inspect `publication.py`, `releases.py` and current authority checks before implementation. No checkpoint grants release approval.

## Historical initial inventory

This inventory records the TASK-036 review boundaries inspected at
`adfe380fc0e485056941143f330151e3439f5f76` with the local Studytomy changes.
The existing content editor and draft history are present. Managed content
review records, a managed course designation, and an approved course release
are missing. None of these paths has release approval from this inventory.

| Path | Observed behavior | Required integration |
| --- | --- | --- |
| Normal course editor and course API | `update_course` in `apps/api/src/services/courses/courses.py` changes publication and visibility fields. | Gate managed publication on the reviewed course revision and all member activities. Keep exceptional withdrawal available to authorized staff. |
| Activity create, update and version restore | `create_activity` and `update_activity` write activity state. `restore_activity_version` in `apps/api/src/services/courses/activities/versioning.py` separately replaces stored content. | Use a reviewed snapshot for student reads. Editing or restoring a draft must change the candidate revision and leave the approved release untouched. |
| Assignment editor, task writes and assignment publication | `update_assignment` in `apps/api/src/services/courses/activities/assignments.py` synchronizes its publication flag to the activity. Activity updates can also synchronize the assignment flag. | Gate both directions and snapshot the exact assessment, tasks, references, release and help rules under TASK-037. |
| Course package transfer, LMS import and migration | Transfer import constructs courses and activities directly and can publish the course when `set_unpublished` is false. The migration service currently creates unpublished courses and activities. | A managed import becomes a new review candidate and cannot inherit approval. Treat provenance and redistribution rights as review inputs. |
| Course and activity clone | The course clone starts unpublished; an activity clone copies or overrides its own visibility and rewrites content references. | Treat copied resources as new candidates. A reviewed source ID or client-supplied metadata cannot approve a different clone. |
| Media replacements | Video and PDF services have content writes outside the general activity update service. | Include reviewed asset content identities, and route media changes to the pending revision rather than replacing the released media in place. |
| AI and integration tools | Course tools can change course/activity publication, assignment tools write drafts and can change publication, and content-edit tools call the activity update and version-restore services. | Apply the same managed gate in the server write paths. AI instructions and tool approval UI do not grant content review authority. |

## Review authority and evidence

The existing `CONTENT_RELEASE_REVIEW` capability now requires current
organization decisions D-02, D-10, D-15 and D-16 before the existing named
education owner and exact course scope checks. The capability alone grants no
right to publish. A review write must supply the owner and other conflicting
participants to the existing conflict-exclusion check.

The focused access suite passed 18 tests with a disposable loopback
PostgreSQL 16 database at Alembic head `61b26e413fa2`. Shared reviewer
assertions ran once through the normal SQLite fixture and again against
PostgreSQL inside a rollback transaction. They checked each missing policy,
missing owner assignment, missing course scope, a granted scope, and
self-review denial. The fixture records are synthetic and the container
was removed.

The private evidence workflow now stores both learner eligibility and course
evidence. Migration `9b2c7e4a16d3` makes the scopes mutually exclusive. A
course evidence row must include its course and fingerprint and purpose
(`content_rights` or `content_accessibility`). Learner evidence retains its
learner profile and existing purpose. The dedicated course endpoints require
an authenticated scoped reviewer and current named-owner approval evidence.
Uploads also require the approved D-13 content evidence retention category
and D-15 file rules. The scope, digest and upload event commit together.

The v4 fingerprint represents the course, chapter and activity rows, including
stored media references, explicit navigation order, native assignment settings
and task definitions. The scoped candidate API returns the digest and a bounded
page of ready evidence for that digest and the current policy versions. Private
task contents are used internally for hashing. Earlier evidence stays readable
under its recorded fingerprint. A new upload must use the current candidate
fingerprint. Released assessment policy and verified file bytes at the media
references are still open. The immutable
release snapshot work needs those before using an approval to publish a
managed course. An upload attaches evidence to the candidate. It is never a
distribution-rights decision.

Generic content handlers for both object and local storage explicitly deny
the reserved Studytomy namespace. Evidence is downloadable only as a private,
non-cacheable attachment through the scoped endpoints. The storage adapter was
stubbed in the API tests. A production storage configuration and rights review
remain unverified.

On disposable loopback PostgreSQL 16, the migration from
`61b26e413fa2` to `9b2c7e4a16d3` preserved a pre-existing synthetic learner
evidence row without assigning it a course scope. The focused mixed API
selection passed 176 tests, including database rejection of mixed and missing
scopes, current named-owner approval checks, replay, stale content and
private download guards. The test container was removed.

The generic storage guard and Windows path normalization were verified
separately on clean `main` (44 tests passed) and published as
`43f314540404c1c1478744e6bf7a53802d1fb430`. Remote `main` matched the
commit, and GitHub listed zero checks and workflow runs for it. The course
evidence schema and workflow remain local.

The next implementation step is an owner/reviewer decision record bound to
the candidate and its scoped evidence. Managed write and read paths need to
use the released snapshot before that decision can govern general
publication. Normal LearnHouse course publication and real Studytomy learner
access have no new runtime path in this checkpoint.

The candidate API regression verifies that a ready verified upload appears
under its exact candidate, disappears from the listing after a material edit,
and remains downloadable under its historical evidence ID by a scoped
reviewer. The focused access and content evidence selection passed 21 tests
with no failures or skips. The HTTP workflow used SQLite and temporary local
storage. Existing PostgreSQL scope and authorization checks used disposable
loopback PostgreSQL 16. The container was removed. Candidate listing metadata
does not continuously verify the storage object or record a review decision.

A scoped reviewer can preflight two selected attachments through
`POST .../content-review-candidate/verify-evidence`. It accepts one rights
attachment and one accessibility attachment for the exact current fingerprint
and policy versions. It reads each stored object and checks its byte count and
digest. The HTTP regression rejects a reused wrong-purpose attachment, a
same-length disk modification and a stale fingerprint. It uses temporary
filesystem storage. This endpoint only verifies inputs. A persisted reviewer
decision must run the checks again in its own transaction.

Migration `8a91ce74f623` adds the append-only decision table. A record binds
the organization, course fingerprint, distinct owner and reviewer, reviewer
assignment, both evidence rows, policy versions, and accessibility and
assessment findings. An approval requires both findings to be confirmed.
The empty-schema PostgreSQL rehearsal
bootstrapped the new head, downgraded to `dc48e62a7b91`, and re-upgraded to
`8a91ce74f623` with the immutability trigger installed.

The private course review decision route now records a decision and its
business event in the same transaction. It resolves the course owner from
active LearnHouse creator authorship and current organization membership. It
excludes active creators, contributors and maintainers from reviewing and
requires an exact course scope under an active education assignment. It checks
the current candidate and verified attachments before a new decision. An
identical authorized retry returns the recorded receipt.

The temporary-filesystem HTTP workflow verified self-review denial, a
successful independent review, and one persisted record after replay. The
focused access and evidence selection passed 19 tests with two PostgreSQL
cases deselected. This step does not create a managed release or change the
native publication flag.

Deletion preflight now rejects removal of a course, organization, owner
account or reviewer account referenced by a retained course review. The
course workflow verifies these checks after a material edit. The PostgreSQL
regression inserted an approved review in a disposable transaction, attempted
an update and a delete, and verified the original decision survived both
attempts. The focused content, staff access and demo teardown selection passed
35 tests with no skips. Disposable loopback PostgreSQL 16 was removed.

The course dashboard now offers Content review after the current staff
session's scoped candidate request succeeds. The panel shows the candidate
fingerprint and separates rights and accessibility attachments. It downloads
selected files through the authenticated evidence route and records approval
only after both confirmations. An identical retry uses the same operation key
while the form is open. A stale response prompts reloading the candidate.
Scoped ESLint, web TypeScript and four focused rendering tests passed.
The initial broad run found three editor timeouts and a missing Arabic
Progress reports label. The timeouts did not reproduce in isolation. After
adding the label, the full web command passed 723 tests with zero failures.
The dashboard also accepts a private file and an evidence purpose for the
displayed candidate. The server enforces the approved file rules and confirms
storage integrity. An identical retry retains its upload key while the form
is mounted. A successful upload selects the new attachment. Changing either
review attachment resets both confirmations. Web TypeScript, scoped ESLint
and four focused rendering tests passed. Browser acceptance and the managed
publication gate remain open.

The signed mobile Chromium fixture now covers the course dashboard at
390 × 844, multipart attachment upload, selection and confirmation,
keyboard submission, focus on a stale-candidate error, and reloading and
reviewing the replacement candidate. The browser used real local sign-in and
course creation and intercepted review requests with synthetic responses.
One Chromium test and E2E TypeScript passed. An initial typecheck caught an
organization UUID unavailable in the fixture's type; the request matcher now
uses the existing organization-segment pattern. Disposable loopback
PostgreSQL 16 at `8a91ce74f623` and Redis were removed. Browser verification
against the real review endpoints remains open.

The scoped candidate read now returns the latest recorded decision only when
both its fingerprint and its policy versions match the current candidate.
The dashboard refreshes that decision after a write, including after a replay
of an older receipt. A review decision remains separate from release status.
The HTTP regression checks that approval is visible for the reviewed candidate
and disappears from the current view after an edit. Nineteen focused API tests
passed with three PostgreSQL cases deselected.

TASK-037 now has a private candidate snapshot builder shared with the
TASK-036 fingerprint. The v4 payload retains the native row identifiers needed
to resolve navigation links and associate assignment tasks with their parent
records. The HTTP test checks that the hash is computed from that payload and
that editing the live retry rule and correct answer leaves the captured values
and hash intact. The payload includes private grading inputs and is never
returned through the candidate API. It remains a snapshot input without
durable release storage, policy or media binding, or learner read integration.

Migration `627b1fd4a981` adds private append-only release storage with one
snapshot per review, a media manifest, and references to the assessment,
progression, retention and rights policy versions. An empty disposable
PostgreSQL 16 database bootstrapped at the new head, downgraded to
`8a91ce74f623`, and upgraded again. PostgreSQL rejected both updates and
deletes of a synthetic release row and preserved its snapshot. The focused
selection passed 23 tests with zero failures or skips. The release table
still needs an application writer that validates the active policies, review
and media. No learner read path uses it yet.

The release policy resolver checks current named approvals for D-08, D-09,
D-13 and D-16. It captures each version's ID, UUID, version number and body
digest. D-13 must specify `retention.course_release_retention_category`;
the resolver supplies no default. The synthetic regression verifies denial
with missing approvals and missing category, then verifies exact policy
binding. Twenty focused API tests passed with three PostgreSQL cases
deselected. Course-specific rule interpretation, media binding and invoking
the resolver from the release writer remain open.

The local release migration also guards inserts. It requires an approved
review for the same organization, course and snapshot digest, plus approved
organization policy rows of the expected decision types. This guard is
installed by both migration and fresh-schema bootstrap. It complements the
future writer's named-approval, timing and media verification. The
PostgreSQL regression rejects a different course, different digest,
changes-requested review, and mismatched policy key.

The native media capture helper streams stored course thumbnails, hosted video
and PDF lesson files, and assignment solution and task reference attachments.
It records the SHA-256 and byte count for each recognized storage reference.
The temporary-filesystem regression confirms that a same-length replacement
changes its digest and a missing object refuses capture. Embedded editor media,
video derivatives and external materials still need an explicit capture or
denial rule. The helper records digests without retaining copies of the files.

Migration `08fd9332b6ac` adds the private media manifest to the immutable
review record. Approvals capture the native file digests before committing.
Older approvals keep null manifests and require a fresh review before use
by a future release writer. The HTTP regression verified that replacing
stored thumbnail bytes after approval and replaying the request leaves the
original manifest intact. The focused selection passed 12 tests with zero
failures or skips. The empty PostgreSQL 16 schema downgrade and re-upgrade
passed, and the disposable instance was removed.

The scoped release preflight checks the latest approval for the current
candidate and review policies, the release policy approvals, both evidence
files, and the approved native media manifest. It returns the review UUID
and candidate digest. The HTTP regression verified the preflight succeeds
with synthetic approvals and temporary files, then rejects a same-length
media replacement and a later content edit. Twenty-one focused API tests
passed with three PostgreSQL cases deselected. The preflight creates no
release. The writer must run these checks in its own transaction and retain
the verified bytes under private storage before the release can be used.

The private copy helper stages each reviewed file in a temporary stream,
compares the observed digest and length to approval, and verifies the saved
copy under a fresh reserved release path. An ordinary failure during a batch
attempts to remove only the new objects from that attempt. A temporary
filesystem regression verified that same-length replacement rejects copying
and leaves earlier retained bytes intact. Three focused tests passed with two
PostgreSQL cases deselected. The helper still needs release-transaction and
orphan-reconciliation integration. S3 and cancellation recovery remain
unverified.

The private release writer now uses the same review and policy checks within
an organization-locked transaction. It copies the approved native files,
stores their private manifest with the exact review snapshot and policy
references, and commits the business event with the release. A retry while
the approval and its inputs remain current returns the existing receipt.
The writer compensates ordinary pre-commit failures and preserves media if
commit acknowledgment is uncertain. The synthetic HTTP regression verified
one release after replay, matching retained bytes after the source changes,
and unchanged private and unpublished native course flags. The focused
selection passed 31 tests with no skips. The HTTP case used SQLite and real
temporary filesystem storage. PostgreSQL checks ran at `08fd9332b6ac` in a
disposable loopback container. S3, crash reconciliation, comprehensive media
coverage, and managed learner publication remain open.

An authorized replay of a committed release now returns its historical
receipt even after source media or draft content changes. New release
requests still enforce all live approval and media checks. The HTTP
regression injected a lost acknowledgment after the SQLAlchemy commit,
changed the source media, and verified replay returned one retained release
and one private file with the original bytes. It also confirmed the live
release preflight still rejects the changed candidate. Twenty-one focused
tests passed with three PostgreSQL cases deselected.

The PostgreSQL concurrency regression pauses the first release call during
private file retention and uses `pg_blocking_pids` to verify that a second
session waits on its organization lock. Both calls returned the same
receipt. Exactly one release row, business event and retained file were
present. The test supplies synthetic authorization and checked review inputs
to isolate the transaction, while using real PostgreSQL locks and temporary
filesystem storage. The focused content and concurrency selection passed
17 tests with zero skips. The disposable PostgreSQL 16 instance was removed.

Release preparation refuses known unsupported dependencies before copying.
The guard rejects external and embed activity formats, non-text dynamic
editor nodes or marks, chapter thumbnails, and hosted videos with caption
or thumbnail metadata. Native course editing remains available. The
focused regression exercises representative embed, YouTube, caption and
editor image cases. Assignment-specific embedded dependencies and full
snapshot rendering still need verification before publication.

Migration `7be224c591a6` persists each new release's policy version IDs,
UUIDs, version numbers and approved body digests with its retention category.
The private HTTP regression and the PostgreSQL release race confirm the
binding matches the input policies. Older release rows keep a null binding.
Seventeen focused tests passed with no skips. The disposable PostgreSQL 16
database rehearsed empty downgrade and re-upgrade. Once the race saved its
release, the downgrade was refused and retained the binding at the current
head. The container was removed.

Migration `b043fde62c98` adds the internal designation field
`course.studytomy_managed`. Existing catalog rows default to false. Both
native course update and a PostgreSQL check reject attempts to make a
designated course public or published. The designation is absent from the
native write schema and excluded from the content fingerprint. No
application route sets it yet. The native reader still needs a managed
scope check because unpublished courses may be accessible to group members.
Forty-two focused tests passed with no skips. Fresh PostgreSQL bootstrap
and the empty downgrade/re-upgrade passed. Disposable PostgreSQL 16 was
removed. The broader lint selection still flags existing SQLAlchemy
boolean-comparison style elsewhere in the native course service.

The shared RBAC course and chapter read path now denies managed drafts to
regular members regardless of group grants and to organization and platform
API tokens. Authenticated course staff retain access through existing
membership, authorship, administration and MFA checks. The fixture verifies
the group-grant branch is unreachable for these managed reads. The final
RBAC and native course selection passed 131 tests with 40 existing async
mock warnings. An earlier check showed direct activity RBAC still expects a
legacy chapter ID that the current Activity model lacks. Direct activity,
list, content-file and other alternate paths need independent verification
before the designation can be exposed.

Direct RBAC reads of managed activities now use the current owning
`Activity.course_id` before any platform-token shortcut. The regression
verifies that the managed course denies member and token reads at the
activity boundary while a staff session can read the lesson. The platform
token retains its existing behavior for unmanaged activities. Native
activity renderers continue to apply their own chapter and activity locks.
Course lists and generic file delivery still need separate coverage.

The local and object-storage content handlers now apply the managed course
staff gate before serving course metadata, thumbnails or activity paths.
Managed metadata and thumbnails use the private response path, and a forged
organization prefix is denied. The regression also checks uppercase course
identifiers for Windows filesystem aliases and keeps unmanaged thumbnails
public under the existing rules. Ninety-nine focused content, assignment and
access-hook tests passed. The object-storage test exercises authorization
before storage access. Live S3 delivery and native course listings remain
open.

The manual bank evidence ledger now retains the verified document digest,
external and evidence references, settlement time, amount, currency and
verifier for one exact attempt. PostgreSQL requires that matching retained
row before inserting a manual confirmed movement and rejects reuse of
the external reference. Nine focused tests passed on fresh disposable
PostgreSQL 16. The test inserted synthetic evidence directly. The staff
evidence intake and finance confirmation services remain open.

The internal evidence service now records an assigned finance operator's
attestation against the exact manual attempt, under the current D-19
decision. Identical evidence can replay. Conflicting references, future
settlement and out-of-scope operators are denied. Nine focused tests
passed with disposable PostgreSQL 16, including assigned and unassigned
actors and no money movement after attestation. Bank evidence is reviewed
by staff. This service does not contact a bank or confirm collection.

The internal confirmation service derives the confirmed amount and
currency from retained manual evidence and checks the accepted order
and current D-19 `staff_authority.manual_collection_limits_minor` for
that currency. A retry returns the existing movement. A second or
mismatched collection returns a remedy-review conflict and the evidence
remains available for review. The signed synthetic mixed run passed nine
tests with disposable PostgreSQL enabled, including mismatch and exact
confirmation replay. Confirmation does not allocate learner access.

Mismatched or additional collections now return a retained finance
remedy case while preserving the original bank evidence. The case and
audit event share the caller's transaction, and retries return the same
case. PostgreSQL serializes movement and remedy insertion for the same
attempt and rejects a movement while a remedy exists. The parent upgrade
and nine focused tests passed on disposable PostgreSQL 16. The fixture
verified the mismatch case and rejection of a bypassing movement.
Late-capacity classification and a remedy-resolution workflow remain open.

Confirmation now checks that the exact accepted offer is still open in
D-17. A finite offer also requires settlement within the original hold
window and consumption before the hold expires. Unavailable capacity or
closed sales retain a remedy case rather than a confirmed movement.
Successful confirmation consumes the hold in the same transaction.
The signed finite-offer journey and separate capacity race passed ten
focused tests on disposable PostgreSQL 16. The signed test covered an
out-of-window bank settlement and a valid one-time hold consumption.

Staff can use the mounted manual attempt, bank evidence and confirmation
routes under `/api/v1/studytomy/orgs/{org_uuid}/collections`. They reuse
session, organization, finance assignment and policy checks. Successful
responses carry only an attempt reference and status. A retained remedy
returns HTTP 202 after committing the case. The signed PostgreSQL fixture
verifies purchaser denial, assigned staff registration and attestation,
additional-collection remedy and confirmed replay. Ten focused tests
passed, and the disposable database was removed.

The account's My learning page passed a local Chromium journey after
real synthetic sign-in against the disposable PostgreSQL and Redis
portal. The browser used intercepted learning responses to verify the
390×844 keyboard path, one explicit first-use POST, focus transfer and
absence of horizontal overflow. At 1440×900 it verified return to the
lesson list and removal of lesson content after a simulated withdrawal.
The first full script removed its route handler while resizing. The
corrected script passed. Browser fixtures, local servers, and database
containers were stopped. The signed API fixture supplies separate
real-endpoint evidence.

The mounted finance allocation action now records grants from a
confirmed manual attempt. It returns only the attempt reference and
grant count. The signed disposable PostgreSQL journey verified purchaser
denial, unconfirmed-attempt denial, assigned finance allocation and
replay, then read the exact release as the learner following consent.
All nine focused tests passed.

Assigned finance staff can record evidence of an externally resolved
manual remedy. D-07 must list the outcome in
`withdrawal_remedies.manual_collection_outcomes`, and D-19 must enable
`staff_authority.manual_remedy_resolution_enabled`. D-19 can also require
an operator independent of the evidence attester and case opener.
Resolution is append-only, replays exactly and does not execute a refund,
reopen capacity or issue a grant. The signed disposable PostgreSQL run
passed nine tests, including scope-gated resolution, unapproved outcome
denial, exact replay and conflicting evidence rejection.

The finance review endpoint lists only unresolved manual exceptions for
which the authenticated operator retains exact collection scope. It
returns a customer reference, attempt reference, reason and time, with
private cursor pagination. Signed PostgreSQL verification covered staff
pagination, an empty purchaser view and disappearance after resolution.
Nine focused tests passed with disposable PostgreSQL 16 enabled.

TASK-023 now has a private purchaser receipt projection for accepted
orders. It derives collection status from retained attempts and money
movements, displays the accepted per-allocation price and access terms,
and reports unresolved finance review independently of collection.
Current D-05, D-12 and D-18 approval and the D-12
`information_uses.purchase_receipt` scope `order_summary` are required.
No learner IDs, academic data or bank evidence are returned. The signed
PostgreSQL fixture verified before-collection and confirmed states and
404 for another account. Nine focused tests passed. Versioned receipt
documents, credit confirmations and purchase-management UI remain open.

The purchaser's private purchase list now returns accepted-order summary
references in reverse acceptance order with an actor-bound cursor.
Unaccepted drafts and other purchasers' orders are excluded. The signed
disposable PostgreSQL run passed nine checks, including the cursor
boundary, another account's empty list and denial of its foreign cursor.

Confirmed manual collection can now fund named order-line grants through
the internal allocation service. It rechecks assigned finance authority,
current learner/purchaser authority, consumed finite capacity and the
exact retained releases. D-05 must approve the exact offer version in
`learner_allocation.order_line_offer_versions` and exact access terms in
`access_periods`. The after-purchase window starts at the retained bank
settlement timestamp. The grant and audit writes share the caller's
transaction, and replay preserves the original grant. Ten focused tests
passed with disposable PostgreSQL 16, including denial for an unconfirmed
attempt and issuance to the exact learner and release.

The first learner release read returns only the retained course name and
exact course and release identifiers. It requires current organization
membership, independent learner eligibility, an active grant for that
exact release, and D-12 consent for the `course_release` learning-terms
scope. The signed PostgreSQL journey passed nine tests, covering 404
before consent, approved self-read, finance-user denial and 404 after
withdrawal. The overview does not return lesson content or start a
first-use clock. Dependent access and lesson delivery remain open.

The private lesson route now returns plain text from a retained published
page when the exact pinned D-09 decision lists the activity under
`release_schedule.ungated_text_activities` for the course. Restricted or
dripped chapters, unsupported blocks and all assessment activities are
excluded. The signed PostgreSQL fixture verified retained text, denial
for an assessment even when listed, and denial after consent withdrawal.
Nine focused tests passed on disposable PostgreSQL 16. Rich content,
progression conditions, media and first-use activation remain open.

The explicit lesson `/open` action now starts a first-use window only
after the exact lesson, pinned approval, learner eligibility and consent
are validated. It commits the PostgreSQL grant claim before returning
text. A GET does not start a clock. The signed fixture verified denial
leaves the start unset, simultaneous open requests share one recorded
window, and withdrawal denies both GET and POST. The fixture uses a
synthetic free grant for the first-use case. Twenty-two focused tests
passed on disposable PostgreSQL 16.

The private release lesson index lists only eligible plain-text lesson
identifiers and names in retained navigation order. A valid first-use
grant can list those lessons after consent without reading text or
starting the grant clock. The signed test verified the pending and
started states, assessment exclusion and denial after consent
withdrawal. Nine focused tests passed with disposable PostgreSQL 16.

The current independent learner can discover reviewed managed releases
through a private paginated catalog. It includes active grants and valid
unstarted first-use grants, deduplicates multiple grants for one release,
and returns only verified release names and identifiers. The signed
PostgreSQL fixture checked the granted entry, cursor boundary, pending
grant visibility and denial after consent withdrawal. Nine focused tests
passed, and the disposable database was removed.

Native organization lists, counts and course/lesson search now omit managed
drafts for group-only viewers. A current course author with an editing
authorship may still see the draft, and the organization admin dashboard
keeps its unpublished view. The regression verifies a group member can see
an ordinary draft and loses that listing when it is designated managed. It
also verifies an active reporter cannot find the managed draft and an active
contributor can. The focused native course and search suites passed 144
tests, and the expanded authorship case passed on its own.

Native AI retrieval excludes managed courses from the organization-wide
search scope, including for administrators, and rejects explicit requests
using the current database marker. Native background indexing checks the
owning course before extracting or sending its course or activity content
to an embedding or transcription provider. The retrieval regression seeds
existing managed vectors and verifies that only native content is eligible
for search. All 31 focused RAG access and pipeline tests passed. Managed
content publication, existing embedding retention, and other AI surfaces
require separate verification.

Native learning paths omit managed drafts from course cards and completion
inputs. The path editor rejects new references to a managed draft, the
reverse course-to-path lookup returns 404, and a managed course completion
cannot trigger native path completion. The regression switches an existing
linked draft to managed and checks both learner and administrator views,
the reverse lookup and the native editor. All 64 focused path service,
router and AI-tool tests passed with the existing local fixtures.

Native personal progress omits historical managed trail runs and steps
while keeping their stored rows. Native enrollment and activity
self-completion return 404 for a managed draft, including for course
staff. Twenty focused trail, router and certificate regression tests
passed with local fixtures.

The authenticated Studytomy review API can designate a private unpublished
course using `PUT /orgs/{org_uuid}/courses/{course_uuid}/managed-draft`
under the Studytomy route prefix. It requires the existing exact course
review scope and current policy approval evidence. The organization and
course locks serialize the decision, and the marker and business event
commit together. Repeating the request does not append another event.
The mounted fixture rejected missing policies and public visibility, then
designated a private draft twice and continued through private review and
release. The focused suite passed 32 tests with four PostgreSQL-only
cases skipped. Learner release delivery remains open.

The disposable PostgreSQL 16 rerun at Alembic head `b043fde62c98` passed
all 37 tests with no skips. Two concurrent HTTP designation requests used
independent database sessions and both received 204. The course remained
private and unpublished, and the committed ledger held one designation
event. This concurrency fixture supplies a synthetic scope decision.
The separate mounted SQLite workflow exercises the real course and
policy scope resolver. The PostgreSQL container was removed.

TASK-024 now has a persistence foundation. Migration `9fe43e84ad21`
binds each learner grant to the same organization's exact course release
and deduplicates each source independently. It constrains fixed and
first-use clocks and requires fixed windows for provisional grants. An
empty downgrade and re-upgrade passed on disposable loopback PostgreSQL
16. The migration graph and metadata suite passed nine tests. Issuance,
immutable transitions, atomic first use, and learner reads remain open.

The entitlement service now resolves the union of current grants for a
learner and exact release. A lookup has no side effects by default. A caller
performing an authorized first use can lock the applicable grants and
claim their clocks inside the caller's transaction. Independent PostgreSQL
sessions verified the second request waits for the first and reuses the
recorded window. The expired provisional source did not affect the valid
order-line source, and the final second of access is bounded by an exclusive
end time. Twenty focused tests passed on the mixed suite with disposable
loopback PostgreSQL 16 enabled. The service is not mounted on learner routes.

PostgreSQL now retains course grant history on both bootstrap and the
`9fe43e84ad21` migration path. The guard allows one first-use transition
and one revocation, rejecting changes to the source, release and existing
window or deletion of the row. The PostgreSQL regression verified those
rejections and a successful revocation. All 20 focused tests passed with
the disposable PostgreSQL 16 database enabled. This run used fresh
bootstrap. A separate disposable PostgreSQL 16 rehearsal downgraded the
empty database to `b043fde62c98`, applied `9fe43e84ad21` through Alembic,
and passed the same 20 focused tests with no skips. After those tests
committed two grants, a downgrade was refused. Both rows and revision
`9fe43e84ad21` remained present. The test container was removed.

The commerce persistence helper now serializes creation by organization
and resolves repeats by exact grant source, learner and course. It
returns the original row on an exact replay, including its existing
first-use window, and rejects changed terms with a 409. The helper requires
its caller to verify the source and policies and commit the business event
with issuance. Twenty focused local tests passed with disposable loopback
PostgreSQL 16 enabled. There is no confirmed-collection or approved-free
issuance entry point yet.

Private TASK-017 offer drafts can now reference an exact release of a
managed course. The service verifies the matching approved review,
organization, course, policy binding and digest, then snapshots the title
and digest from retained content. Drafts without a release preserve their
previous canonical terms and request payload shape. The fixture verifies
replacement of client-supplied metadata and denial for an unknown release.
Offer publication and order acceptance remain gated.

Offer readiness now reports a missing reviewed release for any included
course without a pinned UUID and digest. It also reports repeated course
references, which would make a later order line's single grant per course
ambiguous. Staff may retain incomplete draft terms. The focused suite
passed 20 tests with disposable PostgreSQL 16 enabled and no skips.

The staff draft readiness endpoint reports term blockers and current
D-02, D-05, D-18 and D-20 policy blockers for the exact draft version.
It uses the existing scoped offer authorization and current approval
resolver. The response is private and non-cacheable. The mounted SQLite
test distinguishes a policy marked approved without named-owner evidence
from three missing policies and denies an unprivileged organization
member. The focused suite passed 26 tests with seven PostgreSQL cases
deselected. Readiness is informational. The offer remains a draft.

Offer readiness revalidates pinned releases and checks their exact policy
binding against the currently approved D-08, D-09, D-13 and D-16 decisions.
A missing release, changed digest, unavailable approval or changed policy
binding produces a blocker. Historical draft and release records remain
readable to authorized staff. The mounted test covers a missing release.
The retained-release fixture exercises a matching current binding and a
simulated changed binding.

TASK-019 now has finite-capacity reservation and consumption primitives.
They lock the offer before counting live holds and consumed places.
Expired holds stay in history and free capacity. Late confirmation or
confirmation after a sales pause returns a remedy-review conflict.
Eight focused tests passed on fresh disposable PostgreSQL 16 at
`71af29d0c8e4`, including a wait on the last-place race, expiry and
consumption replay. Policy-derived sale state, checkout and remedy
records are separate dependencies and are not wired to these primitives.

The capacity history trigger permits one consumption and rejects changes
to recorded terms, consumption reversal and deletion. The disposable
PostgreSQL 16 rehearsal applied Alembic revision `71af29d0c8e4` from
`9fe43e84ad21` and passed eight focused tests. A subsequent downgrade
was refused with two retained holds. Their rows and the current revision
were preserved, and the container was removed.

Finite offer readiness now requires named current D-04 staffing and D-17
pilot place limits and a D-05 hold duration bound to the exact offer UUID.
The proposed limit must fit under both approved limits. The contract reads
`staffing.offer_place_limits`, `pilot_size.offer_place_limits` and
`learner_allocation.offer_hold_minutes` from the respective decision areas.
There are no default business values. The mounted fixture verifies that
missing approval produces a blocker, and the resolver fixture verifies
synthetic matching terms and denial above the pilot limit. The mixed run
passed ten tests including the disposable PostgreSQL reservation check.

TASK-020 adds private order and named allocation storage in migration
`60e9c124ba73`. Composite keys bind lines to the same tenant, learner,
order and exact offer version. Separate learners can have separate lines
for the same offer. Duplicate allocations and missing offer versions fail
at the database boundary. The disposable PostgreSQL rehearsal applied the
migration from `71af29d0c8e4` and passed 21 focused tests including demo
retention checks. The first synthetic seed omitted required user names,
then passed with those fields supplied. Authority, server quotes and
acceptance are still required before these records can be used at checkout.

The internal order quote now derives price, currency and per-learner
breakdown from the organization's current immutable offer version. It
checks the stored digest, exact D-05 `prices` entry keyed by offer UUID,
and, when discounts are present, the exact D-20 `discounts` entry. It
returns a calculated total without writing an order or initiating
collection. Three focused SQLite tests passed. The quote test verifies
missing approval, a synthetic exact price, stale version and currency
mismatch. The successful case substitutes a synthetic policy resolution.

Internal purchaser authorization now requires current organization
membership and an active named learner. Self-purchase requires independent
control and current learning and purchase decisions. A guardian route
requires current learning eligibility and the exact approved
`purchase_allocation` purpose through the existing authority resolver.
The synthetic fixture covers dependent self-purchase denial and missing
purchase consent, with simulated eligibility and guardian decisions.
No purchaser endpoint or charge is enabled.

The internal named checkout preview resolves each distinct learner UUID
within the offer organization and checks purchaser authority for all
allocations before calculating a quote. Exact customer terms must match
the current D-18 per-offer decision. The preview reports only which of the
already authorized learners have overlapping course access, with no
allocation source or academic detail. Four focused SQLite tests passed.
The new test simulates purchaser and policy decisions and covers denial
of a second learner, missing terms and a two-learner quote.

Checkout preview now returns a confirmation digest binding the
organization, purchaser, offer, exact version and amounts, customer terms,
learner list and overlap notice. The internal confirmation verifier locks
the organization and recomputes the quote using current authority and
policies. Changed terms require fresh confirmation, and overlapping
access requires explicit acknowledgement. Four focused SQLite tests
passed, including changed learner order and a simulated overlap. No
order acceptance or payment is recorded by this verifier.

An internal order draft writer now records the verified quote as one
private reference and one line per named learner, with the originating
offer version and frozen terms. The draft, audit event and completed
idempotency claim share the caller's transaction. A matching retry returns
the original reference, while changed confirmation data under the same
key returns 409. Four focused SQLite tests passed with synthetic policy
and authority fixtures. The writer has no mounted checkout endpoint.

The disposable PostgreSQL 16 run also exercised two independent sessions
using the same draft key. The second waited for the first commit and
replayed its reference. Exactly two named lines and one audit event were
recorded for that reference. This race fixture simulates confirmation;
the existing local fixture exercises the quote verifier. Ten focused
tests passed with no skips, and the container was removed.

PostgreSQL rejects updates and deletions to order drafts and their
named allocation lines. The parent upgrade from `71af29d0c8e4` to
`60e9c124ba73` installed the guards, and ten focused tests passed,
including direct rejection of currency changes, amount changes and line
deletion. The disposable container was removed. An acceptance record
that seals the full line set remains to be implemented.

Private drafts now seal their line set before the event and replay
reference commit. PostgreSQL checks that the lines exist and sum to the
draft total, rejects insertion into a sealed draft, and keeps the one-time
seal immutable. The ten-test mixed run passed on fresh disposable
PostgreSQL 16. The new seal rules were exercised on bootstrap. A draft
seal records a fixed quote and remains separate from order acceptance.

Order acceptance has independent append-only storage for the purchaser,
confirmation digest, policy binding and overlap acknowledgement. PostgreSQL
requires the same purchaser and organization as a sealed draft and permits
one acceptance row per order. The parent upgrade and ten focused tests
passed on disposable PostgreSQL 16. The regression inserted synthetic
acceptance evidence directly and verified wrong-purchaser, duplicate and
deletion rejection. No service creates acceptance records yet.

The draft row now retains the exact confirmation digest from the
verified quote. A sealed draft must have a digest, and PostgreSQL rejects
acceptance with a different digest even from the right purchaser. The
mixed suite passed ten tests after fresh bootstrap on disposable
PostgreSQL 16. The acceptance writer remains open.

The internal writer now accepts a sealed order only after rechecking the
quote, stored allocations, purchaser authority, offer completeness and
current reviewed releases. D-17 must explicitly identify the exact
course offer version at `pilot_size.order_acceptance_offer_versions`,
alongside the other applicable current decisions. Finite offers reserve
capacity in the acceptance and audit transaction. An existing acceptance
can replay without starting collection. Eleven focused tests passed in
the mixed disposable PostgreSQL 16 run. The SQLite acceptance fixture
simulates approved release and authority checks.

Checkout quote, draft and acceptance routes are mounted under
`/api/v1/studytomy/orgs/{org_uuid}/checkout`. They require an authenticated
session, organization membership checks and the applicable current
decision gates. Quotes and draft writes check exact D-17 pilot approval
before returning purchaser data. Draft and acceptance responses expose
only the private customer reference. Ten focused tests passed, including
HTTP rejection of client prices and missing pilot approval with no draft
written. The positive path still needs a signed, approval-backed
integration fixture. No charge or learner-grant route is enabled.

The mounted checkout now has a signed synthetic PostgreSQL journey for
an independent learner. It creates named policy approvals and current
learning and purchase decisions, pins a retained managed release and an
exact pilot offer version, and submits the quote, draft and acceptance
requests through the real authentication dependency. The retained
acceptance matches the sealed quote digest. All nine focused tests
passed on the disposable database with no skips. This verifies checkout
through acceptance for that fixture. Collection and grant issuance
remain separate.

TASK-021 migration `425be90c73a1` adds payment attempts anchored to an
accepted order and immutable money movements anchored to an attempt.
A pending attempt creates no confirmed money. The database deduplicates
external references per organization and rejects edits to recorded
collections. The disposable PostgreSQL parent upgrade and nine focused
tests passed. The test inserts synthetic ledger evidence after checking
that signed checkout wrote no collection attempt. Finance confirmation,
provider verification, remedy review and grant issuance remain open.

Assigned finance staff can now register a pending manual attempt through
the internal service. It requires the existing exact collection scope,
named D-19 approval with `staff_authority.manual_collection_enabled`,
an accepted positive-value order and a different purchaser. A retry
returns the existing attempt. The signed checkout fixture exercised the
real finance scope and named approval, and verified that no movement was
created by registration. Nine focused tests passed with disposable
PostgreSQL 16 enabled. Bank evidence verification and a staff API remain
open.

### TASK-021 / TASK-024 concurrent signed allocation

Two concurrent allocation requests for the same confirmed collection used
signed finance credentials and separate PostgreSQL sessions. Both received
the same one-grant receipt. The database contained one grant for the exact
learner and release, dated from the verified settlement. The same workflow
denied allocation by the purchaser and from an unconfirmed attempt.
Both checkout tests passed with zero failures or skips using the regular
SQLite fixture and a fresh, disposable loopback PostgreSQL 16 database at
`425be90c73a1`. The container was removed. This verifies the manual
collection allocation path with synthetic evidence.

### TASK-023 account purchase and receipt view

The existing Purchases account tab now lists accepted Studytomy orders
from the purchaser-owned API. An order opens an individual receipt with
the recorded amount, collection status, finance review state, terms,
access period and line items. The overview continues to render the
existing LearnHouse payments component. The receipt client partitions
in-memory results by authenticated account, organization and order,
aborts obsolete requests and revalidates when the page becomes visible.

Web TypeScript, scoped ESLint and 723 Bun tests passed. E2E type checking
passed. One Chromium scenario passed with zero failures or skips at
390 × 844. It opened an accepted order by keyboard, checked the payment
and finance-review states, access period and accepted terms, then
reloaded after a simulated authorization withdrawal. The sensitive
receipt text disappeared and the return link opened the empty list.
Sign-in used the local API with disposable loopback PostgreSQL 16
at `425be90c73a1` and Redis 7. Purchase and receipt responses were
intercepted. The servers and containers were stopped, and generated
browser authentication fixtures were removed. A browser run against
the real receipt endpoints remains open.

The commerce router now reuses the adaptive router's private cache
policy for HTTP errors. Focused tests verified private no-store headers
on missing policy and wrong-purchaser responses while retaining the
existing 409 and 404 results. All 13 checkout, adaptive availability and
signed adaptive workflow tests passed with no failures or skips. The
PostgreSQL cases used disposable loopback pgvector/PostgreSQL 16 at
`425be90c73a1`. The container was removed.

### TASK-022 staff collection review workspace

Signed-in operators can open `/studytomy/finance` on their organization
host without a dashboard administrator role. The page reads only cases
returned under the existing per-collection finance scope. It shows the
reason, order reference and date, and lets assigned operators attest an
externally completed outcome with evidence reference and digest. The
current policy checks, independent reviewer rule and immutable resolution
stay in the API. Recording an outcome does not initiate a transfer.

Web and E2E TypeScript and scoped ESLint passed. Local Chromium passed
two scenarios at 390 × 844. The first used intercepted synthetic case
responses to verify keyboard opening, policy rejection, draft preservation,
and refresh after recording an outcome. Its initial run failed on an
exact label locator; the accessibility tree showed the expected named
combobox, and the corrected locator passed. The second used the real
queue API and verified a private no-store empty response for an unassigned
member of a fresh tenant. Sign-in and the real queue used disposable
loopback PostgreSQL 16 at `425be90c73a1` and Redis 7. Servers, containers
and generated browser authentication fixtures were removed. An assigned
operator's browser write through the real API remains open.

### TASK-024 explicitly approved zero-price orders

A zero-price quote requires the exact offer and version in
D-05 `learner_allocation.free_order_offer_versions`, in addition to the
approved price, pilot, buyer authority and checkout terms. Acceptance and
grant issuance share a transaction. For a limited offer the transaction
consumes the reserved place before issuing access. The grant references the
accepted order line and its reviewed release. An after-purchase window starts
at the retained acceptance time. Free orders create no payment attempt or
money movement. The confirmed-payment path retains its settlement-time basis.

Six checkout, order-integrity and capacity tests passed with no failures
or skips. Signed synthetic cases covered missing free approval, approved
free acceptance and replay, and the existing paid workflow. PostgreSQL
cases used disposable loopback PostgreSQL 16 at `425be90c73a1`; the
container was removed. Sponsorship, provisional grants and actual
business approval remain open.

The signed PostgreSQL rollback scenario withholds D-07 until after the
first acceptance attempt. The request returns 409 after the draft is
sealed, and an independent database session confirms there is no accepted
order, capacity hold or grant. Adding the synthetic named approval allows
the same draft to succeed and replay with one consumed place and one grant.
Six checkout and capacity tests passed, plus four order-quote and migration
graph checks. The loopback PostgreSQL 16 test container was removed.

The independent-grant PostgreSQL test also verifies that a paid grant and
a fixed-period free grant can be active for the same learner and release.
Revoking the paid grant leaves the free grant active in a separate
database session. At the free grant's exact end time no grant is active,
even though the expired provisional grant and the revoked paid
grant remain in history. Twenty content evidence and existing contract
tests passed with zero failures or skips. The disposable loopback
PostgreSQL 16 container was removed.

### TASK-027 allocation-use evidence

With current D-12 `information_uses.allocation_correction` approval
covering `first_content_delivery`, the managed lesson GET and open paths
retain one first-delivery event per authorizing order grant. The event
contains the grant reference, actor and policy version. It contains no
lesson text, answers or assessment result. Authorization, first-use
claim where applicable, and event retention share the organization lock.
An index read or invalid lesson request creates no event. The event
records that the server authorized a lesson response. It does not infer
learner completion or mastery from that response.

The signed PostgreSQL fixture confirmed that an invalid lesson left no
event and two concurrent valid GETs returned the lesson and retained
one event for the exact grant. Eighteen checkout and content-evidence
tests passed with zero failures or skips. Scoped Ruff passed, and the
disposable loopback PostgreSQL 16 container was removed. A missing event
does not establish unused access for a grant that predates approved
evidence coverage. Correction decisions and replacement lineage remain
open.

Grant issuance now pins the current D-12 version in its immutable
issuance event only when that version explicitly approves first-delivery
retention for correction review. The reader uses the same policy resolver.
The signed paid checkout matched the issuance and first-delivery policy
references. Signed free fixtures verified issuance both with and without
tracking approval. All 18 checkout and content-evidence tests passed on
their regular fixtures and disposable loopback PostgreSQL 16 at
`425be90c73a1` with zero failures or skips. The container was removed.
The issuance reference shows which policy covered the grant at creation.
A correction decision still needs evidence covering intervening policy
changes and the approved substantive-use rule.

Both signed free-order fixtures accepted learning consent and retrieved
the reviewed lesson via open and GET. Only the fixture with the explicit
D-12 correction tracking scope retained a delivery event. Five checkout
tests passed with zero failures or skips. The signed cases used disposable
loopback PostgreSQL 16, removed after the run.

### TASK-028 retained progress protection

Native course and activity removal, admin unenrollment (single and bulk),
activity uncompletion and course progress reset now require reviewed
correction for a managed course. The services inspect the current course
under a row lock before removing retained progress. The shared fixture
confirmed that all six actions returned 409 and preserved the completed
step and enrollment. The ordinary course fixture still reset its step.
The first run detected that the synthetic managed course was still public;
the fixture was corrected to satisfy the existing private-course
constraint. All 212 focused native admin and trail tests then passed
using their regular SQLite or mock fixtures.

The PostgreSQL race primes a resetter session with the unmanaged course,
then lets a separate transaction mark it managed while holding the
course row lock. The reset waits, refreshes the course after commit and
returns 409. Another session confirms the completed step and enrollment
still exist. An initial fixture insert omitted the explicit
`open_to_contributors` flag and failed before the race. The corrected
run passed one PostgreSQL contention case and four SQLite reset cases
with 151 deselected. The disposable loopback PostgreSQL 16 container was
removed.

Managed native assignments now stay pending when a required response is
missing, server grading is unavailable, or a subjective response lacks a
valid manual mark. A pending evaluation leaves the stored aggregate score alone
and skips the grade audit event and graded webhook. The regression covers
an absent response, an unreviewed file with a stored score, an out-of-range
manual mark and a valid educator mark. All 172 focused native assignment
tests passed on SQLite. The adjacent adaptive curriculum suite passed
both cases, including its PostgreSQL round trip and concurrency checks
on disposable loopback PostgreSQL at migration head `4a9f72c6e801`.
The managed-grade regression itself ran on SQLite. Frozen aggregate
result revisions, simultaneous markers and reviewed award issuance
remain separate acceptance work.

The shared AI assignment grading tool rejects managed courses before
setting task scores, marking them as manually reviewed or finalizing an
aggregate. Educators continue to use the native review path. Both managed
denial cases passed with the AI and manual-grading suites (84 SQLite tests).

The native educator marking path locks the managed assignment attempt
before writing a targeted task mark. It requires a handed-in attempt,
enforces task score bounds and retains the learner's stored response
when the existing educator client echoes response data alongside a mark.
This preserves the input for future immutable aggregate result revisions.
An explicit educator task score now appends an attributed business
event in the same transaction as the mark. The event binds the score,
prior manual score and task maximum to the native attempt round and
task submission. Raw learner responses and feedback stay out of the
business event. This is task-level score history. Aggregate assessment
revisions and rubric binding remain open.

Managed graded hand-in now records the native grading contract digest
and evaluator contract version atomically, keyed to the native attempt
round. The digest covers task identities, score limits, scoring
definitions, grade type and passing threshold. The immutable event
holds no answer key or learner response. At grade time, managed
finalization requires the contract for that exact round and the current
scoring definition must match it. A missing or changed contract
returns an awaiting-review result before any task or aggregate grade
is written. Earlier attempts without a retained contract also require
review. For managed rounds with reviewed adaptive assignment mappings,
the existing exposure transaction now records the contract at issuance.
Hand-in reuses that record even if the scoring definition changed,
leaving the changed definition to be flagged at finalization. Managed
assignments outside the adaptive exposure path reserve a pending round
and freeze the contract on the first authorized learner task read.
Single-task and task-list reads share that rule. Direct hand-in without
a prior task read still records the contract at hand-in.
Managed answer saves reserve or lock the native round before writing a
task response and recheck its status under the lock. A direct first
save also binds the grading contract, and the submitted round rejects
late edits. A PostgreSQL regression ran both native write services
in separate sessions and observed the answer-save connection blocked
by hand-in. After the hand-in committed, the save returned 403 and
preserved the answer. Both focused PostgreSQL tests passed. The test
stubs access and external side effects and exercises the real
database state transition and answer write guard.
Managed attempts and task responses are protected from native raw
deletion. The native aggregate update endpoint also rejects managed
changes to score or status. Educator grading and controlled retry
use their existing paths. A managed rejection or adjustment with
reviewed disposition still needs the dedicated workflow.
Native assignment, task, activity and course deletion checks now
protect sources with managed attempts or saved responses. Managed
issuance, answer saves and hand-in use the same organization lock
as the deletion check. The guarded deletion paths can still remove
managed draft sources that have no assessment state.
The private assessment review evidence endpoint scopes one managed
submission to its organization, course, assignment and learner. It
requires both learner and course moderation scopes and current D-04,
D-08 and D-15 decisions. It excludes educators who recorded a mark
or finalized a result for the round. The response contains retained
result metadata only. A signed-in learner can now request review
of their current finalized result. The intake locks the attempt,
records the result and current approval references, and returns the
same receipt on replay. It accepts no free-text evidence and leaves
the grade unchanged. An independent moderator can record a
confirmation for that exact result. The learner can retrieve the
private receipt and confirmation status. A confirmation appends a
review outcome and preserves the original grade. A separate
moderated correction accepts an explicit score within the frozen
maximum, recomputes the pass decision, and commits a linked
revision with the native aggregate and appeal outcome. Native
regrading cannot overwrite the moderated result or change its
underlying task marks through the native marking endpoint. An independent
moderator can reopen the current confirmed decision by its exact
identifier. The reopened receipt preserves the preceding outcome
and must be referenced when deciding again. A corrected result
requires its own appeal. The native learner grade dialog now loads
appeal availability and the latest owned receipt when opened. It
offers review for the exact current result and refreshes receipt
status without browser persistence. English and Arabic copy cover
the waiting, confirmed, corrected and reopened outcomes. Task-level
corrections, browser journey verification and outbound communications
remain open.
The appeal panel can reload a corrected score without leaving the
grade dialog. A web DOM regression verifies the original result ID
submitted, the announced receipt state, and the new review option
after the corrected result loads. The server reports new-request
availability separately from the retained receipt, so an expired
approval can close intake while the learner still sees the
existing decision.
An assigned assessment moderator can record a deadline extension
for one open managed attempt using current D-04, D-08 and D-15
approvals. The record identifies the reviewer, the effective period,
and a bounded reason code. The extension is checked on task
issuance, answer saves, uploads and hand-in. The learner's
assignment detail can show the adjusted deadline without changing
the course-wide deadline or the attempt count. Format adjustments
and retry grants remain separate work.
Managed answer save and hand-in check the deadline after locking
the attempt, so time spent waiting does not extend the approved
window. The PostgreSQL regression grants the adjustment over signed
HTTP and exercises the native detail, answer save and hand-in
services with their unrelated access and side effects stubbed.
New managed graded hand-ins append a round-bound digest of submitted
task responses in the same transaction. Grading checks the digest
before scoring. The event retains no raw responses. Earlier rounds
without a digest retain their existing grading behavior. A digest
verifies the private response archive used for managed retry.
Native retry now requires an exact digest match and an effective
D-13 decision that specifies `managed_assessment_archive_days`.
It saves the private payload before clearing the live rows in the
same transaction. The database rejects overwrites and early
deletion. This adds no approved retention value. Independent
moderators with both course and learner scopes can read a named
prior attempt through the private archive endpoint when current
D-13 record-access policy explicitly permits it. Reads check the
archive expiry and digest and record actor attribution without
copying responses into the event log. Expired archive removal
requires explicit D-13 removal approval and two assigned privacy
reviewers for the same round. The executor rechecks the stored
deadline, archive digest and open appeals before removal. The
audit event retains no responses. Special moderator retry grants
remain open.
Moderators can select an earlier attempt when reviewing an appeal
that was filed before a retry. Historical corrections create a
linked result revision for the selected attempt and leave the
current attempt's grade and status untouched.
A signed HTTP test on disposable PostgreSQL exercised three
denials with the real session, scope and policy checks. It rejected
a delegated session, withheld the resource when learner scope was
missing, and returned POLICY_NOT_APPROVED when both scopes existed
but the required policies were absent. The policy error was marked
private and no-store. With synthetic named-owner approvals and a
finalized result, the same HTTP test verified the exact private
response fields and no-store header. After recording the moderator
as a marker for that round, the route returned 404 again.

Managed finalization takes the native attempt row lock and records the
aggregate result alongside the native grade projection. A changed
score or task-score breakdown appends a revision linked to its
predecessor. Exact score replay reuses the existing revision. The
record retains actor attribution, the contract digest, score, maximum,
threshold and pass decision. On disposable loopback PostgreSQL migrated
to `4a9f72c6e801`, the concurrent-finalization regression observed the
second backend waiting on the first transaction via
`pg_blocking_pids`. Both finalizers returned 100 points and the native
grade committed alongside exactly one result revision. The focused
PostgreSQL case passed. Feedback revision storage and concurrent
submission of different educator task marks remain open.

The dedicated managed grade-read endpoint requires a finalized result
for the current round. It renders the latest recorded aggregate using
the retained score, maximum, grading type and passing threshold.
Task breakdown appears only while the live definition and task scores
match the finalized record. An edited task or an unfinalized mark does
not change the displayed aggregate. The paginated assignment
submissions list uses the same rendering rules and fetches current
round results in one query for the page. A managed row without a
verifiable result is presented as awaiting review. The focused SQLite
suites passed all 158 cases. The disposable PostgreSQL concurrency
case also verified that the batch query returns the single committed
result. The shared AI/MCP assignment tools withhold managed submission
details, learner answers, grade lists and submission counts. The
native educator review path continues to serve these records under
its existing permissions. The shared learner-insight tools also reject
managed course rosters, course-specific progress and course or
assignment statistics. Cross-course progress and code attempts
exclude managed courses. Until the organization-level activity
providers can isolate course scope, an organization with managed
courses omits cross-course timelines and engagement aggregates.

Managed native assignments require direct sessions at the shared
assignment authorization boundary. Tokens pass the ordinary organization
and rights checks before the managed-course restriction applies. A
regression covers assignment and task reads, submission lists, grade
reads, answer saves, hand-in and educator finalization. The learner
answer and attempt stay unchanged. Native token tests also cover
unmanaged authoring, reads, and on-behalf submissions.

The learner-owned access-history API returns one effective window for an
exact reviewed release, including an expired or revoked window when
there is no current one. The read checks current learning consent,
current D-12 `learning_access_history.access_window` authority and the
release's current D-13 retention version. It returns dates and the
retained course name without lesson bodies or payment references. Five
checkout tests passed on regular fixtures and disposable loopback
PostgreSQL 16 at `425be90c73a1`. The signed test verified active and
expired access, denial to a different account, and denial after consent
withdrawal. Scoped Ruff passed and the container was removed. Discovery
of expired releases is now available through a paginated learner-owned
catalog under the same consent and D-12/D-13 checks. The signed test
confirmed the expired release appears in history while the active
catalog is empty, the cursor advances, and a different account or
withdrawn consent cannot list it. Five checkout tests passed with no
failures or skips, using disposable loopback PostgreSQL 16 for the
signed cases. The container was removed.

My Learning now includes a separate Access history view for authorized
retained releases. Selecting a release shows its effective status, access
basis and window in UTC. This view uses the history endpoints exclusively.
Existing session cleanup and focus handling apply to history navigation.
Web and E2E TypeScript, scoped ESLint, whitespace checks and 723 Bun tests
passed. The mobile Chromium scenario passed at 390 × 844 with zero
failures or skips. It opened history by keyboard from an empty active
catalog, verified focus, expired status, access basis and end timestamp,
and returned to the retained list. Sign-in used the real local API on
disposable loopback PostgreSQL 16 at `425be90c73a1` and Redis 7. The
catalog and history responses were intercepted. The test servers,
containers and generated browser authentication fixtures were removed.
A browser run against real history endpoints and separate record or
award disclosure remain open.

The signed free-order checks now vary first-delivery tracking and access
history approval independently. With only tracking approval the
learner can read the current lesson but receives private no-store 404s
from both history endpoints. With history approval and tracking absent,
both history reads succeed and the lesson creates no delivery event.
Five checkout tests passed with zero failures or skips. The disposable
loopback PostgreSQL 16 container was removed.

### TASK-029 refund reservation storage

Revision `c0f63d7b29a4` adds immutable reservation records with an exact
confirmed-movement tenant, order and currency binding. Each row records
the amount, request reference, reason, requester, approver and two
policy version references. An empty rollback removes the new schema.
A populated rollback is guarded to preserve reservations.

Fresh disposable PostgreSQL 16 bootstrap passed 14 checkout, contract
and migration-graph tests. On a second disposable database Alembic
rehearsed `c0f63d7b29a4 → 425be90c73a1 → c0f63d7b29a4` while the new
ledger was empty. The signed paid fixture then passed on that migrated
schema with four cases deselected. Both paths rejected wrong currency,
duplicate references and attempts to rewrite or delete a reservation.
Both containers were removed.

The staff reservation endpoint now checks the existing exact-order
refund capability, documented delegation and requester exclusion. It
requires current D-07 reason approval and D-19 enablement, currency
and amount limits. Under the collection lock it subtracts retained
reservations from the confirmed manual collection. One of two competing
reservations was refused, a retry replayed the same receipt, and a
different amount on that reference returned a conflict. The API
rejects alternate destination fields and returns only a reserved
status. Twenty-four focused checkout and authorization tests passed
with zero failures or skips, including signed disposable PostgreSQL
tests at `c0f63d7b29a4`. The container was removed. The endpoint uses
a staff-supplied request reference. The purchaser request workflow,
separate request evidence verification, chargeback adjustments,
release of unused reservations, dual approval and settlement remain
open.

Refund authorization now checks the exact order assignment before
looking up confirmed collection evidence. The signed fixture compares
the purchaser's responses for real and unknown references, verifies a
finance operator has no access outside their order assignment, and
rechecks the competing reservation invariant. All 24 checkout and
authorization tests passed with zero failures or skips. Scoped Ruff
passed and the disposable loopback PostgreSQL 16 container was removed.

Purchasers can now submit a refund request against their own confirmed
manual order. The immutable request stores a digest binding the exact
order, collection, purchaser, amount, currency and reason. An identical
retry returns the same request receipt. A new staff reservation requires
that request and cannot change its terms. The signed test rejected a
staff request on another account's order, an unrequested reservation and
a changed amount, then verified the existing concurrent balance guard.
Twenty-four checkout and authorization tests passed with zero failures
or skips. Scoped Ruff passed and the disposable loopback PostgreSQL 16
container was removed.

An exact-order staff detail endpoint now validates the retained request
digest and projects only the requested amount, currency, reason code,
opaque references and requested/reserved status. Purchaser identity and
bank references stay server-side. Newly recorded requests retain the
validated structured fields. Older digest-only fixture events remain
unreadable through the detail endpoint. The signed suite verified access
denial, exact request values and the status change after reservation.
Twenty-four tests passed with zero failures or skips and the disposable
PostgreSQL container was removed. Scoped Ruff passed for the refund
paths. The business-event file retains existing type-style lint findings.
The finance workspace now accepts the order and refund request references,
shows the retained amount and reason, and submits those unchanged terms.
It clears loaded details on a hidden page or an account change. A successful
reservation displays that money return is pending. Web and E2E TypeScript,
scoped ESLint and whitespace checks passed. The new Chromium scenario
passed at 390 × 844 as part of the three-case Chromium finance suite.
It verified the exact retained reservation payload, a single submission,
and the pending money-return message. This case intercepted request and
reservation responses. The signed API fixture separately covers the real
staff action. Browser sign-in and the real empty finance queue used
disposable loopback PostgreSQL 16 at `c0f63d7b29a4` and Redis 7. The
test servers, containers and generated sign-in fixtures were removed.
Dual approval, chargeback adjustments, purchaser-facing UI and
settlement remain open.

The purchaser refund-options endpoint now returns the policy-approved
reason codes, collection currency and maximum request amount for an
owned order with exactly one confirmed manual collection. The maximum
is an intake ceiling, not an approval or an available refund balance.
Unconfirmed orders, free orders and other accounts cannot
retrieve options. The signed checkout verified these boundaries.
All 24 checkout and authorization tests passed, scoped Ruff passed,
and the disposable loopback PostgreSQL 16 container was removed.

The Purchases receipt now renders an optional refund request form for
confirmed collections with returned policy options. It converts a decimal
amount to exact minor units, restricts reasons to the returned choices
and retains the same opaque reference for retry after an ambiguous
response while the form remains open. A successful request displays the
reference and awaiting-review status. Web and E2E TypeScript and scoped
ESLint passed. The mobile Chromium scenario passed at 390 × 844 with
zero failures or skips. It verified a 503 followed by a retry with the
exact same request payload and reference, then the awaiting-review
receipt. Purchase and refund responses were intercepted. Sign-in used
the local API with disposable PostgreSQL 16 at `c0f63d7b29a4` and Redis 7.
The test services and generated authentication fixtures were removed.
Real-endpoint browser execution remains open.

An owned order now has a private, paginated refund request status API.
It returns only opaque references and requested/reserved status, giving
the receipt a server-side recovery source after reload. The signed test
verified the empty state, newest-first pagination, the reserved status
after approval, wrong-purchaser denial and unknown cursor denial. All
24 checkout and authorization tests passed with zero failures or skips.
Scoped Ruff passed and the disposable loopback PostgreSQL container was
removed.

Purchase receipts now fetch recorded refund requests from the status API
and display the reference and current requested or reserved state. Recorded
requests remain visible when new-request options are unavailable. The
view does not persist financial fields or references in browser storage.
Web and E2E TypeScript and scoped ESLint passed. The updated Chromium
scenario verifies the retained reference and reserved state after reload
with intake unavailable. Browser execution of that update remains open.

The D-19 refund configuration must now explicitly specify whether dual
approval is required. In dual mode the first approval records the
request digest and current policy versions but reserves no funds.
Replaying as the same approver remains pending. A distinct, delegated
operator can reserve funds only after the service rechecks the first
approver's current authority and the remaining collection balance.
The signed PostgreSQL workflow verified same-actor replay, independent
approval, and competing second approvals yielding one reservation.
All 24 checkout and authorization tests passed with zero failures or
skips. Scoped Ruff and ESLint and web and E2E TypeScript passed.
The disposable PostgreSQL container was removed. Browser verification
of the dual status and settlement remain open.

The immutable reservation event now references the exact first-approval
event used. The signed PostgreSQL fixture checked that the two events
identify distinct operators, retain the same policy versions, and link
through the recorded event UUID. All 24 focused tests passed with zero
failures or skips. The disposable loopback PostgreSQL container was
removed.

### TASK-030 refund settlement evidence

Revision `3e8a91f7c062` adds an immutable settlement record keyed to the
original reservation. It retains the external and evidence references,
digest, verifier, policy versions and settlement time. The reservation
remains the source for the tenant, collection, amount and currency.
PostgreSQL rejects returns dated before approval and edits or deletion
of settlement evidence.

Fresh disposable PostgreSQL bootstrap passed 14 tests. A second
disposable instance rehearsed downgrade to `c0f63d7b29a4` and re-upgrade
while empty. The signed paid scenario passed on the migrated schema with
four cases deselected, then a populated downgrade was refused while
preserving the head and settlement row. Scoped Ruff passed. Both
containers were removed.

Assigned staff can now attest external settlement against the exact
reservation. The endpoint verifies the original amount and currency,
effective D-07 and D-19 policy, delegated access, time and unique external
reference. This implementation requires an explicit preserved-access
decision and an explicit independent-verifier setting. It performs no
provider operation. The signed suite exercised unauthorized and
mismatched evidence denial, concurrent exact replay, conflicting
evidence and immutable retention. All 24 focused tests passed with zero
failures or skips, scoped Ruff passed and the PostgreSQL container was
removed. The signed fixture used the explicit non-independent verifier
setting. Independent-verifier acceptance, chargebacks and access-ending
effects remain open.

Purchaser history and staff review now project settled status only when
the reservation has a retained settlement record. The signed test
verified reserved before attestation and settled afterward without
disclosing bank evidence. All 24 checkout and authorization tests passed
with no failures or skips. Scoped Ruff, ESLint and web and E2E TypeScript
passed. The disposable PostgreSQL container was removed. The updated
receipt browser scenario covers both states and awaits execution.

The signed settlement suite now uses a D-19 policy that requires an
independent verifier. Both delegated refund approvers received 403.
A third operator holding the exact order scope and a current financial
delegation verified the return. Both concurrent exact replays returned
the same outcome, and the ledger retained the third operator as
verifier. The previous signed run covered the explicit non-independent
setting. All 24 focused tests passed with zero failures or skips;
scoped Ruff passed and the disposable PostgreSQL container was removed.

An explicit D-07 `end_fully_returned_order_access` rule now ends only
the original order-line grants when verified returns cumulatively match
the full confirmed order amount. Partial returns preserve access. The
settlement transaction retains the exact access effect, and that evidence
prevents subsequent allocation from restoring access for the refunded
order. The signed flow settled 65 and then 60 minor units against a 125
minor-unit collection. The original grant ended only after the second
settlement, while an overlapping independent grant still authorized the
lesson. Reallocation was rejected. All 18 checkout and content-evidence
tests passed with zero failures or skips, scoped Ruff passed and the
disposable PostgreSQL container was removed. Chargeback reconciliation
and provider-managed returns remain open.

### TASK-029 / TASK-030 confirmed chargeback budgeting

Revision `e2a9c571d643` adds an immutable confirmed-debit record scoped
to the original collection, order, organization and currency. Under the
existing collection lock, refund approval now subtracts confirmed
adjustments along with prior reservations. The signed test retained a
synthetic 61-unit chargeback against a 125-unit collection and verified
that a 65-unit reservation was refused. PostgreSQL also rejected attempts
to rewrite or delete the adjustment. The test rolled back the synthetic
adjustment before the existing full-return workflow.

Fresh PostgreSQL bootstrap passed 14 tests. On a separate disposable
database the empty downgrade to `3e8a91f7c062` and re-upgrade passed,
then the signed paid scenario passed with four deselected. Both
containers were removed. Scoped Ruff passed. Authorized chargeback
intake is now available to assigned collection staff under explicit
D-07 preserved-access and D-19 attestation policies. It validates the
original collection, currency, date, amount and external case evidence.
The service retains the debit without moving funds or changing access.
When confirmed debits overlap retained reservations above the collected
total, it returns a review-required flag and records that disposition.
The signed test verified one adjustment across concurrent exact replays,
refusal of changed evidence, and access remaining available through an
independent grant. All 24 focused tests passed with zero failures or
skips. Scoped Ruff passed and the disposable PostgreSQL container was
removed.

Revision `b2c71f4a6089` retains verified full chargeback reversals as
separate immutable records. Assigned finance staff can attest a reversal
only with current explicit D-07 and D-19 permission, exact debit amount,
currency and case, and a verified date and reference. A reversed debit
stops reducing the available balance. The original debit and settlement
history remain intact, and previously ended order grants stay ended.
The signed flow rejected mismatched evidence, retained one reversal
across simultaneous retries and cleared the financial overlap flag.
The reviewed snapshot changed even though the balance again matched.

Fresh PostgreSQL bootstrap passed 15 tests with zero failures or skips.
A separate disposable PostgreSQL database completed the empty
`b2c71f4a6089 → e2a9c571d643 → b2c71f4a6089` round trip and verified the
installed history trigger. Scoped Ruff passed and both containers were
removed. Partial reversals and provider intake remain open.

The signed chargeback-first scenario now verifies that a full reversal
restores only the available balance. An earlier 65-unit refund remains
settled. A 60-unit request denied before reversal then obtains two
approvals and reserves the exact remaining funds, and a subsequent
one-unit request cannot reserve more. Both paid scenarios passed on
disposable PostgreSQL 16 with four deselected. Scoped Ruff passed and
the container was removed.

### TASK-027 scoped correction preflight

The staff preflight requires the exact learner assignment, current
D-03/D-05 and D-12 decisions, and an approved first-content-delivery rule
before evaluating the learner's order-line grants. It returns used when
there is a retained delivery event. An unclaimed, unrevoked first-use
window can establish an unstarted state. A fixed window with no retained
event returns evidence unavailable. The response includes no academic
record or payment data and changes no grant.

The signed PostgreSQL scenario verified evidence unavailable before a
fixed-window lesson read and used after the event, plus denial to the
purchaser and an out-of-scope learner. Twenty-five tests passed with
zero failures or skips. Scoped Ruff passed and the disposable
PostgreSQL container was removed.

The signed first-use variant verified that an unclaimed grant is
reported as unstarted. A regular lesson GET was refused without changing
that result. The first-use open then set the original access clock,
retained the delivery event and changed the review result to used.
Three free checkout scenarios passed on disposable PostgreSQL 16 with
three deselected. Scoped Ruff passed and the container was removed.
The correction transaction requires exact reviewer scope on both learners,
current purchaser authority for the replacement and an approved correction
reason. It revokes the original grant, issues the equivalent replacement
and records the lineage without modifying the original order line or learning
records. It refuses correction after any recorded refund or chargeback on
the collection. The signed checkout scenarios passed on disposable PostgreSQL
(4 passed, 3 deselected), including concurrent correction retries, rejection
of a changed replay and denial of the original learner's content access.
The full checkout module subsequently passed on disposable PostgreSQL
(8 passed, no failures or skips), including the race between correction
and first content open. The test requires exactly one operation to succeed
and checks the corresponding retained allocation state.

The owning purchaser's private receipt displays a corrected marker on the
affected line when current D-12 policy includes `allocation_correction_status`
under `information_uses.purchase_receipt`. The receipt retains the accepted
terms and does not expose learner identifiers, the reviewer's reason or
learning evidence. The marker reports that a correction occurred; it does
not certify that the replacement grant is still active.
The receipt privacy regression retains an actual correction and verifies
that an order-summary-only D-12 policy leaves the receipt line unchanged.
The free checkout selection passed on disposable PostgreSQL (6 passed,
3 deselected).

The family services workspace accepts an exact original learner reference
and order reference. It displays correction controls only when server
preflight returns `unstarted` with approved D-05 reason codes. Reviewers
enter an exact replacement learner reference. The correction endpoint
rechecks staff scope, eligibility, purchaser authority, finance state and
retained usage at submission. Used allocations display the exception-review
requirement. The workspace does not create or approve an exception.

The signed PostgreSQL correction scenarios also verify an eligible learner
within reviewer scope is unavailable for reassignment until the purchaser
has current authority for that exact learner. The refused request leaves
the original grant active and writes no correction event (3 passed,
6 deselected on disposable PostgreSQL).

The signed paid checkout also verifies that a settled partial refund
blocks an otherwise eligible correction. The exact release grant remains
unstarted and unrevoked, and no correction event is written. The complete
checkout module passed on disposable PostgreSQL (10 passed, no skips).

### TASK-028 native progress protection

The existing managed-course guard was exercised against concurrent
designation in disposable PostgreSQL. Admin reset, single and bulk unenrol,
and activity uncompletion, plus learner course and activity removal, each
waited for the course row lock, re-read its managed state and returned 409.
Every case preserved the completed trail run and step (6 passed, no skips).
This verifies those six removal entry points. Retained awards and new-period
reporting still need separate verification.

Course, chapter and individual activity deletion now check native progress
before storage cleanup or row deletion. The course row is locked and the
guard checks only the activities the operation would remove, or all retained
runs and steps for whole-course deletion. The PostgreSQL suite passed 29
tests across progress retention, content evidence and existing contracts.
All nine removal scenarios preserved the completed run and step.

The signed re-entry scenario starts with a synthetic expired grant and a
completed native run. Checkout reports no overlap, and concurrent acceptance
of the new order retains one new grant and one issuance event. The expired
window and original run ID, status and dates remain intact. The free checkout
selection passed on disposable PostgreSQL (7 passed, 4 deselected). This
checks grant separation and history preservation. Period-specific reporting
still needs its own acceptance check.

Access history can show a retained native course completion recorded before
the displayed window. This course-level marker requires the existing learner
and release checks, D-12 `learning_access_history` scope `course_completion`,
and D-13 `record_access.self_course_completion` with a positive
`retention.course_completion_days` value. Only a unique completed run with
an offset-bearing timestamp inside the approved retention window qualifies.
Older naive timestamps are left unclassified. The marker conveys neither an
award nor an assessment earned in the current period.
The checkout module passed all 11 cases on disposable PostgreSQL. The
re-entry case verified absence of a prior-window marker on the expired
agreement, continued denial of lesson content, and the marker on the new
agreement after re-entry. Web TypeScript and scoped Ruff passed.

### TASK-062 native record-operation boundary

The native headless export serializes the ordinary organization roster,
trail data and certificate rows, and native anonymization scrubs the global
account and its tokens. These are not scoped learner case workflows. Both
now refuse protected learner profiles and require reviewed record handling.
Ordinary accounts retain the native operations. The admin regression
selection passed nine tests on the standard SQLite service/router fixtures.
Both native operations now take the same account row lock as learner intake
and read the current profile directly. Two PostgreSQL cases verified that
export and anonymization wait for pending profile creation, then return 409
with the account intact. The disposable database was removed. Learner
self-service intake and reviewed delivery are separate from those native operations.

The private learner-self intake accepts an opaque request UUID and no record
payload. It derives the requester from a direct session and their independent
learner profile. Current D-03, D-12 and D-13 approvals are required, including
D-12 `record_request.self_learning_records` scope and D-13
`record_access.self_learning_record_request` with an approved due-day rule
and a current named privacy owner assignment. It persists the request,
due date and owner with the policy references. Neither paid access nor
learning consent is used as record-request authority. Account Privacy
checks availability without creating a case and shows the control only
after a successful check. It displays the receipt and due date, and retries
within the same view reuse the opaque reference. Staff processing,
reviewed delivery and guardian requests remain open.

Staff can retrieve a known request's receipt only with a direct session,
current privacy retention review authority and exact case scope. The
reviewer must hold the currently named privacy assignment retained on the
request, and cannot be the requester. The response contains receipt dates
and reference only. The focused PostgreSQL re-entry case verified denial
before assignment and successful retrieval after exact case scope was
assigned (1 passed, 10 deselected).

The same reviewer can record one immutable decision, either denial or
approval to prepare records, with an outcome-specific D-13 reason code and
an opaque evidence reference attested by staff. The event binds the exact
intake and current policy versions. An exact retry returns the retained
decision and a changed decision conflicts. The reference has format
validation only. Learners can read their own status by exact receipt
reference when D-12 approves `self_learning_request_status` and the current
D-13 rule enables learner status. The response omits internal reasons,
actor identity and evidence references. Docker PostgreSQL verified the
pending and approved-for-preparation states and refused a foreign read
(1 passed, 10 deselected). Account Privacy now provides a status lookup by
receipt reference independently of current intake availability. It renders
the learner-facing outcome and the pending review due date. Evidence
verification, reviewed delivery and later correction remain open.

The staff queue returns up to 50 pending receipts for the current named
privacy owner. Each row requires an active exact case scope and passes
the shared staff access evaluator. It exposes only receipt metadata and
removes decided cases. The PostgreSQL re-entry case verified the empty
unassigned queue, requester denial, scoped inclusion and removal after
decision (1 passed, 10 deselected). The signed-in workspace at
`/studytomy/privacy-reviews` presents the pending receipts and due dates.
It clears case metadata while hidden and refreshes when visible. Reviewers
can open a pending request and select the outcomes and reasons returned by
the private D-13 decision-options endpoint. The server rechecks policy and
exact case authority at submission. A successful decision refreshes the
queue. A staff navigation link remains open.

Revision `4a9f72c6e801` extends the private evidence metadata store with a
dedicated record-request evidence purpose tied to the intake event and
learner. PostgreSQL rejects cross-tenant references, wrong learners and
rebinding of an existing case file. Existing eligibility and course evidence
retain their scope constraints. Downgrade refuses to discard case evidence
once such rows exist. The staff upload endpoint requires case scope and
explicit D-12 purpose, D-13 retention and D-15 file rules. A decision now
requires a verified attachment for the exact intake and current policies,
rechecks stored bytes, and records the digest. The staff review form uploads
evidence for the selected case, retains the retry key for the selected file,
and fills in the verified reference.

The reviewed delivery API supports a bounded JSON export of the requester’s
retained managed-course completions. Staff preview the current projection
and release its exact digest after a verified preparation decision under
the same policy versions. The learner downloads with a direct session,
current independent authority, current policy versions and an unexpired
delivery window. A changed projection is withheld and needs a new review.
Ambiguous timestamps are excluded and oversized cases require separate
review. The export contains only course references and recording dates.
The staff workspace previews and releases the exact digest by approved
request reference. Account Privacy offers the private JSON download from an
approved case status. The learner download uses POST and commits one handoff
audit event per released digest and recipient before returning the attachment.
Authorized retries return the same receipt. The event records the server
handoff and cannot establish that a browser saved or opened the attachment.
Wider record categories and browser acceptance remain open.

### TASK-063 reviewed case-evidence holds

The current retention inventory includes native trail, assignment and
certificate records, adaptive evidence and reports, payment and adjustment
ledgers, AI chat state, family authority and consent events, private evidence
objects, delivery events and outbox rows. Search derivatives and restored
backups need their own handling before any removal worker is enabled.
Course and account cascades are outside the case-evidence hold scope.

An assigned privacy reviewer can record an exact case-evidence hold when
current D-18 selected-market policy enables holds and D-13 supplies its
reason and review-date bounds. The immutable event records the named actor,
exact intake, reason and review date. Reaching the review date does not
automatically lift the hold. The read-only dry run requires explicit D-18
enablement and a D-13 case retention period and category. It reports file
references and dispositions at a fixed cutoff. The digest binds the current
holds, policy versions and file hashes. A new hold invalidates a previous
manifest even when its cutoff is unchanged. Explicit releases require an
approved D-18 market rule and D-13 release reason. Release appends an event
linked to the exact hold. The dry-run digest retains the complete hold and
release history so releasing a hold cannot restore an older manifest's
digest. An assigned reviewer can approve a nonempty candidate manifest with
an approved D-13 removal reason and explicit D-18 market enablement. Approval
recomputes the dry run under the organization lock and retains the exact
digest and cutoff without removing files. With separate explicit D-18
execution enablement, staff can target one approved case evidence file on
filesystem storage. Execution recomputes the manifest and checks current
holds, the canonical private path and file bytes. It commits an intent
before unlinking, rechecks under lock, and retains an immutable receipt.
The metadata row stays available for reconciliation. With explicit D-18
restore-replay enablement, a scoped reviewer can reapply an exact retained
removal receipt to matching restored filesystem bytes. The original
approval and current holds are rechecked, a mismatched copy is preserved,
and the replay receipt links to the original removal. This requires a
database that retains the removal event. Older database snapshots that
lack the audit event, object-store version deletion, and automated
backup-wide reconciliation remain open.

The staff privacy workspace now presents the case retention preview and
records approved manifests. It shows the cutoff, hold count, dispositions
and digest. Approval uses exactly the displayed cutoff and digest and is
offered only when there is a candidate. The interface does not execute
removal or replay. Completed removals appear beside the original manifest
as audit receipts and are excluded from new approvals. Progress receipts do
not change the eligibility digest, so an existing approval can resume and
its original candidate count can be recovered on replay.

### TASK-045 native award boundary

The shared native certificate issuance path locks the course and refuses
managed awards pending reviewed issuance. Automatic completion still records
the native completion state without issuing a managed certificate. The
public native verification endpoint hides managed awards and certificates
held by protected learners from other users. Existing award records remain
intact. Native automatic revocation leaves managed awards in place. Manual
revocation, template changes with issued managed awards, and managed
course deletion that would cascade into retained awards require review.
A reviewed issuance ledger, correction and withdrawal history, and a
private managed-award projection remain open.

Settlement now also handles an adjustment recorded before the return.
If total verified returns and confirmed chargebacks exceed the original
collection, it retains the return, marks it for review and leaves
access unchanged. Exact replay returns the recorded review disposition.
A signed PostgreSQL variant recorded a 70-unit debit before attesting a
65-unit return against a 125-unit collection and verified one settlement
across concurrent replays and preservation of the existing grant. All
25 focused tests passed with zero failures or skips. Scoped Ruff passed
and the disposable PostgreSQL container was removed. Dispute case
transitions and reviewer resolution of the overlap remain open.

Assigned collection staff can now retrieve verified collection,
reserved, settled and chargeback totals together with the exact ledger
snapshot digest. With explicit D-07 approval they can acknowledge that
snapshot. The acknowledgement leaves the review-required flag and all
financial and access states intact. The signed test rejected a stale
digest and retained one audit event across concurrent acknowledgements.
Twenty-five focused tests passed with zero failures or skips. Scoped
Ruff passed and the disposable PostgreSQL container was removed.

The purchaser receipt now projects the existing finance-review flag
when confirmed chargebacks and refund reservations overlap for any of
the order's collections. The signed scenario closed the unrelated
review items, verified a clear receipt, and then verified that the
debit changed only the review flag. The flag remains set after staff
acknowledgement. All 25 focused tests passed with zero failures or
skips. Scoped Ruff passed and the disposable PostgreSQL container was
removed.
