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

function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

test('GuardianConsentPanel discards late child-A reads and mutations after switching to child-B or org/session change (T-02)', async () => {
  const { default: GuardianConsentPanel } = await import('../components/Studytomy/GuardianConsentPanel.tsx')
  const dom = createDomHarness()
  const { createRoot } = await import('react-dom/client')
  const container = dom.document.createElement('div')
  dom.document.body.appendChild(container)
  const root = createRoot(container)
  const i18n = createInstance()
  await i18n.init({ lng: 'en', resources: { en: { translation: english } } })

  const childANotice = deferred()
  const childAHistory = deferred()
  const childAMutation = deferred()
  let childAReadResolved = false
  let childBHistoryResolve = null

  const fetchSpy = spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
    const href = String(url)
    const method = (init?.method || 'GET').toUpperCase()

    if (href.includes('/my-relationships')) {
      if (href.includes('/orgs/org_two/')) {
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      return new Response(
        JSON.stringify([
          {
            relationship_uuid: 'rel_child_a',
            learner_uuid: 'learner_a',
            status: 'accepted',
            approved_scopes: ['consent_management'],
            valid_from: '2026-01-01T00:00:00Z',
            valid_to: '2027-01-01T00:00:00Z',
            authority_version: 1,
          },
          {
            relationship_uuid: 'rel_child_b',
            learner_uuid: 'learner_b',
            status: 'accepted',
            approved_scopes: ['consent_management'],
            valid_from: '2026-01-01T00:00:00Z',
            valid_to: '2027-01-01T00:00:00Z',
            authority_version: 2,
          },
        ]),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    }

    if (href.includes('/dependents/learner_a/consent-notices/learning_terms')) {
      if (childAReadResolved) {
        return new Response(
          JSON.stringify({
            purpose: 'learning_terms',
            notice_version: 'v-a',
            notice_text: 'Notice for Child A.',
            scope_codes: ['course_release'],
            policy_version_ids: [9],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        )
      }
      return childANotice.promise
    }
    if (href.includes('/dependents/learner_a/consent-notices/')) {
      return new Response(JSON.stringify({ detail: 'Unavailable' }), { status: 404 })
    }
    if (method === 'GET' && href.includes('/dependents/learner_a/consent-events')) {
      if (childAReadResolved) {
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      return childAHistory.promise
    }
    if (method === 'POST' && href.endsWith('/dependents/learner_a/consent-events')) {
      return childAMutation.promise
    }

    if (href.includes('/dependents/learner_b/consent-notices/learning_terms')) {
      return new Response(
        JSON.stringify({
          purpose: 'learning_terms',
          notice_version: 'v-b',
          notice_text: 'Notice specifically for Child B.',
          scope_codes: ['course_release'],
          policy_version_ids: [10],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    }
    if (href.includes('/dependents/learner_b/consent-notices/')) {
      return new Response(JSON.stringify({ detail: 'Unavailable' }), { status: 404 })
    }
    if (method === 'GET' && href.includes('/dependents/learner_b/consent-events')) {
      return await new Promise((resolve) => {
        childBHistoryResolve = () =>
          resolve(
            new Response(JSON.stringify([]), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            })
          )
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
          React.createElement(GuardianConsentPanel, {
            accessToken: 'tok_guardian',
            orgUuid: 'org_one',
          })
        )
      )
    })
    await act(async () => {
      await new Promise((r) => setTimeout(r, 15))
    })

    // Switch to child B while child A reads are still inflight
    const selectEl = container.querySelector('#guardian-dependent-select')
    expect(selectEl).toBeTruthy()
    await act(async () => {
      selectEl.value = 'learner_b'
      selectEl.dispatchEvent(new dom.Event('change', { bubbles: true }))
    })
    await act(async () => {
      childBHistoryResolve?.()
      await new Promise((r) => setTimeout(r, 15))
    })

    // Resolve delayed child A read responses AFTER selecting child B
    await act(async () => {
      childAReadResolved = true
      childANotice.resolve(
        new Response(
          JSON.stringify({
            purpose: 'learning_terms',
            notice_version: 'v-a',
            notice_text: 'STALE NOTICE FOR CHILD A',
            scope_codes: ['course_release'],
            policy_version_ids: [9],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        )
      )
      childAHistory.resolve(
        new Response(
          JSON.stringify([
            {
              event_uuid: 'ev_stale_child_a',
              purpose: 'learning_terms',
              choice: 'accept',
              withdraws_event_uuid: null,
              notice_version: 'v-b',
              scope_codes: ['course_release'],
              policy_version_ids: [10],
              provenance: 'guardian',
              effective_at: '2026-11-20T00:00:00Z',
              created_at: '2026-11-20T00:00:00Z',
            },
          ]),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        )
      )
      await new Promise((r) => setTimeout(r, 15))
    })

    expect(container.textContent).toContain('Notice specifically for Child B.')
    expect(container.textContent).not.toContain('STALE NOTICE FOR CHILD A')
    expect(container.textContent).toContain(english.account.privacy_consent_no_choice)

    // Switch back to child A, let A finish loading, start A accept mutation, then switch back to B before mutation settles
    await act(async () => {
      selectEl.value = 'learner_a'
      selectEl.dispatchEvent(new dom.Event('change', { bubbles: true }))
    })
    await act(async () => {
      await new Promise((r) => setTimeout(r, 15))
    })
    const acceptBtnA = [...container.querySelectorAll('button')].find((b) =>
      b.textContent?.includes(english.account.privacy_consent_accept)
    )
    expect(acceptBtnA).toBeTruthy()
    await act(async () => {
      acceptBtnA.click()
    })
    await act(async () => {
      selectEl.value = 'learner_b'
      selectEl.dispatchEvent(new dom.Event('change', { bubbles: true }))
    })
    await act(async () => {
      childBHistoryResolve?.()
      await new Promise((r) => setTimeout(r, 15))
    })
    // Now resolve A's pending mutation
    await act(async () => {
      childAMutation.resolve(
        new Response(
          JSON.stringify({
            event_uuid: 'ev_late_mutation_a',
            purpose: 'learning_terms',
            choice: 'accept',
            withdraws_event_uuid: null,
            notice_version: 'v-b',
            scope_codes: ['course_release'],
            policy_version_ids: [10],
            provenance: 'guardian',
            effective_at: '2026-11-20T01:00:00Z',
            created_at: '2026-11-20T01:00:00Z',
          }),
          { status: 201, headers: { 'Content-Type': 'application/json' } }
        )
      )
      await new Promise((r) => setTimeout(r, 15))
    })
    expect(container.textContent).toContain('Notice specifically for Child B.')
    expect(container.textContent).toContain(english.account.privacy_consent_no_choice)
    expect(container.textContent).not.toContain(english.account.privacy_consent_saved)

    // Organization switch clears previous org's state cleanly
    await act(async () => {
      root.render(
        React.createElement(
          I18nextProvider,
          { i18n },
          React.createElement(GuardianConsentPanel, {
            accessToken: 'tok_guardian',
            orgUuid: 'org_two',
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
