import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const siteUrl = process.env.NEXT_PUBLIC_DOCS_URL?.trim()
if (!siteUrl) {
  console.log('Skipping sitemap generation: set NEXT_PUBLIC_DOCS_URL to the verified documentation origin.')
  process.exit(0)
}

const bin = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'node_modules', '.bin', 'next-sitemap')
const result = spawnSync(bin, [], { stdio: 'inherit', env: process.env })
if (result.error) throw result.error
process.exit(result.status ?? 1)
