import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'

type PublicSafeguardingContact = {
  contact_instructions: string
  monitored_hours: string
  emergency_guidance: string
}

type SafeguardingReceipt = { case_uuid: string }

function safeguardingUrl(orgUuid: string) {
  return `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/safeguarding`
}

export async function getPublicSafeguardingContact(orgUuid: string): Promise<PublicSafeguardingContact | null> {
  const response = await secureFetch(`${safeguardingUrl(orgUuid)}/public-contact`, {
    cache: 'no-store', redirect: 'error',
  })
  return response.ok ? response.json() : null
}

export async function isSafeguardingReportingAvailable(orgUuid: string): Promise<boolean> {
  const response = await secureFetch(`${safeguardingUrl(orgUuid)}/reporting-availability`, {
    cache: 'no-store', redirect: 'error',
  })
  if (!response.ok) return false
  const readiness: { available: boolean } = await response.json()
  return readiness.available === true
}

export async function submitSafeguardingReport(
  orgUuid: string, token: string, requestId: string, narrative: string, signal: AbortSignal,
): Promise<SafeguardingReceipt> {
  const response = await secureFetch(`${safeguardingUrl(orgUuid)}/reports`, {
    ...RequestBodyWithAuthHeader('POST', { request_id: requestId, narrative }, null, token),
    redirect: 'error',
    signal,
  })
  if (!response.ok) throw new Error('The report receipt is unavailable.')
  return response.json()
}


export async function getSafeguardingReportStatus(
  orgUuid: string, token: string, reference: string, signal: AbortSignal,
): Promise<string | null> {
  const response = await secureFetch(`${safeguardingUrl(orgUuid)}/reports/${encodeURIComponent(reference)}`, {
    ...RequestBodyWithAuthHeader('GET', null, null, token),
    redirect: 'error',
    signal,
  })
  if (response.status === 404) return null
  if (!response.ok) throw new Error('The report status is unavailable.')
  const receipt: { status: string } = await response.json()
  return receipt.status
}

export type SafeguardingTriageItem = { case_uuid: string; version: number }

export async function listSafeguardingTriage(
  orgUuid: string, token: string, after: string | null, signal: AbortSignal,
): Promise<SafeguardingTriageItem[] | null> {
  const url = `${safeguardingUrl(orgUuid)}/triage${after ? `?after=${encodeURIComponent(after)}` : ''}`
  const response = await secureFetch(url, {
    ...RequestBodyWithAuthHeader('GET', null, null, token),
    redirect: 'error',
    signal,
  })
  if (response.status === 404) return null
  if (!response.ok) throw new Error('Triage is unavailable.')
  return response.json()
}

export async function submitSafeguardingTriage(
  orgUuid: string, token: string, item: SafeguardingTriageItem,
  verificationReference: string, subjects: string[], signal: AbortSignal,
): Promise<SafeguardingTriageItem & { status: string }> {
  const response = await secureFetch(`${safeguardingUrl(orgUuid)}/triage/${encodeURIComponent(item.case_uuid)}`, {
    ...RequestBodyWithAuthHeader('POST', {
      expected_version: item.version,
      verification_reference: verificationReference,
      subject_user_uuids: subjects,
    }, null, token),
    redirect: 'error',
    signal,
  })
  if (!response.ok) throw new Error('Triage could not be recorded.')
  return response.json()
}
