import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'
import type {
  StudyFamilyReviewQueueItem,
  StudyRelationshipDecisionCreate,
  StudyRelationshipRead,
  StudyRelationshipReview,
} from '@/types/studytomy'

async function parseResponse<T>(response: Response, fallback: string): Promise<T> {
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = body?.detail
    throw new Error(
      typeof detail === 'string'
        ? detail
        : detail?.message || fallback
    )
  }
  return body as T
}

export async function getFamilyReviewQueue(
  orgUuid: string,
  accessToken: string
): Promise<StudyFamilyReviewQueueItem[]> {
  const response = await secureFetch(
    `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/family-review-queue`,
    { ...RequestBodyWithAuthHeader('GET', null, null, accessToken), cache: 'no-store' }
  )
  return parseResponse(response, 'Unable to load the family review queue.')
}

export type AllocationPreflight = {
  status: 'used' | 'unstarted' | 'evidence_unavailable'
  reason_codes: string[]
}

export async function reviewAllocation(
  orgUuid: string,
  learnerUuid: string,
  orderRef: string,
  accessToken: string
): Promise<AllocationPreflight> {
  const url = allocationUrl(orgUuid, learnerUuid, orderRef)
  const response = await secureFetch(
    `${url}/correction-review`,
    { ...RequestBodyWithAuthHeader('GET', null, null, accessToken), cache: 'no-store' }
  )
  return parseResponse(response, 'Unable to review this allocation.')
}

export async function correctAllocation(
  orgUuid: string,
  learnerUuid: string,
  orderRef: string,
  accessToken: string,
  command: { replacement_learner_uuid: string; reason_code: string }
): Promise<{ correction_uuid: string; status: 'corrected' }> {
  const url = allocationUrl(orgUuid, learnerUuid, orderRef)
  const response = await secureFetch(
    `${url}/correction`,
    RequestBodyWithAuthHeader('PUT', command, null, accessToken)
  )
  return parseResponse(response, 'Unable to correct this allocation.')
}

function allocationUrl(orgUuid: string, learnerUuid: string, orderRef: string): string {
  return `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/learners/${encodeURIComponent(learnerUuid)}/allocations/${encodeURIComponent(orderRef)}`
}

export async function getRelationshipForReview(
  orgUuid: string,
  learnerUuid: string,
  relationshipUuid: string,
  accessToken: string
): Promise<StudyRelationshipReview> {
  const response = await secureFetch(
    `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/learners/${encodeURIComponent(learnerUuid)}/relationships/${encodeURIComponent(relationshipUuid)}`,
    { ...RequestBodyWithAuthHeader('GET', null, null, accessToken), cache: 'no-store' }
  )
  return parseResponse(response, 'Unable to load this assigned review.')
}

export interface StudyEvidenceFileItem {
  evidence_uuid: string
  learner_uuid: string
  purpose: string
  content_sha256: string
  policy_version_ids: number[]
  media_type: string
  size_bytes: number
  retention_category: string
  upload_status: string
  created_at: string
  updated_at: string
  verified_at?: string | null
}

export async function listRelationshipEvidence(
  orgUuid: string,
  relationshipUuid: string,
  accessToken: string
): Promise<StudyEvidenceFileItem[]> {
  const response = await secureFetch(
    `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/relationships/${encodeURIComponent(relationshipUuid)}/evidence-files`,
    { ...RequestBodyWithAuthHeader('GET', null, null, accessToken), cache: 'no-store' }
  )
  return parseResponse(response, 'Unable to load bound relationship evidence.')
}

export async function uploadRelationshipEvidence(
  orgUuid: string,
  relationshipUuid: string,
  file: File,
  accessToken: string,
  idempotencyKey: string
): Promise<StudyEvidenceFileItem> {
  const form = new FormData()
  form.append('file', file)
  const response = await secureFetch(
    `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/relationships/${encodeURIComponent(relationshipUuid)}/evidence-files`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${accessToken}`,
        'Idempotency-Key': idempotencyKey,
      },
      body: form,
    }
  )
  return parseResponse(response, 'Unable to upload guardian-authority evidence.')
}

export async function fetchRelationshipEvidenceContent(
  orgUuid: string,
  relationshipUuid: string,
  evidenceUuid: string,
  accessToken: string
): Promise<{ blob: Blob; mediaType: string }> {
  const response = await secureFetch(
    `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/relationships/${encodeURIComponent(relationshipUuid)}/evidence-files/${encodeURIComponent(evidenceUuid)}/content`,
    {
      method: 'GET',
      headers: { Authorization: `Bearer ${accessToken}` },
      cache: 'no-store',
    }
  )
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    const detail = payload && typeof payload.detail === 'string' ? payload.detail : 'Unable to open original evidence file.'
    throw new Error(detail)
  }
  const blob = await response.blob()
  return { blob, mediaType: response.headers.get('content-type') || blob.type || 'application/octet-stream' }
}

export interface StudyEligibilityDecisionItem {
  decision_uuid: string
  eligibility_kind: 'participation_eligibility' | 'independent_control_eligibility'
  outcome: 'eligible' | 'ineligible' | 'renewal_required'
  policy_version_ids: number[]
  evidence_reference: string
  effective_from: string
  effective_to: string | null
  review_due_at: string | null
  policy_current: boolean
  currently_eligible: boolean
  created_at: string
}

export interface StudyLearnerProfileItem {
  learner_uuid: string
  user_uuid: string
  status: string
  control_mode: 'pending_review' | 'dependent' | 'independent'
  time_zone: string | null
  access_version: number
  eligibility_decisions: StudyEligibilityDecisionItem[]
  created_at: string
  updated_at: string
}

export async function createLearnerProfile(
  orgUuid: string,
  accessToken: string,
  idempotencyKey: string,
  command: { user_uuid: string; time_zone?: string | null }
): Promise<StudyLearnerProfileItem> {
  const url = `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/learners`
  const options = RequestBodyWithAuthHeader('POST', command, null, accessToken)
  const headers = new Headers(options.headers)
  headers.set('Idempotency-Key', idempotencyKey)
  options.headers = headers
  const response = await secureFetch(url, options)
  return parseResponse(response, 'Unable to create a pending learner profile.')
}

export async function getLearnerProfile(
  orgUuid: string,
  learnerUuid: string,
  accessToken: string
): Promise<StudyLearnerProfileItem> {
  const response = await secureFetch(
    `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/learners/${encodeURIComponent(learnerUuid)}`,
    { ...RequestBodyWithAuthHeader('GET', null, null, accessToken), cache: 'no-store' }
  )
  return parseResponse(response, 'Unable to load this learner profile.')
}

export async function recordLearnerEligibilityDecision(
  orgUuid: string,
  learnerUuid: string,
  accessToken: string,
  idempotencyKey: string,
  command: {
    eligibility_kind: 'participation_eligibility' | 'independent_control_eligibility'
    outcome: 'eligible' | 'ineligible' | 'renewal_required'
    evidence_reference: string
    effective_to?: string | null
    review_due_at?: string | null
    control_mode?: 'dependent' | 'independent' | null
  }
): Promise<StudyEligibilityDecisionItem> {
  const url = `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/learners/${encodeURIComponent(learnerUuid)}/eligibility-decisions`
  const options = RequestBodyWithAuthHeader('POST', command, null, accessToken)
  const headers = new Headers(options.headers)
  headers.set('Idempotency-Key', idempotencyKey)
  options.headers = headers
  const response = await secureFetch(url, options)
  return parseResponse(response, 'Unable to record this learner eligibility decision.')
}

export async function activateLearnerProfile(
  orgUuid: string,
  learnerUuid: string,
  accessToken: string,
  idempotencyKey: string,
  command: { expected_access_version: number; reason_code: string }
): Promise<StudyLearnerProfileItem> {
  const url = `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/learners/${encodeURIComponent(learnerUuid)}/activate`
  const options = RequestBodyWithAuthHeader('POST', command, null, accessToken)
  const headers = new Headers(options.headers)
  headers.set('Idempotency-Key', idempotencyKey)
  options.headers = headers
  const response = await secureFetch(url, options)
  return parseResponse(response, 'Unable to activate this learner profile.')
}

export async function decideRelationship(
  orgUuid: string,
  learnerUuid: string,
  relationshipUuid: string,
  accessToken: string,
  idempotencyKey: string,
  decision: StudyRelationshipDecisionCreate
): Promise<StudyRelationshipRead> {
  const url = `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/learners/${encodeURIComponent(learnerUuid)}/relationships/${encodeURIComponent(relationshipUuid)}/decisions`
  const options = RequestBodyWithAuthHeader('POST', decision, null, accessToken)
  const headers = new Headers(options.headers)
  headers.set('Idempotency-Key', idempotencyKey)
  options.headers = headers
  const response = await secureFetch(url, options)
  return parseResponse(response, 'Unable to record this family authority decision.')
}
