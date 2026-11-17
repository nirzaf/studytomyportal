import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'
import type {
  StudyFamilyReviewQueueItem,
  StudyRelationshipDecisionCreate,
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

export async function decideRelationship(
  orgUuid: string,
  learnerUuid: string,
  relationshipUuid: string,
  accessToken: string,
  idempotencyKey: string,
  decision: StudyRelationshipDecisionCreate
): Promise<StudyRelationshipReview> {
  const url = `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/learners/${encodeURIComponent(learnerUuid)}/relationships/${encodeURIComponent(relationshipUuid)}/decisions`
  const options = RequestBodyWithAuthHeader('POST', decision, null, accessToken)
  const headers = new Headers(options.headers)
  headers.set('Idempotency-Key', idempotencyKey)
  options.headers = headers
  const response = await secureFetch(url, options)
  return parseResponse(response, 'Unable to record this family authority decision.')
}
