import { randomUUID } from 'node:crypto'
import { test, expect } from '../../../core/fixtures'
import { API_URL, BASE_URL } from '../../../core/instance'
import { getOrg } from '../../../core/client'
import { STUDENT_STATE } from '../../../core/sharedAuth'

test.use({ storageState: STUDENT_STATE, viewport: { width: 390, height: 844 } })
test.skip(
  ![BASE_URL, API_URL].every(url => ['localhost', '127.0.0.1', '[::1]'].includes(new URL(url).hostname)),
  'Requires a loopback instance and synthetic learner session.',
)

test('managed tutor and recommendations retain the exact released lesson context', async ({ page }) => {
  const { org_uuid: orgUuid } = await getOrg()
  const courseUuid = `course_${randomUUID()}`
  const releaseUuid = `course_release_${randomUUID()}`
  const sourceUuid = `activity_${randomUUID()}`
  const targetUuid = `activity_${randomUUID()}`
  const releaseName = 'Synthetic released measurements'
  const base = `**/studytomy/orgs/${orgUuid}/learning`
  const adaptive = `**/studytomy/adaptive/orgs/${orgUuid}/me/`
  const release = { course_uuid: courseUuid, release_uuid: releaseUuid, name: releaseName }
  const source = {
    activity_uuid: sourceUuid, name: 'Measurement lesson', position: 1,
    format: 'text', media_type: null, available: true, unavailable_reason: null,
  }
  const target = { ...source, activity_uuid: targetUuid, name: 'Released unit review', position: 2 }
  const lessonsPath = `${base}/courses/${courseUuid}/releases/${releaseUuid}/lessons`
  let tutorSessionPayload: unknown

  await page.route(`${base}/releases`, route => route.fulfill({
    json: { releases: [release], next_cursor: null },
  }))
  await page.route(lessonsPath, route => route.fulfill({
    json: { requires_first_use: false, lessons: [source, target] },
  }))
  await page.route(`${lessonsPath}/${sourceUuid}`, route => route.fulfill({
    json: { ...source, paragraphs: ['Read the released measurement notes.'] },
  }))
  await page.route(`${lessonsPath}/${targetUuid}`, route => route.fulfill({
    json: { ...target, paragraphs: ['Review the released units.'] },
  }))
  await page.route(`${adaptive}lesson-mastery?*`, route => {
    const params = new URL(route.request().url()).searchParams
    expect(params.get('activity_uuid')).toBe(sourceUuid)
    expect(params.get('course_uuid')).toBe(courseUuid)
    expect(params.get('course_release_uuid')).toBe(releaseUuid)
    return route.fulfill({ json: { concepts: [], next_offset: null } })
  })
  await page.route(`${adaptive}attempts/options?*`, route => route.fulfill({ json: [] }))
  await page.route(`${adaptive}attempts/current?*`, route => route.fulfill({ json: null }))
  await page.route(`${adaptive}recommendations?*`, route => {
    const params = new URL(route.request().url()).searchParams
    expect(params.get('activity_uuid')).toBe(sourceUuid)
    expect(params.get('course_uuid')).toBe(courseUuid)
    expect(params.get('course_release_uuid')).toBe(releaseUuid)
    return route.fulfill({ json: [{
      activity_uuid: targetUuid,
      course_uuid: courseUuid,
      course_release_uuid: releaseUuid,
      activity_title: target.name,
      reason: 'practice_needed',
    }] })
  })
  await page.route(`${adaptive}tutor-options?*`, route => {
    const params = new URL(route.request().url()).searchParams
    expect(params.get('activity_uuid')).toBe(sourceUuid)
    expect(params.get('course_uuid')).toBe(courseUuid)
    expect(params.get('course_release_uuid')).toBe(releaseUuid)
    return route.fulfill({ json: {
      mode: 'guided', reason: null, actions: ['explain'], practice_attempt_uuid: null,
    } })
  })
  const tutorUuid = `tutor_${randomUUID()}`
  await page.route(`${adaptive}tutor-sessions`, route => {
    tutorSessionPayload = route.request().postDataJSON()
    expect(tutorSessionPayload).toEqual({
      activity_uuid: sourceUuid,
      course_uuid: courseUuid,
      course_release_uuid: releaseUuid,
    })
    return route.fulfill({ status: 201, json: {
      uuid: tutorUuid, activity_uuid: sourceUuid, version: 1, closed_at: null,
    } })
  })

  await page.goto(new URL('/account/learning', BASE_URL).href, {
    waitUntil: 'domcontentloaded', timeout: 90_000,
  })
  await page.getByRole('button', { name: releaseName, exact: true }).click()
  await page.getByRole('button', { name: source.name, exact: true }).click()
  await expect(page.getByRole('heading', { name: source.name, exact: true })).toBeVisible()

  const progress = page.getByRole('button', { name: 'Concept progress', exact: true })
  await expect(progress).toBeVisible()
  await progress.click()
  const suggestion = page.getByRole('button', { name: target.name, exact: true })
  await expect(suggestion).toBeVisible()
  await suggestion.click()
  await expect(page.getByRole('heading', { name: target.name, exact: true })).toBeVisible()
  await expect(page.getByText('Review the released units.')).toBeVisible()

  await page.getByRole('button', { name: 'Back to lessons' }).click()
  await page.getByRole('button', { name: source.name, exact: true }).click()
  const guidance = page.getByRole('button', { name: 'Lesson guidance', exact: true })
  await expect(guidance).toBeVisible()
  await guidance.click()
  await page.getByRole('button', { name: 'Start guidance', exact: true }).click()
  await expect.poll(() => tutorSessionPayload).toEqual({
    activity_uuid: sourceUuid,
    course_uuid: courseUuid,
    course_release_uuid: releaseUuid,
  })
})
