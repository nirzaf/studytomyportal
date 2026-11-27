import { expect, test } from 'bun:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import GroundedQuizDraftForm from '../components/Studytomy/GroundedQuizDraftForm'

function render({ selected = [], canAddTask = true, tasks = [] } = {}) {
  return renderToStaticMarkup(React.createElement(GroundedQuizDraftForm, {
    route: 'synthetic-draft',
    token: 'synthetic-token',
    selected,
    destination: { assignmentUuid: 'assignment_synthetic', tasks, canAddTask, onSaved() {} },
  }))
}

test('grounded quiz previews require a selected source and do not show save controls yet', () => {
  expect(render()).toMatch(/<button[^>]*disabled=""[^>]*>Generate quiz draft/)
  const ready = render({ selected: ['synthetic-source'] })
  expect(ready).not.toMatch(/<button[^>]*disabled=""[^>]*>Generate quiz draft/)
  expect(ready).not.toContain('Save unpublished quiz')
  expect(ready).not.toContain('synthetic-token')
})

test('citation lookup remains available at the assignment task limit', () => {
  const markup = render({ canAddTask: false, tasks: [{ uuid: 'task_synthetic', title: 'Fractions' }] })
  expect(markup).not.toContain('Generate quiz draft')
  expect(markup).toContain('Check quiz citations')
  expect(markup).toContain('Fractions')
})
