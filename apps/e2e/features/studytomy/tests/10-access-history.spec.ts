import { randomUUID } from 'node:crypto'
import { test, expect } from '../../../core/fixtures'
import { API_URL, BASE_URL } from '../../../core/instance'
import { STUDENT_STATE } from '../../../core/sharedAuth'

test.use({ storageState: STUDENT_STATE, viewport: { width: 390, height: 844 } })
test.skip(
  ![BASE_URL, API_URL].every(url => ['localhost', '127.0.0.1', '[::1]'].includes(new URL(url).hostname)),
  'Requires a local synthetic account.',
)

test('my learning lists an expired release and shows its retained access window', async ({ page }) => {
  test.setTimeout(180_000)
  const release = { course_uuid: `course_${randomUUID()}`, release_uuid: randomUUID(), name: 'Synthetic expired course' }
  const api = '**/studytomy/orgs/*/learning'
  await page.route(`${api}/releases`, route => route.fulfill({ json: { releases: [], next_cursor: null } }))
  await page.route(`${api}/access-history`, route => route.fulfill({ json: { releases: [release], next_cursor: null } }))
  await page.route(`${api}/courses/${release.course_uuid}/releases/${release.release_uuid}/access-history`, route =>
    route.fulfill({ json: {
      ...release, status: 'expired', start_basis: 'fixed_period', duration_days: null,
      starts_at: '2026-10-01T10:00:00Z', ends_at: '2026-10-02T10:00:00Z',
    } }),
  )
  await page.goto(new URL('/account/learning', BASE_URL).href, { waitUntil: 'domcontentloaded', timeout: 90_000 })
  await expect(page.getByText('No reviewed courses are available.')).toBeVisible({ timeout: 60_000 })
  const history = page.getByRole('button', { name: 'Access history', exact: true })
  await history.focus()
  await expect(history).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: 'Access history' })).toBeFocused()
  const course = page.getByRole('button', { name: release.name })
  await expect(course).toBeVisible()
  await course.click()
  const details = page.getByRole('region', { name: release.name })
  await expect(details.getByText('Expired', { exact: true })).toBeVisible()
  await expect(details.getByText('Fixed period', { exact: true })).toBeVisible()
  const dates = details.locator('time')
  await expect(dates).toHaveCount(2)
  await expect(dates.last()).toHaveAttribute('datetime', '2026-10-02T10:00:00Z')
  const back = details.getByRole('button', { name: 'Back to access history' })
  await expect(back).toBeVisible()
  await back.click()
  await expect(course).toBeVisible()
})
