import { randomUUID } from 'node:crypto'
import { test, expect } from '../../../core/fixtures'
import { API_URL, BASE_URL } from '../../../core/instance'
import { STUDENT_STATE } from '../../../core/sharedAuth'

test.use({ storageState: STUDENT_STATE, viewport: { width: 390, height: 844 } })
test.skip(
  ![BASE_URL, API_URL].every(url => ['localhost', '127.0.0.1', '[::1]'].includes(new URL(url).hostname)),
  'Requires a local synthetic account.',
)

test('quotes exact learner terms and accepts a manual-collection pilot order', async ({ page }) => {
  const offerUuid = randomUUID()
  const offerVersionUuid = randomUUID()
  const learnerUuid = randomUUID()
  const customerRef = randomUUID()
  const idempotencyKeys: string[] = []
  const api = '**/studytomy/orgs/*/'

  await page.route(`${api}purchases?*`, route => route.fulfill({
    json: { orders: [], next_cursor: null },
  }))
  await page.route(`${api}checkout/offers`, route => route.fulfill({
    json: [{ offer_uuid: offerUuid, offer_version_uuid: offerVersionUuid, title: 'Synthetic Year 8 Mathematics' }],
  }))
  await page.route(`${api}checkout/learners?*`, route => route.fulfill({
    json: [{ learner_uuid: learnerUuid, label: 'Synthetic Learner' }],
  }))
  await page.route(`${api}checkout/quote`, async route => {
    expect(route.request().method()).toBe('POST')
    expect(route.request().postDataJSON()).toEqual({
      offer_uuid: offerUuid,
      offer_version_uuid: offerVersionUuid,
      learner_uuids: [learnerUuid],
    })
    return route.fulfill({ json: {
      offer_version_uuid: offerVersionUuid,
      offer_sha256: 'a'.repeat(64),
      price_per_learner: {
        currency: 'AUD', total_minor: 150,
        lines: [{ kind: 'charge', label: 'Fourteen day course access', amount_minor: 150 }],
      },
      learner_count: 1,
      total_minor: 150,
      learner_uuids: [learnerUuid],
      learners_with_overlap: [learnerUuid],
      customer_terms: 'Synthetic terms bound to this displayed offer version.',
      confirmation_sha256: 'b'.repeat(64),
    } })
  })
  await page.route(`${api}checkout/orders`, async route => {
    expect(route.request().method()).toBe('POST')
    idempotencyKeys.push(route.request().headers()['idempotency-key'] || '')
    expect(route.request().postDataJSON()).toEqual({
      offer_uuid: offerUuid,
      offer_version_uuid: offerVersionUuid,
      learner_uuids: [learnerUuid],
      confirmation_sha256: 'b'.repeat(64),
      acknowledged_overlap: true,
    })
    return route.fulfill({ json: { customer_ref: customerRef } })
  })
  await page.route(`${api}checkout/orders/${customerRef}/accept`, async route => {
    expect(route.request().method()).toBe('POST')
    expect(route.request().postDataJSON()).toEqual({
      offer_uuid: offerUuid,
      offer_version_uuid: offerVersionUuid,
      learner_uuids: [learnerUuid],
      confirmation_sha256: 'b'.repeat(64),
      acknowledged_overlap: true,
    })
    return route.fulfill({ json: { customer_ref: customerRef } })
  })
  await page.route(`${api}checkout/orders/${customerRef}/receipt`, route => route.fulfill({ json: {
    customer_ref: customerRef,
    accepted_at: '2026-11-18T08:00:00Z',
    currency: 'AUD',
    order_total_minor: 150,
    confirmed_minor: 0,
    collection_status: 'awaiting_collection',
    finance_review_required: false,
    allocations: [{
      position: 1,
      courses: ['Synthetic Year 8 Mathematics'],
      access: { clock: 'after_first_access', duration_days: 14, starts_at: null, ends_at: null },
      price: {
        currency: 'AUD', total_minor: 150,
        lines: [{ kind: 'charge', label: 'Fourteen day course access', amount_minor: 150 }],
      },
      customer_terms: 'Synthetic terms bound to this displayed offer version.',
    }],
  } }))

  await page.goto(new URL('/account/purchases', BASE_URL).href, { waitUntil: 'domcontentloaded', timeout: 90_000 })
  const checkout = page.getByRole('region', { name: 'Course checkout' })
  await expect(checkout.getByLabel('Course offer')).toHaveValue(offerUuid)
  await checkout.getByRole('checkbox', { name: 'Synthetic Learner' }).check()
  await checkout.getByRole('button', { name: 'Review current price and terms' }).click()
  await expect(checkout.getByText('Synthetic terms bound to this displayed offer version.')).toBeVisible()
  await expect(checkout.getByText('Payment is handled outside this portal. Paid orders remain pending until collection is confirmed.')).toBeVisible()
  const accept = checkout.getByRole('button', { name: 'Accept order' })
  await expect(accept).toBeDisabled()
  await checkout.getByRole('checkbox', { name: /already have overlapping course access/ }).check()
  await expect(accept).toBeEnabled()
  await accept.click()
  await expect(checkout.getByRole('status')).toContainText('No online payment was taken.')
  expect(idempotencyKeys).toHaveLength(1)
  expect(idempotencyKeys[0]).toMatch(/^[0-9a-f-]{36}$/i)

  await checkout.getByRole('link', { name: 'Open order receipt' }).click()
  const receipt = page.getByRole('region', { name: 'Studytomy receipt' })
  await expect(receipt.getByRole('heading', { name: 'Synthetic Year 8 Mathematics' })).toBeVisible()
  await expect(receipt.getByText('Awaiting payment')).toBeVisible()
  await expect(receipt.getByText('Synthetic terms bound to this displayed offer version.')).toBeVisible()
})
