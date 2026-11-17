import { randomUUID } from 'node:crypto'
import { test, expect } from '../../../core/fixtures'
import { API_URL, BASE_URL } from '../../../core/instance'
import { STUDENT_STATE } from '../../../core/sharedAuth'

test.use({ storageState: STUDENT_STATE, viewport: { width: 390, height: 844 } })
test.skip(
  ![BASE_URL, API_URL].every(url => ['localhost', '127.0.0.1', '[::1]'].includes(new URL(url).hostname)),
  'Requires a local synthetic account.',
)

test('open an accepted purchase by keyboard and revalidate its private receipt', async ({ page }) => {
  test.setTimeout(240_000)
  const order = {
    customer_ref: randomUUID(),
    accepted_at: '2026-11-17T09:00:00Z',
    currency: 'AUD',
    order_total_minor: 125,
  }
  let authorized = true
  let reserved = false
  let settled = false
  const submissions: string[] = []
  const api = '**/studytomy/orgs/*/'
  await page.route(`${api}purchases?*`, route => route.fulfill({
    json: { orders: authorized ? [order] : [], next_cursor: null },
  }))
  await page.route(`${api}checkout/orders/${order.customer_ref}/receipt`, route => {
    if (!authorized) return route.fulfill({ status: 404, json: { detail: 'Order receipt unavailable.' } })
    return route.fulfill({
      json: {
        ...order,
        confirmed_minor: 125,
        collection_status: 'confirmed',
        finance_review_required: true,
        allocations: [{
          position: 1,
          courses: ['Synthetic course'],
          access: { clock: 'after_first_access', duration_days: 14, starts_at: null, ends_at: null },
          price: { currency: 'AUD', total_minor: 125, lines: [
            { kind: 'charge', label: 'Course access', amount_minor: 150 },
            { kind: 'discount', label: 'Pilot discount', amount_minor: 25 },
          ] },
          customer_terms: 'Synthetic terms accepted at checkout.',
        }],
      },
    })
  })
  await page.route(`${api}checkout/orders/${order.customer_ref}/refund-options`, route =>
    reserved ? route.fulfill({ status: 404, json: {} })
      : route.fulfill({ json: { currency: 'AUD', maximum_minor: 125, reason_codes: ['synthetic_refund'] } }),
  )
  await page.route(`${api}checkout/orders/${order.customer_ref}/refund-requests`, route => route.fulfill({
    json: {
      requests: submissions.length >= 2
        ? [{ refund_reference: submissions[0], status: settled ? 'settled' : reserved ? 'reserved' : 'requested' }]
        : [],
      next_cursor: null,
    },
  }))
  await page.route(`${api}checkout/orders/${order.customer_ref}/refund-requests/*`, route => {
    expect(route.request().method()).toBe('PUT')
    expect(route.request().postDataJSON()).toEqual({ amount_minor: 60, currency: 'AUD', reason_code: 'synthetic_refund' })
    const reference = new URL(route.request().url()).pathname.split('/').at(-1)!
    submissions.push(reference)
    return submissions.length === 1
      ? route.fulfill({ status: 503, json: {} })
      : route.fulfill({ json: { refund_reference: reference, status: 'requested' } })
  })

  await page.goto(new URL('/account/purchases', BASE_URL).href, { waitUntil: 'domcontentloaded', timeout: 90_000 })
  const purchases = page.getByRole('region', { name: 'StudyTomy purchases' })
  const link = purchases.getByRole('link', { name: /Receipt for/ })
  await expect(link).toBeVisible({ timeout: 60_000 })
  await expect(link).toHaveAttribute('href', new RegExp(`order=${order.customer_ref}$`))
  await link.focus()
  await expect(link).toBeFocused()
  await page.keyboard.press('Enter')

  const receipt = page.getByRole('region', { name: 'StudyTomy receipt' })
  await expect(receipt.getByRole('heading', { name: 'Synthetic course' })).toBeVisible()
  await expect(receipt.getByText('Payment confirmed')).toBeVisible()
  await expect(receipt.getByText('Finance review is pending.')).toBeVisible()
  await expect(receipt.getByText('14 days of access from first use')).toBeVisible()
  await expect(receipt.getByText('Synthetic terms accepted at checkout.')).toBeVisible()
  const refund = receipt.getByRole('region', { name: 'Refund request', exact: true })
  const amount = refund.getByRole('textbox', { name: 'Refund amount (AUD)' })
  await expect(amount).toBeVisible()
  await amount.fill('0.60')
  await refund.getByRole('combobox', { name: 'Refund reason' }).selectOption('synthetic_refund')
  await refund.getByRole('button', { name: 'Submit refund request' }).click()
  await expect(refund.getByRole('alert')).toContainText('Retry with the same reference')
  await refund.getByRole('button', { name: 'Retry request' }).click()
  await expect(refund.getByRole('status')).toContainText('Refund request received. It is awaiting review.')
  expect(submissions).toHaveLength(2)
  expect(submissions[1]).toBe(submissions[0])
  reserved = true
  await page.reload({ waitUntil: 'domcontentloaded' })
  await expect(receipt).toBeVisible({ timeout: 60_000 })
  const history = receipt.getByRole('region', { name: 'Refund request history', exact: true })
  await expect(history).toBeVisible({ timeout: 60_000 })
  await expect(history.getByText(submissions[0], { exact: true })).toBeVisible()
  await expect(history.getByText('Funds reserved. Money return is pending.')).toBeVisible()
  await expect(refund).toHaveCount(0)
  settled = true
  await page.reload({ waitUntil: 'domcontentloaded' })
  await expect(history.getByText('Money return confirmed.')).toBeVisible({ timeout: 60_000 })
  await expect(history.getByText('Funds reserved. Money return is pending.')).toHaveCount(0)

  authorized = false
  await page.reload({ waitUntil: 'domcontentloaded' })
  await expect(receipt.getByText('This receipt is unavailable.')).toBeVisible()
  await expect(receipt.getByText('Synthetic terms accepted at checkout.')).toHaveCount(0)
  const back = receipt.getByRole('link', { name: 'Back to purchases' })
  await expect(back).toBeVisible()
  await back.click()
  await expect(purchases.getByText('No StudyTomy purchases to show.')).toBeVisible()
})
