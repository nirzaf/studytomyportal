import { expect, test } from 'bun:test'
import i18next from 'i18next'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { I18nextProvider } from 'react-i18next'
import fixture from '../../api/src/tests/fixtures/learning_surface_v1.json'
import LearningSurfaceView from '../components/Objects/Activities/AI/LearningSurfaceView'
import english from '../locales/en.json'

const locale = i18next.createInstance()
await locale.init({ lng: 'en', resources: { en: { translation: { ai: english.ai } } } })

const render = (surface, onAction) => renderToStaticMarkup(
  createElement(I18nextProvider, { i18n: locale }, createElement(LearningSurfaceView, { surface, onAction })),
)

test('practice fraction visual is read-only and preserves the reviewed parameters', () => {
  const markup = render(fixture)
  expect(markup).toContain('2 of 4 equal parts selected. 2/4 is equivalent to 1/2.')
  expect(markup).not.toContain('<select')
  expect(markup).not.toContain('Reset visual')
  expect(markup).not.toContain('Submit answer')
  expect(markup).not.toContain('Practice answer saved.')
})

test('practice number line is read-only while retaining the answer contract', () => {
  const surface = structuredClone(fixture)
  surface.components[1] = { kind: 'number_line', id: 'visual', first_mark: -1, last_mark: 1, divisions_per_unit: 3 }
  const markup = render(surface, async () => {})
  expect(markup).not.toContain('Move the marker')
  expect(markup).not.toContain('type="range"')
  expect(markup).not.toContain('Reset visual')
  expect(markup).toContain('-1')
  expect(markup).toContain('1')
  expect(markup).toContain('Your answer (for example, 1/2)')
  expect(markup).toContain('Submit answer')
  expect(markup).toContain('Show a hint')
})

test('exploration visuals remain interactive and ungraded', () => {
  const surface = structuredClone(fixture)
  surface.mode = 'explore'
  surface.components = surface.components.filter((item) => item.kind !== 'question')
  surface.actions = []
  const markup = render(surface, async () => {})
  expect(markup).toContain('Equal parts<select')
  expect(markup).toContain('Selected parts<select')
  expect(markup).toContain('Reset visual')
  expect(markup).not.toContain('Submit answer')
  expect(markup).not.toContain('Show a hint')
})

test('exploration number line exposes its interactive marker without an answer form', () => {
  const surface = structuredClone(fixture)
  surface.mode = 'explore'
  surface.components = surface.components
    .filter((item) => item.kind !== 'question')
    .map((item) => item.kind === 'fraction_explorer'
      ? { kind: 'number_line', id: item.id, first_mark: 0, last_mark: 2, divisions_per_unit: 4 }
      : item)
  surface.actions = []
  const markup = render(surface, async () => {})
  expect(markup).toContain('Move the marker')
  expect(markup).toContain('type="range"')
  expect(markup).toContain('max="8"')
  expect(markup).toContain('Reset visual')
  expect(markup).not.toContain('Submit answer')
})

test('practice feedback comes from the server projection', () => {
  const surface = structuredClone(fixture)
  surface.components.push({
    kind: 'feedback', id: 'result', attempt_id: '033f8d7f-8ab0-4240-aac4-2cbdf25eea18',
    outcome: 'incorrect', text: 'Compare the size of each part.',
  })
  surface.actions = ['retry_practice']
  const markup = render(surface, async () => {})
  expect(markup).toContain('Not quite')
  expect(markup).toContain('Compare the size of each part.')
  expect(markup).toContain('Practice answer saved.')
  expect(markup).toContain('Try again')
  expect(markup).not.toContain('Submit answer')
  expect(markup).not.toContain('<select')
  expect(markup).not.toContain('Reset visual')
})
