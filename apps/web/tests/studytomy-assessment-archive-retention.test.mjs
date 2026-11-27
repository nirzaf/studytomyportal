import { expect, test } from 'bun:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import AssessmentArchiveRetention from '../components/Studytomy/AssessmentArchiveRetention'

test('archive retention opens in approval mode and offers execution as a separate action', () => {
  const markup = renderToStaticMarkup(React.createElement(AssessmentArchiveRetention, {
    orgUuid: 'synthetic-org', token: 'synthetic-token',
  }))
  expect(markup).toContain('Assessment round reference')
  expect(markup).toContain('Record archive removal approval')
  expect(markup).toContain('Execute an existing approval')
  expect(markup).not.toContain('Remove archived responses')
  expect(markup).not.toContain('synthetic-token')
})
