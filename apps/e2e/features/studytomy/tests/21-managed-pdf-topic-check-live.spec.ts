import { lstatSync, readFileSync, realpathSync, statSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { test, expect } from '../../../core/fixtures'
import { blockNonLoopbackRequests } from '../../../core/browser-egress-guard'
import { API_URL, BASE_URL } from '../../../core/instance'

type Fixture = {
  orgSlug: string
  orgUuid: string
  learnerUuid: string
  learnerUserUuid: string
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
const loopback = [BASE_URL, API_URL].every(value => {
  const url = new URL(value)
  return url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
    && !url.username && !url.password && !url.search && !url.hash
})
const repository = realpathSync(fileURLToPath(new URL('../../../../../', import.meta.url)))
function privateFixtureFile(file: string): string {
  if (!path.isAbsolute(file)) throw new Error('Topic-check fixture paths must be absolute.')
  const meta = lstatSync(file)
  const real = realpathSync(file)
  if (!meta.isFile() || (meta.mode & 0o077) !== 0
    || (statSync(path.dirname(real)).mode & 0o077) !== 0
    || real.startsWith(repository + path.sep)) {
    throw new Error('Keep topic-check fixtures and browser states in an owner-only directory outside Git.')
  }
  return real
}
const fixture = fixturePath && loopback
  ? JSON.parse(readFileSync(privateFixtureFile(fixturePath), 'utf8')) as Fixture
  : undefined
if (fixture) {
  if (!fixture.learnerUserUuid) throw new Error('The topic-check fixture must include the learner user UUID.')
  for (const role of ['learner', 'admin', 'author', 'unrelatedTeacher', 'otherLearner'] as const) {
    fixture.states[role] = privateFixtureFile(fixture.states[role])
  }
}

test.skip(!fixture, 'Requires a loopback test stack and a fresh synthetic reviewed PDF topic-check fixture.')
test.use({ storageState: fixture?.states.learner, serviceWorkers: 'block' })

test('resumes a PDF topic recheck and limits staff history to authorized identities', async ({ page, browser }) => {
  const data = fixture!
  const blockedHosts: string[] = []
  await blockNonLoopbackRequests(page.context(), blockedHosts)
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
  let submitted = 0
  page.on('request', request => {
    if (isTopic(request.url()) && request.method() === 'POST') submitted += 1
  })
  await answer.fill(data.wrongAnswer)
  // Wait for the browser's asynchronous draft write before navigating away.
  const draftKey = JSON.stringify([data.orgUuid, data.learnerUserUuid, data.courseUuid, data.activityUuid])
  await expect.poll(() => page.evaluate(({ key, expected }) => new Promise<boolean>(resolve => {
    const open = indexedDB.open('studytomy-topic-drafts')
    open.onerror = () => resolve(false)
    open.onsuccess = () => {
      const db = open.result
      if (!db.objectStoreNames.contains('drafts')) {
        db.close()
        resolve(false)
        return
      }
      const record = db.transaction('drafts').objectStore('drafts').get(key)
      record.onsuccess = () => {
        db.close()
        resolve(record.result?.answer === expected)
      }
      record.onerror = () => { db.close(); resolve(false) }
    }
  }), { key: draftKey, expected: data.wrongAnswer })).toBe(true)
  await page.reload()
  await openPdf()
  await expect(answer).toHaveValue(data.wrongAnswer)
  expect(submitted).toBe(0)

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
    const context = await browser.newContext({ baseURL: BASE_URL, storageState: data.states[role], serviceWorkers: 'block' })
    try {
      await blockNonLoopbackRequests(context, blockedHosts)
      const staff = await context.newPage()
      await staff.goto(`/orgs/${encodeURIComponent(data.orgSlug)}/dash/courses/course/${encodeURIComponent(data.courseUuid.replace(/^course_/, ''))}/adaptive`)
      const heading = staff.getByRole('heading', { name: 'Reviewed PDF topic check timeline' })
      const welcome = staff.getByRole('dialog').filter({
        has: staff.getByRole('button', { name: /^get started$/i }),
      })
      await expect(welcome.or(heading).first()).toBeVisible()
      if (await welcome.isVisible()) {
        await staff.keyboard.press('Escape')
        await expect(welcome).toBeHidden()
      }
      const timeline = heading.locator('..')
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
    const context = await browser.newContext({ baseURL: BASE_URL, storageState: data.states[role], serviceWorkers: 'block' })
    try {
      const refresh = await context.request.get('/api/auth/refresh', { maxRedirects: 0 })
      expect(refresh.status()).toBe(200)
      const token = (await refresh.json()).access_token
      expect(typeof token).toBe('string')
      const headers = { Authorization: `Bearer ${token}` }
      const session = await context.request.get(new URL(`${root.pathname.replace(/\/$/, '')}/users/session`, root).href, {
        headers, maxRedirects: 0,
      })
      expect(session.status()).toBe(200)
      const response = await context.request.get(new URL(`${historyPath}?${scope}`, root).href, {
        headers, maxRedirects: 0,
      })
      expect([403, 404]).toContain(response.status())
    } finally {
      await context.close()
    }
  }
  expect(blockedHosts).toEqual([])
})
