import fs from 'node:fs'
import path from 'node:path'
import { randomUUID } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { test, expect } from '../../../core/fixtures'
import { API_URL, BASE_URL } from '../../../core/instance'
import { blockNonLoopbackRequests } from '../../../core/browser-egress-guard'
import { login, req } from '../../../core/client'

type LiveScenario = {
  orgUuid: string
  wrongOrgUuid: string
  courseUuid: string
  releaseUuid: string
  mismatchedReleaseUuid: string
  releaseName: string
  labActivityUuid: string
  asgActivityUuid: string
  assignmentUuid: string
  taskNumUuid: string
  taskFileUuid: string
  expiredReleaseUuid: string
  expiredAsgActivityUuid: string
  expiredTaskNumUuid: string
  labBaseUrl: string
  teacher: { email: string; password: string }
  reviewer: { email: string; password: string }
  learner: { email: string; password: string }
  stranger: { email: string; password: string }
  learnerStorageState: string
  consentEventUuid?: string
  authoringReceipts?: {
    managedDraftStatus: number
    browserAuthoredVersion: number
    staleReviewStatus: number
    selfReviewDeniedStatus: number
    rightsEvidenceUuid: string
    accessibilityEvidenceUuid: string
    reviewUuid: string
    revisionSha256: string
    preflightStatus: number
    releaseStatus: number
  }
}

const scenarioFile = process.env.E2E_LIVE_VLAB_SCENARIO
const scenario: LiveScenario | null =
  scenarioFile && fs.existsSync(scenarioFile)
    ? JSON.parse(fs.readFileSync(scenarioFile, 'utf8'))
    : null

test.skip(
  !scenario ||
    ![BASE_URL, API_URL].every(url =>
      ['localhost', '127.0.0.1', '[::1]'].includes(new URL(url).hostname),
    ),
  'Requires E2E_LIVE_VLAB_SCENARIO JSON pointing at a local PostgreSQL-backed stack.',
)

function buildMinimalZip(tmpDir: string, csvPath: string, zipName: string): string {
  const zipPath = path.join(tmpDir, zipName)
  execFileSync('python3', [
    '-c',
    'import sys, zipfile; z=zipfile.ZipFile(sys.argv[1],"w"); z.write(sys.argv[2], "exported-run.csv"); z.close()',
    zipPath,
    csvPath,
  ])
  return zipPath
}

test.describe('Managed Virtual Lab Live E2E (non-mocked PG/FastAPI/Next.js/Static Lab)', () => {
  test('full teacher->reviewer->release->learner lab/submit->teacher grade->feedback + mobile & negatives', async ({
    browser,
    }, testInfo) => {
    const sc = scenario!
    const tmpDir = testInfo.outputDir
    fs.mkdirSync(tmpDir, { recursive: true })

    // Pre-login real teacher, independent reviewer, learner, and unauthorized stranger tokens against real API
    const teacherToken = await login(sc.teacher.email, sc.teacher.password)
    const reviewerToken = await login(sc.reviewer.email, sc.reviewer.password)
    const learnerToken = await login(sc.learner.email, sc.learner.password)
    const strangerToken = await login(sc.stranger.email, sc.stranger.password)

    // Verify real-API teacher -> independent reviewer -> immutable release receipts (F-05)
    expect(sc.authoringReceipts).toBeTruthy()
    expect(sc.authoringReceipts!.managedDraftStatus).toBe(204)
    expect(sc.authoringReceipts!.browserAuthoredVersion).toBeGreaterThan(1)
    expect(sc.authoringReceipts!.staleReviewStatus).toBe(409)
    expect(sc.authoringReceipts!.selfReviewDeniedStatus).toBe(403)
    expect(sc.authoringReceipts!.rightsEvidenceUuid).toMatch(/^ev_[0-9a-f-]{36}$/)
    expect(sc.authoringReceipts!.accessibilityEvidenceUuid).toMatch(/^ev_[0-9a-f-]{36}$/)
    expect(sc.authoringReceipts!.reviewUuid).toMatch(/^course_review_[0-9a-f-]{36}$/)
    expect(sc.authoringReceipts!.revisionSha256).toMatch(/^[0-9a-f]{64}$/)
    expect(sc.authoringReceipts!.preflightStatus).toBe(200)
    expect(sc.authoringReceipts!.releaseStatus).toBe(201)

    const liveCandidate = await req<any>(
      'GET',
      `/studytomy/orgs/${sc.orgUuid}/courses/${sc.courseUuid}/content-review-candidate`,
      reviewerToken,
    )
    expect(liveCandidate.media_review_blocked).toBe(false)
    expect(liveCandidate.current_review?.decision).toBe('approved')

    // 1. Desktop learner context using real storageState
    const desktopContext = await browser.newContext({
      storageState: sc.learnerStorageState,
      viewport: { width: 1280, height: 800 },
      acceptDownloads: true,
      serviceWorkers: 'block',
    })
    const egressViolations: string[] = []
    await blockNonLoopbackRequests(desktopContext, egressViolations)
    const page = await desktopContext.newPage()

    // Navigate cleanly to My Learning on real Next.js app
    await page.goto('/account/learning', { waitUntil: 'domcontentloaded' })
    const releaseBtn = page.getByRole('button', { name: sc.releaseName, exact: true })
    await expect(releaseBtn).toBeVisible({ timeout: 20_000 })
    await releaseBtn.click()

    // First-use release opens Virtual Lab
    const openLabBtn = page.getByRole('button', { name: /Virtual Lab: Conservation of Linear Momentum/i })
    await expect(openLabBtn).toBeVisible()
    const openLabResPromise = page.waitForResponse(res =>
      res.url().includes(`/releases/${sc.releaseUuid}/labs/${sc.labActivityUuid}/open`) &&
      res.request().method() === 'POST',
    )
    await openLabBtn.click()
    const openLabRes = await openLabResPromise
    expect(openLabRes.status()).toBe(200)
    const labDescriptor = await openLabRes.json()
    expect(labDescriptor.build_version).toBe('1.0.0-pilot.1')
    expect(labDescriptor.entry_url).toContain('/conservation-momentum/1.0.0-pilot.1/index.html')

    // Verify sandboxed iframe containment and live physics lab rendering inside iframe
    const iframe = page.locator('iframe[title*="Conservation"]')
    await expect(iframe).toBeVisible()
    await expect(iframe).toHaveAttribute('sandbox', 'allow-scripts allow-downloads')
    const labFrame = page.frameLocator('iframe[title*="Conservation"]')
    await expect(labFrame.getByRole('heading', { name: /Conservation of Linear Momentum/i })).toBeVisible()

    // Interact inside hosted lab: adjust Body 1 Mass (m1) slider via real keyboard interaction, verify HUD & export CSV readings (F-01, F-02)
    const mass1Slider = labFrame.getByRole('slider', { name: /Body 1 Mass/i })
    await expect(mass1Slider).toBeVisible()
    await expect(mass1Slider).toHaveValue('2')
    await mass1Slider.focus()
    await mass1Slider.press('ArrowRight')
    await mass1Slider.press('ArrowRight')
    await mass1Slider.press('ArrowRight')
    await expect(mass1Slider).toHaveValue('3.5')
    await expect(labFrame.locator('#live-status')).toContainText('P_x=11.50 kg·m/s')

    const exportBtn = labFrame.getByRole('button', { name: /Export Active Run CSV/i })
    await expect(exportBtn).toBeVisible()
    const downloadPromise = page.waitForEvent('download')
    await exportBtn.click()
    const csvDownload = await downloadPromise
    expect(csvDownload.suggestedFilename()).toMatch(/\.csv$/i)
    const csvPath = path.join(tmpDir, csvDownload.suggestedFilename())
    await csvDownload.saveAs(csvPath)
    const csvBody = fs.readFileSync(csvPath, 'utf8')
    expect(csvBody).toContain('Initial Parameters')
    expect(csvBody).toContain('mass1=3.5')
    expect(csvBody).toContain('total_p_x')

    // Zero unauthorized external AI egress
    expect(egressViolations).toEqual([])

    // 2. Open linked practical assignment & verify NO leaked answer keys
    const openAsgBtn = page.getByRole('button', { name: /Open practical assignment/i })
    await expect(openAsgBtn).toBeVisible()
    await page.bringToFront()
    await openAsgBtn.focus()
    const openAsgPromise = page.waitForResponse(res =>
      res.url().includes(`/releases/${sc.releaseUuid}/assignments/${sc.asgActivityUuid}`) &&
      res.request().method() === 'GET',
    )
    await openAsgBtn.click()
    const asgRes = await openAsgPromise
    expect(asgRes.status()).toBe(200)
    const asgText = await asgRes.text()
    expect(asgText).not.toContain('correct_value')

    // Rejection of direct CSV in UI
    const fileInput = page.locator(`#task-file-${sc.taskFileUuid}`)
    await fileInput.setInputFiles(csvPath)
    await expect(page.locator('p[role="alert"]')).toContainText('Direct CSV uploads are not accepted')

    const apiBase = `${API_URL}/studytomy/orgs/${sc.orgUuid}/learning`
    // Invalid direct CSV upload via API while unlocked -> 415
    const csvApiUpload = await page.request.post(
      `${apiBase}/courses/${sc.courseUuid}/releases/${sc.releaseUuid}/assignments/${sc.asgActivityUuid}/tasks/${sc.taskFileUuid}/files`,
      {
        headers: { Authorization: `Bearer ${learnerToken}`, 'Idempotency-Key': `csv-reject-${randomUUID()}` },
        multipart: {
          sub_file: { name: 'raw.csv', mimeType: 'text/csv', buffer: Buffer.from('t,p\n0,4\n') },
        },
      },
    )
    expect(csvApiUpload.status()).toBe(415)

    // Client-supplied grade injection via extra field -> 422
    const gradeInject = await page.request.put(
      `${apiBase}/courses/${sc.courseUuid}/releases/${sc.releaseUuid}/assignments/${sc.asgActivityUuid}/tasks/${sc.taskNumUuid}/draft`,
      {
        headers: { Authorization: `Bearer ${learnerToken}`, 'Content-Type': 'application/json' },
        data: { task_submission: { answer: '2.5' }, grade: 10 },
      },
    )
    expect(gradeInject.status()).toBe(422)

    // Offline transient failure recovery check during draft save
    const numInput = page.locator(`#task-num-${sc.taskNumUuid}`)
    await numInput.fill('2.5')
    await desktopContext.setOffline(true)
    await page.getByRole('button', { name: 'Save draft' }).click()
    await expect(page.locator('p[role="alert"]')).toBeVisible()
    await expect(numInput).toHaveValue('2.5')
    await desktopContext.setOffline(false)

    // Retry draft save cleanly -> 200 persisted in Postgres
    const draftSavePromise = page.waitForResponse(res =>
      res.url().includes(`/tasks/${sc.taskNumUuid}/draft`) && res.request().method() === 'PUT',
    )
    await page.getByRole('button', { name: 'Save draft' }).click()
    expect((await draftSavePromise).status()).toBe(200)
    await expect(page.locator('p[role="status"]').filter({ hasText: 'Draft saved.' })).toBeVisible()

    // Package downloaded CSV into ZIP and upload + submit
    const zipPath = buildMinimalZip(tmpDir, csvPath, 'momentum-lab-report.zip')
    const uploadPromise = page.waitForResponse(res =>
      res.url().includes(`/tasks/${sc.taskFileUuid}/files`) && res.request().method() === 'POST',
    )
    await fileInput.setInputFiles(zipPath)
    const uploadRes = await uploadPromise
    expect(uploadRes.status()).toBe(200)
    const uploadedMeta = await uploadRes.json()
    expect(uploadedMeta.file_uuid).toMatch(/\.zip$/)
    await expect(page.getByText(`Attached file:`)).toBeVisible()

    // Explicit Submit
    const submitPromise = page.waitForResponse(res =>
      res.url().includes(`/assignments/${sc.asgActivityUuid}/submit`) && res.request().method() === 'POST',
    )
    await page.getByRole('button', { name: 'Submit assignment' }).click()
    expect((await submitPromise).status()).toBe(200)
    await expect(page.getByText('Submitted — awaiting teacher review')).toBeVisible()

    // 3. Negative checks via real HTTP against real FastAPI server
    // Duplicate submit with new key -> 409
    const dupRes = await page.request.post(
      `${apiBase}/courses/${sc.courseUuid}/releases/${sc.releaseUuid}/assignments/${sc.asgActivityUuid}/submit`,
      { headers: { Authorization: `Bearer ${learnerToken}`, 'Idempotency-Key': randomUUID() } },
    )
    expect(dupRes.status()).toBe(409)

    // File upload after submission is locked -> 409
    const lockedUploadRes = await page.request.post(
      `${apiBase}/courses/${sc.courseUuid}/releases/${sc.releaseUuid}/assignments/${sc.asgActivityUuid}/tasks/${sc.taskFileUuid}/files`,
      {
        headers: { Authorization: `Bearer ${learnerToken}`, 'Idempotency-Key': `locked-${randomUUID()}` },
        multipart: {
          sub_file: { name: 'late.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\n%%EOF\n') },
        },
      },
    )
    expect(lockedUploadRes.status()).toBe(409)

    // Wrong org denied
    const wrongOrgRes = await page.request.get(
      `${API_URL}/studytomy/orgs/${sc.wrongOrgUuid}/learning/releases`,
      { headers: { Authorization: `Bearer ${learnerToken}` } },
    )
    expect([403, 404]).toContain(wrongOrgRes.status())

    // Unauthorized stranger cannot read lab, assignment, or learner's uploaded file
    const strangerLab = await page.request.get(
      `${apiBase}/courses/${sc.courseUuid}/releases/${sc.releaseUuid}/labs/${sc.labActivityUuid}`,
      { headers: { Authorization: `Bearer ${strangerToken}` } },
    )
    expect(strangerLab.status()).toBe(404)

    const strangerFile = await page.request.get(
      `${apiBase}/courses/${sc.courseUuid}/releases/${sc.releaseUuid}/assignments/${sc.asgActivityUuid}/tasks/${sc.taskFileUuid}/files/${uploadedMeta.file_uuid}`,
      { headers: { Authorization: `Bearer ${strangerToken}` } },
    )
    expect([403, 404]).toContain(strangerFile.status())

    // Mismatched release UUID -> 404
    const wrongRel = await page.request.get(
      `${apiBase}/courses/${sc.courseUuid}/releases/${sc.mismatchedReleaseUuid}/assignments/${sc.asgActivityUuid}`,
      { headers: { Authorization: `Bearer ${learnerToken}` } },
    )
    expect(wrongRel.status()).toBe(404)

    // Expired assignment deadline -> 403 on write
    const pastDueDraft = await page.request.put(
      `${apiBase}/courses/${sc.courseUuid}/releases/${sc.expiredReleaseUuid}/assignments/${sc.expiredAsgActivityUuid}/tasks/${sc.expiredTaskNumUuid}/draft`,
      {
        headers: { Authorization: `Bearer ${learnerToken}`, 'Content-Type': 'application/json' },
        data: { task_submission: { answer: '2.5' } },
      },
    )
    expect(pastDueDraft.status()).toBe(403)

    // 4. Teacher inspects & downloads uploaded ZIP and grades real submission (F-03)
    const submissions = await req<any[]>('GET', `/assignments/${sc.assignmentUuid}/submissions`, teacherToken)
    expect(submissions.length).toBe(1)
    const learnerUserId = submissions[0].user_id

    const fileSub = await req<{
      assignment_task_submission_uuid: string
      assignment_task_id: number
      task_submission: { fileUUID?: string }
    }>(
      'GET',
      `/assignments/${sc.assignmentUuid}/tasks/${sc.taskFileUuid}/submissions/user/${learnerUserId}`,
      teacherToken,
    )
    expect(fileSub).toBeTruthy()
    expect(fileSub.task_submission?.fileUUID).toBe(uploadedMeta.file_uuid)

    // Teacher downloads learner's ZIP report via gated /content path
    const apiRootUrl = API_URL.replace(/\/api\/v1\/?$/, '')
    const teacherFileDownload = await page.request.get(
      `${apiRootUrl}/content/orgs/${sc.orgUuid}/courses/${sc.courseUuid}/activities/${sc.asgActivityUuid}/assignments/${sc.assignmentUuid}/tasks/${sc.taskFileUuid}/subs/${uploadedMeta.file_uuid}`,
      { headers: { Authorization: `Bearer ${teacherToken}` } },
    )
    expect(teacherFileDownload.status()).toBe(200)
    const downloadedBytes = await teacherFileDownload.body()
    expect(downloadedBytes.subarray(0, 2).toString('utf8')).toBe('PK')

    // Teacher marks manual FILE_SUBMISSION task (7/8) and finalizes grade
    await req(
      'PUT',
      `/assignments/${sc.assignmentUuid}/tasks/${sc.taskFileUuid}/submissions`,
      teacherToken,
      {
        assignment_task_submission_uuid: fileSub.assignment_task_submission_uuid,
        grade: 7,
        task_submission_grade_feedback: 'Clear ZIP export and graph.',
        manually_graded: true,
      },
    )
    const gradedResult = await req<any>(
      'POST',
      `/assignments/${sc.assignmentUuid}/submissions/${learnerUserId}/grade`,
      teacherToken,
      { overall_feedback: 'Excellent experimental analysis of momentum conservation.' },
    )
    expect(gradedResult.grade).toBe(9)
    expect(gradedResult.max_grade).toBe(10)

    await desktopContext.close()

    // 5. Mobile viewport (390x844) learner sees GRADED result & feedback cleanly
    const mobileContext = await browser.newContext({
      storageState: sc.learnerStorageState,
      viewport: { width: 390, height: 844 },
      serviceWorkers: 'block',
    })
    await blockNonLoopbackRequests(mobileContext, egressViolations)
    const mobilePage = await mobileContext.newPage()
    await mobilePage.goto('/account/learning', { waitUntil: 'domcontentloaded' })
    await mobilePage.getByRole('button', { name: sc.releaseName, exact: true }).click()
    await mobilePage.getByRole('button', { name: /Virtual Lab: Conservation of Linear Momentum/i }).click()
    const openAsgMobile = mobilePage.getByRole('button', { name: /Open practical assignment/i })
    await expect(openAsgMobile).toBeVisible()
    const mobileAsgGetPromise = mobilePage.waitForResponse(res =>
      res.url().includes(`/assignments/${sc.asgActivityUuid}`) && res.request().method() === 'GET',
    )
    await openAsgMobile.focus()
    await openAsgMobile.click()
    expect((await mobileAsgGetPromise).status()).toBe(200)

    await expect(mobilePage.getByText('Graded')).toBeVisible()
    await expect(mobilePage.getByText('9 / 10')).toBeVisible()
    await expect(mobilePage.getByText('Excellent experimental analysis of momentum conservation.')).toBeVisible()
    await expect(mobilePage.getByText('Clear ZIP export and graph.')).toBeVisible()
    await expect(mobilePage.getByRole('button', { name: 'Submit assignment' })).toHaveCount(0)

    // Verify no horizontal overflow on mobile viewport
    const overflow = await mobilePage.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    )
    expect(overflow).toBe(false)

    // 6. Consent withdrawal revokes further access immediately (404)
    if (sc.consentEventUuid) {
      const withdrawRes = await mobilePage.request.post(
        `${API_URL}/studytomy/orgs/${sc.orgUuid}/learners/me/consent-events/${sc.consentEventUuid}/withdraw`,
        {
          headers: {
            Authorization: `Bearer ${learnerToken}`,
            'Idempotency-Key': `withdraw-${randomUUID()}`,
          },
        },
      )
      expect([200, 201, 204]).toContain(withdrawRes.status())
      const afterWithdraw = await mobilePage.request.get(
        `${apiBase}/courses/${sc.courseUuid}/releases/${sc.releaseUuid}/assignments/${sc.asgActivityUuid}`,
        { headers: { Authorization: `Bearer ${learnerToken}` } },
      )
      expect(afterWithdraw.status()).toBe(404)
    }
    await mobileContext.close()
    expect(egressViolations).toEqual([])
  })
})
