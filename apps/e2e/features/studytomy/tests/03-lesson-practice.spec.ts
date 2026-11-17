import { randomUUID } from 'node:crypto'
import { test, expect } from '../../../core/fixtures'
import { ADMIN_EMAIL, ADMIN_PASSWORD, API_URL, BASE_URL, uniqueSuffix } from '../../../core/instance'
import { getOrg, login, req } from '../../../core/client'
import { STUDENT_STATE } from '../../../core/sharedAuth'
import fixture from '../../../../api/src/tests/fixtures/learning_surface_v1.json' with { type: 'json' }
import type { LearningSurface } from '../../../../web/services/ai/learningSurface'

test.use({ storageState: STUDENT_STATE })
test.skip(
  ![BASE_URL, API_URL].every((value) => ['localhost', '127.0.0.1', '[::1]'].includes(new URL(value).hostname)),
  'Requires a local synthetic lesson and browser transport fixtures.',
)

test('lesson practice preserves a draft, recovers saved feedback, and falls back to text help', async ({ page }) => {
  test.setTimeout(300_000)
  const org = await getOrg()
  const token = await login(ADMIN_EMAIL, ADMIN_PASSWORD)
  const fd = new FormData()
  fd.set('name', `Studytomy fractions fixture ${uniqueSuffix()}`)
  fd.set('description', 'Synthetic fractions lesson')
  fd.set('public', 'true')
  fd.set('about', 'Synthetic lesson')
  fd.set('learnings', '[]')
  fd.set('tags', '')
  const courseResponse = await fetch(`${API_URL}/courses/?org_id=${org.id}`, {
    method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: fd,
  })
  expect(courseResponse.ok).toBe(true)
  const course: { id: number; course_uuid: string } = await courseResponse.json()
  const chapter = await req<{ id: number }>('POST', '/chapters/', token, {
    name: 'Fractions', description: '', org_id: org.id, course_id: course.id,
  })
  const lesson = await req<{ activity_uuid: string }>(
    'POST', `/activities/?coursechapter_id=${chapter.id}&org_id=${org.id}`, token, {
      name: 'Fractions practice fixture', activity_type: 'TYPE_DYNAMIC',
      activity_sub_type: 'SUBTYPE_DYNAMIC_PAGE', chapter_id: chapter.id, published: true,
    },
  )
  await req('PUT', `/courses/${course.course_uuid}`, token, { public: true, published: true })
  await page.addInitScript(() => localStorage.setItem('aiPanelMode', 'hover'))

  let saved: LearningSurface | null = null
  let generationCalls = 0
  let actionCalls = 0
  let answerRequest: unknown
  let quotaDenied = false
  let policyDenied = false
  let availabilityReads = 0
  let delayOnce = true
  let releaseDelayed: (() => void) | undefined
  await page.route('**/ai/activity/*/learning_surface', (route) => {
    availabilityReads++
    return route.fulfill(policyDenied
      ? { status: 409, json: { detail: { code: 'POLICY_NOT_APPROVED' } } }
      : { json: { available: true, surface: saved } })
  })
  await page.route('**/ai/stream/start/learning_surface', async (route) => {
    generationCalls++
    if (quotaDenied) {
      await route.fulfill({ status: 403, json: { detail: { code: 'LEARNING_SURFACE_ALLOWANCE' } } })
      return
    }
    const { request_id, activity_uuid, mode } = route.request().postDataJSON()
    expect(activity_uuid).toBe(lesson.activity_uuid)
    expect(mode).toBe('practice')
    if (delayOnce) {
      delayOnce = false
      await new Promise<void>((resolve) => { releaseDelayed = resolve })
      await route.abort().catch(() => {})
      return
    }
    saved = { ...fixture as LearningSurface, request_id, activity_id: activity_uuid, surface_id: randomUUID() }
    await route.fulfill({
      contentType: 'text/event-stream',
      body: `data: ${JSON.stringify({ type: 'learning_surface', surface: saved })}\n\n`,
    })
  })
  await page.route('**/ai/activity/*/learning_surface/actions', async (route) => {
    const body = route.request().postDataJSON()
    if (!saved) throw new Error('Synthetic practice surface is missing')
    actionCalls++
    if (body.action === 'submit_answer' && answerRequest) {
      expect(body).toEqual(answerRequest)
      await route.fulfill({ json: saved })
      return
    }
    expect(body.surface_id).toBe(saved.surface_id)
    expect(body.expected_revision).toBe(saved.revision)
    if (body.action === 'request_hint') {
      saved = {
        ...saved, revision: 2, actions: ['submit_answer'],
        components: [...saved.components, { kind: 'hint', id: 'hint', text: 'Divide both numbers by two.' }],
      }
    } else {
      expect(body.action).toBe('submit_answer')
      expect(body.answer).toBe('2/4')
      saved = {
        ...saved, revision: 3, actions: ['next_question'],
        components: [...saved.components, {
          kind: 'feedback', id: 'result', attempt_id: randomUUID(), text: 'The fractions are equivalent.',
          outcome: 'correct',
        }],
      }
      answerRequest = body
      await route.abort()
      return
    }
    await route.fulfill({ json: saved })
  })

  const path = `/course/${course.course_uuid.replace(/^course_/, '')}/activity/${lesson.activity_uuid.replace(/^activity_/, '')}`
  await page.goto(new URL(path, BASE_URL).href, { waitUntil: 'domcontentloaded', timeout: 180_000 })
  const ask = page.getByText('Ask AI', { exact: true }).first()
  await expect(ask).toBeVisible({ timeout: 60_000 })
  await ask.click()
  const practice = page.getByRole('region', { name: 'Lesson visuals and practice' })
  const start = practice.getByRole('button', { name: 'Practise this' })
  await expect(start).toBeVisible()
  await start.click()
  await expect.poll(() => generationCalls).toBe(1)
  const cancel = practice.getByRole('button', { name: 'Cancel request' })
  await expect(cancel).toBeVisible()
  await cancel.click()
  await expect(practice.getByText(/request cancelled/i)).toBeVisible()
  releaseDelayed?.()
  await expect(practice.getByText('Practice answer saved.')).toHaveCount(0)
  await start.click()
  const answer = practice.getByRole('textbox', { name: 'Your answer (for example, 1/2)' })
  await expect(answer).toBeVisible()
  await answer.fill('2/4')
  await expect(answer).toHaveValue('2/4')
  await practice.getByRole('button', { name: 'Show a hint' }).click()
  await expect(practice.getByText('Divide both numbers by two.')).toBeVisible()
  await expect(answer).toHaveValue('2/4')
  const submit = practice.getByRole('button', { name: 'Submit answer' })
  await submit.click()
  await expect.poll(() => actionCalls).toBe(2)
  await expect(submit).toBeEnabled()
  await expect(answer).toHaveValue('2/4')
  await expect(practice.getByText('Practice answer saved.')).toHaveCount(0)
  await submit.click()
  await expect(practice.getByText('Practice answer saved.')).toBeVisible()
  expect(actionCalls).toBe(3)

  await page.reload({ waitUntil: 'domcontentloaded', timeout: 90_000 })
  await expect(ask).toBeVisible({ timeout: 60_000 })
  await ask.click()
  await expect(practice.getByText('Practice answer saved.')).toBeVisible()
  expect(generationCalls).toBe(2)
  quotaDenied = true
  await start.click()
  await expect(practice.getByText(/a new visual is unavailable/i)).toBeVisible()
  await expect(practice.getByText('Practice answer saved.')).toBeVisible()
  await expect(page.getByRole('textbox', { name: 'Ask AI About this Lecture' })).toBeVisible()
  policyDenied = true
  const priorReads = availabilityReads
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')))
  await expect.poll(() => availabilityReads).toBeGreaterThan(priorReads)
  await expect(practice).toHaveCount(0)
  const beforeRestore = availabilityReads
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true })))
  await expect(practice).toHaveCount(0)
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })))
  await expect.poll(() => availabilityReads).toBeGreaterThan(beforeRestore)
  await expect(practice).toHaveCount(0)
  await page.reload({ waitUntil: 'domcontentloaded', timeout: 90_000 })
  await expect(ask).toBeVisible({ timeout: 60_000 })
  await ask.click()
  await expect(page.getByRole('textbox', { name: 'Ask AI About this Lecture' })).toBeVisible()
  await expect(practice).toHaveCount(0)
})
