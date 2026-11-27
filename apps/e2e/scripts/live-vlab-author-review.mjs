// Invoked by the synthetic fixture provisioner with an owner-only input pipe.
import { readFileSync } from 'node:fs'
import { chromium, expect } from '@playwright/test'

const input = JSON.parse(readFileSync(0, 'utf8'))
const base = new URL(input.web)
if (
  base.protocol !== 'http:' || !['localhost', '127.0.0.1', '[::1]'].includes(base.hostname)
  || !base.port || base.username || base.password || base.pathname !== '/' || base.search || base.hash
) throw new Error('Require a bare loopback web origin.')

let step = 'launch'
let candidateStatus = 'not requested'
const browser = await chromium.launch({ args: [
  '--disable-background-networking',
  '--host-resolver-rules=MAP * 0.0.0.0,EXCLUDE localhost,EXCLUDE 127.0.0.1,EXCLUDE [::1]',
] })

async function signedInContext(account) {
  const context = await browser.newContext({ baseURL: base.origin, serviceWorkers: 'block' })
  await context.route('**/*', route => {
    const host = new URL(route.request().url()).hostname
    return ['localhost', '127.0.0.1', '[::1]'].includes(host)
      ? route.continue() : route.abort('blockedbyclient')
  })
  context.setDefaultTimeout(15_000)
  const page = await context.newPage()
  await page.goto('/login', { waitUntil: 'domcontentloaded' })
  await page.locator('input[type="email"]').fill(account.email)
  await page.locator('input[type="password"]').fill(account.password)
  await page.locator('form button[type="submit"]').click()
  await page.waitForFunction(() => document.cookie.includes('LH_session='), { timeout: 30_000 })
  await page.close()
  return context.newPage()
}

function responseFor(page, method, suffix) {
  return page.waitForResponse(
    response => response.request().method() === method && new URL(response.url()).pathname.endsWith(suffix),
    { timeout: 30_000 },
  )
}

async function openDraft(page, course) {
  const activity = input.activityUuid.replace(/^activity_/, '')
  step = 'navigate to activity editor'
  const edit = `/course/${course}/activity/${activity}/edit`
  await page.goto(edit, { waitUntil: 'domcontentloaded' })
  step = 'lab build picker'
  await expect(page.getByRole('heading', { name: 'Virtual science lab' })).toBeVisible({ timeout: 45_000 })
}

async function saveDraft(page, requireBlank) {
  if (requireBlank) await expect(page.getByText(/^Saved build:/)).toHaveCount(0)
  await page.getByLabel('Simulator build').selectOption({
    label: `Conservation of momentum · ${input.buildVersion} · ${input.labHost}`,
  })
  step = 'draft save'
  const [response] = await Promise.all([
    responseFor(page, 'PUT', `/activities/${input.activityUuid}`),
    page.getByRole('button', { name: 'Save', exact: true }).click(),
  ])
  if (!response.ok()) {
    step = `draft save HTTP ${response.status()}`
    throw new Error('Draft save rejected')
  }
  const draft = await response.json()
  step = requireBlank ? 'reload saved draft' : 'confirm concurrent save'
  if (requireBlank) await page.reload({ waitUntil: 'domcontentloaded' })
  await expect(page.getByText(`Saved build: ${input.buildVersion}`)).toBeVisible({ timeout: 30_000 })
  return draft
}

try {
  const course = input.courseUuid.replace(/^course_/, '')
  if (input.phase === 'author') {
    step = 'teacher login'
    const page = await signedInContext(input.account)
    await openDraft(page, course)
    const draft = await saveDraft(page, true)
    console.log(JSON.stringify(draft))
  } else if (input.phase === 'review') {
    step = 'teacher login for concurrent save'
    const teacher = await signedInContext(input.teacher)
    await openDraft(teacher, course)
    step = 'independent reviewer login'
    const page = await signedInContext(input.account)
    step = 'review candidate'
    page.on('response', response => {
      if (new URL(response.url()).pathname.endsWith('/content-review-candidate')) {
        candidateStatus = String(response.status())
      }
    })
    await page.goto(`/dash/courses/course/${course}/content-review`, { waitUntil: 'domcontentloaded' })
    const panel = page.getByRole('region', { name: 'Content review' })
    const welcome = page.getByRole('dialog').filter({
      has: page.getByRole('button', { name: /^get started$/i }),
    })
    await expect(welcome.or(panel).first()).toBeVisible({ timeout: 45_000 })
    if (await welcome.isVisible()) {
      await page.keyboard.press('Escape')
      await expect(welcome).toBeHidden()
    }
    await expect(panel).toBeVisible({ timeout: 60_000 })
    const rights = panel.getByRole('combobox', { name: 'Rights evidence', exact: true })
    const accessibility = panel.getByRole('combobox', { name: 'Accessibility evidence', exact: true })
    const approve = panel.getByRole('button', { name: 'Record approval' })
    const publish = panel.getByRole('button', { name: 'Record reviewed release' })
    const accessibilityConfirmed = panel.getByRole('checkbox', { name: /confirm the accessibility/ })
    const assessmentConfirmed = panel.getByRole('checkbox', { name: /confirm the assessment/ })
    async function confirmAttachments(rightsId, accessibilityId) {
      await rights.selectOption(rightsId)
      await accessibility.selectOption(accessibilityId)
      await accessibilityConfirmed.check()
      await assessmentConfirmed.check()
      await expect(approve).toBeEnabled()
    }

    step = 'review evidence selection'
    await expect(publish).toHaveCount(0)
    await confirmAttachments(input.rights, input.accessibility)

    step = 'concurrent author save'
    await saveDraft(teacher, false)

    step = 'reject stale review'
    const [stale] = await Promise.all([responseFor(page, 'POST', '/content-review-decisions'), approve.click()])
    if (stale.status() !== 409 || (await stale.json()).detail?.code !== 'STALE_VERSION') {
      throw new Error('The stale candidate was not rejected')
    }
    await expect(panel.getByRole('alert')).toContainText('The candidate changed')
    await expect(publish).toHaveCount(0)

    step = 'reload and attach current evidence'
    const currentCandidate = responseFor(page, 'GET', '/content-review-candidate')
    await panel.getByRole('button', { name: 'Reload candidate' }).click()
    const candidate = await (await currentCandidate).json()
    if (candidate.revision_sha256 === input.oldDigest) throw new Error('The candidate did not change')
    await expect(rights).toHaveValue('')
    await expect(accessibility).toHaveValue('')
    await expect(accessibilityConfirmed).not.toBeChecked()
    await expect(assessmentConfirmed).not.toBeChecked()
    await expect(approve).toBeDisabled()
    const currentEvidence = {}
    for (const purpose of ['content_rights', 'content_accessibility']) {
      await panel.getByLabel('Evidence purpose').selectOption(purpose)
      await panel.getByLabel('Evidence file').setInputFiles({
        name: 'synthetic-evidence.pdf', mimeType: 'application/pdf',
        buffer: Buffer.from(input.pdf[purpose], 'base64'),
      })
      const uploaded = responseFor(page, 'POST', '/evidence-files')
      await panel.getByRole('button', { name: 'Upload attachment' }).click()
      const attachment = await uploaded
      if (attachment.status() !== 201) throw new Error('Current evidence rejected')
      const receipt = await attachment.json()
      if (receipt.revision_sha256 !== candidate.revision_sha256) throw new Error('Wrong evidence digest')
      currentEvidence[purpose] = receipt.evidence_uuid
    }
    await confirmAttachments(currentEvidence.content_rights, currentEvidence.content_accessibility)
    step = 'review approval'
    const decided = responseFor(page, 'POST', '/content-review-decisions')
    await panel.getByRole('button', { name: 'Record approval' }).click()
    const decision = await decided
    if (decision.status() !== 201) throw new Error('Review rejected')
    const review = await decision.json()
    step = 'reviewed release'
    const reviewPath = `/content-review-decisions/${review.uuid}`
    const preflight = responseFor(page, 'POST', `${reviewPath}/release-preflight`)
    const released = responseFor(page, 'POST', `${reviewPath}/release`)
    await panel.getByRole('button', { name: 'Record reviewed release' }).click()
    if ((await preflight).status() !== 200) throw new Error('Preflight rejected')
    const releaseResponse = await released
    if (releaseResponse.status() !== 201) throw new Error('Release rejected')
    const release = await releaseResponse.json()
    await expect(panel.getByText(`Release reference ${release.uuid}`)).toBeVisible()
    console.log(JSON.stringify({ review, release, currentEvidence, staleStatus: stale.status() }))
  } else {
    throw new Error('Unknown browser phase')
  }
} catch {
  console.error(`Synthetic momentum browser failed at: ${step} (candidate: ${candidateStatus})`)
  process.exitCode = 1
} finally {
  await browser.close()
}
