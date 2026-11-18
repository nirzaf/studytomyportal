import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { lstatSync, readFileSync, readlinkSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export type ForkIdentity = {
  commitSha: string
  sourceTreeSha256: string
  dirty: boolean
}

export type RuntimeIdentity = {
  build_sha: string
  source_tree_sha256: string
  source_migration_heads: string[]
  database_migration_heads: string[]
}

const VALUE_FLAGS = new Set([
  '-c', '-g', '-o', '-p', '-r', '-w',
  '--config', '--grep', '--grep-invert', '--global-timeout', '--max-failures',
  '--output', '--project', '--reporter', '--timeout', '--workers',
])

function git(root: string, args: string[]): string {
  return execFileSync('git', args, { cwd: root, encoding: 'utf8' }).trim()
}

function appendFileDigest(hash: ReturnType<typeof createHash>, root: string, relativePath: string): void {
  const pathBytes = Buffer.from(relativePath, 'utf8')
  const pathLength = Buffer.alloc(4)
  pathLength.writeUInt32BE(pathBytes.length)
  hash.update(pathLength)
  hash.update(pathBytes)

  let content: Buffer
  let mode = 'file'
  try {
    const stat = lstatSync(join(root, relativePath))
    if (stat.isSymbolicLink()) {
      mode = 'symlink'
      content = Buffer.from(readlinkSync(join(root, relativePath)), 'utf8')
    } else if (stat.isFile()) {
      mode = (stat.mode & 0o111) !== 0 ? 'executable' : 'file'
      content = readFileSync(join(root, relativePath))
    } else if (stat.isDirectory()) {
      mode = 'directory'
      content = Buffer.from(git(join(root, relativePath), ['rev-parse', 'HEAD']), 'utf8')
    } else {
      mode = 'unsupported'
      content = Buffer.alloc(0)
    }
  } catch {
    mode = 'deleted'
    content = Buffer.alloc(0)
  }

  hash.update(mode)
  hash.update(Buffer.from([0]))
  const contentLength = Buffer.alloc(8)
  contentLength.writeBigUInt64BE(BigInt(content.length))
  hash.update(contentLength)
  hash.update(content)
}

export function currentForkIdentity(repoRoot?: string): ForkIdentity {
  const moduleRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../../..')
  const root = repoRoot ?? git(moduleRoot, ['rev-parse', '--show-toplevel'])
  const commitSha = git(root, ['rev-parse', 'HEAD'])
  const tracked = git(root, ['ls-files', '-z']).split('\0').filter(Boolean)
  const untracked = git(root, ['ls-files', '-o', '--exclude-standard', '-z']).split('\0').filter(Boolean)
  const files = [...new Set([...tracked, ...untracked])].sort((left, right) =>
    left < right ? -1 : left > right ? 1 : 0,
  )
  const hash = createHash('sha256')
  for (const relativePath of files) appendFileDigest(hash, root, relativePath)

  return {
    commitSha,
    sourceTreeSha256: hash.digest('hex'),
    dirty: git(root, ['status', '--porcelain', '--untracked-files=all']).length > 0,
  }
}

/** Require exact web/API source identity and a database at the served code's heads. */
export function runtimeIdentityProblems(
  actual: Partial<RuntimeIdentity> | null | undefined,
  expected: ForkIdentity,
): string[] {
  if (!actual) return ['runtime identity response is missing']
  const problems: string[] = []
  if (actual.build_sha !== expected.commitSha) problems.push('commit SHA does not match the local checkout')
  if (actual.source_tree_sha256 !== expected.sourceTreeSha256) {
    problems.push('source tree digest does not match the local checkout')
  }
  if (!Array.isArray(actual.source_migration_heads) || actual.source_migration_heads.length === 0) {
    problems.push('served migration heads are missing')
  }
  if (!Array.isArray(actual.database_migration_heads)) {
    problems.push('database migration heads are missing')
  } else if (
    Array.isArray(actual.source_migration_heads) &&
    JSON.stringify([...actual.database_migration_heads].sort()) !==
      JSON.stringify([...actual.source_migration_heads].sort())
  ) {
    problems.push('database migration heads do not match the served source')
  }
  return problems
}

/** Full-suite runs include Studytomy; explicit upstream-only paths remain usable. */
export function includesStudyTomySpecs(argv: string[]): boolean {
  const paths: string[] = []
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index]
    if (VALUE_FLAGS.has(arg)) {
      index += 1
      continue
    }
    if (arg.startsWith('-') || arg === 'test' || arg === 'playwright') continue
    if (arg.includes('/') || /\.(spec|test)\.[cm]?[jt]sx?$/.test(arg)) paths.push(arg.replaceAll('\\', '/'))
  }
  if (paths.length === 0) return true
  return paths.some(path => /(^|\/)studytomy(\/|$)/i.test(path))
}

export function isLoopbackUrl(rawUrl: string): boolean {
  try {
    return ['localhost', '127.0.0.1', '[::1]', '::1'].includes(new URL(rawUrl).hostname)
  } catch {
    return false
  }
}
