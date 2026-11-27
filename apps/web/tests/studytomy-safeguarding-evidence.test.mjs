import { expect, test } from 'bun:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import SafeguardingCaseEvidence from '../components/Studytomy/SafeguardingCaseEvidence'

function render(canAttach) {
  return renderToStaticMarkup(React.createElement(SafeguardingCaseEvidence, {
    orgUuid: 'org_synthetic', token: 'synthetic-token', caseUuid: 'case-synthetic', canAttach,
  }))
}

test('case evidence upload controls are conditional while authorized lookups can still download', () => {
  expect(render(true)).toContain('Attach evidence')
  const readOnly = render(false)
  expect(readOnly).not.toContain('Attach evidence')
  expect(readOnly).toContain('Download evidence')
  expect(readOnly).not.toContain('synthetic-token')
})
