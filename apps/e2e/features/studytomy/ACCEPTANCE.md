# Studytomy live acceptance map

Source baseline `b7e57929eba1230577fe23767aac162fefccf80d` plus the local
remediation worktree. Status refers to the complete observable scenario in the
[atomic specification](../../../../STUDYTOMY-FAMILY-LEARNING-ATOMIC-IMPLEMENTATION-SPEC.md).
A candidate spec, an intercepted response, and a fixture-created approval are
supporting evidence only. There is no live acceptance pass recorded here.

Use the guarded loopback setup in the [E2E runbook](../../README.md).
Spec numbers below refer to files in [tests](./tests). Where a prerequisite
policy is missing, `BLOCKED` refers to the approved business scenario.
Negative authorization checks can still be executed with synthetic records.

The momentum rehearsal passed on an isolated real stack. Separate Chromium
sessions saved the pinned lab draft, recorded the independent approval and
release, and then exercised learner submission and staff grading. The fixture
prepared native course and assignment records, attachments, and policy
prerequisites. The reviewer was also blocked from publishing before approval, received
HTTP 409 on a stale candidate, and had to upload new evidence and reconfirm
its findings before recording the release. Broader AC-27 verification across
other activity formats remains open. See the
[local verification record](../../../../docs/LOCAL_END_TO_END_TESTING.md#audit-worktree-momentum-verification).

| AC | Status | Actors, fixture, and observable browser action | Candidate or gap |
|---|---|---|---|
| 01 | NOT_RUN | Guardian selects a child and accepts a versioned offer with allocation and collection evidence | 16 checkout uses intercepted API responses |
| 02 | NOT_RUN | Payer opens own receipt and attempts to open the allocated learner’s records | 06 account purchases and 05 account reports cover separate surfaces |
| 03 | NOT_RUN | Guardian allocates two orders to different children and checks both access windows | No combined two-child journey |
| 04 | NOT_RUN | Scoped family reviewer disputes a verified relationship, then guardian refreshes access and pending delivery is checked | 05 account reports covers link revalidation |
| 05 | NOT_RUN | Reviewer ends authority or approves learner transition, then independent learner explicitly shares a bounded record | No combined transition and sharing journey |
| 06 | NOT_RUN | Staff corrects an allocation and verifies other learner evidence remains isolated | API checkout has correction tests. Browser workflow missing |
| 07 | NOT_RUN | Visitor opens a reviewed sample and compares offer details with checkout terms | 16 checkout begins with prepared offers |
| 08 | NOT_RUN | Payer submits a failed or unconfirmed collection and learner checks access is absent | 09 finance review and 16 checkout cover separate surfaces |
| 09 | NOT_RUN | Finance reviewer repeats a late collection and checks a single ledger outcome | 09 finance review has no duplicate collection journey |
| 10 | BLOCKED | Member cancels under approved renewal rules, then checks eligibility for later periods | D-05/D-06/D-07/D-19 and renewal handling |
| 11 | BLOCKED | Learner checks independent overlapping access after a failed renewal | D-05/D-06/D-07/D-19 and renewal handling |
| 12 | NOT_RUN | Payer retries a refund and reviewer checks a single settlement | 09 finance review is supplementary |
| 13 | NOT_RUN | Author changes an offer and payer reopens original purchased terms | 06 account purchases covers receipt revalidation |
| 14 | NOT_RUN | Learner opens a scheduled or prerequisite-bound release and verifies allowed sequence | 10 access history and 14 release media cover narrower access paths |
| 15 | NOT_RUN | Learner completes a lesson then checks independent concept evidence stayed unchanged | 04 adaptive attempt and 21 topic check cover separate views |
| 16 | NOT_RUN | Learner submits assisted or group work and staff checks separate evidence classification | No combined browser journey |
| 17 | NOT_RUN | Learner asks a question with insufficient approved sources and sees no fabricated answer | 19 and 20 Explore Topics cover retrieval boundaries |
| 18 | NOT_RUN | Learner requests help during restricted assessment or provider outage and sees explicit denial | 03 lesson practice and 15 managed context are supplementary |
| 19 | NOT_RUN | Learner uses ungraded practice and staff reconciles graded attempts separately | 17 live lab and 21 live topic check cover separate paths |
| 20 | NOT_RUN | Learner interrupts a submission and staff applies an approved adjustment | 17 live lab covers persistence and manual submission feedback |
| 21 | NOT_RUN | Staff corrects a mark or certificate and learner reopens retained history | 17 live lab covers initial grading |
| 22 | NOT_RUN | Reviewer publishes a reviewed report and authorized guardian opens it | 05 account reports and 06 staff reports cover separate surfaces |
| 23 | NOT_RUN | Reviewer records wrong disclosure or result and verifies correction or incident receipt | No combined journey |
| 24 | NOT_RUN | Learner changes optional reminder choice and checks delivery and completion evidence | No approved delivery journey |
| 25 | NOT_RUN | Learner reports harmful contact and the implicated adult receives no case access | 11 support case is supplementary |
| 26 | BLOCKED | Learner joins approved community and staff reviews a misconduct action | D-11 audience, membership, adult contact and moderation approval |
| 27 | NOT_RUN | Author edits content, separate reviewer checks evidence, and stale or unreviewed publication is rejected | 07 content review, 12 grounded draft, 17 and 21 live specs. Momentum author/reviewer stages now use browser actions. Momentum stale-review rejection passed. PDF authoring and other activity formats are still open. |
| 28 | NOT_RUN | Restricted learner opens Help and submits a complaint with a private receipt | 11 support case has narrower tests |
| 29 | NOT_RUN | Assigned staff records an approved adjustment and the learner verifies the adjusted workflow | Approved adjustment rules and combined browser journey required |
| 30 | NOT_RUN | Learner grants, then revokes optional publicity consent and checks public visibility | No combined journey |
| 31 | NOT_RUN | Learner makes a record request and leaves while required records are retained | No combined browser journey |
| 32 | BLOCKED | Staff attempts launch and sees unresolved evidence and approvals | D-17 and separate launch sign-off |
| 33 | NOT_RUN | Reviewer restores account access and old session can no longer open another learner record | 01 session isolation and 02 draft isolation cover ordinary switching |
| 34 | NOT_RUN | Learner finds safe support and opens the first eligible reviewed activity | 11 support case, 14 release media, and 21 topic check cover separate steps |
