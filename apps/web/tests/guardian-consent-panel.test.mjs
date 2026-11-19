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
  globalThis.Node = dom.Node
  globalThis.Event = dom.Event
  globalThis.MouseEvent = dom.MouseEvent
  globalThis.CustomEvent = dom.CustomEvent
  globalThis.Element = dom.Element
  return dom
}

test('GuardianConsentPanel hides UI when user lacks accepted consent_management scope (TASK-08)', async () => {
  const { default: GuardianConsentPanel } = await import('../components/Studytomy/GuardianConsentPanel.tsx')
  const dom = createDomHarness()
  const { createRoot } = await import('react-dom/client')
  const container = dom.document.createElement('div')
  dom.document.body.appendChild(container)
  const root = createRoot(container)
  const i18n = createInstance()
  await i18n.init({ lng: 'en', resources: { en: { translation: english } } })

  const fetchSpy = spyOn(globalThis, 'fetch').mockImplementation(async (url) => {
    if (String(url).includes('/my-relationships')) {
      return new Response(
        JSON.stringify([
          {
            relationship_uuid: 'rel_payer_only',
            learner_uuid: 'learner_other',
            status: 'accepted',
            approved_scopes: ['purchase_allocation'],
            valid_from: '2026-01-01T00:00:00Z',
            valid_to: '2027-01-01T00:00:00Z',
            authority_version: 2,
          },
        ]),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    }
    return new Response(JSON.stringify([]), { status: 404 })
  })

  try {
    await act(async () => {
      root.render(
        React.createElement(
          I18nextProvider,
          { i18n },
          React.createElement(GuardianConsentPanel, {
            accessToken: 'tok_payer',
            orgUuid: 'org_lk',
          })
        )
      )
    })
    expect(container.textContent?.trim()).toBe('')
  } finally {
    fetchSpy.mockRestore()
    await act(async () => root.unmount())
    await dom.happyDOM.close()
  }
})

test('GuardianConsentPanel allows verified guardian to view notice, accept, and withdraw dependent consent (TASK-08)', async () => {
  const { default: GuardianConsentPanel } = await import('../components/Studytomy/GuardianConsentPanel.tsx')
  const dom = createDomHarness()
  const { createRoot } = await import('react-dom/client')
  const container = dom.document.createElement('div')
  dom.document.body.appendChild(container)
  const root = createRoot(container)
  const i18n = createInstance()
  await i18n.init({ lng: 'en', resources: { en: { translation: english } } })

  const recordedCalls = []
  const fetchSpy = spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
    const href = String(url)
    const method = (init?.method || 'GET').toUpperCase()
    recordedCalls.push({ href, method, headers: init?.headers, body: init?.body })

    if (href.includes('/my-relationships')) {
      return new Response(
        JSON.stringify([
          {
            relationship_uuid: 'rel_guard_1',
            learner_uuid: 'learner_child_lk_1',
            status: 'accepted',
            approved_scopes: ['consent_management', 'adaptive_report_summary'],
            valid_from: '2026-01-01T00:00:00Z',
            valid_to: '2027-01-01T00:00:00Z',
            authority_version: 2,
          },
        ]),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    }
    if (href.includes('/dependents/learner_child_lk_1/consent-notices/learning_terms')) {
      return new Response(
        JSON.stringify({
          purpose: 'learning_terms',
          notice_version: 'v1-lk-learning',
          notice_text: 'Sri Lanka Grades 6-13 approved learning terms notice.',
          scope_codes: ['course_release', 'adaptive_progress_report'],
          policy_version_ids: [1, 2],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    }
    if (href.includes('/dependents/learner_child_lk_1/consent-notices/')) {
      return new Response(JSON.stringify({ detail: 'Unavailable' }), { status: 404 })
    }
    if (method === 'GET' && href.includes('/dependents/learner_child_lk_1/consent-events')) {
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    if (method === 'POST' && href.endsWith('/dependents/learner_child_lk_1/consent-events')) {
      return new Response(
        JSON.stringify({
          event_uuid: 'ev_guard_accept_1',
          purpose: 'learning_terms',
          choice: 'accept',
          notice_version: 'v1-lk-learning',
          scope_codes: ['course_release', 'adaptive_progress_report'],
          policy_version_ids: [1, 2],
          effective_at: '2026-11-20T00:00:00Z',
        }),
        { status: 201, headers: { 'Content-Type': 'application/json' } }
      )
    }
    if (
      method === 'POST' &&
      href.endsWith('/dependents/learner_child_lk_1/consent-events/ev_guard_accept_1/withdraw')
    ) {
      return new Response(
        JSON.stringify({
          event_uuid: 'ev_guard_withdraw_1',
          purpose: 'learning_terms',
          choice: 'withdraw',
          notice_version: 'v1-lk-learning',
          scope_codes: ['course_release', 'adaptive_progress_report'],
          policy_version_ids: [1, 2],
          effective_at: '2026-11-20T00:05:00Z',
        }),
        { status: 201, headers: { 'Content-Type': 'application/json' } }
      )
    }
    return new Response(JSON.stringify({}), { status: 404 })
  })

  try {
    await act(async () => {
      root.render(
        React.createElement(
          I18nextProvider,
          { i18n },
          React.createElement(GuardianConsentPanel, {
            accessToken: 'tok_guardian',
            orgUuid: 'org_lk',
          })
        )
      )
    })
    // Wait a tick for async dependent notice/history load
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20))
    })

    expect(container.textContent).toContain('Guardian Consent for Dependent Learners')
    expect(container.textContent).toContain('learner_child_lk_1')
    expect(container.textContent).toContain('Sri Lanka Grades 6-13 approved learning terms notice.')

    const acceptBtn = [...container.querySelectorAll('button')].find((b) =>
      b.textContent?.includes(english.account.privacy_consent_accept)
    )
    expect(acceptBtn).toBeTruthy()
    await act(async () => {
      acceptBtn.click()
    })

    const postCall = recordedCalls.find(
      (c) => c.method === 'POST' && c.href.endsWith('/dependents/learner_child_lk_1/consent-events')
    )
    expect(postCall).toBeTruthy()
    expect(new Headers(postCall.headers).get('Idempotency-Key')).toBeTruthy()

    const withdrawBtn = [...container.querySelectorAll('button')].find((b) =>
      b.textContent?.includes(english.account.privacy_consent_withdraw)
    )
    expect(withdrawBtn).toBeTruthy()
    await act(async () => {
      withdrawBtn.click()
    })

    const withdrawCall = recordedCalls.find(
      (c) =>
        c.method === 'POST' &&
        c.href.endsWith('/dependents/learner_child_lk_1/consent-events/ev_guard_accept_1/withdraw')
    )
    expect(withdrawCall).toBeTruthy()
    expect(new Headers(withdrawCall.headers).get('Idempotency-Key')).toBeTruthy()
  } finally {
    fetchSpy.mockRestore()
    await act(async () => root.unmount())
    await dom.happyDOM.close()
  }
})
