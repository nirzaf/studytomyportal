import { randomUUID } from 'node:crypto'
import { test, expect } from '../../../core/fixtures'
import { API_URL, BASE_URL } from '../../../core/instance'
import { getOrg } from '../../../core/client'
import { STUDENT_STATE } from '../../../core/sharedAuth'

test.use({ storageState: STUDENT_STATE, viewport: { width: 390, height: 844 } })
test.skip(
  ![BASE_URL, API_URL].every(url => ['localhost', '127.0.0.1', '[::1]'].includes(new URL(url).hostname)),
  'Requires a loopback instance and synthetic learner session.',
)

test('managed virtual lab renders pinned sandboxed build and connects to linked assignment feedback with zero AI egress (T-17)', async ({ page }) => {
  const { org_uuid: orgUuid } = await getOrg()
  const courseUuid = `course_${randomUUID()}`
  const releaseUuid = `course_release_${randomUUID()}`
  const labActivityUuid = `activity_lab_${randomUUID()}`
  const asgActivityUuid = `activity_asg_${randomUUID()}`
  const assignmentUuid = `assignment_${randomUUID()}`
  const base = `**/studytomy/orgs/${orgUuid}/learning`
  const release = { course_uuid: courseUuid, release_uuid: releaseUuid, name: 'Grade 12 Physics — Momentum Pilot' }
  const buildSha = 'e5b013fc9943ba82be536ba49b6aa12b2c0f8c9672fe094dc50320fe00448c7f'

  const egressRequests: string[] = []
  page.on('request', req => {
    const url = req.url()
    if (/generativelanguage\.googleapis\.com|openai\.com|anthropic\.com/i.test(url)) {
      egressRequests.push(url)
    }
  })

  await page.route(`${base}/releases`, route => route.fulfill({
    json: { releases: [release], next_cursor: null },
  }))
  await page.route(`${base}/courses/${courseUuid}/releases/${releaseUuid}/lessons`, route => route.fulfill({
    json: {
      requires_first_use: false,
      lessons: [
        {
          activity_uuid: labActivityUuid,
          name: 'Virtual Lab: Conservation of Linear Momentum',
          position: 1,
          format: 'virtual_lab',
          media_type: null,
          available: true,
          linked_assignment_activity_uuid: asgActivityUuid,
          unavailable_reason: null,
        },
      ],
    },
  }))
  await page.route(`${base}/courses/${courseUuid}/releases/${releaseUuid}/labs/${labActivityUuid}`, route => route.fulfill({
    json: {
      course_uuid: courseUuid,
      release_uuid: releaseUuid,
      name: 'Virtual Lab: Conservation of Linear Momentum',
      activity_uuid: labActivityUuid,
      lab_id: 'conservation-momentum',
      experiment_id: 'conservation-momentum',
      build_version: '1.0.0-pilot.1',
      build_sha256: buildSha,
      entry_url: 'https://labs.studytomy.com/conservation-momentum/v1/index.html',
      units_system: 'SI',
      submission_formats_note: 'Submit numerical answers and a PDF or ZIP report containing exported CSV files.',
      linked_assignment: {
        activity_uuid: asgActivityUuid,
        assignment_uuid: assignmentUuid,
        title: 'Momentum Practical Report',
      },
    },
  }))
  await page.route(`${base}/courses/${courseUuid}/releases/${releaseUuid}/assignments/${asgActivityUuid}`, route => route.fulfill({
    json: {
      course_uuid: courseUuid,
      release_uuid: releaseUuid,
      name: 'Momentum Practical Report',
      activity_uuid: asgActivityUuid,
      assignment_uuid: assignmentUuid,
      title: 'Momentum Practical Report',
      description: 'Submit calculations and your PDF/ZIP practical report.',
      due_date: '2026-12-31',
      grading_type: 'NUMERIC',
      submission_status: 'GRADED',
      grade: 9,
      overall_feedback: 'Clear SI momentum conservation analysis.',
      attempt_number: 1,
      tasks: [
        {
          assignment_task_uuid: 'task_1',
          title: 'Upload Practical Report (PDF or ZIP)',
          description: 'Include elastic and inelastic trial readings.',
          assignment_type: 'FILE_SUBMISSION',
          max_grade_value: 10,
          student_grade: 9,
          teacher_feedback: 'Accurate elastic/inelastic comparison.',
        },
      ],
      accepted_file_formats: ['PDF', 'ZIP', 'PNG', 'JPG', 'DOCX', 'XLSX', 'PPTX'],
      manual_review_note: 'Practical lab reports are reviewed manually by your teacher and do not automatically award independent mastery.',
    },
  }))

  await page.goto('/account/learning')
  await page.getByRole('button', { name: release.name }).click()
  await page.getByRole('button', { name: /Virtual Lab: Conservation of Linear Momentum/ }).click()

  await expect(page.getByText('1.0.0-pilot.1')).toBeVisible()
  await expect(page.getByText(buildSha.slice(0, 12))).toBeVisible()
  await expect(page.getByText(/does not automatically record course completion or independent mastery/i)).toBeVisible()

  const frame = page.locator('iframe[title="Virtual Lab: Conservation of Linear Momentum"]')
  await expect(frame).toHaveAttribute('sandbox', 'allow-scripts allow-downloads')
  await expect(frame).toHaveAttribute('referrerpolicy', 'no-referrer')

  await page.getByRole('button', { name: /Open practical assignment: Momentum Practical Report/ }).click()
  await expect(page.getByText('Clear SI momentum conservation analysis.')).toBeVisible()
  await expect(page.getByText(/Package exported CSV files inside a ZIP archive/i)).toBeVisible()
  await expect(page.getByText('Accurate elastic/inelastic comparison.')).toBeVisible()
  expect(egressRequests).toEqual([])
})
