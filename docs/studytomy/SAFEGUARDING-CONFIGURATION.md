# Safeguarding report configuration

Online intake is disabled by default. Set `LEARNHOUSE_STUDYTOMY_SAFEGUARDING_REPORTS_ENABLED=true` for synthetic local verification. Enable real-user intake only after applicable approvals and end-to-end acceptance. Existing reporters can still check their own case status.

The public contact endpoint reads the current D-04 policy and checks named-owner approval evidence. Set only material approved for public display in these fields of the specified decision values:

- `support_routes.public_safeguarding.contact_instructions`
- `support_routes.public_safeguarding.emergency_guidance`
- `monitored_hours.public_safeguarding`

Keep staff names, private contacts, and internal routing information outside these fields. The endpoint returns only the three public text fields. Missing, invalid, expired, or unapproved guidance is unavailable.

Authenticated reports require current safeguarding policy and organization membership. Paid lesson access is independent of reporting. Configure the lead and backup using the approved D-04 `staffing.safeguarding` value with `lead_owner_assignment_uuid` and `backup_owner_assignment_uuid`. Both must refer to distinct active safeguarding assignments in the organization. The reviewer still needs the safeguarding capability and independent case authorization.

D-13 must specify a `safeguarding_evidence_retention_category` under retention. Staff file attachments also use approved D-15 file formats and limits. Keep real-user reporting gated pending the required approvals and end-to-end acceptance.

The staff triage workspace is at `/studytomy/cases` within the organization. Designated reviewers can see unassigned case references and record exclusions after independent verification. The verification reference is an opaque UUID for the approved verification record held outside this workspace. Keep identifying details and narratives out of the triage form.

## Local verification

A disposable loopback-only PostgreSQL 16 database with pgvector reached revision `61e73f9b4c20` using the pinned baseline. A synthetic private course evidence row survived upgrade to `72c1a8f0d4e6` and rollback to the baseline. After re-upgrading, a retained synthetic safeguarding case caused rollback to fail as designed. The revision, evidence row, and case were still present after the failed rollback.

The focused API suite passed 42 tests, including the real PostgreSQL claim race and the reviewer exclusion and approval-expiry checks. Chromium smoke testing used a synthetic API for report submission, status lookup, and triage. These browser results cover the UI and request shape.

A privacy reviewer needs an exact case grant for the safeguarding retention inventory. To place or release a hold, the current D-13 retention area must specify `safeguarding_evidence_holds` with approved placement and release reason codes and a review window. The D-18 selected-market decision must explicitly enable the corresponding action. A review date does not release a hold automatically.
