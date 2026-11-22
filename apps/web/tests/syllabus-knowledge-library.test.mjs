import { expect, test } from 'bun:test'
import { readFileSync } from 'node:fs'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { OrgContext } from '../components/Contexts/OrgContext'
import { SessionContext } from '../components/Contexts/LHSessionContext'
import ExploreTopics from '../components/Studytomy/ExploreTopics'
import SyllabusLibraryManager from '../components/Studytomy/SyllabusLibraryManager'
import { buildExploreTopicsPayload } from '../components/Studytomy/exploreTopicsRequest'

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

test('follow-up request keeps the selected source set and prior question while fresh search clears both', () => {
  const filters = {
    selectedCurriculumUuid: 'curriculum-12',
    grade: 'Grade 12',
    subject: 'Chemistry',
    jurisdiction: 'Sri Lanka',
  }
  const followUp = buildExploreTopicsPayload('  Why do they differ?  ', filters, {
    sourceVersionUuids: ['version-a', 'version-b'],
    previousQuestion: 'How do carbon isotopes differ?',
  })
  expect(followUp).toEqual({
    query: 'Why do they differ?',
    context_question: 'How do carbon isotopes differ?',
    selected_curriculum_uuid: 'curriculum-12',
    grade: 'Grade 12',
    subject: 'Chemistry',
    jurisdiction: 'Sri Lanka',
    source_version_uuids: ['version-a', 'version-b'],
  })

  const fresh = buildExploreTopicsPayload('Atomic number', filters, null)
  expect(fresh.context_question).toBeNull()
  expect(fresh.source_version_uuids).toEqual([])
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
