/**
 * Shared TypeScript contracts for Studytomy governance, staff capabilities,
 * and scoped access decisions (TASK-002, TASK-003).
 *
 * Backend scopes are authoritative; client-side flags are display hints only
 * and never grant permission.
 */

export type StudyPolicyKey =
  | 'D-01'
  | 'D-02'
  | 'D-03'
  | 'D-04'
  | 'D-05'
  | 'D-06'
  | 'D-07'
  | 'D-08'
  | 'D-09'
  | 'D-10'
  | 'D-11'
  | 'D-12'
  | 'D-13'
  | 'D-14'
  | 'D-15'
  | 'D-16'
  | 'D-17'
  | 'D-18'
  | 'D-19'
  | 'D-20'

export type StudyPolicyStatus = 'draft' | 'approved' | 'superseded' | 'withdrawn'
export type StudyOfferKind = 'course' | 'membership'

export type StudyOwnerFunction =
  | 'business'
  | 'education'
  | 'safeguarding'
  | 'family_services'
  | 'support'
  | 'commercial'
  | 'service'
  | 'privacy'

export type StudyStaffScopeKind =
  | 'course'
  | 'learner'
  | 'case'
  | 'offer'
  | 'order'
  | 'collection'
  | 'function'

export type StudytomyCapabilityName =
  | 'family_authority_review'
  | 'learner_dossier_read'
  | 'safeguarding_case_review'
  | 'financial_reconciliation'
  | 'refund_approval'
  | 'offer_publication'
  | 'content_release_review'
  | 'assessment_moderation'
  | 'progress_report_review'
  | 'privacy_retention_review'

export interface StudytomyCapabilities {
  family_authority_review: boolean
  learner_dossier_read: boolean
  safeguarding_case_review: boolean
  financial_reconciliation: boolean
  refund_approval: boolean
  offer_publication: boolean
  content_release_review: boolean
  assessment_moderation: boolean
  progress_report_review: boolean
  privacy_retention_review: boolean
}

export const DEFAULT_DENY_STUDYTOMY_CAPABILITIES: Readonly<StudytomyCapabilities> = {
  family_authority_review: false,
  learner_dossier_read: false,
  safeguarding_case_review: false,
  financial_reconciliation: false,
  refund_approval: false,
  offer_publication: false,
  content_release_review: false,
  assessment_moderation: false,
  progress_report_review: false,
  privacy_retention_review: false,
}

export interface StudytomyAccessDecision {
  allowed: boolean
  code: string
  scope: string | null
  policy_version_ids: number[]
  access_version: number
  next_action: string | null
  public_message: string
}

export interface StudyStaffScopeCreate {
  capability: StudytomyCapabilityName
  scope_kind: StudyStaffScopeKind
  scope_uuid: string
  is_exclusion?: boolean
  evidence_reference: string
}

export interface StudyStaffScopeRead extends StudyStaffScopeCreate {
  scope_record_uuid: string
  owner_uuid: string
  status: 'active' | 'revoked'
  created_by_user_uuid: string
  created_at: string
}

export interface StudyStaffScopeRevoke {
  evidence_reference: string
}

/** Returned only after server authorization; never accepted as proof of delegation. */
export interface FinancialDelegationLimit {
  currency: string
  max_amount_minor: number
  policy_version_id: number
  evidence_reference: string
}

export interface StudyReadinessBlocker {
  code: string
  policy_key: StudyPolicyKey | null
  message: string
  next_step: string
}

export interface StudyCourseReadiness {
  course_uuid: string
  offer_kind: StudyOfferKind
  ready_to_sell: boolean
  blockers: StudyReadinessBlocker[]
  evaluated_at: string
}

export interface StudyPrivacyStatus {
  learner_profile_protected: boolean
  visibility_mode: 'private_default' | 'standard_account_profile'
  publicity_controls_available: false
}

export type StudyRelationshipStatus =
  | 'pending'
  | 'accepted'
  | 'denied'
  | 'restricted'
  | 'disputed'
  | 'revoked'
  | 'expired'

export interface StudyFamilyReviewQueueItem {
  learner_uuid: string
  relationship_uuid: string
  status: StudyRelationshipStatus
  requested_scopes: string[]
  approved_scopes: string[]
  authority_version: number
  created_at: string
  updated_at: string
}

export interface StudyRelationshipReview extends StudyFamilyReviewQueueItem {
  relationship_type: 'guardian'
  valid_from: string | null
  valid_to: string | null
  adult_user_uuid: string
  request_evidence_reference: string
  request_policy_version_ids: number[]
  decision_policy_version_ids: number[] | null
  decision_evidence_reference: string | null
  decision_reason_code: string | null
  decided_at: string | null
}

export interface StudyRelationshipDecisionCreate {
  outcome: 'accept' | 'deny'
  expected_version: number
  approved_scopes: string[]
  valid_from: string | null
  valid_to: string | null
  evidence_reference: string
  reason_code: string
}
