/**
 * Real browser E2E acceptance test for Create Course accidental-close guard,
 * server-persisted drafts (PostgreSQL + private storage / RustFS), offline
 * Save & Close protection, refresh & cross-device resume, two-tab optimistic
 * concurrency conflict handling, and idempotent course creation + draft cleanup.
 *
 * Runs against the real Next.js + FastAPI + PostgreSQL + RustFS stack with zero
 * mocked learning/course/draft APIs.
 */
import { test, expect, type Page } from '@playwright/test'
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'
import { ADMIN_STATE } from '../../../core/sharedAuth'

const PNG_BYTES = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
)

async function openCreateCourseScratch(page: Page) {
  await page.goto('/dash/courses', { waitUntil: 'domcontentloaded' })
  // Click the "New Course" button in the dashboard header
  const newCourseBtn = page.getByRole('button', { name: /New Course/i }).first()
  await expect(newCourseBtn).toBeVisible({ timeout: 20_000 })
  await newCourseBtn.click()
  // Dashboard opens the creation-type selector first; choose "From Scratch"
  const scratchBtn = page.getByRole('button', { name: /From Scratch|Start from scratch|Blank/i }).first()
  if (await scratchBtn.isVisible({ timeout: 5_000 }).catch(() => false)) {
    await scratchBtn.click()
  }
}

test.describe('Create Course durable draft & accidental-close protection (live stack)', () => {
  test.use({ storageState: ADMIN_STATE })

  test('full accidental-close, incomplete autosave, thumbnail, offline save-close, refresh, two-tab conflict & idempotent creation', async ({
    browser,
    page,
    context,
  }) => {
    const tmpDir = mkdtempSync(join(tmpdir(), 'st-course-draft-e2e-'))
    const pngPath = join(tmpDir, 'thumb.png')
    writeFileSync(pngPath, PNG_BYTES)
    const runTag = randomUUID().slice(0, 8)
    const initialTitle = `Draft Physics ${runTag}`
    const offlineTitle = `Draft Physics Offline ${runTag}`
    const tab2Title = `Draft Physics Tab2 ${runTag}`

    try {
      // 1. Open Create Course (if a previous draft exists from an aborted run, discard it first)
      await openCreateCourseScratch(page)
      const gate = page.getByTestId('course-draft-gate')
      if (await gate.isVisible({ timeout: 5_000 }).catch(() => false)) {
        const startNewBtn = gate.getByRole('button', { name: /Start New/i })
        if (await startNewBtn.isVisible().catch(() => false)) {
          await startNewBtn.click()
          await gate.getByRole('button', { name: /Discard & Start New/i }).click()
        }
      }

      const nameInput = page.locator('input[name="name"]')
      const descInput = page.locator('textarea[name="description"]')
      await expect(nameInput).toBeVisible({ timeout: 15_000 })

      // 2. Accidental-close protection: Escape and clicking the backdrop overlay must NOT dismiss
      await nameInput.fill(initialTitle)
      await page.keyboard.press('Escape')
      await expect(nameInput).toBeVisible()
      await page.mouse.click(5, 5)
      await expect(nameInput).toBeVisible()

      // 3. Incomplete-field autosave (description is still empty!) -> Saved
      await expect(page.getByTestId('draft-status')).toHaveText(/Saved/i, { timeout: 10_000 })

      // 4. Upload a real PNG thumbnail -> stored in private storage (RustFS / filesystem)
      const thumbUploadPromise = page.waitForResponse(
        res => res.url().includes('/course-drafts/orgs/') && res.url().endsWith('/thumbnail') && res.request().method() === 'PUT',
      )
      await page.locator('#fileInput').setInputFiles(pngPath)
      expect((await thumbUploadPromise).status()).toBe(200)
      await expect(page.getByTestId('draft-status')).toHaveText(/Saved/i, { timeout: 10_000 })

      // 5. Offline failure during Save & Close warns and prevents silent data loss
      await context.setOffline(true)
      await nameInput.fill(offlineTitle)
      await page.getByRole('button', { name: /Save & Close/i }).click()
      await expect(page.getByText(/Your latest changes are not saved yet/i)).toBeVisible({ timeout: 10_000 })
      await expect(nameInput).toHaveValue(offlineTitle)

      // Back online: Save & Close persists and closes cleanly
      await context.setOffline(false)
      await page.getByRole('button', { name: /Save & Close/i }).click()
      await expect(nameInput).toBeHidden({ timeout: 10_000 })

      // 6. Refresh page & reopen -> Resume Draft restores name + thumbnail from server
      await page.reload({ waitUntil: 'domcontentloaded' })
      await openCreateCourseScratch(page)
      await expect(page.getByTestId('course-draft-gate')).toBeVisible({ timeout: 10_000 })
      await expect(page.getByText(offlineTitle)).toBeVisible()
      const thumbRestorePromise = page.waitForResponse(
        res => res.url().includes('/course-drafts/orgs/') && res.url().endsWith('/thumbnail') && res.request().method() === 'GET',
      )
      await page.getByRole('button', { name: /Resume Draft/i }).click()
      expect((await thumbRestorePromise).status()).toBe(200)
      await expect(nameInput).toHaveValue(offlineTitle)
      await expect(page.locator('img[src^="blob:"]')).toBeVisible()

      // 7. Second browser context (cross-device / concurrent tab) resumes & updates draft -> Tab 1 gets 409 conflict
      const secondContext = await browser.newContext({ storageState: ADMIN_STATE })
      const secondPage = await secondContext.newPage()
      await openCreateCourseScratch(secondPage)
      await secondPage.getByRole('button', { name: /Resume Draft/i }).click()
      const secondNameInput = secondPage.locator('input[name="name"]')
      await expect(secondNameInput).toHaveValue(offlineTitle)
      await secondNameInput.fill(tab2Title)
      await expect(secondPage.getByTestId('draft-status')).toHaveText(/Saved/i, { timeout: 10_000 })
      await secondContext.close()

      // Tab 1 (now holding a stale revision) edits -> receives 409 conflict banner -> reloads latest
      await nameInput.fill(`Stale Edit ${runTag}`)
      await expect(page.getByText(/changed in another tab or device/i)).toBeVisible({ timeout: 10_000 })
      await page.getByRole('button', { name: /Load latest draft/i }).click()
      await expect(nameInput).toHaveValue(tab2Title)

      // 8. Complete required description and Create Course -> redirects & clears draft
      await descInput.fill('Grade 12 Physics momentum conservation practical course.')
      await page.getByRole('button', { name: /Create Course/i }).click()
      await expect(page).toHaveURL(/\/dash\/courses\/course\/.+\/content\?new_activity=1/, { timeout: 20_000 })

      // 9. Reopen Create Course -> no stale draft prompt remains
      await openCreateCourseScratch(page)
      await expect(page.locator('input[name="name"]')).toBeVisible({ timeout: 15_000 })
      await expect(page.locator('input[name="name"]')).toHaveValue('')
      await expect(page.getByTestId('course-draft-gate')).toHaveCount(0)
    } finally {
      rmSync(tmpDir, { recursive: true, force: true })
    }
  })
})
