import { expect, test } from 'bun:test'
import { createInstance } from 'i18next'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { I18nextProvider } from 'react-i18next'
import AdaptiveConfiguration from '../components/Studytomy/AdaptiveConfiguration'

const locale = createInstance()
await locale.init({ lng: 'en', resources: { en: { translation: {} } } })

const curriculum = {
  uuid: 'curriculum_synthetic',
  label: 'Synthetic Fractions',
  status: 'published',
  version: 2,
  curriculum_uuid: 'curriculum_synthetic',
  content_digest: 'a'.repeat(64),
}

function render(can_author, can_review) {
  return renderToStaticMarkup(React.createElement(
    I18nextProvider, { i18n: locale },
    React.createElement(AdaptiveConfiguration, {
      orgUuid: 'org_synthetic',
      courseUuid: 'course_synthetic',
      accessToken: 'synthetic-token',
      initial: {
        can_author, can_review, curricula: [curriculum], concepts: [], items: [], diagnostics: [], activities: [],
      },
    }),
  ))
}

test('course authors can configure drafts and receive an accessible empty-pool message', () => {
  const markup = render(true, false)
  expect(markup).toContain('Save curriculum draft')
  expect(markup).toContain('Publish an original diagnostic item first.')
  expect(markup).toContain('name="mastery_policy_uuid"')
  expect(markup).toContain('role="status"')
  expect(markup).not.toContain('Approved review evidence reference')
})

test('review-only staff see the definition without authoring forms or credential data', () => {
  const markup = render(false, true)
  expect(markup).toContain('Synthetic Fractions')
  expect(markup).toContain('Open review')
  expect(markup).not.toContain('Save curriculum draft')
  expect(markup).not.toContain('synthetic-token')
})


test('the course dashboard includes a labelled staff report lookup without private report data', () => {
  const markup = render(false, true)
  expect(markup).toContain('Progress report review')
  expect(markup).toContain('Report reference')
  expect(markup).toContain('Learner reference')
  expect(markup).toContain('Find reports')
  expect(markup).not.toContain('Create report draft')
  expect(markup).toContain('name="report_reference"')
  expect(markup).not.toContain('Approve reviewed version')
  expect(markup).not.toContain('Withdraw report')
})
