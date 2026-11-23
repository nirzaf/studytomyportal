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

test('adaptive diagnostic resumes, completes, and refreshes lesson progress', async ({ page }) => {
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
  type MockAttempt = {
    uuid: string; purpose: 'diagnostic'; status: 'active' | 'abandoned' | 'completed'; version: number
    answered_count: number; skipped_count: number; current_item: typeof first | typeof second | null
  }
  let attempt: MockAttempt = {
    uuid: `attempt_${randomUUID()}`, purpose: 'diagnostic', status: 'active', version: 2,
    answered_count: 0, skipped_count: 0, current_item: first,
  }
  let attemptStarts = 0
  let active = false
  let evidenceUpdated = false
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
        { concept_uuid: 'synthetic-measurement', title: 'Measurement concepts',
          state: evidenceUpdated ? 'developing' : 'not_assessed',
          independent_family_count: evidenceUpdated ? 1 : 0, assisted_count: 1, review_due: false, score: null },
        { concept_uuid: 'synthetic-units', title: 'Units', state: 'insufficient_evidence',
          independent_family_count: 1, assisted_count: 0, review_due: false },
      ],
    } })
  })
  await page.route(`${api}recommendations?*`, route => route.fulfill({
    json: evidenceUpdated ? [{
      activity_uuid: suggestedActivity, course_uuid: course.course_uuid, activity_title: 'Next supported lesson',
      reason: 'practice_needed',
    }] : offerSuggestion ? [{
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
    if (attemptStarts > 0) {
      attempt = {
        ...attempt, uuid: `attempt_${randomUUID()}`, status: 'active', version: 2,
        answered_count: 0, skipped_count: 0, current_item: first,
      }
    }
    attemptStarts++
    active = true
    return route.fulfill({ status: 201, json: attempt })
  })
  await page.route(`${api}attempts/*/responses`, route => {
    const payload = route.request().postDataJSON()
    expect(new URL(route.request().url()).pathname).toContain(`/attempts/${attempt.uuid}/responses`)
    expect(payload.response).toEqual({ kind: 'number', value: '12.5' })
    expect(payload.declared_external_help).toBe(false)
    const key = route.request().headers()['idempotency-key']
    expect(key).toBeTruthy()
    if (!answerKey) {
      expect(payload).toEqual({
        item_uuid: first.uuid, expected_version: 2,
        response: { kind: 'number', value: '12.5' }, declared_external_help: false,
      })
      answerKey = key
      attempt = { ...attempt, version: 3, answered_count: 1, current_item: second }
      return route.abort()
    }
    if (payload.item_uuid === first.uuid) {
      expect(payload.item_uuid).toBe(first.uuid)
      expect(key).toBe(answerKey)
      attempt = { ...attempt, version: 3, answered_count: 1, current_item: second }
      return route.fulfill({ json: { status: 'active', version: 3, next_item: second } })
    }
    expect(payload).toEqual({
      item_uuid: second.uuid, expected_version: 3,
      response: { kind: 'number', value: '12.5' }, declared_external_help: false,
    })
    evidenceUpdated = true
    active = false
    attempt = { ...attempt, status: 'completed', version: 4, answered_count: 2, current_item: null }
    return route.fulfill({ json: { status: 'completed', version: attempt.version, next_item: null } })
  })
  await page.route(`${api}attempts/*/summary`, route => route.fulfill({ json: {
    coverage_complete: true, correct_count: 2, scored_count: 2,
  } }))
  await page.route(`${api}attempts/*/abandon`, route => {
    expect(route.request().postDataJSON()).toEqual({ expected_version: 2, reason: 'leave_assessment' })
    active = false
    return route.fulfill({ json: { ...attempt, status: 'abandoned', version: 3, current_item: null } })
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
  await panel.getByRole('button', { name: 'Save response' }).click()
  await expect(panel.getByRole('alert')).toContainText('Save could not be confirmed')
  await panel.getByRole('button', { name: 'Retry save' }).click()
  await expect(panel.getByText('Response saved.')).toBeVisible()
  await expect(panel.getByRole('group', { name: 'Enter the next measurement' })).toBeVisible()
  await page.reload({ waitUntil: 'domcontentloaded', timeout: 60_000 })
  await expect(guidance).toHaveCount(0)
  await expect(explain).toHaveCount(0)
  const resumedPanel = page.getByRole('region', { name: 'Lesson diagnostic', exact: true })
  await expect(resumedPanel.getByRole('group', { name: 'Enter the next measurement' })).toBeVisible()
  const resumedAnswer = resumedPanel.getByRole('textbox', { name: 'Your answer (cm)' })
  await expect(resumedAnswer).toHaveValue('')
  const refreshedProgress = page.getByRole('button', { name: 'Concept progress', exact: true })
  await refreshedProgress.click()
  await expect(page.getByText('Not yet assessed', { exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Review measurement units' })).toBeVisible()
  await resumedAnswer.fill('12.5')
  await resumedPanel.getByRole('button', { name: 'Save response' }).click()
  await expect(resumedPanel.getByText('Attempt completed.')).toBeVisible()
  await expect(page.getByText('Developing', { exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Next supported lesson' })).toBeVisible()

  await page.reload({ waitUntil: 'domcontentloaded', timeout: 60_000 })
  const finalPanel = page.getByRole('region', { name: 'Lesson diagnostic', exact: true })
  await finalPanel.getByRole('button', { name: 'Start Measurements' }).click()
  const end = finalPanel.getByRole('button', { name: 'End attempt', exact: true })
  await end.focus()
  await page.keyboard.press('Enter')
  await expect(finalPanel.getByRole('button', { name: 'Keep working' })).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(finalPanel.getByRole('button', { name: 'End this attempt' })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(finalPanel.getByText('Attempt ended.')).toBeVisible()
})
