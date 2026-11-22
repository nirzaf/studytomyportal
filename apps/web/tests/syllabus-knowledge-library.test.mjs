import { expect, test } from 'bun:test'
import { readFileSync } from 'node:fs'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { OrgContext } from '../components/Contexts/OrgContext'
import { SessionContext } from '../components/Contexts/LHSessionContext'
import ExploreTopics from '../components/Studytomy/ExploreTopics'
import SyllabusLibraryManager from '../components/Studytomy/SyllabusLibraryManager'

const session = {
  status: 'authenticated',
  data: { tokens: { access_token: 'synthetic-session-token' }, user: { id: 7001 } },
}
const orgContext = {
  org: { org_uuid: 'synthetic-org' },
  isUserPartOfTheOrg: true,
  orgslug: 'synthetic-org',
}

function render(component) {
  return renderToStaticMarkup(createElement(
    SessionContext.Provider,
    { value: session },
    createElement(OrgContext.Provider, { value: orgContext }, component),
  ))
}

test('Explore Topics renders search, syllabus context, and source-safety guidance', () => {
  const markup = render(createElement(ExploreTopics))

  expect(markup).toContain('Learn from approved sources')
  expect(markup).toContain('What would you like to understand?')
  expect(markup).toContain('Only approved, permitted PDFs appear')
  expect(markup).toContain('Any approved syllabus')
  expect(markup).toContain('Leave it unselected to search all available approved materials.')
})

test('Educator source manager explains review and rights before learner access', () => {
  const markup = render(createElement(SyllabusLibraryManager))

  expect(markup).toContain('Syllabus source library')
  expect(markup).toContain('Usage permission status')
  expect(markup).toContain('Uploaded rights values are claims until an independent reviewer verifies permission.')
  expect(markup).toContain('Image-only scans are held with OCR guidance.')
  expect(markup).toContain('students can retrieve only approved and permitted versions')
  expect(markup).toContain('No PDF sources uploaded yet.')

  const source = readFileSync(new URL('../components/Studytomy/SyllabusLibraryManager.tsx', import.meta.url), 'utf8')
  expect(source).toContain('Independently verified license / permission reference')
  expect(source).toContain('Approve for student retrieval')
})
