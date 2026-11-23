# ADR: Learner credentials and recovery

- **Status:** Proposed for decision; not approved
- **Date:** 2026-11-15
- **Scope:** Studytomy Portal learner accounts and account recovery
- **Source task:** TASK-010 in `STUDYTOMY-FAMILY-LEARNING-ATOMIC-IMPLEMENTATION-SPEC.md`
- **Decision gates:** D-01, D-03 and D-12 remain unapproved

## Context

The source specification leaves the permitted markets, learner ages, guardian
authority, information use and recovery responsibilities unresolved. It
explicitly prohibits fabricated email addresses, shared parent sessions,
synthetic paid logins and a second identity store.

The current LearnHouse identity contract requires an email address:

- `apps/api/src/db/users.py` defines `UserBase.email` as `EmailStr`, and the
  `User` table inherits that field.
- `apps/api/src/routers/auth.py` authenticates with an email username.
- `apps/api/src/routers/users.py` scopes password reset requests by email and
  organization. The existing reset-code flow is not authority-reviewed
  recovery for a managed learner.

The new Studytomy learner profile deliberately references an existing user and
does not provision credentials. A profile or eligibility decision does not
grant login access.

## Options

1. **Use the existing per-user email login.** Consider only for a cohort and
   market that the accountable business, education, family-services and
   safeguarding owners explicitly approve. Each learner must have an
   individually usable login address under the approved policy. This does not
   establish that any age or market is currently permitted.
2. **Extend the existing User/auth system for managed learners.** This would
   need a separate reviewed design for identifiers, verification, account
   uniqueness, abuse resistance, guardian-assisted recovery, independence
   transitions, session revocation and recovery disputes. Any nullable-email or
   identifier change requires a migration and authentication contract tests.
   No schema or auth change is authorized by this ADR.
3. **Use a guardian's credentials, a shared password, or a second identity
   database.** Rejected by the source requirements. These approaches obscure
   the acting learner, enable inappropriate account takeover and split
   authorization from the platform's existing identity boundary.

## Decision

**No credential option is selected.** The authorized approvers have not supplied
the market, cohort, guardian/recovery authority or information-use decisions
required by D-01, D-03 and D-12. A future decision must name the selected
option, approved cohort and scope, accountable approver, rationale, effective
date, evidence and review date.

Until that record exists, do not create or activate a learner account that
depends on an unapproved credential arrangement. Existing per-user accounts
remain governed by the unchanged LearnHouse authentication contract. Receipt
or payer knowledge does not establish learner account authority.

## Consequences and implementation boundary

- No User schema, sign-in, password reset, session, cookie or identity-provider
  code changes are made.
- No learner identity or credentials are created by the TASK-009 profile API.
- The existing email reset route must not be treated as child-safe recovery;
  it cannot grant guardian, purchaser or staff rights.
- After an explicit credential decision, implement only the approved branch in
  the existing User/auth system, with separate migration and contract review if
  its schema or sign-in contract changes.
- Recovery must revoke relevant sessions, preserve purchases and learning
  evidence, avoid granting guardian permissions, and remain unavailable until
  its authority and privacy rules are approved.

## Approval record

| Field | State |
|---|---|
| Selected option and permitted cohort | Pending |
| Market and age policy (D-01) | Unapproved |
| Guardian and recovery authority (D-03) | Unapproved |
| Information use and privacy policy (D-12) | Unapproved |
| Named approver and evidence | Pending |
| Effective and review dates | Pending |
