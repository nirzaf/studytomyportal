import { randomUUID } from 'node:crypto'
import { test, expect } from '../../../core/fixtures'
import { ADMIN_EMAIL, ADMIN_PASSWORD, API_URL, BASE_URL, uniqueSuffix } from '../../../core/instance'
import { getOrg, login, req } from '../../../core/client'
import { STUDENT_STATE } from '../../../core/sharedAuth'

test.use({ storageState: STUDENT_STATE, viewport: { width: 390, height: 844 } })
test.skip(
  ![BASE_URL, API_URL].every(url => ['localhost', '127.0.0.1', '[::1]'].includes(new URL(url).hostname)),
  'Requires a local synthetic learner and course.',
)

test('adaptive diagnostic starts, resumes, retries and ends in the lesson', async ({ page }) => {
  test.setTimeout(180_000)
  const org = await getOrg()
  const token = await login(ADMIN_EMAIL, ADMIN_PASSWORD)
  const fields = new FormData()
  fields.set('name', `Adaptive measurements ${uniqueSuffix()}`)
  fields.set('description', 'Synthetic adaptive course')
  fields.set('public', 'true')
  fields.set('about', 'Synthetic measurements lesson')
  fields.set('learnings', '[]')
  fields.set('tags', '')
  const courseResponse = await fetch(`${API_URL}/courses/?org_id=${org.id}`, {
    method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: fields,
  })
  expect(courseResponse.ok).toBe(true)
  const course = await courseResponse.json() as { id: number; course_uuid: string }
  const chapter = await req<{ id: number }>('POST', '/chapters/', token, {
    name: 'Measurements', description: '', org_id: org.id, course_id: course.id,
  })
  const lesson = await req<{ activity_uuid: string }>(
    'POST', `/activities/?coursechapter_id=${chapter.id}&org_id=${org.id}`, token, {
      name: 'Synthetic measurement', activity_type: 'TYPE_DYNAMIC',
      activity_sub_type: 'SUBTYPE_DYNAMIC_PAGE', chapter_id: chapter.id, published: true,
    },
  )
  await req('PUT', `/courses/${course.course_uuid}`, token, { public: true, published: true })

  const revision = `diagnostic_${randomUUID()}`
  const first = {
    uuid: `issued_${randomUUID()}`, ordinal: 1, allowed_actions: ['answer', 'skip'],
    question: { kind: 'number', prompt: 'Enter a synthetic measurement', unit: 'cm' },
  }
  const second = {
    ...first, uuid: `issued_${randomUUID()}`, ordinal: 2,
    question: { ...first.question, prompt: 'Enter the next measurement' },
  }
  let attempt = {
    uuid: `attempt_${randomUUID()}`, purpose: 'diagnostic', status: 'active', version: 2,
    answered_count: 0, skipped_count: 0, current_item: first,
  }
  let active = false
  let answerKey: string | undefined
  const api = '**/studytomy/adaptive/orgs/*/me/'
  const tutorUuid = `tutor_${randomUUID()}`
  let tutorOperation: string | undefined
  let tutorSubmissions = 0
  await page.route(`${api}tutor-options?*`, route => route.fulfill({
    json: active ? { mode: 'unavailable', reason: 'assessment_in_progress', actions: [], practice_attempt_uuid: null }
      : { mode: 'guided', reason: null, actions: ['explain', 'check_understanding'], practice_attempt_uuid: null },
  }))
  await page.route(`${api}tutor-sessions`, route => {
    expect(route.request().postDataJSON()).toEqual({ activity_uuid: lesson.activity_uuid })
    return route.fulfill({ status: 201, json: { uuid: tutorUuid, activity_uuid: lesson.activity_uuid, version: 1, closed_at: null } })
  })
  await page.route(`${api}tutor-sessions/${tutorUuid}/turns`, route => {
    tutorSubmissions++
    tutorOperation = route.request().postDataJSON().operation_uuid
    expect(tutorOperation).toBeTruthy()
    expect(route.request().postDataJSON().action).toBe('explain')
    return route.abort()
  })
  await page.route(`${api}tutor-sessions/${tutorUuid}/turns/*`, route => {
    expect(new URL(route.request().url()).pathname.endsWith(`/${tutorOperation}`)).toBe(true)
    return route.fulfill({ json: {
      operation_uuid: tutorOperation, state: 'completed', session_version: 2,
      result: { message: 'Compare the two measurements.', check_prompt: null,
        fallback_used: true, context_limited: false, hint_level: null },
    } })
  })
  const feedbackRequests: unknown[] = []
  await page.route(`${api}tutor-sessions/${tutorUuid}/feedback`, route => {
    const payload = route.request().postDataJSON()
    expect(payload).toEqual({ turn_uuid: tutorOperation, issue_code: 'unclear' })
    feedbackRequests.push(payload)
    if (feedbackRequests.length === 1) return route.abort()
    return route.fulfill({ json: { ...payload, received_at: new Date().toISOString() } })
  })
  await page.route(`${api}tutor-sessions/${tutorUuid}/close`, route => {
    expect(route.request().postDataJSON()).toEqual({ expected_version: 2 })
    return route.fulfill({ json: {
      uuid: tutorUuid, activity_uuid: lesson.activity_uuid, version: 3, closed_at: new Date().toISOString(),
    } })
  })
  const suggestedActivity = `activity_${randomUUID()}`
  let offerSuggestion = true
  await page.route(`${api}lesson-mastery?*`, route => {
    expect(new URL(route.request().url()).searchParams.get('activity_uuid')).toBe(lesson.activity_uuid)
    return route.fulfill({ json: {
      next_offset: null, concepts: [
        { concept_uuid: 'synthetic-measurement', title: 'Measurement concepts', state: 'not_assessed',
          independent_family_count: 0, assisted_count: 1, review_due: false, score: null },
        { concept_uuid: 'synthetic-units', title: 'Units', state: 'insufficient_evidence',
          independent_family_count: 1, assisted_count: 0, review_due: false },
      ],
    } })
  })
  await page.route(`${api}recommendations?*`, route => route.fulfill({
    json: offerSuggestion ? [{
      activity_uuid: suggestedActivity, course_uuid: course.course_uuid, activity_title: 'Review measurement units',
      reason: 'practice_needed',
    }] : [{ reason: 'no_accessible_activity', support_action: 'return_to_lesson' }],
  }))
  await page.route(`${api}attempts/options?*`, route => {
    expect(new URL(route.request().url()).searchParams.get('source_activity_uuid')).toBe(lesson.activity_uuid)
    return route.fulfill({ json: [{ purpose: 'diagnostic', uuid: revision, label: 'Measurements' }] })
  })
  await page.route(`${api}attempts/current?*`, route => route.fulfill({
    json: active && new URL(route.request().url()).searchParams.get('purpose') === 'diagnostic'
      ? attempt : null,
  }))
  await page.route(`${api}diagnostic-attempts`, route => {
    expect(route.request().postDataJSON()).toEqual({
      source_activity_uuid: lesson.activity_uuid, diagnostic_revision_uuid: revision,
    })
    expect(route.request().headers()['idempotency-key']).toBeTruthy()
    active = true
    return route.fulfill({ status: 201, json: attempt })
  })
  await page.route(`${api}attempts/${attempt.uuid}/responses`, route => {
    expect(route.request().postDataJSON()).toEqual({
      item_uuid: first.uuid, expected_version: 2,
      response: { kind: 'number', value: '12.5' }, declared_external_help: false,
    })
    const key = route.request().headers()['idempotency-key']
    expect(key).toBeTruthy()
    if (!answerKey) {
      answerKey = key
      attempt = { ...attempt, version: 3, answered_count: 1, current_item: second }
      return route.abort()
    }
    expect(key).toBe(answerKey)
    return route.fulfill({ json: { status: 'active', version: 3, next_item: second } })
  })
  await page.route(`${api}attempts/${attempt.uuid}/abandon`, route => {
    expect(route.request().postDataJSON()).toEqual({ expected_version: 3, reason: 'leave_assessment' })
    active = false
    return route.fulfill({ json: { ...attempt, status: 'abandoned', version: 4, current_item: null } })
  })

  const path = `/course/${course.course_uuid.replace(/^course_/, '')}/activity/${lesson.activity_uuid.replace(/^activity_/, '')}`
  await page.goto(new URL(path, BASE_URL).href, { waitUntil: 'domcontentloaded', timeout: 90_000 })
  await expect(page.getByRole('heading', { name: 'Synthetic measurement', exact: true })).toBeVisible({ timeout: 60_000 })
  const progress = page.getByRole('button', { name: 'Concept progress', exact: true })
  await expect(progress).toBeVisible({ timeout: 45_000 })
  await expect(progress).toHaveAttribute('aria-expanded', 'false')
  await progress.focus()
  await page.keyboard.press('Enter')
  await expect(progress).toHaveAttribute('aria-expanded', 'true')
  await expect(page.getByText('Not yet assessed', { exact: true })).toBeVisible()
  await expect(page.getByText('More evidence needed', { exact: true })).toBeVisible()
  await expect(page.getByText(/No independent evidence yet/)).toBeVisible()
  await expect(page.getByText(/Supported practice does not count/)).toBeVisible()
  await expect(page.getByRole('region', { name: 'Concept progress' }).getByText('0%', { exact: true })).toHaveCount(0)
  const suggestion = page.getByRole('link', { name: 'Review measurement units' })
  await expect(suggestion).toHaveAttribute('href', `/course/${course.course_uuid.slice(7)}/activity/${suggestedActivity.slice(9)}`)
  await page.evaluate(() => { document.documentElement.dir = 'rtl' })
  const progressRegion = page.getByRole('region', { name: 'Concept progress' })
  await expect(progressRegion.getByText('Not yet assessed')).toBeVisible()
  expect(await progressRegion.evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true)
  await page.evaluate(() => { document.documentElement.dir = 'ltr' })
  offerSuggestion = false
  await progress.click()
  await expect(suggestion).toHaveCount(0)
  await progress.click()
  await expect(page.getByText('Continue with this lesson for now.')).toBeVisible()
  await expect(suggestion).toHaveCount(0)
  await progress.click()
  await expect(progress).toHaveAttribute('aria-expanded', 'false')
  const guidance = page.getByRole('button', { name: 'Lesson guidance', exact: true })
  await expect(guidance).toBeVisible()
  await guidance.click()
  const guided = page.getByRole('region', { name: 'Guided lesson help' })
  const beginGuidance = guided.getByRole('button', { name: 'Start guidance' })
  await expect(beginGuidance).toBeVisible()
  await beginGuidance.click()
  const explain = guided.getByRole('button', { name: 'Explain this concept' })
  await expect(explain).toBeVisible()
  await explain.click()
  await expect(guided.getByRole('status')).toContainText('could not be confirmed')
  const checkRequest = guided.getByRole('button', { name: 'Check request status' })
  await expect(checkRequest).toBeEnabled()
  await checkRequest.click()
  await expect(guided.getByText('Reviewed guidance')).toBeVisible()
  await expect(guided.getByText('Compare the two measurements.')).toBeVisible()
  expect(tutorSubmissions).toBe(1)
  const report = guided.getByRole('button', { name: 'Report an issue' })
  await expect(report).toBeVisible()
  await report.focus()
  await page.keyboard.press('Enter')
  const issue = guided.getByRole('combobox', { name: 'Issue with this response' })
  await expect(issue).toBeVisible()
  const sendFeedback = guided.getByRole('button', { name: 'Send feedback' })
  await expect(sendFeedback).toBeDisabled()
  await issue.selectOption('unclear')
  await expect(sendFeedback).toBeEnabled()
  await sendFeedback.click()
  await expect(guided.getByText('Feedback could not be confirmed.', { exact: false })).toBeVisible()
  await expect(issue).toBeDisabled()
  await guided.getByRole('button', { name: 'Retry report' }).click()
  await expect(guided.getByText('Feedback received.', { exact: true })).toBeVisible()
  expect(feedbackRequests).toHaveLength(2)
  expect(feedbackRequests[1]).toEqual(feedbackRequests[0])
  await guided.getByRole('button', { name: 'End guidance' }).click()
  await expect(guided.getByText('Guidance ended.', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Close AI panel' }).click()
  await expect(guided).toHaveCount(0)
  const panel = page.getByRole('region', { name: 'Lesson diagnostic', exact: true })
  const start = panel.getByRole('button', { name: 'Start Measurements' })
  await expect(start).toBeVisible({ timeout: 45_000 })
  await start.click()
  const answer = panel.getByRole('textbox', { name: 'Your answer (cm)' })
  await expect(answer).toBeVisible()
  await answer.fill('12.5')
  await expect(answer).toHaveValue('12.5')
  await page.reload({ waitUntil: 'domcontentloaded', timeout: 60_000 })
  await expect(guidance).toHaveCount(0)
  await expect(explain).toHaveCount(0)
  await expect(answer).toHaveValue('', { timeout: 60_000 })
  await answer.fill('12.5')
  await expect(answer).toHaveValue('12.5')
  await panel.getByRole('button', { name: 'Save response' }).click()
  await expect(panel.getByRole('alert')).toContainText('Save could not be confirmed')
  await panel.getByRole('button', { name: 'Retry save' }).click()
  await expect(panel.getByText('Response saved.')).toBeVisible()
  await expect(panel.getByRole('group', { name: 'Enter the next measurement' })).toBeVisible()

  const end = panel.getByRole('button', { name: 'End attempt', exact: true })
  await end.focus()
  await page.keyboard.press('Enter')
  await expect(panel.getByRole('button', { name: 'Keep working' })).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(panel.getByRole('button', { name: 'End this attempt' })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(panel.getByText('Attempt ended.')).toBeVisible()
  await expect(answer).toHaveCount(0)
})
