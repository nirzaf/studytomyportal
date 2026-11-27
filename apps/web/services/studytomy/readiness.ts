import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'
import type {
  StudyCourseReadiness,
  StudyFinancialDelegationRead,
  StudyOfferDraftRead,
  StudyOfferKind,
  StudyOwnerAssignmentRead,
  StudyOwnerFunction,
  StudyPolicyBody,
  StudyPolicyKey,
  StudyPolicyVersionRead,
  StudyStaffScopeCreate,
  StudyStaffScopeRead,
} from '@/types/studytomy'

async function governanceRequest<T>(
  orgUuid: string,
  accessToken: string,
  path: string,
  method = 'GET',
  body?: unknown,
  idempotent = false,
): Promise<T> {
  const headers = new Headers({ Authorization: `Bearer ${accessToken}` })
  if (body !== undefined) headers.set('Content-Type', 'application/json')
  if (idempotent) headers.set('Idempotency-Key', crypto.randomUUID())
  const response = await secureFetch(
    `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/${path}`,
    {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: 'no-store',
      credentials: 'include',
    },
  )
  if (!response.ok) {
    const result = await response.json().catch(() => null)
    const detail = result?.detail
    throw new Error(typeof detail === 'string' ? detail : detail?.message || 'The governance request was denied.')
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export async function getCourseOfferReadiness(
  orgUuid: string,
  courseUuid: string,
  accessToken: string,
  offerKind: StudyOfferKind = 'course'
): Promise<StudyCourseReadiness> {
  const query = new URLSearchParams({ offer_kind: offerKind })
  const response = await fetch(
    `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/courses/${encodeURIComponent(courseUuid)}/readiness?${query}`,
    RequestBodyWithAuthHeader('GET', null, null, accessToken)
  )
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = body?.detail
    throw new Error(typeof detail === 'string' ? detail : detail?.message || 'Unable to load course readiness.')
  }
  return body as StudyCourseReadiness
}

export const listStudyPolicies = (org: string, token: string) =>
  governanceRequest<StudyPolicyVersionRead[]>(org, token, 'policies?limit=100')

export const proposeStudyPolicy = (
  org: string,
  token: string,
  proposal: { policy_key: StudyPolicyKey; body: StudyPolicyBody; evidence_reference: string; effective_from: string; effective_to?: string | null },
) => governanceRequest<StudyPolicyVersionRead>(org, token, 'policies', 'POST', proposal, true)

export const approveStudyPolicy = (
  org: string,
  token: string,
  policyUuid: string,
  approval: { responsibility: StudyOwnerFunction; evidence_reference: string; rationale: string },
) => governanceRequest<StudyPolicyVersionRead>(
  org, token, `policies/${encodeURIComponent(policyUuid)}/approvals`, 'POST', approval, true,
)

export const listServiceOwners = (org: string, token: string) =>
  governanceRequest<StudyOwnerAssignmentRead[]>(org, token, 'service-owners?limit=100')

export const createServiceOwner = (
  org: string,
  token: string,
  assignment: { user_uuid: string; responsibility: StudyOwnerFunction; evidence_reference: string; effective_from: string; effective_to?: string | null },
) => governanceRequest<StudyOwnerAssignmentRead>(org, token, 'service-owners', 'POST', assignment, true)

export const listServiceOwnerScopes = (org: string, token: string, ownerUuid: string) =>
  governanceRequest<StudyStaffScopeRead[]>(
    org, token, `service-owners/${encodeURIComponent(ownerUuid)}/scopes?limit=200`,
  )

export const grantServiceOwnerScope = (
  org: string,
  token: string,
  ownerUuid: string,
  scope: StudyStaffScopeCreate,
) => governanceRequest<StudyStaffScopeRead>(
  org, token, `service-owners/${encodeURIComponent(ownerUuid)}/scopes`, 'POST', scope, true,
)

export const revokeServiceOwnerScope = (
  org: string,
  token: string,
  scopeRecordUuid: string,
  evidence_reference: string,
) => governanceRequest<void>(
  org, token, `staff-scopes/${encodeURIComponent(scopeRecordUuid)}/revoke`, 'POST', { evidence_reference }, true,
)

export const setFinancialDelegation = (
  org: string,
  token: string,
  ownerUuid: string,
  delegation: { policy_uuid: string; currency: string; max_amount_minor: number; evidence_reference: string },
) => governanceRequest<StudyFinancialDelegationRead>(
  org, token, `service-owners/${encodeURIComponent(ownerUuid)}/financial-delegation`, 'PUT', delegation, true,
)

export const createStudyOfferDraft = (
  org: string,
  token: string,
  draft: { offer_uuid: string; offer_kind: StudyOfferKind; terms: Record<string, unknown> },
) => governanceRequest<StudyOfferDraftRead>(org, token, 'offers/drafts', 'POST', draft, true)

export const getStudyOfferDraft = (org: string, token: string, offerUuid: string) =>
  governanceRequest<StudyOfferDraftRead>(org, token, `offers/${encodeURIComponent(offerUuid)}/draft`)

export const reviseStudyOfferDraft = (
  org: string,
  token: string,
  offerUuid: string,
  expectedVersion: number,
  terms: Record<string, unknown>,
) => governanceRequest<StudyOfferDraftRead>(
  org,
  token,
  `offers/${encodeURIComponent(offerUuid)}/draft`,
  'PUT',
  { expected_version: expectedVersion, terms },
  true,
)
