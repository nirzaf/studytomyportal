import { test, expect } from '../../../core/fixtures'
import { BASE_URL } from '../../../core/instance'
import { sharedStudent } from '../../../core/sharedAuth'
import { TaskContents } from '../../assignments/api'
import { AssignmentPage } from '../../assignments/pages/student'
import { setupScenario } from '../../assignments/scenario'
import { getSessionSwitchStudent, type SessionSwitchStudent } from '../session-user'

function profileMenuTrigger(page: import('@playwright/test').Page, username: string) {
  return page.locator('button[aria-haspopup="menu"]').filter({ hasText: username })
}

async function signOut(page: import('@playwright/test').Page, username: string) {
  const trigger = profileMenuTrigger(page, username)
  await expect(trigger).toBeVisible()
  await trigger.click()
  const signOutItem = page.getByRole('menuitem', { name: /sign out/i })
  await expect(signOutItem).toBeVisible()
  await signOutItem.click()
  await expect(profileMenuTrigger(page, username)).toHaveCount(0)
}

async function signIn(
  page: import('@playwright/test').Page,
  email: string,
  password: string,
) {
  await page.goto(new URL('/login', BASE_URL).toString())
  const emailField = page.getByRole('textbox', { name: 'Email' })
  const passwordField = page.getByRole('textbox', { name: 'Password' })
  await expect(emailField).toBeVisible()
  await emailField.fill(email)
  await passwordField.fill(password)
  await page.getByRole('button', { name: 'Login', exact: true }).click()
}

let scenario: Awaited<ReturnType<typeof setupScenario>>
let accountB: SessionSwitchStudent

test.beforeAll(async () => {
  scenario = await setupScenario('offline-draft-isolation', {
    courseName: 'E2E Offline Draft Isolation Course',
    assignmentTitle: 'Offline Draft Isolation',
    tasks: [
      {
        title: 'Short answer',
        assignment_type: 'SHORT_ANSWER',
        contents: TaskContents.shortAnswer(['Acknowledged answer']),
      },
    ],
  })
  accountB = await getSessionSwitchStudent()
})

test('an offline answer stays with its learner after a shared-browser account switch', async ({ browser }) => {
  const accountA = sharedStudent()
  const acknowledgedAnswer = 'Acknowledged answer'
  const offlineDraft = 'PRIVATE OFFLINE ANSWER FOR ACCOUNT A'
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
  })
  const firstTab = await context.newPage()
  const secondTab = await context.newPage()

  try {
    // Sign in fresh for this spec. The preceding session-isolation test logs
    // this shared learner out, which correctly invalidates its saved refresh
    // cookie; reusing that old storageState would test a stale fixture instead
    // of the offline-draft behavior.
    await signIn(firstTab, accountA.email, accountA.password)
    await expect(profileMenuTrigger(firstTab, accountA.username)).toBeVisible()

    const assignment = new AssignmentPage(firstTab)
    await assignment.open(scenario.bareCourseUuid, scenario.bareActivityUuid)
    const firstAnswer = firstTab.getByPlaceholder('Type your answer...').first()
    await expect(firstAnswer).toHaveValue('')

    const acknowledgedSave = firstTab.waitForResponse(
      (response) =>
        response.request().method() === 'PUT' &&
        /\/assignments\/[^/]+\/tasks\/[^/]+\/submissions(?:\?|$)/.test(response.url()),
      { timeout: 20_000 },
    )
    await firstAnswer.fill(acknowledgedAnswer)
    const acknowledgedResponse = await acknowledgedSave
    expect(acknowledgedResponse.ok(), 'the acknowledged answer should reach the API').toBe(true)
    await expect(firstTab.getByText('Saved', { exact: true })).toBeVisible()

    await secondTab.goto(BASE_URL)
    await expect(profileMenuTrigger(secondTab, accountA.username)).toBeVisible()

    await context.setOffline(true)
    await firstAnswer.fill(offlineDraft)
    await expect(firstTab.getByText("Couldn't save, retrying…", { exact: true })).toBeVisible({
      timeout: 20_000,
    })

    // Restore ordinary navigation while keeping assignment writes unavailable.
    // This lets logout/login complete and proves the unsaved value is not
    // silently retried under the next account.
    await context.route('**/assignments/*/tasks/*/submissions', async (route) => {
      if (route.request().method() === 'PUT') {
        await route.abort('internetdisconnected')
        return
      }
      await route.continue()
    })
    await context.setOffline(false)
    await signOut(firstTab, accountA.username)
    await expect(firstTab.getByRole('link', { name: /log ?in/i }).first()).toBeVisible()
    await expect(secondTab.getByRole('link', { name: /log ?in/i }).first()).toBeVisible()

    await signIn(firstTab, accountB.email, accountB.password)
    await expect(profileMenuTrigger(firstTab, accountB.username)).toBeVisible()
    await expect(profileMenuTrigger(secondTab, accountB.username)).toBeVisible()

    await new AssignmentPage(secondTab).open(scenario.bareCourseUuid, scenario.bareActivityUuid)
    const accountBAnswer = secondTab.getByPlaceholder('Type your answer...').first()
    await expect(accountBAnswer).toHaveValue('')
    await expect(secondTab.locator('body')).not.toContainText(offlineDraft)

    await signOut(firstTab, accountB.username)
    await signIn(firstTab, accountA.email, accountA.password)
    await expect(profileMenuTrigger(firstTab, accountA.username)).toBeVisible()
    await new AssignmentPage(firstTab).open(scenario.bareCourseUuid, scenario.bareActivityUuid)
    const recoveredAnswer = firstTab.getByPlaceholder('Type your answer...').first()
    await expect(recoveredAnswer).toHaveValue(acknowledgedAnswer)
    await expect(recoveredAnswer).not.toHaveValue(offlineDraft)

    // A server-declared expired refresh session must clear both tabs without
    // deleting the answer that the server had already acknowledged.
    const denyRefresh = async (route: import('@playwright/test').Route) => {
      if (route.request().method() === 'GET') {
        await route.fulfill({
          status: 401,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'Expired test refresh session' }),
        })
        return
      }
      await route.continue()
    }
    await context.route('**/api/auth/refresh', denyRefresh)
    const expiredRefreshResponse = firstTab.waitForResponse(
      (response) => response.url().includes('/api/auth/refresh') && response.status() === 401,
      { timeout: 15_000 },
    )
    await firstTab.reload({ waitUntil: 'domcontentloaded' })
    expect((await expiredRefreshResponse).status()).toBe(401)
    await expect(firstTab.getByRole('link', { name: /log ?in/i }).first()).toBeVisible()
    await expect(profileMenuTrigger(firstTab, accountA.username)).toHaveCount(0)
    await expect(secondTab.getByRole('link', { name: /log ?in/i }).first()).toBeVisible()
    await expect(firstTab.locator('body')).not.toContainText(acknowledgedAnswer)
    await expect(firstTab.locator('body')).not.toContainText(offlineDraft)
    await context.unroute('**/api/auth/refresh', denyRefresh)

    await signIn(firstTab, accountA.email, accountA.password)
    await expect(profileMenuTrigger(firstTab, accountA.username)).toBeVisible()
    await new AssignmentPage(firstTab).open(scenario.bareCourseUuid, scenario.bareActivityUuid)
    await expect(firstTab.getByPlaceholder('Type your answer...').first()).toHaveValue(
      acknowledgedAnswer,
    )
  } finally {
    await context.close()
  }
})
