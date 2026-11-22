import { test, expect } from '../../../core/fixtures'
import { API_URL, BASE_URL, ORG_SLUG } from '../../../core/instance'
import { STUDENT_STATE } from '../../../core/sharedAuth'

test.use({ storageState: STUDENT_STATE, viewport: { width: 390, height: 844 } })

const curriculum = {
  uuid: 'e2e-curriculum-chemistry-12', curriculum_key: 'chemistry-12', revision: 1,
  subject: 'Chemistry', level: 'Grade 12', language: 'English', jurisdiction: 'Sri Lanka', status: 'published',
}

function groundedResult() {
  return {
    status: 'grounded_answer',
    explanation: 'Electrolysis uses electricity to drive a chemical change.',
    answer_blocks: [{
      text: 'An electric current drives a chemical change in an electrolyte.',
      citations: [1],
      evidence_quote: 'An electric current causes chemical change in an electrolyte.',
      kind: 'teaching',
    }],
    sources: [{
      reference: 1, source_uuid: 'e2e-source', version_uuid: 'e2e-source-version',
      title: 'Chemistry Resource Book', publisher: 'Synthetic fixture', version_number: 1,
      source_revision: '1', source_type: 'resource_book', jurisdiction: 'Sri Lanka',
      grade: '12', subject: 'Chemistry', syllabus_edition: 'Synthetic', page: 5,
      excerpt: 'An electric current causes chemical change in an electrolyte.',
      pdf_url: '/synthetic.pdf', coverage: 'supplementary',
    }],
    coverage: 'supplementary', selected_curriculum_uuid: curriculum.uuid,
    selected_curriculum_label: 'Chemistry · Grade 12', ai_unavailable_reason: null,
  }
}

test('learner explores approved sources, keeps curriculum separate from scope, follows citations, and handles revocation', async ({ page }) => {
  const topicRequests: Record<string, unknown>[] = []
  let pdfRevoked = false

  await page.route(`${API_URL}/studytomy/orgs/**/explore/curricula`, async (route) => {
    await route.fulfill({ json: [curriculum] })
  })
  await page.route(`${API_URL}/studytomy/orgs/**/explore/topics`, async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>
    topicRequests.push(body)
    if (String(body.query).toLowerCase().includes('unsupported')) {
      await route.fulfill({ json: {
        ...groundedResult(), status: 'insufficient_evidence', explanation: null,
        answer_blocks: [], sources: [], coverage: 'unverified', ai_unavailable_reason: null,
      } })
      return
    }
    await route.fulfill({ json: groundedResult() })
  })
  await page.route(`${API_URL}/studytomy/orgs/**/syllabus-sources/**/pdf`, async (route) => {
    if (pdfRevoked) {
      await route.fulfill({ status: 409, json: { detail: 'The approved source is no longer available.' } })
      return
    }
    await route.fulfill({ status: 200, contentType: 'application/pdf', body: Buffer.from('%PDF-1.4\n% synthetic test fixture\n%%EOF') })
  })

  await page.goto(`${BASE_URL}/orgs/${ORG_SLUG}/explore`)
  await expect(page.getByRole('heading', { name: 'Learn from approved sources' })).toBeVisible()
  await page.getByLabel('Topic or question').fill('Explain electrolysis in simple words')
  await page.getByRole('button', { name: 'Explore topic' }).click()
  await expect(page.getByRole('heading', { name: 'Chemistry Resource Book' })).toBeVisible()
  await expect(page.getByText('page 5', { exact: false })).toBeVisible()
  await expect(page.getByText('Source-grounded explanation')).toBeVisible()
  await page.getByRole('link', { name: '[1]' }).click()
  await expect(page).toHaveURL(/#source-1$/)
  await expect(page.locator('#source-1')).toBeVisible()

  const syllabusContext = page.getByRole('combobox', { name: 'Syllabus context' })
  await syllabusContext.click()
  await page.getByRole('option', { name: /Chemistry · Grade 12/ }).click()
  await expect(page.getByRole('region', { name: 'Topic results' })).toHaveCount(0)
  await expect(page.getByText('All approved resources')).toBeVisible()

  await page.getByRole('button', { name: 'Explore topic' }).click()
  await expect(page.getByRole('heading', { name: 'Chemistry Resource Book' })).toBeVisible()
  expect(topicRequests.at(-1)).toMatchObject({
    selected_curriculum_uuid: curriculum.uuid,
    search_scope: 'all_approved',
  })

  await page.getByRole('button', { name: /Ask a follow-up using these/ }).click()
  await expect(page.getByText(/Follow-up searches only 1 selected approved source version/)).toBeVisible()
  await page.getByLabel('Topic or question').fill('Why does it need an electric current?')
  await page.getByRole('button', { name: 'Explore topic' }).click()
  expect(topicRequests.at(-1)).toMatchObject({
    context_question: 'Explain electrolysis in simple words',
    source_version_uuids: ['e2e-source-version'],
  })

  const searchScope = page.getByRole('combobox', { name: 'Search scope' })
  await searchScope.click()
  await page.getByRole('option', { name: 'This subject across grades' }).click()
  await expect(page.getByRole('region', { name: 'Topic results' })).toHaveCount(0)
  await expect(page.getByText(/Follow-up searches only/)).toHaveCount(0)
  await page.getByRole('button', { name: 'Explore topic' }).click()
  expect(topicRequests.at(-1)).toMatchObject({
    selected_curriculum_uuid: curriculum.uuid,
    search_scope: 'selected_subject',
    context_question: null,
    source_version_uuids: [],
  })
  await expect(page.getByRole('heading', { name: 'Chemistry Resource Book' })).toBeVisible()

  const popupPromise = page.waitForEvent('popup')
  await page.getByRole('button', { name: 'Open PDF · p. 5' }).click()
  const pdfPage = await popupPromise
  await expect.poll(() => pdfPage.url()).toContain('#page=5')
  await pdfPage.close()

  pdfRevoked = true
  await page.getByRole('button', { name: 'Open PDF · p. 5' }).click()
  await expect(page.getByRole('status')).toContainText('This approved PDF is no longer available')
  await expect(page.getByRole('region', { name: 'Topic results' })).toHaveCount(0)

  await page.getByLabel('Topic or question').fill('unsupported topic with no evidence')
  await page.getByRole('button', { name: 'Explore topic' }).click()
  await expect(page.getByText('No sufficient approved source found')).toBeVisible()
  await expect(page.locator('[id^="source-"]')).toHaveCount(0)
  await expect(page.locator('img[src*="lrn"]')).toHaveCount(0)

  await page.route(`${API_URL}/studytomy/orgs/**/syllabus-library/access`, async (route) => {
    await route.fulfill({ status: 403, json: { detail: 'Not assigned to source review.' } })
  })
  await page.goto(`${BASE_URL}/orgs/${ORG_SLUG}/dash/studytomy/syllabus-library`)
  await expect(page.getByText('This account cannot access the educator source library.')).toBeVisible()
  await expect(page.getByRole('button', { name: /approve/i })).toHaveCount(0)
})
