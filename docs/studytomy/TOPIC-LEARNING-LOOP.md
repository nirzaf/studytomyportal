# Topic learning loop

This is the product and acceptance contract, not a release receipt. Use the [implementation status](IMPLEMENTATION-STATUS.md) for current delivery and the [local runbook](../LOCAL_END_TO_END_TESTING.md) for executable evidence. The inspected foundation was local `main` at `594ba9b4d` on 24 November 2026.

## Authoring and approved sources

The storage hierarchy is **Course → ordered Chapter → ordered Activity**. A lesson is a lesson activity, not a second container above activities. One lesson may cover one or more explicit topics; each topic names its learning objective and the approved evidence supporting it. Keep lesson-local ordering deterministic. Do not infer mastery or objectives from the activity title or chapter order.

An authorized administrator or teacher creates the course, chapters and lesson activities, attaches teaching materials, defines topic boundaries and checks, and submits through the applicable review and publication path. A reviewed text PDF alone is enough for the first slice. Images, audio, video and links are optional for authoring and playback. An attachment is not automatically an approved AI source: record its exact authorized revision and a page, section or time locator. In the PDF-first implementation, bind citations to the reviewed PDF hash (SHA-256) and page. Unsupported or empty extraction must not become usable evidence. Preserve tenant, course and lesson permissions and the rights, reviewer and curriculum requirements.

Existing evidence: [activity types](../../apps/api/src/db/courses/activities.py), [chapter ordering](../../apps/api/src/db/courses/course_chapters.py), [lesson/activity ordering](../../apps/api/src/db/courses/chapter_activities.py), [dashboard activity authoring](../../apps/web/components/Objects/Modals/Activities/Create/NewActivity.tsx) and [grounded lesson and quiz drafts](../../apps/api/src/services/studytomy/grounding.py). The explicit grounded draft intake in that snapshot is feature-gated and PDF-only. Generic [editor-block extraction](../../apps/api/src/services/ai/rag/sources/dynamic.py) indexes PDF text and available audio/video transcripts, but image indexing uses metadata rather than visual interpretation. These mechanisms do not prove reviewed multimodal tutoring.

## Learner experience and scoring

1. The learner opens the authorized published lesson version and reaches a topic boundary.
2. Present a short reviewed question aligned to that topic. Score against the reviewed answer or rubric on the server. Correct answers can complete the topic under the approved rule. Ambiguous answers or unsupported rubric formats are not automatically marked correct.
3. For an incorrect answer, choose a different explanation of the same concept with citations to approved evidence from that lesson version. Use a reviewed fallback when the approved context or authorized AI provider cannot support an explanation. Do not fetch unrelated material or claim mastery if evidence is insufficient.
4. Offer a **different**, reviewed equivalent question targeting the same objective. Record the retry separately. Mark topic completion only according to the server's scored result and approved policy; keep the learner in supported practice when further help is needed. Existing LMS activity completion is not proof of topic mastery.

Generated visuals may support the explanation, but should use a small approved catalog of validated text/diagram/chart/interactive components. Reuse reviewed D-16 examples and the existing learning-surface/action journal where supported. Keep inputs, labels, units and data tied to the same objective and authorized source. Reject unsupported component types and unknown actions; do not run generated code or insert raw HTML. A cited text explanation must still be usable without a visual. Advanced widget and multimodal generation are follow-on work rather than prerequisites for the PDF-first loop.

## Learner history and staff scope

Keep a server-owned chronological history scoped to **learner + exact released lesson version + topic**: initial check and score, where the learner struggled, help the server returned and its source references, recheck and score, and the resulting completion state. History uses server-controlled ordering and keeps distinct published versions separate. Idempotent retries do not duplicate events; a client cannot author an outcome or reassign another learner's history.

Allow authorized administrators and the lesson creator to see only learners and courses in their current permission scope and authorized lesson versions. Being an author or knowing a record identifier is not sufficient. Supported new PDF authoring records the authenticated creator on the server. For pre-existing PDFs without verified creator attribution, non-admin teacher history access fails closed. Do not guess or backfill the creator from the last editor, course ownership, or other indirect signals; applicable scoped administrator access remains unchanged. Re-check access on every read, including after reassignment or revocation, and deny unrelated learners, staff and organizations. No new guardian disclosure is implied; guardians continue through the separate approved disclosure policy. Minimize stored content and provider transfers, enforce applicable consent and retention, and do not store raw prompts, private uploads or unnecessary identifiers in the history view. Use synthetic accounts and materials until programme, safeguarding, source rights and deployment approvals exist.

## Acceptance on the same published version

- An authorized educator creates a lesson with a reviewed PDF and publishes it through the applicable approval path; the learner sees that exact release. Verify an unpublished source and a different lesson's source cannot be used.
- In Chrome with a synthetic learner, finish a topic, answer the initial question incorrectly, see a different explanation citing that lesson's evidence, answer a distinct equivalent question correctly, reload, and see the resulting completion. A wrong recheck must not complete the topic.
- Reopen history in a fresh session. The scoped admin and scoped lesson creator see the same ordered events. Deny the unrelated teacher, another learner and a different tenant; confirm revocation takes effect and a newer version cannot overwrite or mix with earlier evidence. Retry a request and check the event count is unchanged.
- Confirm blocked/unavailable AI returns a reviewed fallback or an honest unavailable state and never completes a topic; unapproved visual types or off-scope data never render. Record the tested SHA or exact local diff, fixture roles, feature flags, browser steps, and actual outcomes.

## Delivery boundary

Course authoring, approved releases, grounded PDF drafts, diagnostics, managed guidance, supported practice and reviewed reporting provide reusable foundations. Previously tested slices do not prove this automatic check → reteach → recheck loop or the complete staff-facing chronology. Track in-progress implementations and separate browser verification from source review and local tests. Approved multimodal tutor sources, broader generated interactivity and production rollout still require separate verification.

## Local implementation checkpoint

The managed PDF lesson screen now exposes the reviewed fractions topic check. The server binds each check to current learner authority, the exact released UUID and learner grant, curriculum and reviewed PDF hash. Before publication, reviewers bind the D-16 topic check to the course review candidate’s immutable `release_snapshot_sha256`, not a future release UUID. Release must use the same currently approved D-16 rights version; a changed snapshot or superseded rights approval fails closed. The initial check and recheck use distinct reviewed examples. A wrong recheck leaves the topic needing review. The staff timeline shows the reviewed prompt, result, assistance, explanation and PDF page, and any reviewed visual. It does not expose stored learner responses or infer a misconception from a wrong answer. Topic-check results do not award independent mastery.

### Local acceptance evidence

In a disposable local stack with synthetic accounts and a reviewed fractions PDF, the native APIs created the lesson, independently reviewed and published it, and allocated the exact release to the learner. In signed-in Chrome the learner opened that PDF, answered incorrectly, received cited reteaching and a different recheck, and recovered the saved state after reloading. The scoped lesson creator and administrator could read the timeline; an unrelated identity was denied.

The interruption checks covered both an answer request stopped before reaching the backend and a committed answer whose response was lost. Recovery showed the last server-confirmed state and the committed response did not duplicate evidence. The server did not save unsubmitted text. The later browser-local recovery pilot is described below.

The local verification recorded 43 focused PostgreSQL/API tests and 6 frontend checks passing, plus a passing TypeScript check. These results apply to the tested local source; GitHub Actions for the exact PR commit are separate evidence. This pilot does not establish every course flow, multimodal source grounding, broader generated widgets, or production readiness.

### Repeating the scenario

The checked-in provisioning does not yet reproduce the complete custom synthetic case. To repeat it, use an isolated loopback stack and a fresh synthetic learner episode, with separate configured author, reviewer, learner, scoped administrator, and unrelated identities. Prepare the reviewed PDF, policy and grant through the native authoring and release APIs, then run the learner and staff cases in the [acceptance contract](#acceptance-on-the-same-published-version). Keep all credentials and session state outside Git.

### Recovering an unsubmitted topic-check answer

The managed PDF topic check temporarily saves fraction-like typed text in the same browser's IndexedDB, not a server or another device. Each draft is partitioned by signed-in learner, organization, course and lesson, and tied to the exact released question binding and checkpoint. Reopening or reconnecting restores the text only after a fresh authorized read confirms that checkpoint. Recovery never submits the draft. If a prior submission was accepted but the response was lost, the next read shows the saved server checkpoint instead of the old draft.

A new binding or checkpoint, a confirmed submission, loss of access detected by the topic check, sign-out, or a confirmed account switch prevents restoration of the previous draft when local storage is available. Local cleanup is best-effort if storage is blocked or stalls. Drafts expire 30 minutes after the last edit; expired entries are not restored and are removed when active or on the next topic-check storage use. If browser storage is disabled, closing the tab can still lose unsubmitted text. The feature does not promise recovery from a browser or device crash before storage commits. On a shared device, sign out when finished.

The focused IndexedDB Playwright check uses synthetic checkpoints in a real browser. A separate signed-in Chrome exercise on a disposable, native local API and PostgreSQL stack verified tab close/reopen, offline/reconnect, a real submission advancing to a blank recheck, sign-out clearing a nonempty draft, and isolation from another synthetic account. Storage failure preserves live typing but still makes draft recovery best-effort.
