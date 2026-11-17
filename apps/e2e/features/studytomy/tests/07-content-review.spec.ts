import { randomUUID } from 'node:crypto'
import { test, expect } from '../../../core/fixtures'
import { ADMIN_EMAIL, ADMIN_PASSWORD, API_URL, BASE_URL, uniqueSuffix } from '../../../core/instance'
import { getOrg, login } from '../../../core/client'
import { ADMIN_STATE } from '../../../core/sharedAuth'

test.use({ storageState: ADMIN_STATE, viewport: { width: 390, height: 844 } })
test.skip(
  ![BASE_URL, API_URL].every(url => ['localhost', '127.0.0.1', '[::1]'].includes(new URL(url).hostname)),
  'Requires a local synthetic staff account and course.',
)

test('staff attaches evidence and reviews the current course candidate', async ({ page }) => {
  test.setTimeout(180_000)
  const org = await getOrg()
  const token = await login(ADMIN_EMAIL, ADMIN_PASSWORD)
  const course = new FormData()
  course.set('name', `Synthetic content review ${uniqueSuffix()}`)
  course.set('description', 'Synthetic review course')
  course.set('about', 'Synthetic review course')
  course.set('public', 'false')
  course.set('learnings', '[]')
  course.set('tags', '')
  const response = await fetch(`${API_URL}/courses/?org_id=${org.id}`, {
    method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: course,
  })
  expect(response.ok).toBe(true)
  const { course_uuid } = await response.json() as { course_uuid: string }

  const api = `**/studytomy/orgs/*/courses/${course_uuid}`
  let revision = 'a'.repeat(64)
  let rights = `ev_${randomUUID()}`
  let accessibility = `ev_${randomUUID()}`
  let attached = false
  let decisions = 0
  const attachment = (id: string, purpose: string) => ({
    evidence_uuid: id, purpose, revision_sha256: revision, verified_at: '2026-11-01T00:00:00Z',
  })
  await page.route(`${api}/content-review-candidate`, route => route.fulfill({ json: {
    revision_sha256: revision,
    evidence: [
      attachment(rights, 'content_rights'),
      ...(attached ? [attachment(accessibility, 'content_accessibility')] : []),
    ],
    next_before_id: null,
  } }))
  await page.route(`${api}/evidence-files`, route => {
    const request = route.request()
    expect(request.method()).toBe('POST')
    expect(request.headers()['idempotency-key']).toBeTruthy()
    expect(request.headers()['content-type']).toContain('multipart/form-data; boundary=')
    expect(request.postData()).toContain('content_accessibility')
    expect(request.postData()).toContain(revision)
    expect(request.postData()).toContain('%PDF-1.4')
    attached = true
    return route.fulfill({ status: 201, json: attachment(accessibility, 'content_accessibility') })
  })
  await page.route(`${api}/content-review-decisions`, route => {
    expect(route.request().headers()['idempotency-key']).toBeTruthy()
    expect(route.request().postDataJSON()).toEqual({
      revision_sha256: revision,
      rights_evidence_uuid: rights,
      accessibility_evidence_uuid: accessibility,
      decision: 'approved',
      accessibility_confirmed: true,
      assessment_confirmed: true,
    })
    decisions += 1
    if (decisions === 1) {
      revision = 'b'.repeat(64)
      rights = `ev_${randomUUID()}`
      accessibility = `ev_${randomUUID()}`
      return route.fulfill({ status: 409, json: { detail: { code: 'STALE_VERSION' } } })
    }
    return route.fulfill({ status: 201, json: {
      uuid: `course_review_${randomUUID()}`, revision_sha256: revision, decision: 'approved',
    } })
  })

  const routePath = `/dash/courses/course/${course_uuid.replace(/^course_/, '')}/content-review`
  await page.goto(new URL(routePath, BASE_URL).href, { waitUntil: 'domcontentloaded', timeout: 90_000 })
  const panel = page.getByRole('region', { name: 'Content review' })
  await expect(panel).toBeVisible({ timeout: 60_000 })
  const rightsSelect = panel.getByRole('combobox', { name: 'Rights evidence', exact: true })
  const accessibilitySelect = panel.getByRole('combobox', { name: 'Accessibility evidence', exact: true })
  const approve = panel.getByRole('button', { name: 'Record approval' })
  const attach = panel.getByRole('button', { name: 'Upload attachment' })
  await expect(attach).toBeDisabled()
  await panel.getByLabel('Evidence purpose').selectOption('content_accessibility')
  await expect(panel.getByLabel('Evidence purpose')).toHaveValue('content_accessibility')
  await panel.getByLabel('Evidence file').setInputFiles({
    name: 'synthetic.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\n%%EOF\n'),
  })
  await expect(attach).toBeEnabled()
  await attach.click()
  await expect(panel.getByRole('status')).toHaveText('Evidence attached to this candidate.')
  await expect(accessibilitySelect).toHaveValue(accessibility)

  async function confirmAndApprove() {
    await expect(approve).toBeDisabled()
    await rightsSelect.selectOption(rights)
    await expect(rightsSelect).toHaveValue(rights)
    await accessibilitySelect.selectOption(accessibility)
    await expect(accessibilitySelect).toHaveValue(accessibility)
    const accessibilityFinding = panel.getByRole('checkbox', { name: /confirm the accessibility/ })
    await accessibilityFinding.check()
    await expect(accessibilityFinding).toBeChecked()
    await expect(approve).toBeDisabled()
    const assessmentFinding = panel.getByRole('checkbox', { name: /confirm the assessment/ })
    await assessmentFinding.check()
    await expect(approve).toBeEnabled()
    await approve.focus()
    await page.keyboard.press('Enter')
  }

  await confirmAndApprove()
  const error = panel.getByRole('alert')
  await expect(error).toContainText('The candidate changed')
  await expect(error).toBeFocused()
  await panel.getByRole('button', { name: 'Reload candidate' }).click()
  await expect(panel.getByText(revision)).toBeVisible()
  await expect(accessibilitySelect).toHaveValue('')
  await confirmAndApprove()
  await expect(panel.getByRole('status')).toHaveText('Review decision recorded.')
  expect(decisions).toBe(2)
})
