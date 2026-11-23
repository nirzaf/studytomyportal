import { expect, test } from 'bun:test'
import { Window } from 'happy-dom'
import React, { act } from 'react'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import AdaptiveLessonProgress from '../components/Objects/Activities/AdaptiveLessonProgress'

test('completed adaptive attempts refresh visible mastery and recommendation data', async () => {
  const dom = new Window({ url: 'http://localhost/' })
  const previous = {
    window: globalThis.window,
    document: globalThis.document,
    navigator: globalThis.navigator,
    HTMLElement: globalThis.HTMLElement,
    Node: globalThis.Node,
    Event: globalThis.Event,
    MouseEvent: globalThis.MouseEvent,
    IS_REACT_ACT_ENVIRONMENT: globalThis.IS_REACT_ACT_ENVIRONMENT,
  }
  globalThis.window = dom
  globalThis.document = dom.document
  globalThis.navigator = dom.navigator
  globalThis.HTMLElement = dom.HTMLElement
  globalThis.Node = dom.Node
  globalThis.Event = dom.Event
  globalThis.MouseEvent = dom.MouseEvent
  globalThis.IS_REACT_ACT_ENVIRONMENT = true

  const { createRoot } = await import('react-dom/client')
  const container = dom.document.createElement('div')
  dom.document.body.appendChild(container)
  const root = createRoot(container)
  const i18n = createInstance()
  await i18n.init({ lng: 'en', resources: { en: { translation: {} } } })

  const release = { course_uuid: 'course_synthetic', course_release_uuid: 'course_release_synthetic' }
  let completed = false
  let failNextMastery = false
  let failNextRecommendation = false
  let returnNoRecommendations = false
  const calls = []
  const client = async path => {
    calls.push(path)
    if (path.startsWith('me/lesson-mastery?')) {
      if (failNextMastery) {
        failNextMastery = false
        throw new Error('Synthetic temporary failure')
      }
      return {
        next_offset: null,
        concepts: [{
          concept_uuid: 'concept_synthetic', title: 'Equivalent fractions',
          state: completed ? 'developing' : 'not_assessed',
          independent_family_count: completed ? 1 : 0,
          assisted_count: 0, review_due: false,
        }],
      }
    }
    if (path.startsWith('me/recommendations?')) {
      if (failNextRecommendation) {
        failNextRecommendation = false
        throw new Error('Synthetic temporary recommendation failure')
      }
      if (returnNoRecommendations) return []
      return [{
        activity_uuid: 'activity_next', course_uuid: release.course_uuid,
        course_release_uuid: release.course_release_uuid,
        activity_title: completed ? 'Next supported lesson' : 'Review equivalent fractions',
        reason: 'practice_needed',
      }]
    }
    throw new Error(`Unexpected adaptive request: ${path}`)
  }
  const render = refreshKey => root.render(React.createElement(
    I18nextProvider,
    { i18n },
    React.createElement(AdaptiveLessonProgress, {
      client,
      activityUuid: 'activity_current',
      release,
      refreshKey,
      onOpenRecommendation: async () => true,
    }),
  ))

  try {
    await act(async () => render(0))
    const progress = container.querySelector('button')
    expect(progress?.textContent).toContain('Concept progress')
    await act(async () => progress.dispatchEvent(new dom.MouseEvent('click', { bubbles: true })))
    expect(container.textContent).toContain('Not yet assessed')
    expect(container.textContent).toContain('Review equivalent fractions')

    completed = true
    await act(async () => render(1))

    expect(container.textContent).toContain('Developing')
    expect(container.textContent).toContain('Next supported lesson')
    expect(container.textContent).not.toContain('Review equivalent fractions')
    expect(calls.filter(path => path.startsWith('me/lesson-mastery?'))).toHaveLength(3)
    expect(calls.filter(path => path.startsWith('me/recommendations?'))).toHaveLength(2)

    failNextMastery = true
    await act(async () => render(2))
    expect(container.textContent).toContain('Concept progress is unavailable.')
    const progressAfterFailure = container.querySelector('button')
    await act(async () => progressAfterFailure.dispatchEvent(new dom.MouseEvent('click', { bubbles: true })))
    await act(async () => progressAfterFailure.dispatchEvent(new dom.MouseEvent('click', { bubbles: true })))
    expect(container.textContent).toContain('Developing')
    expect(container.textContent).not.toContain('Concept progress is unavailable.')
    expect(calls.filter(path => path.startsWith('me/lesson-mastery?'))).toHaveLength(6)
    expect(calls.filter(path => path.startsWith('me/recommendations?'))).toHaveLength(4)

    failNextRecommendation = true
    await act(async () => render(3))
    expect(container.textContent).toContain('Suggested activities are unavailable.')
    expect(container.textContent).not.toContain('No activities to suggest right now.')
    const retrySuggestions = [...container.querySelectorAll('button')]
      .find(button => button.textContent?.includes('Retry suggestions'))
    expect(retrySuggestions).toBeDefined()

    returnNoRecommendations = true
    await act(async () => retrySuggestions.dispatchEvent(new dom.MouseEvent('click', { bubbles: true })))
    expect(container.textContent).not.toContain('Suggested activities are unavailable.')
    expect(container.textContent).toContain('No activities to suggest right now.')
    expect(calls.filter(path => path.startsWith('me/recommendations?'))).toHaveLength(6)

    returnNoRecommendations = false
    failNextRecommendation = true
    await act(async () => render(4))
    expect(container.textContent).toContain('Suggested activities are unavailable.')
    await act(async () => render(5))
    expect(container.textContent).toContain('Next supported lesson')
    expect(container.textContent).not.toContain('Suggested activities are unavailable.')
    expect(calls.filter(path => path.startsWith('me/recommendations?'))).toHaveLength(8)
  } finally {
    await act(async () => root.unmount())
    await dom.happyDOM.close()
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete globalThis[key]
      else globalThis[key] = value
    }
  }
})
