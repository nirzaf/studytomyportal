import { expect, test } from 'bun:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

import FamilyWorkspace from '../components/Studytomy/FamilyWorkspace'
import AllocationReview from '../components/Studytomy/AllocationReview'

test('family workspace explains missing session before showing queue loading', () => {
  const markup = renderToStaticMarkup(
    React.createElement(FamilyWorkspace, {
      orgUuid: undefined,
      accessToken: undefined,
    })
  )

  expect(markup).toContain('role="alert"')
  expect(markup).toContain('Workspace unavailable')
  expect(markup).toContain(
    'Sign in with an assigned family-services account to view this workspace.'
  )
  expect(markup).not.toContain('Loading assigned relationships')
})

test('family workspace shows an accessible loading state for a signed-in reviewer', () => {
  const markup = renderToStaticMarkup(
    React.createElement(FamilyWorkspace, {
      orgUuid: 'synthetic-org',
      accessToken: 'synthetic-access-token',
    })
  )

  expect(markup).toContain('role="status"')
  expect(markup).toContain('Loading assigned relationships')
})

test('allocation review starts with labelled references and no correction command', () => {
  const markup = renderToStaticMarkup(
    React.createElement(AllocationReview, { orgUuid: 'synthetic-org', accessToken: 'synthetic-token' })
  )

  expect(markup).toMatch(/<label[^>]*>Original learner reference<input[^>]*name="learner"/)
  expect(markup).toMatch(/<label[^>]*>Order reference<input[^>]*name="order"/)
  expect(markup).toContain('Check allocation')
  expect(markup).not.toContain('Correct allocation')
  expect(markup).not.toContain('synthetic-token')
})
