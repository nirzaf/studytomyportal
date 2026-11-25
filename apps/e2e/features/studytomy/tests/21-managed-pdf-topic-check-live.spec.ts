import { readFileSync } from 'node:fs'
import { test, expect } from '../../../core/fixtures'
import { API_URL, BASE_URL } from '../../../core/instance'

type Fixture = {
  orgSlug: string
  orgUuid: string
  learnerUuid: string
  courseUuid: string
  releaseUuid: string
  activityUuid: string
  courseName: string
  pdfName: string
  pdfPage: number
  wrongAnswer: string
  recheckAnswer: string
  states: { learner: string; admin: string; author: string; unrelatedTeacher: string; otherLearner: string }
}

const fixturePath = process.env.E2E_STUDYTOMY_TOPIC_CHECK_FIXTURE
const loopback = [BASE_URL, API_URL].every(url =>
  ['localhost', '127.0.0.1', '[::1]'].includes(new URL(url).hostname),
)
const fixture = fixturePath && loopback ? JSON.parse(readFileSync(fixturePath, 'utf8')) as Fixture : undefined

test.skip(!fixture, 'Requires a loopback test stack and a fresh synthetic reviewed PDF topic-check fixture.')
test.use({ storageState: fixture?.states.learner })

test('resumes a PDF topic recheck and limits staff history to authorized identities', async ({ page, browser }) => {
  const data = fixture!
  const root = new URL(API_URL)
  const path = root.pathname.replace(/\/$/, '') + '/studytomy/orgs/' + encodeURIComponent(data.orgUuid)
  const topicPath = path + '/me/topic-check'
  const scope = new URLSearchParams({
    course_uuid: data.courseUuid, course_release_uuid: data.releaseUuid, activity_uuid: data.activityUuid,
  })
  const historyPath = path + '/staff/learners/' + encodeURIComponent(data.learnerUuid) + '/topic-check'
  const isTopic = (url: string) => {
    const parsed = new URL(url)
    return parsed.origin === root.origin && parsed.pathname === topicPath
  }

  async function openPdf() {
    await page.getByRole('button', { name: data.courseName, exact: true }).click()
    const lesson = page.getByRole('button', { name: new RegExp(`^(Open )?${data.pdfName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')} PDF$`) })
    await expect(lesson).toBeEnabled()
    await lesson.click()
    await expect(page.getByTitle(data.pdfName)).toBeVisible()
  }

  await page.goto('/account/learning')
  await openPdf()
  const check = page.getByRole('button', { name: 'Check answer', exact: true })
  const answer = page.getByRole('textbox', { name: 'Your answer', exact: true })
  await expect(answer).toBeVisible()
  await answer.fill(data.wrongAnswer)
  const initialPost = page.waitForResponse(response => isTopic(response.url()) && response.request().method() === 'POST')
  await check.click()
  const wrong = await initialPost
  expect(wrong.status()).toBe(200)
  const recheck = await wrong.json()
  expect(recheck).toMatchObject({ phase: 'recheck', outcome: 'incorrect', pdf_page: data.pdfPage })
  await expect(page.getByText(recheck.reteaching, { exact: true })).toBeVisible()

  const replay = await page.request.post(wrong.url(), {
    headers: { authorization: (await wrong.request().allHeaders()).authorization },
    data: wrong.request().postDataJSON(),
  })
  expect(replay.status()).toBe(200)
  expect(await replay.json()).toEqual(recheck)

  await page.reload()
  await openPdf()
  await expect(page.getByRole('heading', { name: 'Try a different fractions question' })).toBeVisible()
  await expect(page.getByText(recheck.reteaching, { exact: true })).toBeVisible()
  await answer.fill(data.recheckAnswer)
  await check.click()
  await expect(page.getByText(/Correct\. Topic check complete/)).toBeVisible()

  for (const role of ['admin', 'author'] as const) {
    const context = await browser.newContext({ baseURL: BASE_URL, storageState: data.states[role] })
    try {
      const staff = await context.newPage()
      await staff.goto(`/orgs/${encodeURIComponent(data.orgSlug)}/dash/courses/course/${encodeURIComponent(data.courseUuid)}/adaptive`)
      const timeline = staff.getByRole('heading', { name: 'Reviewed PDF topic check timeline' }).locator('..')
      await timeline.getByLabel('Learner reference').fill(data.learnerUuid)
      await timeline.getByLabel('Release reference').fill(data.releaseUuid)
      await timeline.getByLabel('Lesson reference').fill(data.activityUuid)
      await timeline.getByRole('button', { name: 'View timeline' }).click()
      await expect(timeline.getByRole('listitem')).toHaveCount(2)
      await expect(timeline.getByRole('listitem').nth(0)).toContainText('Initial check: incorrect')
      await expect(timeline.getByRole('listitem').nth(0)).toContainText(recheck.reteaching)
      await expect(timeline.getByRole('listitem').nth(1)).toContainText('Recheck: correct (assisted)')
    } finally {
      await context.close()
    }
  }

  for (const role of ['unrelatedTeacher', 'otherLearner'] as const) {
    const context = await browser.newContext({ storageState: data.states[role] })
    try {
      const response = await context.request.get(new URL(`${historyPath}?${scope}`, root).href)
      expect([403, 404]).toContain(response.status())
    } finally {
      await context.close()
    }
  }
})
