import { test, expect } from '../../../core/fixtures'
import { BASE_URL } from '../../../core/instance'
import { STUDENT_STATE, sharedStudent } from '../../../core/sharedAuth'
import { getSessionSwitchStudent, type SessionSwitchStudent } from '../session-user'

function profileMenuTrigger(page: import('@playwright/test').Page, username: string) {
  return page.locator('button[aria-haspopup="menu"]').filter({ hasText: username })
}

let accountB: SessionSwitchStudent

test.beforeAll(async () => {
  accountB = await getSessionSwitchStudent()
})

test('clears a shared browser session on logout and revalidates another account after back navigation', async ({ browser }) => {
  const accountA = sharedStudent()

  const context = await browser.newContext({
    storageState: STUDENT_STATE,
    viewport: { width: 1920, height: 1080 },
  })
  const firstTab = await context.newPage()
  const secondTab = await context.newPage()

  try {
    await firstTab.goto(BASE_URL)
    await expect(profileMenuTrigger(firstTab, accountA.username)).toBeVisible()

    await secondTab.goto(new URL('/account/privacy', BASE_URL).toString())
    await expect(secondTab.getByRole('heading', { name: /privacy/i })).toBeVisible()
    const secondTabAccountA = profileMenuTrigger(secondTab, accountA.username)
    await expect(secondTabAccountA).toBeVisible()
    await secondTabAccountA.click()
    await expect(secondTab.getByRole('menu').getByText(accountA.email)).toBeVisible()

    // Preserve a private-route history entry so the later back navigation
    // exercises the BFCache/session revalidation path.
    await secondTab.goto(BASE_URL)
    await expect(profileMenuTrigger(secondTab, accountA.username)).toBeVisible()

    const firstTabAccountA = profileMenuTrigger(firstTab, accountA.username)
    await firstTabAccountA.click()
    const signOut = firstTab.getByRole('menuitem', { name: /sign out/i })
    await expect(signOut).toBeVisible()
    await signOut.click()

    await expect(firstTab.getByRole('link', { name: /log ?in/i }).first()).toBeVisible()
    await expect(profileMenuTrigger(firstTab, accountA.username)).toHaveCount(0)
    await expect(secondTab.getByRole('link', { name: /log ?in/i }).first()).toBeVisible()
    await expect(profileMenuTrigger(secondTab, accountA.username)).toHaveCount(0)

    await firstTab.goto(new URL('/login', BASE_URL).toString())
    await expect(firstTab.getByRole('textbox', { name: 'Email' })).toBeVisible()
    await firstTab.getByRole('textbox', { name: 'Email' }).fill(accountB.email)
    await expect(firstTab.getByRole('textbox', { name: 'Email' })).toHaveValue(accountB.email)
    await firstTab.getByRole('textbox', { name: 'Password' }).fill(accountB.password)
    await expect(firstTab.getByRole('textbox', { name: 'Password' })).toHaveValue(/.+/)
    await firstTab.getByRole('button', { name: 'Login', exact: true }).click()

    await expect(profileMenuTrigger(firstTab, accountB.username)).toBeVisible()
    await expect(profileMenuTrigger(secondTab, accountB.username)).toBeVisible()
    await expect(profileMenuTrigger(secondTab, accountA.username)).toHaveCount(0)

    await secondTab.goBack()
    await expect(secondTab).toHaveURL(/\/account\/privacy(?:\?.*)?$/)
    await expect(profileMenuTrigger(secondTab, accountB.username)).toBeVisible()
    await expect(profileMenuTrigger(secondTab, accountA.username)).toHaveCount(0)
    await expect(secondTab.locator('body')).not.toContainText(accountA.email)
  } finally {
    await context.close()
  }
})
