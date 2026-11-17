import { randomUUID } from 'node:crypto'
import { test, expect } from '../../../core/fixtures'
import { ADMIN_EMAIL, ADMIN_PASSWORD, API_URL, BASE_URL, uniqueSuffix } from '../../../core/instance'
import { getOrg, login } from '../../../core/client'
import { ADMIN_STATE } from '../../../core/sharedAuth'

test.use({ storageState: ADMIN_STATE })
test.skip(
  ![BASE_URL, API_URL].every(url => ['localhost', '127.0.0.1', '[::1]'].includes(new URL(url).hostname)),
  'Requires a local synthetic staff account and course.',
)

for (const viewport of [{ width: 390, height: 844 }, { width: 1440, height: 900 }]) {
  test(`staff reviews and corrects a report at ${viewport.width}px`, async ({ page }) => {
    test.setTimeout(180_000)
    await page.setViewportSize(viewport)
    const org = await getOrg()
    const token = await login(ADMIN_EMAIL, ADMIN_PASSWORD)
    const fields = new FormData()
    fields.set('name', `Synthetic staff reports ${uniqueSuffix()}`)
    fields.set('description', 'Synthetic reporting course')
    fields.set('about', 'Synthetic course')
    fields.set('public', 'false')
    fields.set('learnings', '[]')
    fields.set('tags', '')
    const response = await fetch(`${API_URL}/courses/?org_id=${org.id}`, {
      method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: fields,
    })
    expect(response.ok).toBe(true)
    const { course_uuid } = await response.json() as { course_uuid: string }
    const revision = `curriculum_${randomUUID()}`
    const learner = randomUUID()
    const grant = `grant_${randomUUID()}`
    const digest = 'a'.repeat(64)
    const utcStart = '2026-01-01T00:00:00Z'
    const utcEnd = '2026-01-31T00:00:00Z'
    const report = {
      uuid: `report_${randomUUID()}`, version: 1, status: 'draft',
      digest, commentary: '', approved_at: null as string | null,
      period_from: utcStart, period_to: utcEnd, evidence_watermark: 1,
      summary: { native_completions: 1, concepts: [] },
    }
    const api = '**/studytomy/adaptive/orgs/*'
    const staff = `${api}/staff/reports/${report.uuid}`
    let created = false
    let publishAttempts = 0
    await page.route(`${api}/courses/${course_uuid}/configuration`, route => route.fulfill({ json: {
      can_author: false, can_review: true, concepts: [], items: [], diagnostics: [], activities: [],
      curricula: [{ uuid: revision, curriculum_uuid: revision, label: 'Synthetic curriculum',
        status: 'published', version: 1, content_digest: digest }],
    } }))
    await page.route(`${api}/staff/learners/${learner}/reports*`, route => {
      if (route.request().method() === 'GET') {
        expect(new URL(route.request().url()).searchParams.get('curriculum_revision_uuid')).toBe(revision)
        return route.fulfill({ json: { items: created ? [report] : [], next_cursor: null } })
      }
      expect(route.request().postDataJSON()).toEqual({
        operation_uuid: expect.any(String), curriculum_revision_uuid: revision,
        period_from: utcStart, period_to: utcEnd,
      })
      created = true
      return route.fulfill({ status: 201, json: report })
    })
    await page.route(staff, route => {
      if (route.request().method() === 'PUT') {
        expect(route.request().postDataJSON()).toEqual({ expected_version: 1, commentary: 'Staff-only note' })
        report.version++
        report.commentary = 'Staff-only note'
        return route.fulfill({ json: report })
      }
      return route.fulfill({ json: {
        course_uuid, report, review_state: report.status === 'withdrawn' ? 'withdrawn' : 'current',
        review_requests: report.status === 'withdrawn' ? [{ report_version: 4, reason: 'evidence_error', count: 1 }] : [],
      } })
    })
    await page.route(`${staff}/approve`, route => {
      expect(route.request().postDataJSON()).toEqual({ expected_version: 2, expected_digest: digest })
      report.version++
      report.status = 'approved'
      report.approved_at = utcEnd
      return route.fulfill({ json: report })
    })
    await page.route(`${staff}/recipients?*`, route => {
      expect(new URL(route.request().url()).searchParams.get('expected_version')).toBe('3')
      return route.fulfill({ json: { items: [{ grant_uuid: grant, first_name: 'Synthetic recipient' }], next_offset: null } })
    })
    await page.route(`${staff}/recipient-preview`, route => {
      expect(route.request().postDataJSON()).toEqual({ expected_version: 3, recipient_grant_uuid: grant })
      return route.fulfill({ json: {
        uuid: report.uuid, version: 3, learner_name: 'Synthetic learner', course_title: 'Synthetic course',
        curriculum_title: 'Synthetic curriculum', period_from: utcStart, period_to: utcEnd,
        evidence_cutoff: utcEnd, summary: report.summary, commentary: null,
      } })
    })
    await page.route(`${staff}/publish`, route => {
      expect(route.request().postDataJSON()).toEqual({ expected_version: 3, recipient_grant_uuids: [grant] })
      if (++publishAttempts === 1) {
        return route.fulfill({ status: 409, json: { detail: { code: 'STALE_VERSION', message: 'Reload the approved report.' } } })
      }
      report.version++
      report.status = 'published'
      return route.fulfill({ json: { uuid: report.uuid, version: 4, recipient_count: 1 } })
    })
    await page.route(`${staff}/notify?*`, route => {
      expect(new URL(route.request().url()).searchParams.get('expected_version')).toBe('4')
      return route.fulfill({ json: {
        uuid: report.uuid, version: 4, available_count: 1,
        deliveries: [{ status: 'queued', recipient_count: 1 }],
      } })
    })
    await page.route(`${staff}/withdraw`, route => {
      expect(route.request().postDataJSON()).toEqual({ expected_version: 4, reason: 'report_error' })
      report.version++
      report.status = 'withdrawn'
      return route.fulfill({ json: { uuid: report.uuid, version: 5, status: 'withdrawn', notices_suppressed: 0 } })
    })

    const routePath = `/dash/courses/course/${course_uuid.replace(/^course_/, '')}/adaptive`
    await page.goto(new URL(routePath, BASE_URL).href, { waitUntil: 'domcontentloaded', timeout: 90_000 })
    const openPanel = page.getByText('Progress report review', { exact: true })
    await expect(openPanel).toBeVisible({ timeout: 60_000 })
    await openPanel.click()
    await expect(page.getByLabel('Learner reference')).toBeVisible()
    await page.getByLabel('Learner reference').fill(learner)
    await page.getByRole('button', { name: 'Find reports' }).click()
    await expect(page.getByText('There are no reports for this learner and curriculum.')).toBeVisible()
    await page.getByLabel('Start date (UTC)').fill('2026-01-01')
    await page.getByLabel('End date, excluded (UTC)').fill('2026-01-31')
    await page.getByRole('button', { name: 'Create report draft' }).click()
    await expect(page.getByRole('heading', { name: 'Version 1 · Current evidence' })).toBeVisible()
    await page.getByLabel('Reviewer commentary').fill('Staff-only note')
    await page.getByRole('button', { name: 'Save commentary' }).click()
    await expect(page.getByRole('heading', { name: 'Version 2 · Current evidence' })).toBeVisible()
    await page.getByRole('button', { name: 'Approve reviewed version' }).click()
    await expect(page.getByRole('heading', { name: 'Version 3 · Current evidence' })).toBeVisible()

    async function previewAndPublish() {
      await page.getByRole('button', { name: 'Find authorized recipients' }).click()
      const recipient = page.getByRole('combobox', { name: 'Recipient', exact: true })
      await expect(recipient).toBeVisible()
      await recipient.selectOption(grant)
      await expect(recipient).toHaveValue(grant)
      await page.getByRole('button', { name: 'Preview recipient' }).click()
      const preview = page.getByRole('heading', { name: 'Recipient view' }).locator('..')
      await expect(preview.getByText('Synthetic learner')).toBeVisible()
      await expect(preview.getByText('Staff-only note')).toHaveCount(0)
      await page.getByRole('button', { name: 'Publish to selected recipients (1)' }).click()
    }
    await previewAndPublish()
    await expect(page.getByText('Reload the approved report.', { exact: false })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Publish to selected recipients (1)' })).toHaveCount(0)
    await page.getByRole('button', { name: 'Find reports' }).click()
    const entry = page.getByRole('button', { name: /Version 3.*approved/i })
    await expect(entry).toBeVisible()
    await entry.click()
    await expect(page.getByRole('heading', { name: 'Version 3 · Current evidence' })).toBeVisible()
    await previewAndPublish()
    await expect(page.getByRole('heading', { name: 'Version 4 · Current evidence' })).toBeVisible()
    await page.getByRole('button', { name: 'Request report email notices' }).click()
    await expect(page.getByRole('status').filter({ hasText: 'Email notice status — queued 1' })).toBeVisible()
    await page.getByLabel('Reason for change').selectOption('report_error')
    await page.getByRole('button', { name: 'Withdraw report' }).click()
    await expect(page.getByRole('heading', { name: 'Version 5 · Withdrawn' })).toBeVisible()
    await expect(page.getByText('Version 4 · Incorrect learning evidence · 1')).toBeVisible()
  })
}
