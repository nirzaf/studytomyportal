import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'
import type {
  StudyConsentChoice,
  StudyConsentEventRead,
  StudyConsentNoticeRead,
  StudyConsentPurpose,
  StudyPrivacyStatus,
} from '@/types/studytomy'

export class StudyConsentRequestError extends Error {
  readonly status: number

  constructor(status: number) {
    super('Unable to save this privacy choice.')
    this.name = 'StudyConsentRequestError'
    this.status = status
  }
}

export type RecordRequestReceipt = { request_reference: string; received_at: string; due_at: string }
export type RecordRequestStatus = RecordRequestReceipt & {
  status: 'received' | 'approved_for_preparation' | 'denied'
  decided_at: string | null
}
export type RecordRequestOutcome = 'approved_for_preparation' | 'denied'
export type RecordRequestDecisionOptions = Partial<Record<RecordRequestOutcome, string[]>>
export type CompletionExportPreview = {
  completions: { course_uuid: string; recorded_at: string }[]
  snapshot_sha256: string
}
export type EvidenceRetentionManifest = {
  request_reference: string
  as_of: string
  hold_references: string[]
  files: { evidence_reference: string; disposition: 'held' | 'case_open' | 'unverified' | 'retained' | 'candidate'; eligible_at: string | null }[]
  snapshot_sha256: string
  completed_removals: Record<string, string>
}

export type EvidenceRemovalApproval = {
  approval_reference: string
  snapshot_sha256: string
  candidate_count: number
  approved_at: string
}

function recordRequestUrl(orgUuid: string) {
  return `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/learners/me/record-requests`
}

export async function checkRecordRequest(orgUuid: string, token: string, signal: AbortSignal): Promise<boolean> {
  const response = await fetch(`${recordRequestUrl(orgUuid)}/availability`, {
    ...RequestBodyWithAuthHeader('GET', null, null, token), signal, cache: 'no-store',
  })
  return response.status === 204
}

export async function submitRecordRequest(orgUuid: string, token: string, reference: string): Promise<RecordRequestReceipt> {
  const response = await fetch(`${recordRequestUrl(orgUuid)}/${encodeURIComponent(reference)}`, {
    ...RequestBodyWithAuthHeader('PUT', null, null, token), cache: 'no-store',
  })
  if (!response.ok) throw new Error('Record request unavailable.')
  return response.json()
}

export async function getRecordRequestStatus(orgUuid: string, token: string, reference: string): Promise<RecordRequestStatus> {
  const response = await fetch(`${recordRequestUrl(orgUuid)}/${encodeURIComponent(reference)}`, {
    ...RequestBodyWithAuthHeader('GET', null, null, token), cache: 'no-store',
  })
  if (!response.ok) throw new Error('Record request status unavailable.')
  return response.json()
}

export async function listAssignedRecordRequests(
  orgUuid: string, token: string, signal: AbortSignal,
): Promise<RecordRequestReceipt[] | null> {
  const response = await secureFetch(`${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/record-requests?limit=50`, {
    ...RequestBodyWithAuthHeader('GET', null, null, token), cache: 'no-store', signal,
  })
  if ([401, 403, 404, 409].includes(response.status)) return null
  if (!response.ok) throw new Error('Unable to load assigned requests.')
  return response.json()
}

export async function getRecordDecisionOptions(
  orgUuid: string, token: string, reference: string, signal: AbortSignal,
): Promise<RecordRequestDecisionOptions> {
  const response = await secureFetch(`${staffDecisionUrl(orgUuid, reference)}`, {
    ...RequestBodyWithAuthHeader('GET', null, null, token), cache: 'no-store', signal,
  })
  if (!response.ok) throw new Error('Decision options unavailable.')
  return response.json()
}

export async function recordRequestDecision(
  orgUuid: string, token: string, reference: string,
  command: { outcome: RecordRequestOutcome; reason_code: string; evidence_reference: string },
): Promise<void> {
  const response = await secureFetch(staffDecisionUrl(orgUuid, reference), {
    ...RequestBodyWithAuthHeader('PUT', command, null, token), cache: 'no-store',
  })
  if (!response.ok) throw new Error('The decision could not be confirmed.')
}

export async function uploadRecordRequestEvidence(
  orgUuid: string, token: string, reference: string, file: File, key: string, signal: AbortSignal,
): Promise<{ evidence_reference: string; content_sha256: string; verified_at: string }> {
  const body = new FormData()
  body.set('file', file)
  const response = await secureFetch(`${staffRecordRequestUrl(orgUuid, reference)}/evidence`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Idempotency-Key': key },
    cache: 'no-store', signal, body,
  })
  if (!response.ok) throw new Error('Evidence upload unavailable.')
  return response.json()
}

function staffDecisionUrl(orgUuid: string, reference: string): string {
  return `${staffRecordRequestUrl(orgUuid, reference)}/decision`
}

function staffRecordRequestUrl(orgUuid: string, reference: string): string {
  return `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/record-requests/${encodeURIComponent(reference)}`
}

export async function previewEvidenceRetention(
  orgUuid: string, token: string, caseReference: string, cutoff: string, signal: AbortSignal,
): Promise<EvidenceRetentionManifest> {
  const query = new URLSearchParams({ as_of: cutoff })
  const response = await secureFetch(`${staffRecordRequestUrl(orgUuid, caseReference)}/evidence-retention?${query}`, {
    ...RequestBodyWithAuthHeader('GET', null, null, token), cache: 'no-store', signal,
  })
  if (!response.ok) throw new Error('Retention review unavailable.')
  return response.json()
}

export async function approveEvidenceRetention(
  orgUuid: string, token: string, manifest: EvidenceRetentionManifest, reference: string, reason: string, signal: AbortSignal,
): Promise<EvidenceRemovalApproval> {
  const url = `${staffRecordRequestUrl(orgUuid, manifest.request_reference)}/evidence-retention/approvals/${encodeURIComponent(reference)}`
  const response = await secureFetch(url, {
    ...RequestBodyWithAuthHeader('PUT', {
      as_of: manifest.as_of, snapshot_sha256: manifest.snapshot_sha256, reason_code: reason,
    }, null, token), cache: 'no-store', signal,
  })
  if (!response.ok) throw new Error('Removal approval unavailable.')
  return response.json()
}

export async function previewRecordCompletionExport(
  orgUuid: string, token: string, reference: string, signal: AbortSignal,
): Promise<CompletionExportPreview> {
  const response = await secureFetch(`${staffRecordRequestUrl(orgUuid, reference)}/completion-export`, {
    ...RequestBodyWithAuthHeader('GET', null, null, token), cache: 'no-store', signal,
  })
  if (!response.ok) throw new Error('Completion export preview unavailable.')
  return response.json()
}

export async function releaseRecordCompletionExport(
  orgUuid: string, token: string, reference: string, digest: string, signal: AbortSignal,
): Promise<{ available_until: string }> {
  const response = await secureFetch(`${staffRecordRequestUrl(orgUuid, reference)}/completion-export`, {
    ...RequestBodyWithAuthHeader('PUT', { snapshot_sha256: digest }, null, token), cache: 'no-store', signal,
  })
  if (!response.ok) throw new Error('Completion export release unavailable.')
  return response.json()
}

export async function downloadRecordCompletionExport(
  orgUuid: string, token: string, reference: string, signal: AbortSignal,
): Promise<Blob> {
  const response = await secureFetch(`${recordRequestUrl(orgUuid)}/${encodeURIComponent(reference)}/completion-export`, {
    ...RequestBodyWithAuthHeader('POST', null, null, token), cache: 'no-store', signal,
  })
  if (!response.ok) throw new Error('Completion export unavailable.')
  return response.blob()
}

export async function getMyPrivacyStatus(accessToken: string): Promise<StudyPrivacyStatus> {
  const response = await fetch(
    `${getAPIUrl()}studytomy/privacy/me`,
    RequestBodyWithAuthHeader('GET', null, null, accessToken)
  )
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error('Unable to load privacy settings.')
  }
  return body as StudyPrivacyStatus
}

export async function getMyConsentEventsForSelf(
  accessToken: string,
  orgUuid: string
): Promise<StudyConsentEventRead[]> {
  const response = await fetch(
    `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/learners/me/consent-events?limit=100`,
    RequestBodyWithAuthHeader('GET', null, null, accessToken)
  )
  const body = await response.json().catch(() => null)

  // A protected account may belong to another organization in the workspace.
  // Treat the absent local profile as an empty local history, not an account leak.
  if (response.status === 404) return []
  if (!response.ok || !Array.isArray(body)) {
    throw new Error('Unable to load consent history.')
  }
  return body as StudyConsentEventRead[]
}

export async function getMyConsentNoticeForSelf(
  accessToken: string,
  orgUuid: string,
  purpose: StudyConsentPurpose
): Promise<StudyConsentNoticeRead | null> {
  const response = await fetch(
    `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/learners/me/consent-notices/${encodeURIComponent(purpose)}`,
    RequestBodyWithAuthHeader('GET', null, null, accessToken)
  )
  const body = await response.json().catch(() => null)

  // Policy, notice, learner and authority gates stay server-owned. An absent
  // approved notice means the UI must not offer a choice for this purpose.
  if ([403, 404, 409].includes(response.status)) return null
  if (!response.ok || !body || typeof body !== 'object') {
    throw new Error('Unable to load current consent notices.')
  }
  return body as StudyConsentNoticeRead
}

function withIdempotencyKey(options: RequestInit, idempotencyKey: string): RequestInit {
  const headers = new Headers(options.headers)
  headers.set('Idempotency-Key', idempotencyKey)
  return { ...options, headers }
}

export async function recordMyConsentChoiceForSelf(
  accessToken: string,
  orgUuid: string,
  choice: {
    purpose: StudyConsentPurpose
    choice: Extract<StudyConsentChoice, 'accept' | 'decline'>
    notice_version: string
    scope_codes: string[]
  },
  idempotencyKey: string
): Promise<StudyConsentEventRead> {
  const response = await fetch(
    `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/learners/me/consent-events`,
    withIdempotencyKey(
      RequestBodyWithAuthHeader('POST', choice, null, accessToken),
      idempotencyKey
    )
  )
  if (!response.ok) throw new StudyConsentRequestError(response.status)
  return (await response.json()) as StudyConsentEventRead
}

export async function withdrawMyConsentChoiceForSelf(
  accessToken: string,
  orgUuid: string,
  eventUuid: string,
  idempotencyKey: string
): Promise<StudyConsentEventRead> {
  const response = await fetch(
    `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/learners/me/consent-events/${encodeURIComponent(eventUuid)}/withdraw`,
    withIdempotencyKey(
      RequestBodyWithAuthHeader('POST', {}, null, accessToken),
      idempotencyKey
    )
  )
  if (!response.ok) throw new StudyConsentRequestError(response.status)
  return (await response.json()) as StudyConsentEventRead
}
