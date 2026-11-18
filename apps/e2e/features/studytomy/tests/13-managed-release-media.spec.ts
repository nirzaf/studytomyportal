import { randomUUID } from 'node:crypto'
import { getOrg } from '../../../core/client'
import { test, expect } from '../../../core/fixtures'
import { API_URL, BASE_URL } from '../../../core/instance'
import { STUDENT_STATE } from '../../../core/sharedAuth'

test.use({ storageState: STUDENT_STATE, viewport: { width: 390, height: 844 } })
test.skip(
  ![BASE_URL, API_URL].every(url => ['localhost', '127.0.0.1', '[::1]'].includes(new URL(url).hostname)),
  'Requires a loopback instance and synthetic learner session.',
)

test('my learning lists mixed release formats and activates first use before opening a PDF', async ({ page }) => {
  const { org_uuid: orgUuid } = await getOrg()
  const courseUuid = `course_${randomUUID()}`
  const releaseUuid = randomUUID()
  const textUuid = `activity_${randomUUID()}`
  const pdfUuid = `activity_${randomUUID()}`
  const videoUuid = `activity_${randomUUID()}`
  const unsupportedUuid = `activity_${randomUUID()}`
  const base = `**/studytomy/orgs/${orgUuid}/learning`
  const release = { course_uuid: courseUuid, release_uuid: releaseUuid, name: 'Synthetic mixed release' }
  let activated = false

  await page.route(`${base}/releases`, route => route.fulfill({ json: { releases: [release], next_cursor: null } }))
  await page.route(`${base}/courses/${courseUuid}/releases/${releaseUuid}/lessons`, route => route.fulfill({
    json: {
      requires_first_use: !activated,
      lessons: [
        { activity_uuid: textUuid, name: 'Reading', position: 1, format: 'text', media_type: null, available: true, unavailable_reason: null },
        { activity_uuid: pdfUuid, name: 'Reviewed PDF', position: 2, format: 'pdf', media_type: 'application/pdf', available: true, unavailable_reason: null },
        { activity_uuid: videoUuid, name: 'Reviewed video', position: 3, format: 'video', media_type: 'video/mp4', available: false, unavailable_reason: 'captions_or_text_alternative_not_retained' },
        { activity_uuid: unsupportedUuid, name: 'Unsupported audio', position: 4, format: 'unsupported', media_type: null, available: false, unavailable_reason: 'unsupported_release_format' },
      ],
    },
  }))
  await page.route(`**/activities/${pdfUuid}/media/open`, async route => {
    const request = route.request()
    expect(request.method()).toBe('POST')
    expect(request.headers().range).toBe('bytes=0-0')
    activated = true
    await route.fulfill({
      status: 206,
      headers: { 'content-range': 'bytes 0-0/1', 'content-type': 'application/pdf' },
      body: '%',
    })
  })

  await page.goto(new URL('/account/learning', BASE_URL).href, { waitUntil: 'domcontentloaded', timeout: 90_000 })
  const course = page.getByRole('button', { name: release.name })
  await expect(course).toBeVisible({ timeout: 60_000 })
  await course.click()

  await expect(page.getByText('Opening a lesson starts your access period.')).toBeVisible()
  await expect(page.getByRole('button', { name: /Reading Text/ })).toBeEnabled()
  await expect(page.getByRole('button', { name: /Open Reviewed PDF PDF/ })).toBeEnabled()
  await expect(page.getByRole('button', { name: /Reviewed video Video/ })).toBeDisabled()
  await expect(page.getByText(/Video is unavailable until captions or an approved accessible alternative/)).toBeVisible()
  await expect(page.getByRole('button', { name: /Unsupported audio Unsupported/ })).toBeDisabled()

  const mediaPath = `/studytomy/orgs/${orgUuid}/learning/courses/${courseUuid}/releases/${releaseUuid}/activities/${pdfUuid}/media`
  const mediaResponse = page.waitForResponse(response =>
    response.request().method() === 'GET' && new URL(response.url()).pathname.endsWith(mediaPath),
  )
  await page.getByRole('button', { name: /Open Reviewed PDF PDF/ }).click()
  await expect(page.getByTitle('Reviewed PDF')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Refresh document access' })).toBeVisible()
  const frameUrl = await page.getByTitle('Reviewed PDF').getAttribute('src')
  expect(frameUrl).toContain(mediaPath)
  expect(frameUrl).not.toContain('token=')
  // The release list is intercepted, but this frame request reaches the API.
  // The signed-in learner gets 404 because the fixture release is not stored.
  const missingRelease = await mediaResponse
  expect(missingRelease.status()).toBe(404)
  expect(await missingRelease.json()).toMatchObject({ detail: 'Course release unavailable.' })

  await page.getByRole('button', { name: 'Back to lessons' }).click()
  await expect(page.getByRole('button', { name: 'Reviewed PDF PDF', exact: true })).toBeEnabled()
  await expect(page.getByText('Opening a lesson starts your access period.')).toHaveCount(0)
})
