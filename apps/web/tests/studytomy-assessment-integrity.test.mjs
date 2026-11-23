import { expect, spyOn, test } from 'bun:test'
import { Window } from 'happy-dom'
import React, { act } from 'react'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import AssessmentIntegrityCases from '../components/Studytomy/AssessmentIntegrityCases'
import english from '../locales/en.json'

test('learner can recover the case list, dispute a concern, and see its dismissal', async () => {
  const dom = new Window({ url: 'http://localhost/' })
  globalThis.window = dom
  globalThis.document = dom.document
  globalThis.IS_REACT_ACT_ENVIRONMENT = true
  const { createRoot } = await import('react-dom/client')
  const container = dom.document.createElement('div')
  dom.document.body.appendChild(container)
  const root = createRoot(container)
  const i18n = createInstance()
  await i18n.init({ lng: 'en', resources: { en: { translation: english } } })
  const copy = english.assignments.integrity
  const button = (label) => [...container.querySelectorAll('button')].find((item) => item.textContent === label)
  const status = () => container.querySelector('[role=status]')?.textContent
  const requests = []
  let listUnavailable = true
  let pendingList
  const assessmentTitle = 'Synthetic practice assessment'
  let receipt = { reference: 'fixture-case', status: 'awaiting_review' }
  const fetch = spyOn(globalThis, 'fetch').mockImplementation(async (url, options) => {
    requests.push({ url, ...options })
    if (options.method === 'PUT') {
      receipt = { ...receipt, status: 'response_received' }
      return Response.json(receipt)
    }
    if (listUnavailable) return new Response(null, { status: 500 })
    if (pendingList) await pendingList
    return Response.json([{ ...receipt, assessment_title: assessmentTitle, attempt_number: 2 }])
  })

  try {
    await act(async () => root.render(React.createElement(I18nextProvider, { i18n },
      React.createElement(AssessmentIntegrityCases, { orgUuid: 'org_fixture', token: 'synthetic-token' }),
    )))
    expect(container.querySelector('[role=alert]').textContent).toBe(copy.error)
    expect(button(copy.dispute)).toBeUndefined()

    listUnavailable = false
    await act(async () => button(copy.refresh).click())
    expect(container.querySelector('[role=alert]')).toBeNull()
    expect(status()).toBe(copy.awaiting_review)
    expect(container.querySelector('h3').textContent).toBe(assessmentTitle)
    expect(container.textContent).toContain('Attempt 2')
    expect(button(copy.contact)).toBeDefined()

    let resolveList
    pendingList = new Promise((resolve) => { resolveList = resolve })
    await act(async () => button(copy.refresh).click())
    expect(button(copy.refresh).disabled).toBe(true)
    expect(button(copy.dispute).disabled).toBe(true)
    expect(button(copy.contact).disabled).toBe(true)
    await act(async () => button(copy.dispute).click())
    expect(requests.map(({ method }) => method)).toEqual(['GET', 'GET', 'GET'])
    await act(async () => resolveList())
    pendingList = undefined

    await act(async () => button(copy.dispute).click())
    expect(requests[3].url).toEndWith('/orgs/org_fixture/me/assessment-integrity-cases/fixture-case/response')
    expect(JSON.parse(requests[3].body)).toEqual({ response: 'dispute_concern' })
    expect(status()).toBe(copy.response_received)
    expect(container.querySelector('h3').textContent).toBe(assessmentTitle)
    expect(container.textContent).toContain('Attempt 2')
    expect(button(copy.dispute)).toBeUndefined()
    expect(button(copy.contact)).toBeUndefined()

    receipt = { ...receipt, status: 'dismissed' }
    await act(async () => button(copy.refresh).click())
    expect(status()).toBe(copy.dismissed)
    expect(requests.map(({ method }) => method)).toEqual(['GET', 'GET', 'GET', 'PUT', 'GET'])
  } finally {
    await act(async () => root.unmount())
    fetch.mockRestore()
    await dom.happyDOM.close()
  }
})
