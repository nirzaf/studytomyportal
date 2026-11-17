import { readFileSync } from 'node:fs'
import { expect, spyOn, test } from 'bun:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import RecordRequest from '../components/Studytomy/RecordRequest'
import RecordRequestReviewQueue from '../components/Studytomy/RecordRequestReviewQueue'
import RecordCompletionRelease from '../components/Studytomy/RecordCompletionRelease'
import RetentionReview from '../components/Studytomy/RetentionReview'
import { approveEvidenceRetention, downloadRecordCompletionExport, releaseRecordCompletionExport, uploadRecordRequestEvidence } from '../services/studytomy/privacy'

const accountSubpage = readFileSync(
  new URL('../app/orgs/[orgslug]/(withmenu)/account/[subpage]/page.tsx', import.meta.url),
  'utf8'
)
const privacyService = readFileSync(
  new URL('../services/studytomy/privacy.ts', import.meta.url),
  'utf8'
)
const privacySettings = readFileSync(
  new URL('../components/Studytomy/PrivacySettings.tsx', import.meta.url),
  'utf8'
)
const consentSettings = readFileSync(
  new URL('../components/Studytomy/ConsentSettings.tsx', import.meta.url),
  'utf8'
)

test('record-request status can be checked before intake availability resolves', () => {
  const markup = renderToStaticMarkup(React.createElement(RecordRequest, {
    orgUuid: 'synthetic-org', accessToken: 'synthetic-token',
  }))

  expect(markup).toContain('Request reference')
  expect(markup).toContain('name="request_reference"')
  expect(markup).toContain('Check request status')
  expect(markup).not.toContain('Request records')
  expect(markup).not.toContain('synthetic-token')
})

test('staff record queue starts with a sign-in state and no cached case metadata', () => {
  const markup = renderToStaticMarkup(React.createElement(RecordRequestReviewQueue, { orgUuid: 'synthetic-org' }))

  expect(markup).toContain('Sign in to view assigned learning record reviews.')
  expect(markup).not.toContain('Pending assigned requests')
  expect(markup).not.toContain('synthetic-org')
})

test('staff must preview a completion snapshot before release', () => {
  const markup = renderToStaticMarkup(React.createElement(RecordCompletionRelease, {
    orgUuid: 'synthetic-org', token: 'synthetic-token',
  }))
  expect(markup).toContain('Preview export')
  expect(markup).not.toContain('Release this snapshot')
  expect(markup).not.toContain('synthetic-token')
})

test('retention review starts with a dry run and hides approval before preview', () => {
  const markup = renderToStaticMarkup(React.createElement(RetentionReview, {
    orgUuid: 'synthetic-org', token: 'synthetic-token',
  }))
  expect(markup).toContain('Run retention preview')
  expect(markup).not.toContain('Record removal approval')
  expect(markup).not.toContain('synthetic-token')
})

test('retention approval binds the server cutoff and digest without sending file data', async () => {
  const manifest = { request_reference: 'case-id', as_of: '2026-01-02T00:00:00Z', snapshot_sha256: 'b'.repeat(64), files: [], hold_references: [] }
  const stub = spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
    expect(url.endsWith('/record-requests/case-id/evidence-retention/approvals/approval-id')).toBe(true)
    expect(init.method).toBe('PUT')
    expect(init.cache).toBe('no-store')
    expect(JSON.parse(init.body)).toEqual({ as_of: manifest.as_of, snapshot_sha256: manifest.snapshot_sha256, reason_code: 'fixture_reason' })
    return Response.json({ approval_reference: 'approval-id' })
  })
  try {
    const receipt = await approveEvidenceRetention('synthetic-org', 'synthetic-token', manifest,
      'approval-id', 'fixture_reason', new AbortController().signal)
    expect(receipt.approval_reference).toBe('approval-id')
  } finally {
    stub.mockRestore()
  }
})

test('release sends only the reviewed digest and download targets the learner boundary', async () => {
  const digest = 'a'.repeat(64)
  const signal = new AbortController().signal
  const observed = []
  const stub = spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
    observed.push({ url, method: init.method, body: init.body, cache: init.cache })
    return Response.json(init.method === 'PUT' ? { available_until: '2026-01-02T00:00:00Z' } : { completions: [] })
  })
  try {
    await releaseRecordCompletionExport('synthetic-org', 'synthetic-token', 'request-id', digest, signal)
    const blob = await downloadRecordCompletionExport('synthetic-org', 'synthetic-token', 'request-id', signal)
    expect(JSON.parse(await blob.text())).toEqual({ completions: [] })
    expect(observed.map(({ url }) => url.split('/orgs/')[1])).toEqual([
      'synthetic-org/record-requests/request-id/completion-export',
      'synthetic-org/learners/me/record-requests/request-id/completion-export',
    ])
    expect(observed.map(({ method, cache }) => [method, cache])).toEqual([['PUT', 'no-store'], ['POST', 'no-store']])
    expect(JSON.parse(observed[0].body)).toEqual({ snapshot_sha256: digest })
    expect(observed[1].body).toBeNull()
  } finally {
    stub.mockRestore()
  }
})

test('case evidence upload sends private multipart data and the supplied retry key', async () => {
  const receipt = { evidence_reference: 'ev_synthetic_record', content_sha256: 'a'.repeat(64), verified_at: '2026-01-01T00:00:00Z' }
  const signal = new AbortController().signal
  const fetch = spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
    expect(url.endsWith('/orgs/synthetic-org/record-requests/request-id/evidence')).toBe(true)
    expect(init.method).toBe('POST')
    expect(init.cache).toBe('no-store')
    expect(init.signal).toBe(signal)
    expect(init.headers).toEqual({ Authorization: 'Bearer synthetic-token', 'Idempotency-Key': 'retry-key' })
    expect(init.body.get('file').name).toBe('synthetic.pdf')
    return Response.json(receipt, { status: 201 })
  })
  try {
    const file = new File(['%PDF-1.4'], 'synthetic.pdf', { type: 'application/pdf' })
    expect(await uploadRecordRequestEvidence('synthetic-org', 'synthetic-token', 'request-id', file, 'retry-key', signal)).toEqual(receipt)
  } finally {
    fetch.mockRestore()
  }
})

test('privacy account subpage stays reachable and has a page title', () => {
  const allowedSubpages = accountSubpage.match(/const VALID_SUBPAGES = \[([^\]]+)\]/)?.[1]

  expect(allowedSubpages).toContain("'privacy'")
  expect(accountSubpage).toContain("'privacy': 'Privacy'")
})

test('privacy settings read only the authenticated learner consent history', () => {
  expect(privacyService).toContain('/learners/me/consent-events?limit=100')
  expect(privacySettings).toContain('getMyConsentEventsForSelf')
  expect(privacySettings).toContain('studytomy-consent-history-title')
  expect(privacySettings).not.toContain('consent-events/${event.event_uuid}/withdraw')
})

test('consent choices require current approved notices and self-derived write routes', () => {
  expect(privacyService).toContain('/learners/me/consent-notices/')
  expect(privacyService).toContain('/learners/me/consent-events`')
  expect(privacyService).toContain('/learners/me/consent-events/${encodeURIComponent(eventUuid)}/withdraw')
  expect(privacyService).toContain("[403, 404, 409].includes(response.status)")
  expect(privacyService).toContain("headers.set('Idempotency-Key', idempotencyKey)")
  expect(consentSettings).toContain('notice.notice_text')
  expect(consentSettings).toContain('scope_codes: notice.scope_codes')
  expect(consentSettings).toContain('historyReady')
  expect(consentSettings).toContain("saveChoice(purpose, 'decline')")
  expect(consentSettings).toContain('withdrawChoice(purpose, state.withdrawEventUuid!)')
  expect(consentSettings).toContain('isCurrentAcceptedChoice(events, event)')
  expect(consentSettings).toContain('historicalWithdrawals.map')
  expect(consentSettings).not.toContain('learner_uuid')
  expect(consentSettings).not.toContain('localStorage')
})
