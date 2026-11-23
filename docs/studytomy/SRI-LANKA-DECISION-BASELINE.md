# Studytomy — Sri Lankan National School Syllabus Decision Baseline (T-03)

> [!IMPORTANT]
> This document records the engineering scope alignment and open governance decisions for the initial Sri Lankan specialization. It is **not** a substitute for live signed policy approvals (`D-01`–`D-18`), legal review, or publisher/NIE rights grants.

## 1. Confirmed Programme Scope

| Dimension | Initial Specialization | Engineering Enforcement Boundary |
|---|---|---|
| Authority | National Institute of Education (NIE) / Ministry of Education — Sri Lanka | Stored in `StudyCurriculumRevision.jurisdiction` and optional `programme_metadata.authority`; validated against approved `D-02` `programme_manifest` when configured. |
| School Structure | Grades 6–13 (`junior_secondary`: Grades 6–9; `gce_ol`: Grades 10–11; `gce_al`: Grades 12–13) | Grade is a school curriculum stage, **never** a proxy for legal capacity or account control mode (`independent` vs. `dependent`). |
| Instructional Language | English (`en` / `en-LK`) | Propagated as server-owned `instruction_language` into tutor guidance and grounded draft generation, distinct from original source language, English Language subject, and examination medium. |
| Cohort & Syllabus Editions | Explicit `syllabus_edition` and `effective_cohort` (e.g. 2015/2017 editions vs. announced 2027 Grade 6 reform cohort) | New syllabus editions require a new reviewed curriculum revision; earlier cohorts retain their bound releases unchanged. |
| Audience | Direct students and their parents/guardians | Independent learners act for themselves; dependent learners require verified guardian authority (`consent_management`, `purchase_allocation`, `adaptive_report_summary`, `adaptive_report_email`). Payment never implies consent or academic reporting authority. |

## 2. Explicit Exclusions (Not Inferred as Implemented or Approved)

- **Official accreditation or exam equivalence:** Platform completion records, diagnostics, and adaptive playgrounds are not official NIE / Department of Examinations G.C.E. O/L or A/L results.
- **Automated subjective or practical marking:** Objective scoring remains strictly bounded to choice and numeric contracts; essays, worked proofs, speaking/listening, and supervised science practicals require human review.
- **Copyright/reproduction clearance:** Existing textbook/resource-book PDFs carry copyright/all-rights-reserved or free-distribution notices; operational reproduction, adaptation, translation, or AI processing requires an explicit rights approval recorded in `D-16` and `StudyDraftMaterial.source_provenance`.
- **Automatic Dropbox sync / OCR / live-class booking / public-site migration:** Out of scope for this specialization.

## 3. Open Owner Decisions Required Before Live Student Rollout

1. **Selected pilot slice (`D-02`):** Designate exact initial grade(s), subject(s), official syllabus/teacher-guide edition, and effective cohort.
2. **Child, guardian, and privacy policy (`D-01`, `D-03`, `D-12`, `D-13`):** Approve legal capacity rules, guardian identity/evidence verification standards, consent notice texts, data retention categories, and AI provider transfer rules.
3. **Content rights and review (`D-16`):** Record verified publisher/NIE rights references and educator sign-off for each bounded PDF source extract (retaining the 4 MiB / 200-page / 100,000-character intake bounds).
4. **Commercial activation (`D-05`, `D-17`):** Approve pricing currency (`LKR` or selected currency), refund terms, and manual collection procedures before enabling offers.
