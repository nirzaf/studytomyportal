import { expect, spyOn, test } from 'bun:test'
import { Window } from 'happy-dom'
import React, { act } from 'react'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import english from '../locales/en.json'

function createDomHarness() {
  const dom = new Window({ url: 'http://localhost:3000/' })
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  globalThis.window = dom
  globalThis.document = dom.document
  globalThis.navigator = dom.navigator
  globalThis.HTMLElement = dom.HTMLElement
  globalThis.HTMLInputElement = dom.HTMLInputElement
  globalThis.HTMLSelectElement = dom.HTMLSelectElement
  globalThis.HTMLFormElement = dom.HTMLFormElement
  globalThis.FormData = dom.FormData
  globalThis.Node = dom.Node
  globalThis.Event = dom.Event
  globalThis.MouseEvent = dom.MouseEvent
  globalThis.CustomEvent = dom.CustomEvent
  globalThis.Element = dom.Element
  return dom
}

test('T-10 StaffReportReview includes eligible retired curricula alongside published curricula and excludes drafts', async () => {
  const { default: StaffReportReview } = await import('../components/Studytomy/StaffReportReview.tsx')
  const dom = createDomHarness()
  const { createRoot } = await import('react-dom/client')
  const container = dom.document.createElement('div')
  dom.document.body.appendChild(container)
  const root = createRoot(container)
  const i18n = createInstance()
  await i18n.init({ lng: 'en', resources: { en: { translation: english } } })

  const calls = []
  const client = async (path, method = 'GET', body) => {
    calls.push({ path, method, body })
    return { items: [], next_cursor: null }
  }

  const curricula = [
    { uuid: 'curr_a_retired', label: 'Physics G12 Rev 1', status: 'retired', version: 2, content_digest: 'a'.repeat(64) },
    { uuid: 'curr_b_published', label: 'Physics G12 Rev 2', status: 'published', version: 2, content_digest: 'b'.repeat(64) },
    { uuid: 'curr_c_draft', label: 'Physics G12 Draft 3', status: 'draft', version: 1, content_digest: null },
  ]

  try {
    await act(async () => {
      root.render(
        React.createElement(
          I18nextProvider,
          { i18n },
          React.createElement(StaffReportReview, { client, curricula })
        )
      )
    })

    const options = Array.from(container.querySelectorAll('select option')).map((o) => ({
      value: o.value,
      text: o.textContent,
    }))
    expect(options.map((o) => o.value)).toEqual(['curr_a_retired', 'curr_b_published'])
    expect(options[0].text).toContain('Retired')
    expect(options[1].text).toContain('Published')
    expect(options.some((o) => o.value === 'curr_c_draft')).toBe(false)
  } finally {
    await act(async () => root.unmount())
    await dom.happyDOM.close()
  }
})

test('T-13 & T-14 FamilyWorkspace exposes case_ warning, bound evidence open/upload, and learner lifecycle controls', async () => {
  const { default: FamilyWorkspace } = await import('../components/Studytomy/FamilyWorkspace.tsx')
  const dom = createDomHarness()
  const { createRoot } = await import('react-dom/client')
  const container = dom.document.createElement('div')
  dom.document.body.appendChild(container)
  const root = createRoot(container)
  const i18n = createInstance()
  await i18n.init({ lng: 'en', resources: { en: { translation: english } } })

  const openedUrls = []
  dom.open = (url) => {
    openedUrls.push(String(url))
    return null
  }
  const origCreateObj = URL.createObjectURL
  const origRevokeObj = URL.revokeObjectURL
  URL.createObjectURL = () => 'blob:mock-evidence'
  URL.revokeObjectURL = () => {}

  const fetchCalls = []
  const fetchSpy = spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
    const href = String(url)
    const method = (init?.method || 'GET').toUpperCase()
    fetchCalls.push({ href, method })

    if (href.includes('/family-review-queue')) {
      return new Response(
        JSON.stringify([
          {
            learner_uuid: 'lrn_child_1',
            relationship_uuid: 'rel_child_1',
            status: 'pending',
            requested_scopes: ['consent_management'],
            approved_scopes: [],
            authority_version: 1,
            updated_at: '2026-11-20T00:00:00Z',
          },
        ]),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    }
    if (href.includes('/relationships/rel_child_1/evidence-files/ev_bound_1234567890123456/content')) {
      return new Response(new Blob(['%PDF-1.4 mock'], { type: 'application/pdf' }), {
        status: 200,
        headers: { 'Content-Type': 'application/pdf' },
      })
    }
    if (href.includes('/relationships/rel_child_1/evidence-files')) {
      return new Response(
        JSON.stringify([
          {
            evidence_uuid: 'ev_bound_1234567890123456',
            learner_uuid: 'lrn_child_1',
            purpose: 'guardian_authority',
            content_sha256: 'a'.repeat(64),
            policy_version_ids: [1],
            media_type: 'application/pdf',
            size_bytes: 1200,
            retention_category: 'education_7y',
            upload_status: 'ready',
            created_at: '2026-11-20T00:00:00Z',
            updated_at: '2026-11-20T00:00:00Z',
          },
        ]),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    }
    if (href.includes('/relationships/rel_child_1')) {
      return new Response(
        JSON.stringify({
          learner_uuid: 'lrn_child_1',
          relationship_uuid: 'rel_child_1',
          status: 'pending',
          requested_scopes: ['consent_management'],
          approved_scopes: [],
          request_evidence_reference: 'case_external_ticket_000012345',
          authority_version: 1,
          created_at: '2026-11-20T00:00:00Z',
          updated_at: '2026-11-20T00:00:00Z',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    }
    if (href.includes('/allocations/pending')) {
      return new Response(JSON.stringify({ items: [], next_cursor: null }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    return new Response(JSON.stringify({}), { status: 404 })
  })

  try {
    await act(async () => {
      root.render(
        React.createElement(
          I18nextProvider,
          { i18n },
          React.createElement(FamilyWorkspace, {
            orgUuid: 'org_lk',
            accessToken: 'tok_staff',
          })
        )
      )
    })
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30))
    })

    // Opaque case_ reference warning is rendered honestly
    expect(container.textContent).toContain('Opaque external case reference')
    // Upload bound evidence control and Learner lifecycle panel are rendered
    expect(container.textContent).toContain('Upload bound evidence file')
    expect(container.textContent).toContain('Learner intake, eligibility & activation')
    expect(container.textContent).toContain('School grade does not establish learner age')

    // Click "Open original file" button and verify authorized content fetch
    const openBtn = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Open original file')
    )
    expect(openBtn).toBeTruthy()
    await act(async () => {
      openBtn.click()
      await new Promise((r) => setTimeout(r, 20))
    })
    expect(
      fetchCalls.some(
        (c) =>
          c.method === 'GET' &&
          c.href.includes('/relationships/rel_child_1/evidence-files/ev_bound_1234567890123456/content')
      )
    ).toBe(true)
    expect(openedUrls).toEqual(['blob:mock-evidence'])
  } finally {
    URL.createObjectURL = origCreateObj
    URL.revokeObjectURL = origRevokeObj
    fetchSpy.mockRestore()
    await act(async () => root.unmount())
    await dom.happyDOM.close()
  }
})
