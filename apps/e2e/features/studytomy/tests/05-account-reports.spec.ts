import { randomUUID } from 'node:crypto'
import { test, expect } from '../../../core/fixtures'
import { ADMIN_EMAIL, ADMIN_PASSWORD, API_URL, BASE_URL, makeStudent } from '../../../core/instance'
import { STUDENT_STATE } from '../../../core/sharedAuth'
import { createStudent, getOrg, login } from '../../../core/client'
import { getSessionSwitchStudent } from '../session-user'

test.use({ storageState: STUDENT_STATE, viewport: { width: 390, height: 844 } })
test.skip(
  ![BASE_URL, API_URL].every(url => ['localhost', '127.0.0.1', '[::1]'].includes(new URL(url).hostname)),
  'Requires a local synthetic account.',
)

test('account reports revalidate a previously visible report link', async ({ page }) => {
  test.setTimeout(180_000)
  const metadata = {
    uuid: `report_${randomUUID()}`, version: 3,
    course_title: 'Synthetic measurement report', curriculum_title: 'Maths',
    period_from: '2026-10-01T00:00:00Z', period_to: '2026-11-01T00:00:00Z',
  }
  let withdrawn = false
  let reads = 0
  let submissions = 0
  const api = '**/studytomy/adaptive/orgs/*/'
  await page.route(`${api}me/reports?*`, route => route.fulfill({
    json: { items: withdrawn ? [] : [metadata], next_cursor: null },
  }))
  await page.route(`${api}reports/${metadata.uuid}`, route => {
    reads += 1
    return withdrawn ? route.fulfill({ status: 404, json: { detail: 'Learning record not found.' } })
      : route.fulfill({ json: {
        ...metadata, learner_name: 'Synthetic learner', commentary: null, can_request_review: true,
        evidence_cutoff: metadata.period_to,
        summary: {
          native_completions: 1,
          concepts: [{
            concept_uuid: `concept_${randomUUID()}`, title: 'Comparing units',
            independent_correct: 0, independent_incorrect: 0, supported_practice: 2,
            unknown: 1, pending: 0, missing_independent_evidence: true,
          }],
        },
      } })
  })

  await page.route(`${api}reports/${metadata.uuid}/review-requests`, route => {
    submissions += 1
    expect(route.request().postDataJSON()).toEqual({
      expected_version: metadata.version, reason: 'evidence_error',
    })
    if (submissions === 1) {
      metadata.version += 1
      return route.fulfill({ status: 409, json: { detail: { code: 'STALE_VERSION' } } })
    }
    return route.fulfill({ json: {
      uuid: `review_request_${randomUUID()}`, status: 'received', report_version: metadata.version,
    } })
  })

  await page.goto(new URL('/account/reports', BASE_URL).href, { waitUntil: 'domcontentloaded', timeout: 90_000 })
  const reports = page.getByRole('region', { name: 'Progress reports' })
  const link = reports.getByRole('link', { name: metadata.course_title })
  await expect(link).toBeVisible({ timeout: 60_000 })
  const href = await link.getAttribute('href')
  expect(href).toContain(metadata.uuid)
  await link.focus()
  await page.keyboard.press('Enter')
  await expect(reports.getByRole('heading', { name: metadata.course_title })).toBeVisible()
  await expect(reports.getByText('No independent evidence in this period.')).toBeVisible()
  await expect(reports.getByText('Supported practice: 2')).toBeVisible()
  await expect(reports.getByText('Completed activities updated in this period: 1')).toBeVisible()
  const reason = reports.getByLabel('Reason for review')
  const submit = reports.getByRole('button', { name: 'Send review request' })
  await expect(reason).toBeVisible()
  await reason.selectOption('evidence_error')
  await expect(reason).toHaveValue('evidence_error')
  await submit.click()
  await expect(reports.getByText('This report changed.', { exact: false })).toBeVisible()
  await reports.getByRole('button', { name: 'Reload report' }).click()
  await expect(reason).toBeVisible()
  await expect(reason).toHaveValue('')
  await reason.selectOption('evidence_error')
  await expect(reason).toHaveValue('evidence_error')
  await submit.click()
  await expect(reports.getByRole('status')).toHaveText('Your review request was received.')
  expect(submissions).toBe(2)
  const priorReads = reads
  withdrawn = true
  await page.reload({ waitUntil: 'domcontentloaded', timeout: 60_000 })
  await expect(reports.getByText('This report is unavailable.')).toBeVisible({ timeout: 45_000 })
  expect(reads).toBeGreaterThan(priorReads)
  await expect(reports.getByText('Synthetic learner')).toHaveCount(0)
  await reports.getByRole('link', { name: 'Back to reports' }).click()
  await expect(reports.getByText('There are no progress reports available.')).toBeVisible()
  await expect(link).toHaveCount(0)
})

test('discard a pending private report across a shared browser account switch', async ({ page, context }) => {
  test.setTimeout(180_000)
  // Logout revokes every session for an account. Keep shared suite sessions
  // out of this scenario so subsequent learner tests remain authenticated.
  await context.clearCookies()
  const accountA = makeStudent('report-switch')
  const org = await getOrg()
  const adminToken = await login(ADMIN_EMAIL, ADMIN_PASSWORD)
  await createStudent(adminToken, org.id, {
    ...accountA, first_name: accountA.firstName, last_name: accountA.lastName,
  })
  const accountB = await getSessionSwitchStudent()
  const reportId = `report_${randomUUID()}`
  const privateTitle = 'Account A private measurement report'
  const api = '**/studytomy/adaptive/orgs/*/'
  const identity = (tab: typeof page, username: string) =>
    tab.locator('button[aria-haspopup="menu"]').filter({ hasText: username })
  let detailReads = 0
  let releaseDetail = () => {}
  const holdDetail = new Promise<void>(resolve => { releaseDetail = resolve })
  let detailFinished: Promise<void> | undefined
  await page.route(`${api}reports/${reportId}`, route => {
    detailReads += 1
    if (detailReads > 1) return route.fulfill({ status: 404, json: {} })
    detailFinished = holdDetail.then(async () => {
      // Logout can abort this request before the response is delivered.
      await route.fulfill({ json: {
        uuid: reportId, version: 1, course_title: privateTitle,
        curriculum_title: 'Maths', learner_name: 'Account A learner',
        period_from: '2026-10-01T00:00:00Z', period_to: '2026-11-01T00:00:00Z',
        evidence_cutoff: '2026-11-01T00:00:00Z', commentary: null,
        can_request_review: false, summary: { native_completions: 0, concepts: [] },
      } }).catch(() => {})
    })
    return detailFinished
  })
  const reportUrl = new URL(`/account/reports?report=${reportId}`, BASE_URL).href
  const controls = await context.newPage()
  await page.setViewportSize({ width: 1440, height: 900 })
  await controls.setViewportSize({ width: 1440, height: 900 })
  async function signIn(account: { email: string; password: string }) {
    await controls.goto(new URL('/login', BASE_URL).href, { waitUntil: 'domcontentloaded' })
    const email = controls.getByRole('textbox', { name: 'Email' })
    const password = controls.getByRole('textbox', { name: 'Password' })
    await expect(email).toBeVisible()
    await email.fill(account.email)
    await expect(email).toHaveValue(account.email)
    await password.fill(account.password)
    await expect(password).toHaveValue(/.+/)
    await controls.getByRole('button', { name: 'Login', exact: true }).click()
  }
  try {
    await signIn(accountA)
    await expect(identity(controls, accountA.username)).toBeVisible({ timeout: 60_000 })
    await page.goto(reportUrl, { waitUntil: 'domcontentloaded' })
    await expect.poll(() => detailReads, { timeout: 60_000 }).toBe(1)

    await identity(controls, accountA.username).click()
    const signOut = controls.getByRole('menuitem', { name: /sign out/i })
    await expect(signOut).toBeVisible()
    await signOut.click()
    await expect(identity(page, accountA.username)).toHaveCount(0)

    await signIn(accountB)
    await expect(identity(controls, accountB.username)).toBeVisible({ timeout: 60_000 })
    await expect(identity(page, accountB.username)).toBeVisible({ timeout: 30_000 })
    releaseDetail()
    await detailFinished
    await expect(identity(page, accountB.username)).toBeVisible()
    await expect(page.getByText(privateTitle)).toHaveCount(0)

    await page.goto(reportUrl, { waitUntil: 'domcontentloaded' })
    await expect(page.getByText('This report is unavailable.')).toBeVisible({ timeout: 45_000 })
    expect(detailReads).toBeGreaterThan(1)
    await expect(page.getByText(privateTitle)).toHaveCount(0)
    const storedReport = await page.evaluate(id =>
      [localStorage, sessionStorage].some(store => Object.values(store).some(value => value.includes(id))),
    reportId)
    expect(storedReport).toBe(false)
  } finally {
    releaseDetail()
    await detailFinished
    await controls.close()
  }
})
