import { expect, spyOn, test } from 'bun:test'
import { Window } from 'happy-dom'
import React, { act } from 'react'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import AssessmentAppeal from '../components/Studytomy/AssessmentAppeal'
import english from '../locales/en.json'

test('learner requests review of the displayed result and can load a corrected grade', async () => {
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

  const requests = []
  let receipt = null
  let canRequest = true
  let gradeRefreshes = 0
  const fetch = spyOn(globalThis, 'fetch').mockImplementation(async (url, options) => {
    requests.push({ url, ...options })
    if (options.method === 'POST') {
      receipt = { appealed_result_uuid: 'original', result_uuid: 'original', status: 'received' }
      return Response.json(receipt)
    }
    return Response.json({ receipt, can_request_review: canRequest })
  })
  const render = (resultUuid) => root.render(React.createElement(I18nextProvider, { i18n },
    React.createElement(AssessmentAppeal, {
      key: resultUuid, resultUuid, orgUuid: 'org_fixture', courseUuid: 'course_fixture',
      assignmentUuid: 'assignment_fixture', submissionUuid: 'submission_fixture',
      accessToken: 'synthetic-token', onRefreshGrade: async () => {
        gradeRefreshes++
        return true
      },
    }),
  ))
  const button = (label) => [...container.querySelectorAll('button')].find((item) => item.textContent === label)
  try {
    await act(async () => render('original'))
    expect(requests.map(({ method }) => method)).toEqual(['GET'])
    expect(button('Request a review')).toBeDefined()
    await act(async () => button('Request a review').click())
    expect(JSON.parse(requests[1].body)).toEqual({ result_uuid: 'original' })
    expect(container.querySelector('[role=status]').textContent).toContain('Your request was received.')
    expect(button('Request a review')).toBeUndefined()

    receipt = { appealed_result_uuid: 'original', result_uuid: 'corrected', status: 'result_corrected' }
    await act(async () => button('Refresh review status').click())
    expect(container.querySelector('[role=status]').textContent).toContain('The review corrected your result.')
    await act(async () => button('Load updated grade').click())
    expect(gradeRefreshes).toBe(1)

    await act(async () => render('corrected'))
    expect(button('Load updated grade')).toBeUndefined()
    expect(button('Request a review')).toBeDefined()
    canRequest = false
    await act(async () => button('Refresh review status').click())
    expect(button('Request a review')).toBeUndefined()
    expect(container.querySelector('[role=status]').textContent).toContain('The review corrected your result.')
    expect(requests.filter(({ method }) => method === 'POST')).toHaveLength(1)
  } finally {
    await act(async () => root.unmount())
    fetch.mockRestore()
    await dom.happyDOM.close()
  }
})
