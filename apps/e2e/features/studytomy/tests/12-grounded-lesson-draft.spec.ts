import { randomUUID } from 'node:crypto'
import { test, expect } from '../../../core/fixtures'
import { API_URL, BASE_URL } from '../../../core/instance'
import { ADMIN_STATE } from '../../../core/sharedAuth'

test.use({ storageState: process.env.E2E_STUDYTOMY_GROUNDING_STATE ?? ADMIN_STATE })
test.skip(
  process.env.E2E_STUDYTOMY_GROUNDING_FIXTURE !== '1' ||
    ![BASE_URL, API_URL].every(url => ['localhost', '127.0.0.1', '[::1]'].includes(new URL(url).hostname)),
  'Requires a loopback instance and synthetic editor session.',
)

test('review a grounded lesson draft, preserve stale edits, and reload after a successful save', async ({ page }) => {
  test.setTimeout(240_000)
  const orgUuid = `org_${randomUUID()}`
  const courseUuid = `course_${randomUUID()}`
  const activityId = randomUUID()
  const activityUuid = `activity_${activityId}`
  const source = {
    source_uuid: randomUUID(), label: 'energy.pdf', sha256: 'a'.repeat(64),
    readable_pages: [3], pages_without_readable_text: [4],
  }
  const citation = {
    source_uuid: source.source_uuid, label: source.label, sha256: source.sha256,
    page: 3, quote: 'Photosynthesis converts light energy into chemical energy.',
  }
  const unsupportedTopicMessage =
    'The selected source pages do not support the requested draft. Choose a narrower topic or link a source that explains it.'
  let version = 4
  let saved = false
  let saveAttempts = 0
  const requestIds: string[] = []
  await page.route(`**/activities/${activityUuid}/editor-bootstrap`, route => route.fulfill({
    json: {
      org: { id: 1, org_uuid: orgUuid, slug: 'default', name: 'Synthetic Studytomy' },
      course: {
        course_uuid: courseUuid, org_uuid: orgUuid, name: 'Synthetic course', grounded_sources_available: true,
      },
      activity: {
        id: 1, org_id: 1, course_id: 1, activity_uuid: activityUuid, name: 'Synthetic lesson',
        activity_type: 'TYPE_DYNAMIC', activity_sub_type: 'SUBTYPE_DYNAMIC_PAGE',
        published: false, current_version: version,
        content: { type: 'doc', content: [{
          type: 'paragraph', ...(saved ? { content: [{ type: 'text', text: 'Teacher-edited paragraph.' }] } : {}),
        }] },
      },
    },
  }))
  const path = `**/studytomy/orgs/${orgUuid}/courses/${courseUuid}/lessons/${activityUuid}`
  let uploaded = false
  let revoked = false
  let revocationAttempts = 0
  await page.route(`${path}/draft-sources`, route => {
    if (route.request().method() === 'POST') {
      expect(route.request().headers()['content-type']).toContain('multipart/form-data')
      uploaded = true
      return route.fulfill({ status: 201, json: source })
    }
    return route.fulfill({ json: { sources: uploaded && !revoked ? [source] : [], next_cursor: null } })
  })
  await page.route(`${path}/draft-sources/${source.source_uuid}/revoke`, route => {
    expect(route.request().method()).toBe('POST')
    if (++revocationAttempts === 1) {
      return route.fulfill({ status: 503, json: { detail: 'Revocation temporarily unavailable.' } })
    }
    revoked = true
    return route.fulfill({ status: 204 })
  })
  await page.route(`${path}/grounded-lesson-sources`, route =>
    route.fulfill({ json: {
      status: revoked ? 'stale' : saved ? 'current' : 'none',
      current_version: version,
      citations: saved && !revoked ? [citation] : [],
    } }),
  )
  await page.route(`${path}/grounded-lesson-preview`, route => {
    const body = route.request().postDataJSON()
    expect(body.source_uuids).toEqual([source.source_uuid])
    if (body.topic === 'Quadratic equations') {
      return route.fulfill({ status: 422, json: { detail: unsupportedTopicMessage } })
    }
    expect(body.topic).toBe('Photosynthesis')
    return route.fulfill({
      json: { title: 'Photosynthesis', blocks: [{ text: 'Initial draft.', citation }], matching_pages_omitted: 2 },
    })
  })
  await page.route(`${path}/grounded-lesson-draft`, route => {
    const { label, ...savedCitation } = citation
    expect(label).toBe(source.label)
    const { request_id: requestId, ...body } = route.request().postDataJSON()
    expect(requestId).toMatch(/^[0-9a-f-]{36}$/)
    requestIds.push(requestId)
    expect(body).toEqual({
      expected_current_version: saveAttempts === 2 ? 5 : version, title: 'Photosynthesis',
      blocks: [{ text: 'Teacher-edited paragraph.', citation: savedCitation }],
    })
    if (++saveAttempts === 1) {
      version = 5
      return route.fulfill({ status: 409, json: { detail: 'Activity version changed.' } })
    }
    saved = true
    version = 6
    if (saveAttempts === 2) {
      return route.fulfill({ status: 503, json: { detail: 'Save response unavailable. Try again.' } })
    }
    return route.fulfill({ json: { current_version: version, lesson_uuid: activityUuid, status: 'teacher_review_required' } })
  })

  const navigation = await page.goto(`/course/${courseUuid.slice('course_'.length)}/activity/${activityId}/edit`, {
    waitUntil: 'domcontentloaded', timeout: 120_000,
  })
  expect(navigation?.status()).toBe(200)
  const sources = page.getByRole('button', { name: 'Lesson sources' })
  await expect(sources).toBeEnabled({ timeout: 90_000 })
  await sources.click()
  const dialog = page.getByRole('dialog', { name: 'Lesson sources' })
  await expect(dialog.getByText('No active PDF sources on this page.')).toBeVisible()
  await dialog.getByLabel('Searchable PDF').setInputFiles({
    name: source.label, mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 synthetic browser fixture'),
  })
  await dialog.getByRole('button', { name: 'Upload PDF' }).click()
  await expect(dialog.getByText('PDF source saved.')).toBeVisible()
  await expect(dialog.getByText('No readable text on page 4.')).toBeVisible()
  const pdf = dialog.getByRole('checkbox', { name: /energy.pdf/ })
  await expect(pdf).toBeEnabled()
  await pdf.check()
  await expect(pdf).toBeChecked()
  const topic = dialog.getByRole('textbox', { name: 'Lesson topic' })
  await topic.fill('Photosynthesis')
  await expect(topic).toHaveValue('Photosynthesis')
  await dialog.getByRole('button', { name: 'Generate lesson draft' }).click()
  await expect(dialog.getByText(citation.quote)).toBeVisible()
  await expect(dialog.getByRole('status').filter({ hasText: 'Some pages matched the topic' })).toBeVisible()
  await topic.fill('Quadratic equations')
  await dialog.getByRole('button', { name: 'Generate lesson draft' }).click()
  await expect(dialog.getByRole('alert')).toContainText(unsupportedTopicMessage)
  await expect(dialog.getByRole('button', { name: 'Save unpublished lesson' })).toHaveCount(0)
  expect(requestIds).toHaveLength(0)
  await topic.fill('Photosynthesis')
  await dialog.getByRole('button', { name: 'Generate lesson draft' }).click()
  const draft = dialog.getByRole('textbox', { name: 'Draft paragraph 1' })
  await expect(draft).toHaveValue('Initial draft.')
  await draft.fill('Teacher-edited paragraph.')
  await expect(draft).toHaveValue('Teacher-edited paragraph.')
  await dialog.getByRole('button', { name: 'Save unpublished lesson' }).click()
  await expect(dialog.getByRole('alert')).toContainText('This lesson changed')
  await expect(draft).toHaveValue('Teacher-edited paragraph.')
  await page.reload({ waitUntil: 'domcontentloaded' })
  await expect(sources).toBeEnabled({ timeout: 90_000 })
  await sources.click()
  await expect(dialog.getByRole('checkbox', { name: /energy.pdf/ })).toBeEnabled()
  await pdf.check()
  await topic.fill('Photosynthesis')
  await dialog.getByRole('button', { name: 'Generate lesson draft' }).click()
  await expect(draft).toHaveValue('Initial draft.')
  await draft.fill('Teacher-edited paragraph.')
  await dialog.getByRole('button', { name: 'Save unpublished lesson' }).click()
  await expect(dialog.getByRole('alert')).toContainText('Save response unavailable')
  await expect(draft).toHaveValue('Teacher-edited paragraph.')
  await dialog.getByRole('button', { name: 'Save unpublished lesson' }).click()
  await expect(dialog).toBeHidden()
  expect(requestIds).toHaveLength(3)
  expect(requestIds[0]).not.toBe(requestIds[1])
  expect(requestIds[2]).toBe(requestIds[1])
  await expect(page.getByText('Teacher-edited paragraph.', { exact: true })).toBeVisible()
  await sources.click()
  await expect(dialog.getByRole('region', { name: 'Saved citations' }).getByText(citation.quote)).toBeVisible()
  await pdf.check()
  await dialog.getByRole('button', { name: /Revoke source/ }).click()
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click()
  expect(revocationAttempts).toBe(0)
  await dialog.getByRole('button', { name: /Revoke source/ }).click()
  await dialog.getByRole('button', { name: 'Confirm revocation' }).click()
  await expect(dialog.getByRole('alert')).toContainText('Revocation temporarily unavailable.')
  await expect(pdf).toBeChecked()
  await dialog.getByRole('button', { name: 'Confirm revocation' }).click()
  await expect(dialog.getByText('PDF source revoked. Review any affected citations.')).toBeVisible()
  await expect(pdf).toHaveCount(0)
  await expect(dialog.getByText('0 of 5 sources selected')).toBeVisible()
  await expect(dialog.getByText('The sources no longer match this lesson version or are unavailable.')).toBeVisible()
})
