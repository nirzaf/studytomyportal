import { randomUUID } from 'node:crypto'
import { test, expect } from '../../../core/fixtures'
import { API_URL, BASE_URL } from '../../../core/instance'
import { STUDENT_STATE } from '../../../core/sharedAuth'

test.use({ storageState: STUDENT_STATE, viewport: { width: 390, height: 844 } })
test.skip(
  ![BASE_URL, API_URL].every(url => ['localhost', '127.0.0.1', '[::1]'].includes(new URL(url).hostname)),
  'Requires a local synthetic account.',
)

test('pilot cash report shows the authorized offer and hides it from the purchaser', async ({ browser }) => {
  const staffState = process.env.E2E_PILOT_CASH_STAFF_STATE
  const purchaserState = process.env.E2E_PILOT_CASH_PURCHASER_STATE
  test.skip(!staffState || !purchaserState, 'Requires synthetic staff and purchaser sessions with settled pilot cash.')
  const staff = await browser.newContext({ storageState: staffState, viewport: { width: 390, height: 844 } })
  const purchaser = await browser.newContext({ storageState: purchaserState, viewport: { width: 390, height: 844 } })
  const isApiPath = (url: string, suffix: string) =>
    new URL(url).origin === new URL(API_URL).origin && new URL(url).pathname.endsWith(suffix)
  try {
    const page = await staff.newPage()
    const optionsResponse = page.waitForResponse(response => isApiPath(response.url(), '/pilot/cash-report-offers'))
    await page.goto(new URL('/studytomy/finance', BASE_URL).href, { waitUntil: 'domcontentloaded' })
    const dashboard = page.getByRole('region', { name: 'Pilot cash report' })
    await expect(dashboard).toBeVisible({ timeout: 60_000 })
    const options = await optionsResponse
    expect(options.status()).toBe(200)
    const [offer] = await options.json()
    expect(offer.offer_version_uuid).toBeTruthy()
    await dashboard.getByLabel('Offer version').selectOption(offer.offer_version_uuid)
    const now = new Date()
    const yesterday = new Date(now.getTime() - 86_400_000)
    await dashboard.getByLabel('Start (UTC)', { exact: true }).fill(yesterday.toISOString().slice(0, 19))
    await dashboard.getByLabel('End (UTC, excluded)', { exact: true }).fill(now.toISOString().slice(0, 19))
    const reportResponse = page.waitForResponse(response => isApiPath(response.url(), '/confirmed-cash'))
    const load = dashboard.getByRole('button', { name: 'Load cash report' })
    await expect(load).toBeEnabled()
    await load.click()
    const report = await reportResponse
    expect(report.status()).toBe(200)
    expect(report.headers()['cache-control']).toBe('private, no-store')
    expect(await report.json()).toMatchObject({
      offer_version_uuid: offer.offer_version_uuid,
      confirmed_cash: [{ currency: 'AUD', collected_minor: 125, collection_count: 1 }],
      settled_refunds_by_reason: [{ currency: 'AUD', reason_code: 'synthetic_refund', settlement_count: 2, refunded_minor: 125 }],
    })
    await expect(dashboard.getByText('Confirmed collections (1)', { exact: true })).toBeVisible()
    await expect(dashboard.getByText('Net cash', { exact: true })).toBeVisible()
    await dashboard.getByText('Settled refunds by reason', { exact: true }).click()
    await expect(dashboard.getByText('synthetic refund (2)', { exact: true })).toBeVisible()
    await page.setViewportSize({ width: 1440, height: 900 })
    await expect(dashboard.getByText('Net cash', { exact: true })).toBeVisible()

    const otherPage = await purchaser.newPage()
    const otherOptions = otherPage.waitForResponse(response => isApiPath(response.url(), '/pilot/cash-report-offers'))
    await otherPage.goto(new URL('/studytomy/finance', BASE_URL).href, { waitUntil: 'domcontentloaded' })
    const filtered = await otherOptions
    expect(filtered.status()).toBe(200)
    expect(await filtered.json()).toEqual([])
    await expect(otherPage.getByRole('region', { name: 'Pilot cash report' })).toHaveCount(0)
    expect((await purchaser.request.get(report.url())).status()).toBe(403)
  } finally {
    await staff.close()
    await purchaser.close()
  }
})

test('finance review keeps a rejected draft and refreshes after a recorded outcome', async ({ page }) => {
  test.setTimeout(180_000)
  const review = {
    customer_ref: randomUUID(), attempt_reference: randomUUID(),
    reason: 'additional_collection', opened_at: '2026-11-17T08:00:00Z',
  }
  let submissions = 0
  const api = '**/studytomy/orgs/*/collections'
  await page.route(`${api}/reviews?*`, route => route.fulfill({
    json: { cases: submissions >= 2 ? [] : [review], next_cursor: null },
  }))
  await page.route(`${api}/${review.customer_ref}/manual-attempts/${review.attempt_reference}/resolution`, route => {
    expect(route.request().method()).toBe('PUT')
    expect(route.request().postDataJSON()).toEqual({
      outcome: 'returned_externally', evidence_reference: 'synthetic-evidence', record_sha256: 'a'.repeat(64),
    })
    submissions += 1
    return submissions === 1
      ? route.fulfill({ status: 409, json: { detail: { code: 'COLLECTION_REMEDY_NOT_APPROVED' } } })
      : route.fulfill({ json: { attempt_reference: review.attempt_reference, status: 'resolved' } })
  })

  await page.goto(new URL('/studytomy/finance', BASE_URL).href, { waitUntil: 'domcontentloaded', timeout: 90_000 })
  const queue = page.getByRole('region', { name: 'Collection reviews' })
  const reviewButton = queue.getByRole('button', { name: 'Review', exact: true })
  await expect(reviewButton).toBeVisible({ timeout: 60_000 })
  await expect(queue.getByText('Additional collection')).toBeVisible()
  await reviewButton.focus()
  await expect(reviewButton).toBeFocused()
  await page.keyboard.press('Enter')
  const outcome = queue.getByRole('combobox', { name: 'Outcome', exact: true })
  await expect(outcome).toBeVisible()
  await outcome.selectOption('returned_externally')
  const reference = queue.getByLabel('Evidence reference', { exact: true })
  await reference.fill('synthetic-evidence')
  await queue.getByLabel('Evidence SHA-256').fill('a'.repeat(64))
  const submit = queue.getByRole('button', { name: 'Record outcome' })
  await expect(submit).toBeEnabled()
  await submit.click()
  await expect(queue.getByRole('alert')).toContainText('prevented this outcome from being recorded')
  await expect(reference).toHaveValue('synthetic-evidence')
  await expect(submit).toBeEnabled()
  await submit.click()
  await expect(queue.getByRole('status')).toHaveText('External outcome recorded.')
  await expect(queue.getByText('No assigned collection reviews to show.')).toBeVisible()
  expect(submissions).toBe(2)
})

test('an unassigned member receives no cases from the real queue', async ({ page }) => {
  const response = page.waitForResponse(response =>
    response.url().startsWith(API_URL) && new URL(response.url()).pathname.endsWith('/collections/reviews'),
  )
  await page.goto(new URL('/studytomy/finance', BASE_URL).href, { waitUntil: 'domcontentloaded' })
  const queue = page.getByRole('region', { name: 'Collection reviews' })
  await expect(queue.getByText('No assigned collection reviews to show.')).toBeVisible({ timeout: 60_000 })
  const result = await response
  expect(result.status()).toBe(200)
  expect(result.headers()['cache-control']).toBe('private, no-store')
  expect(await result.json()).toEqual({ cases: [], next_cursor: null })
  await expect(queue.getByRole('button', { name: 'Review', exact: true })).toHaveCount(0)
})

test('finance reserves the retained request and shows money return as pending', async ({ page }) => {
  test.setTimeout(180_000)
  const order = randomUUID()
  const request = {
    refund_reference: randomUUID(), attempt_reference: randomUUID(),
    amount_minor: 60, currency: 'AUD', reason_code: 'synthetic_refund', status: 'requested',
  }
  const base = `**/studytomy/orgs/*/collections/${order}`
  await page.route(`${base}/refund-requests/${request.refund_reference}`, route => route.fulfill({ json: request }))
  let reservations = 0
  await page.route(`${base}/manual-attempts/${request.attempt_reference}/refund-reservations/${request.refund_reference}`, route => {
    expect(route.request().method()).toBe('PUT')
    expect(route.request().postDataJSON()).toEqual({ amount_minor: 60, currency: 'AUD', reason_code: 'synthetic_refund' })
    reservations += 1
    return route.fulfill({ json: {
      refund_reference: request.refund_reference, amount_minor: 60, currency: 'AUD', status: 'reserved',
    } })
  })
  await page.goto(new URL('/studytomy/finance', BASE_URL).href, { waitUntil: 'domcontentloaded', timeout: 90_000 })
  const review = page.getByRole('region', { name: 'Refund requests' })
  const orderField = review.getByLabel('Order reference', { exact: true })
  await expect(orderField).toBeVisible({ timeout: 60_000 })
  await orderField.fill(order)
  await review.getByLabel('Refund request reference', { exact: true }).fill(request.refund_reference)
  await review.getByRole('button', { name: 'Find request' }).click()
  await expect(review.getByText('synthetic refund', { exact: true })).toBeVisible()
  const reserve = review.getByRole('button', { name: 'Approve refund' })
  await expect(reserve).toBeEnabled()
  await reserve.click()
  await expect(review.getByRole('status')).toHaveText('Funds reserved. Money return is pending.')
  await expect(reserve).toHaveCount(0)
  expect(reservations).toBe(1)
})
