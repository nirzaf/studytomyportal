/**
 * Playwright global setup.
 *
 * Boots a real Studytomy Portal self-host using the Studytomy Portal CLI (`setup --ci`,
 * pulling the published image) unless we've been pointed at an already-running
 * instance via E2E_BASE_URL / E2E_SKIP_BOOT. Then it waits until the API
 * health endpoint and the bootstrapped organization are both reachable before
 * any test runs, so specs never start against a half-booted stack.
 */
import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { chromium } from '@playwright/test'
import {
  ADMIN_EMAIL,
  ADMIN_PASSWORD,
  API_URL,
  BASE_URL,
  CLI,
  DOMAIN,
  INSTALL_NAME,
  ORG_SLUG,
  PORT,
  SKIP_BOOT,
  makeStudent,
} from './core/instance'
import * as api from './core/client'
import {
  ADMIN_STATE,
  AUTH_DIR,
  STUDENT_STATE,
  ensureAuthDir,
  sharedStudent,
  writeSharedStudent,
} from './core/sharedAuth'
import {
  currentForkIdentity,
  includesStudyTomySpecs,
  isLoopbackUrl,
  runtimeIdentityProblems,
  type RuntimeIdentity,
} from './core/studytomyIdentity'
import { isSafeStudytomyTestDatabase } from './core/testDatabase'

const BOOT_TIMEOUT_MS = 8 * 60 * 1000 // image pull + first-run install can be slow
const POLL_INTERVAL_MS = 3000

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function waitForOk(url: string, timeoutMs: number, label: string): Promise<void> {
  const deadline = Date.now() + timeoutMs
  let lastErr = ''
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(5000) })
      if (res.ok) {
        console.log(`✓ ${label} ready (${url})`)
        return
      }
      lastErr = `status ${res.status}`
    } catch (e) {
      lastErr = (e as Error).message
    }
    await sleep(POLL_INTERVAL_MS)
  }
  throw new Error(`Timed out waiting for ${label} at ${url}: ${lastErr}`)
}

function bootSelfHost(): void {
  console.log(`Booting Studytomy Portal self-host via CLI (install "${INSTALL_NAME}")…`)
  const cmd = [
    CLI,
    'setup',
    '--ci',
    `--name ${INSTALL_NAME}`,
    `--domain ${DOMAIN}`,
    `--port ${PORT}`,
    `--admin-email ${ADMIN_EMAIL}`,
    `--admin-password ${ADMIN_PASSWORD}`,
    `--org-name "E2E Org"`,
    `--org-slug ${ORG_SLUG}`,
  ].join(' ')

  const result = spawnSync(cmd, {
    shell: true,
    stdio: 'inherit',
    timeout: BOOT_TIMEOUT_MS,
    env: process.env,
  })

  if (result.status !== 0) {
    throw new Error(
      `CLI setup failed (exit ${result.status}). ` +
        `Command: ${cmd}\n` +
        `If you already have an instance running, set E2E_BASE_URL to skip booting.`,
    )
  }
}

export default async function globalSetup(): Promise<void> {
  const verifyStudyTomy = includesStudyTomySpecs(process.argv.slice(2))
  if (verifyStudyTomy) {
    if (!process.env.E2E_BASE_URL) {
      throw new Error(
        'Studytomy E2E requires E2E_BASE_URL for an already-running local fork. It will not boot a CLI-managed image because the default runtime is not a verified build of this checkout.',
      )
    }
    if (!isLoopbackUrl(BASE_URL) || !isLoopbackUrl(API_URL)) {
      throw new Error('Studytomy E2E only runs against a loopback web and API instance.')
    }
    // Global setup creates a shared learner, so guard the database before any API access.
    if (!isSafeStudytomyTestDatabase(process.env.STUDYTOMY_TEST_DATABASE_URL)) {
      throw new Error(
        'Studytomy E2E requires STUDYTOMY_TEST_DATABASE_URL to identify the same loopback PostgreSQL _test database used by the API. Global setup will not provision shared test accounts without it.',
      )
    }
  }

  if (SKIP_BOOT) {
    console.log(`Reusing existing instance at ${BASE_URL} (boot skipped).`)
  } else {
    bootSelfHost()
  }

  await waitForOk(`${API_URL}/health`, SKIP_BOOT ? 60_000 : BOOT_TIMEOUT_MS, 'API health')
  await waitForOk(`${API_URL}/orgs/slug/${ORG_SLUG}`, 60_000, `org "${ORG_SLUG}"`)
  console.log(`Studytomy Portal is ready at ${BASE_URL}. Admin: ${ADMIN_EMAIL}`)

  if (verifyStudyTomy) await verifyStudyTomyRuntime()

  await generateSharedAuth()
}

async function verifyStudyTomyRuntime(): Promise<void> {
  const expected = currentForkIdentity()
  const token = await api.login(ADMIN_EMAIL, ADMIN_PASSWORD)

  let apiIdentity: RuntimeIdentity
  try {
    apiIdentity = await api.apiGet<RuntimeIdentity>('/instance/runtime-identity', token)
  } catch (error) {
    throw new Error(
      `Studytomy API identity is unavailable or unconfigured. Set the local build identity and STUDYTOMY_TEST_RUNTIME_IDENTITY=1. ${String(error)}`,
    )
  }
  const apiProblems = runtimeIdentityProblems(apiIdentity, expected)

  let webIdentity: { build_sha?: string; source_tree_sha256?: string } | null = null
  let webFailure = ''
  try {
    const response = await fetch(`${BASE_URL}/api/studytomy/runtime-identity`, {
      cache: 'no-store',
      signal: AbortSignal.timeout(10_000),
    })
    if (!response.ok) {
      webFailure = `web identity endpoint returned ${response.status}`
    } else {
      webIdentity = await response.json()
    }
  } catch (error) {
    webFailure = String(error)
  }
  const webProblems = runtimeIdentityProblems(
    webIdentity
      ? {
          build_sha: webIdentity.build_sha ?? '',
          source_tree_sha256: webIdentity.source_tree_sha256 ?? '',
          source_migration_heads: apiIdentity.source_migration_heads,
          database_migration_heads: apiIdentity.source_migration_heads,
        }
      : null,
    expected,
  )

  if (apiProblems.length > 0 || webProblems.length > 0 || webFailure) {
    const reasons = [
      ...apiProblems.map(problem => `API: ${problem}`),
      ...webProblems.map(problem => `web: ${problem}`),
      ...(webFailure ? [`web: ${webFailure}`] : []),
    ]
    throw new Error(`Studytomy runtime identity check failed: ${reasons.join('; ')}`)
  }

  console.log(`Studytomy runtime verified: ${JSON.stringify({
    commit_sha: expected.commitSha,
    source_tree_sha256: expected.sourceTreeSha256,
    dirty_worktree: expected.dirty,
    database_migration_heads: apiIdentity.database_migration_heads,
    web_and_api_source_match: true,
  })}`)
}

/**
 * Log in ONCE as the admin and ONCE as a reusable student, saving each browser
 * session so specs can reuse them via storageState (keeps the whole suite well
 * under the login rate limit). The shared student is also created here.
 */
async function generateSharedAuth(): Promise<void> {
  ensureAuthDir()

  // Reuse the synthetic student persisted by an earlier local run. Creating a
  // new account every time needlessly consumes signup capacity and causes
  // focused reruns to fail on the API's real signup rate limit.
  const adminToken = await api.login(ADMIN_EMAIL, ADMIN_PASSWORD)
  const sharedStudentPath = join(AUTH_DIR, 'shared.json')
  let stu: Pick<ReturnType<typeof makeStudent>, 'email' | 'password'> | null = null

  if (existsSync(sharedStudentPath)) {
    const existingStudent = sharedStudent()
    try {
      await api.login(existingStudent.email, existingStudent.password)
      stu = { email: existingStudent.email, password: existingStudent.password }
    } catch (error) {
      // Only recreate a fixture when the persisted account is absent from a
      // freshly reset database. Network and rate-limit failures stay visible.
      if (!/-> 401\b|-> 404\b/.test(String(error))) throw error
    }
  }

  if (!stu) {
    const org = await api.getOrg()
    const freshStudent = makeStudent('shared')
    const studentId = await api.createStudent(adminToken, org.id, freshStudent)
    writeSharedStudent({ ...freshStudent, id: studentId })
    stu = { email: freshStudent.email, password: freshStudent.password }
  }

  const browser = await chromium.launch()
  try {
    await saveLogin(browser, ADMIN_EMAIL, ADMIN_PASSWORD, ADMIN_STATE)
    await saveLogin(browser, stu.email, stu.password, STUDENT_STATE)
  } finally {
    await browser.close()
  }
  console.log('Saved shared admin + student sessions for reuse.')
}

async function saveLogin(
  browser: import('@playwright/test').Browser,
  email: string,
  password: string,
  statePath: string,
): Promise<void> {
  const context = await browser.newContext()
  // Seed onboarding state before the app's first render. Writing this after a
  // login navigation raced with client redirects and could lose its execution
  // context before storageState was captured.
  await context.addInitScript(
    ([key, value]) => {
      try {
        window.localStorage.setItem(key, value)
      } catch {
        /* ignore */
      }
    },
    [
      'lh_onboarding',
      JSON.stringify({
        completedSteps: [
          'create_course', 'add_activities', 'experience_editor', 'try_playgrounds',
          'invite_users', 'customize_org', 'teach_the_world',
        ],
        skippedSteps: [], minimized: true, expanded: false, showAllSteps: false,
        dismissed: true, welcomeSeen: true,
      }),
    ] as const,
  )
  const page = await context.newPage()
  for (let attempt = 1; attempt <= 3; attempt++) {
    await page.goto(`${BASE_URL}/login`, { waitUntil: 'domcontentloaded', timeout: 90_000 })
    await page.getByRole('textbox', { name: 'Email' }).fill(email)
    await page.getByRole('textbox', { name: 'Password' }).fill(password)
    await page.getByRole('button', { name: 'Login', exact: true }).click()
    try {
      await page.waitForURL((u) => !/\/login(\?|$)/.test(u.toString()), {
        waitUntil: 'domcontentloaded',
        timeout: 90_000,
      })
      break
    } catch {
      if (attempt === 3) throw new Error(`global-setup: login failed for ${email}`)
      await page.waitForTimeout(15_000)
    }
  }
  await context.storageState({ path: statePath })
  await context.close()
}
