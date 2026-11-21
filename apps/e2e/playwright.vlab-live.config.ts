import path from 'node:path'
import fs from 'node:fs'
import { fileURLToPath } from 'node:url'
import { defineConfig } from '@playwright/test'

const here = path.dirname(fileURLToPath(import.meta.url))
const repositoryRoot = path.resolve(here, '../..')
const outputDir = process.env.E2E_LIVE_VLAB_OUTPUT_DIR

if (!outputDir || !path.isAbsolute(outputDir) || !fs.existsSync(outputDir)) {
  throw new Error('Set E2E_LIVE_VLAB_OUTPUT_DIR to an existing absolute directory outside the checkout.')
}

const resolvedOutputDir = path.resolve(outputDir)
const realRepositoryRoot = fs.realpathSync(repositoryRoot)
const realOutputDir = fs.realpathSync(resolvedOutputDir)
const outputMode = fs.statSync(realOutputDir).mode
if ((outputMode & 0o077) !== 0) {
  throw new Error('Live Virtual Lab browser artifacts must be in an owner-only directory.')
}
if (
  realOutputDir === realRepositoryRoot ||
  realOutputDir.startsWith(`${realRepositoryRoot}${path.sep}`)
) {
  throw new Error('Live Virtual Lab browser artifacts must be written outside the checkout.')
}

export default defineConfig({
  testDir: path.join(here, 'features/studytomy/tests'),
  testMatch: ['17-managed-virtual-lab-live.spec.ts'],
  outputDir: realOutputDir,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://localhost:3000',
    trace: 'off',
    screenshot: 'only-on-failure',
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    launchOptions: {
      args: [
        '--disable-background-networking',
        '--host-resolver-rules=MAP * 0.0.0.0,EXCLUDE localhost,EXCLUDE 127.0.0.1,EXCLUDE [::1]',
      ],
    },
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
})
