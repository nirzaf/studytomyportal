import { expect, spyOn, test } from 'bun:test'
import { Window } from 'happy-dom'
import React, { act } from 'react'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import AssessmentIntegrityReview from '../components/Studytomy/AssessmentIntegrityReview'
import english from '../locales/en.json'

test('moderator reviews the learner response and can dismiss only after choosing a reason', async () => {
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
  const requests = []
  const fetch = spyOn(globalThis, 'fetch').mockImplementation(async (url, options) => {
    requests.push({ url, ...options })
    if (url.includes('submission_denied')) return new Response(null, { status: 404 })
    if (options.method === 'PUT') return Response.json({ reference: 'case_fixture', status: 'dismissed' })
    return Response.json({
      reference: 'case_fixture', status: 'response_received', response: 'request_staff_contact',
    })
  })
  const render = (submissionUuid) => root.render(React.createElement(I18nextProvider, { i18n },
    React.createElement(AssessmentIntegrityReview, {
      key: submissionUuid, orgUuid: 'org_fixture', token: 'synthetic-token',
      courseUuid: 'course_fixture', assignmentUuid: 'assignment_fixture', submissionUuid, attempt: 2,
    })))
  try {
    await act(async () => render('submission_denied'))
    expect(container.querySelector('section')).toBeNull()

    await act(async () => render('submission_fixture'))
    expect(requests[1].url).toEndWith('/submissions/submission_fixture/integrity-cases?attempt_number=2')
    expect(container.querySelector('[role=status]').textContent).toBe(copy.request_staff_contact)
    const button = container.querySelector('button')
    expect(button.disabled).toBe(true)
    const select = container.querySelector('select')
    await act(async () => {
      select.value = 'insufficient_evidence'
      select.dispatchEvent(new dom.Event('change', { bubbles: true }))
    })
    expect(button.disabled).toBe(false)
    await act(async () => button.click())
    expect(requests[2].method).toBe('PUT')
    expect(requests[2].url).toEndWith('/integrity-cases/case_fixture/dismissal?attempt_number=2')
    expect(JSON.parse(requests[2].body)).toEqual({ reason: 'insufficient_evidence' })
    expect(container.querySelector('[role=status]').textContent).toBe(copy.dismissed)
    expect(container.querySelector('button')).toBeNull()
  } finally {
    await act(async () => root.unmount())
    fetch.mockRestore()
    await dom.happyDOM.close()
  }
})
