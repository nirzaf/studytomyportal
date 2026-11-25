import { expect, mock, test } from 'bun:test'
import { readFileSync } from 'node:fs'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

mock.module('@sentry/nextjs', () => ({
  isInitialized: () => false,
  captureException: () => undefined,
  lastEventId: () => undefined,
  showReportDialog: () => undefined,
}))

const { OrgContext } = await import('../components/Contexts/OrgContext')
const { SessionContext } = await import('../components/Contexts/LHSessionContext')
const { default: ExploreTopics } = await import('../components/Studytomy/ExploreTopics')
const { default: SyllabusLibraryManager } = await import('../components/Studytomy/SyllabusLibraryManager')
const { buildExploreTopicsPayload } = await import('../components/Studytomy/exploreTopicsRequest')

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
  expect(markup).toContain('Your syllabus is a learning context. Search scope controls which approved resources are discovered.')
  const source = readFileSync(new URL('../components/Studytomy/ExploreTopics.tsx', import.meta.url), 'utf8')
  expect(source).toContain('All approved resources')
  expect(source).toContain('This subject across grades')
})

test('follow-up request keeps the selected source set and prior question while fresh search clears both', () => {
  const filters = {
    selectedCurriculumUuid: 'curriculum-12',
    searchScope: 'all_approved',
  }
  const followUp = buildExploreTopicsPayload('  Why do they differ?  ', filters, {
    sourceVersionUuids: ['version-a', 'version-b'],
    previousQuestion: 'How do carbon isotopes differ?',
  })
  expect(followUp).toEqual({
    query: 'Why do they differ?',
    context_question: 'How do carbon isotopes differ?',
    selected_curriculum_uuid: 'curriculum-12',
    search_scope: 'all_approved',
    source_version_uuids: ['version-a', 'version-b'],
  })

  const fresh = buildExploreTopicsPayload('Atomic number', filters, null)
  expect(fresh.context_question).toBeNull()
  expect(fresh.source_version_uuids).toEqual([])
  expect(fresh.search_scope).toBe('all_approved')
})

test('learning context does not silently narrow search scope', () => {
  const payload = buildExploreTopicsPayload('Why do objects float?', {
    selectedCurriculumUuid: 'grade-12-chemistry',
    searchScope: 'all_approved',
  }, null)

  expect(payload.selected_curriculum_uuid).toBe('grade-12-chemistry')
  expect(payload.search_scope).toBe('all_approved')
  expect(payload).not.toHaveProperty('grade')
  expect(payload).not.toHaveProperty('subject')
  expect(payload).not.toHaveProperty('jurisdiction')
})

test('Educator source manager waits for server authorization before showing controls', () => {
  const markup = render(createElement(SyllabusLibraryManager))

  expect(markup).toContain('Syllabus source library')
  expect(markup).not.toContain('Usage permission status')
  expect(markup).not.toContain('Approve for student retrieval')

  const source = readFileSync(new URL('../components/Studytomy/SyllabusLibraryManager.tsx', import.meta.url), 'utf8')
  expect(source).toContain('/syllabus-library/access')
  expect(source).toContain('access?.can_author && <section')
  expect(source).toContain('access?.can_review && mapping.status')
  expect(source).toContain('Independently verified license / permission reference')
  expect(source).toContain('Approve for student retrieval')
  expect(source).toContain('canReviewRole && version.uploaded_by_user_id !== currentUserId')
})

test('Explore Topics drops stale results and follow-up scope when learning filters change', () => {
  const source = readFileSync(new URL('../components/Studytomy/ExploreTopics.tsx', import.meta.url), 'utf8')
  expect(source).toContain('setFollowUpContext(null)\n                setResult(null)')
  expect(source).toContain('key={citation.reference}')
  expect(source).toContain('This approved PDF is no longer available. Search again to refresh the source list.')
})
