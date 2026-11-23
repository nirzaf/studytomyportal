import { randomUUID } from 'node:crypto'
import { test, expect } from '../../../core/fixtures'
import { API_URL, BASE_URL } from '../../../core/instance'
import { STUDENT_STATE } from '../../../core/sharedAuth'

test.use({
  storageState: process.env.E2E_STUDYTOMY_SUPPORT_STATE ?? STUDENT_STATE,
  viewport: { width: 390, height: 844 },
})
test.skip(
  process.env.E2E_STUDYTOMY_SUPPORT_FIXTURE !== '1' ||
    ![BASE_URL, API_URL].every(url => ['localhost', '127.0.0.1', '[::1]'].includes(new URL(url).hostname)),
  'Requires a loopback instance with synthetic approved support guidance and staffed intake.',
)

test('support operating report is available only to scoped staff', async ({ browser }) => {
  const staffState = process.env.E2E_STUDYTOMY_SUPPORT_REPORT_STAFF_STATE
  const reporterState = process.env.E2E_STUDYTOMY_SUPPORT_REPORT_REPORTER_STATE
  test.skip(!staffState || !reporterState, 'Requires synthetic support report staff and reporter sessions.')
  const staff = await browser.newContext({ storageState: staffState, viewport: { width: 390, height: 844 } })
  const reporter = await browser.newContext({ storageState: reporterState, viewport: { width: 390, height: 844 } })
  const url = new URL('/studytomy/support-cases', BASE_URL).href
  const isReport = (value: string) =>
    new URL(value).origin === new URL(API_URL).origin &&
    new URL(value).pathname.endsWith('/support/staff/operating-report')
  try {
    const page = await staff.newPage()
    const initial = page.waitForResponse(response => isReport(response.url()))
    await page.goto(url, { waitUntil: 'domcontentloaded' })
    const dashboard = page.getByRole('region', { name: 'Support operating report' })
    await expect(dashboard).toBeVisible({ timeout: 60_000 })
    const report = await initial
    expect(report.status()).toBe(200)
    expect(report.headers()['cache-control']).toBe('private, no-store')
    expect(await report.json()).toMatchObject({ first_resolved_cases: 1, reopened_cases: 1 })
    await expect(dashboard.getByText('First resolutions in period', { exact: true })).toBeVisible()
    await expect(dashboard.getByText('Reopened cases in period', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Assigned to me' })).toBeVisible()
    await page.setViewportSize({ width: 1440, height: 900 })
    await expect(dashboard).toBeVisible()
    const refreshed = page.waitForResponse(response => isReport(response.url()))
    await dashboard.getByRole('button', { name: 'Load report', exact: true }).click()
    expect((await refreshed).status()).toBe(200)
    await expect(dashboard.getByText('Reopened cases in period', { exact: true })).toBeVisible()

    const otherPage = await reporter.newPage()
    const denied = otherPage.waitForResponse(response => isReport(response.url()))
    await otherPage.goto(url, { waitUntil: 'domcontentloaded' })
    expect((await denied).status()).toBe(403)
    await expect(otherPage.getByRole('region', { name: 'Support operating report' })).toHaveCount(0)
  } finally {
    await staff.close()
    await reporter.close()
  }
})

test('reach support after learning access is denied and retry a missing acknowledgement', async ({ page }) => {
  const caseUuid = randomUUID()
  const release = { course_uuid: `course_${randomUUID()}`, release_uuid: randomUUID(), name: 'Synthetic course' }
  const learning = '**/studytomy/orgs/*/learning'
  const releasePath = `${learning}/courses/${release.course_uuid}/releases/${release.release_uuid}`
  await page.route(`${learning}/releases`, route =>
    route.fulfill({ json: { releases: [release], next_cursor: null } }),
  )
  await page.route(`${releasePath}/lessons`, route =>
    route.fulfill({ status: 404, json: { detail: 'Course release unavailable.' } }),
  )
  const requests: Array<{ request_id: string; topic: string; subject_user_uuids: string[] }> = []
  await page.route('**/studytomy/orgs/*/support/cases', async route => {
    expect(route.request().method()).toBe('POST')
    const payload = route.request().postDataJSON()
    requests.push(payload)
    if (requests.length === 1) return route.abort()
    return route.fulfill({ json: { case_uuid: caseUuid, topic: payload.topic, status: 'assigned' } })
  })
  await page.route(`**/studytomy/orgs/*/support/cases/${caseUuid}`, route =>
    route.fulfill({ json: { case_uuid: caseUuid, topic: requests[0].topic, status: 'awaiting_information' } }),
  )

  const learningResponse = await page.goto('/account/learning', { waitUntil: 'domcontentloaded' })
  expect(learningResponse?.status(), 'The local learning route must render').toBe(200)
  const course = page.getByRole('button', { name: release.name })
  await expect(course).toBeEnabled()
  await course.click()
  await expect(page.getByText('Learning is unavailable for this account.')).toBeVisible()
  const contact = page.getByRole('link', { name: 'Contact support' })
  await expect(contact).toHaveAttribute('href', '/help')
  await contact.click()
  await expect(page).toHaveURL(/\/help$/)
  await expect(page.getByRole('heading', { name: 'Get help' })).toBeVisible()
  await expect(page.getByText('Response expectations', { exact: true })).toBeVisible()
  const cases = page.getByRole('region', { name: 'Support cases' })
  const topic = cases.getByRole('combobox', { name: 'Support topic' })
  await expect(topic).toBeEnabled()
  const open = cases.getByRole('button', { name: 'Open support case' })
  await expect(open).toBeDisabled()
  const selected = await topic.selectOption({ index: 1 })
  await expect(topic).toHaveValue(selected[0])
  await open.click()
  await expect(cases.getByRole('alert')).toContainText('The request could not be confirmed')
  await expect(open).toBeEnabled()
  await open.click()
  const receipt = cases.getByRole('status')
  await expect(receipt).toContainText('Assigned for review')
  await expect(receipt).toContainText(caseUuid)
  expect(requests).toHaveLength(2)
  expect(requests[0]).toEqual({ request_id: expect.any(String), topic: selected[0], subject_user_uuids: [] })
  expect(requests[1]).toEqual(requests[0])

  await expect(cases.getByRole('textbox', { name: 'Case reference' })).toHaveValue(caseUuid)
  await cases.getByRole('button', { name: 'Check case status' }).click()
  await expect(receipt).toContainText('Awaiting information')
  await expect(receipt).toContainText('Give your case reference when you contact support')
})
