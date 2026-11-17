import { expect, spyOn, test } from 'bun:test'
import { Window } from 'happy-dom'
import React, { act } from 'react'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { SessionContext } from '../components/Contexts/LHSessionContext'
import RefundReview from '../components/Studytomy/RefundReview'

test('refund approval clears across staff accounts and distinguishes a reservation from settlement', async () => {
  const dom = new Window({ url: 'http://localhost/' })
  globalThis.window = dom
  globalThis.document = dom.document
  const originalFormData = globalThis.FormData
  globalThis.FormData = dom.FormData
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const { createRoot } = await import('react-dom/client')
  const container = dom.document.createElement('div')
  dom.document.body.appendChild(container)
  const root = createRoot(container)
  const i18n = createInstance()
  await i18n.init({ lng: 'en', resources: { en: { translation: {} } } })

  const order = '10000000-0000-0000-0000-000000000001'
  const request = {
    refund_reference: '20000000-0000-0000-0000-000000000002',
    attempt_reference: '30000000-0000-0000-0000-000000000003',
    amount_minor: 60, currency: 'AUD', reason_code: 'synthetic_refund', status: 'requested',
  }
  const writes = []
  const fetch = spyOn(globalThis, 'fetch').mockImplementation(async (url, options) => {
    if (options.method === 'GET') {
      expect(options.cache).toBe('no-store')
      return Response.json(request)
    }
    writes.push({ url, body: JSON.parse(options.body) })
    return Response.json({
      refund_reference: request.refund_reference,
      status: writes.length === 1 ? 'awaiting_second_approval' : 'reserved',
    })
  })

  const render = (id) => root.render(React.createElement(I18nextProvider, { i18n },
    React.createElement(SessionContext.Provider, {
      value: { status: 'authenticated', data: { user: { id }, tokens: { access_token: 'synthetic-token' } } },
    }, React.createElement(RefundReview, { orgUuid: 'org_fixture' })),
  ))
  const approve = () => [...container.querySelectorAll('button')].find((item) => item.textContent === 'Approve refund')
  function lookup() {
    container.querySelector('[name=order]').value = order
    container.querySelector('[name=refund]').value = request.refund_reference
    container.querySelector('form').dispatchEvent(new dom.Event('submit', { bubbles: true, cancelable: true }))
  }

  try {
    await act(async () => render(1))
    expect(approve()).toBeUndefined()
    await act(async () => lookup())
    await act(async () => approve().click())
    expect(container.querySelector('[role=status]').textContent).toContain('A second approver is required')
    expect(approve()).toBeUndefined()

    await act(async () => render(2))
    expect(container.querySelector('[role=status]')).toBeNull()
    expect(container.querySelector('[name=order]').value).toBe('')
    await act(async () => lookup())
    await act(async () => approve().click())
    expect(container.querySelector('[role=status]').textContent).toBe('Funds reserved. Money return is pending.')
    expect(container.textContent).not.toContain('External money return confirmed')
    expect(writes).toHaveLength(2)
    expect(writes[1]).toEqual(writes[0])
    expect(writes[0].url).toEndWith(`/collections/${order}/manual-attempts/${request.attempt_reference}/refund-reservations/${request.refund_reference}`)
    expect(writes[0].body).toEqual({ amount_minor: 60, currency: 'AUD', reason_code: 'synthetic_refund' })
  } finally {
    await act(async () => root.unmount())
    fetch.mockRestore()
    globalThis.FormData = originalFormData
    await dom.happyDOM.close()
  }
})
