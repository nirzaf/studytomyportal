import { expect, test } from 'bun:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ReadinessPanel from '../components/Studytomy/ReadinessPanel'

test('readiness workspace exposes real governance and private-draft workflows without implying approval', () => {
  const markup = renderToStaticMarkup(createElement(ReadinessPanel, {
    orgUuid: 'synthetic-org',
    courseUuid: 'synthetic-course',
    accessToken: 'synthetic-token',
    adminMode: true,
  }))

  expect(markup).toContain('Course offer readiness')
  expect(markup).toContain('Governance and private offer drafts')
  expect(markup).toContain('Save policy draft')
  expect(markup).toContain('Assign service owner')
  expect(markup).toContain('Staff scopes and financial delegation')
  expect(markup).toContain('Staff scopes and financial delegation')
  expect(markup).toContain('Save private offer draft')
  expect(markup).toContain('No policy review date is stored by the current policy contract.')
  expect(markup).toContain('private and non-purchasable')
  expect(markup).not.toContain('Approve and activate')
  expect(markup).not.toContain('synthetic-token')
})
