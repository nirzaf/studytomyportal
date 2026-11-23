import fs from 'node:fs'
import path from 'node:path'
import { randomUUID } from 'node:crypto'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const require = createRequire(import.meta.url)
const { chromium } = require('playwright')
const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..')

function argument(name) {
  const index = process.argv.indexOf(name)
  if (index < 0 || !process.argv[index + 1]) {
    throw new Error(`Missing ${name}`)
  }
  return process.argv[index + 1]
}

function outsideCheckout(rawPath, label) {
  const resolved = path.resolve(rawPath)
  const realRoot = fs.realpathSync(repoRoot)
  const realParent = fs.realpathSync(path.dirname(resolved))
  const actualPath = path.join(realParent, path.basename(resolved))
  if (actualPath === realRoot || actualPath.startsWith(`${realRoot}${path.sep}`)) {
    throw new Error(`${label} must be outside the checkout`)
  }
  return actualPath
}

function requirePrivateDirectory(filePath) {
  const parent = path.dirname(filePath)
  const info = fs.statSync(parent)
  if (!info.isDirectory() || (info.mode & 0o077) !== 0) {
    throw new Error('Scenario and browser-state files must be in an owner-only directory')
  }
}

function requirePrivateScenario(filePath) {
  const info = fs.lstatSync(filePath)
  if (!info.isFile() || (info.mode & 0o077) !== 0) {
    throw new Error('Scenario must be a regular owner-only file')
  }
  requirePrivateDirectory(filePath)
}

function loopbackBase(raw) {
  const url = new URL(raw)
  if (
    url.protocol !== 'http:' ||
    !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname) ||
    !url.port || url.username || url.password || url.pathname !== '/' || url.search || url.hash
  ) {
    throw new Error('Base URL must be a bare loopback HTTP origin with an explicit port')
  }
  return url.origin
}

async function main() {
  const scenarioPath = outsideCheckout(argument('--scenario'), 'Scenario')
  const statePath = outsideCheckout(argument('--state'), 'Storage state')
  const base = loopbackBase(argument('--base'))
  try {
    fs.lstatSync(statePath)
    throw new Error('Storage state already exists')
  } catch (error) {
    if (error.code !== 'ENOENT') throw error
  }
  requirePrivateScenario(scenarioPath)
  requirePrivateDirectory(statePath)

  const scenario = JSON.parse(fs.readFileSync(scenarioPath, 'utf8'))
  if (
    scenario.syntheticOnly !== true ||
    typeof scenario.learner?.email !== 'string' ||
    typeof scenario.learner?.password !== 'string'
  ) {
    throw new Error('A synthetic-only scenario with a learner login is required')
  }

  const browser = await chromium.launch({
    headless: true,
    args: [
      '--disable-background-networking',
      '--host-resolver-rules=MAP * 0.0.0.0,EXCLUDE localhost,EXCLUDE 127.0.0.1,EXCLUDE [::1]',
    ],
  })
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 } })
    const page = await context.newPage()
    await page.route('**/*', route => {
      const host = new URL(route.request().url()).hostname
      if (!['localhost', '127.0.0.1', '[::1]'].includes(host)) {
        return route.abort('blockedbyclient')
      }
      return route.continue()
    })
    await page.goto(`${base}/login`, { waitUntil: 'domcontentloaded' })
    await page.locator('input[type="email"]').fill(scenario.learner.email)
    await page.locator('input[type="password"]').fill(scenario.learner.password)
    await page.locator('form button[type="submit"]').click()
    await page.waitForFunction(() => document.cookie.includes('LH_session='), { timeout: 30_000 })
    await context.storageState({ path: statePath })
    fs.chmodSync(statePath, 0o600)

    scenario.learnerStorageState = statePath
    const temporaryScenario = `${scenarioPath}.${randomUUID()}.tmp`
    fs.writeFileSync(temporaryScenario, JSON.stringify(scenario, null, 2), { flag: 'wx', mode: 0o600 })
    fs.renameSync(temporaryScenario, scenarioPath)
    fs.chmodSync(scenarioPath, 0o600)
    await context.close()
  } finally {
    await browser.close()
  }
  process.stdout.write('PASS normal loopback learner login; owner-only browser state saved; non-loopback requests blocked\n')
}

main().catch(() => {
  process.stderr.write('Browser state generation failed; no credentials or page contents were printed.\n')
  process.exitCode = 1
})
