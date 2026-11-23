import { afterEach, describe, expect, test } from 'bun:test'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import {
  currentForkIdentity,
  includesStudyTomySpecs,
  isLoopbackUrl,
  runtimeIdentityProblems,
} from './studytomyIdentity.ts'

const temporaryRoots = []

afterEach(() => {
  for (const root of temporaryRoots.splice(0)) rmSync(root, { recursive: true, force: true })
})

describe('Studytomy E2E identity', () => {
  test('hashes tracked and untracked source content, not just HEAD', () => {
    const root = mkdtempSync(join(tmpdir(), 'studytomy-identity-'))
    temporaryRoots.push(root)
    execFileSync('git', ['init', '-q'], { cwd: root })
    execFileSync('git', ['config', 'user.name', 'Identity Test'], { cwd: root })
    execFileSync('git', ['config', 'user.email', 'identity@example.invalid'], { cwd: root })
    mkdirSync(join(root, 'src'))
    writeFileSync(join(root, 'src', 'main.py'), 'first revision\n')
    execFileSync('git', ['add', '.'], { cwd: root })
    execFileSync('git', ['commit', '-qm', 'fixture'], { cwd: root })

    const committed = currentForkIdentity(root)
    writeFileSync(join(root, 'src', 'main.py'), 'working tree revision\n')
    const modified = currentForkIdentity(root)
    writeFileSync(join(root, 'src', 'new.py'), 'untracked source\n')
    const withUntracked = currentForkIdentity(root)

    expect(committed.commitSha).toBe(modified.commitSha)
    expect(committed.sourceTreeSha256).not.toBe(modified.sourceTreeSha256)
    expect(modified.sourceTreeSha256).not.toBe(withUntracked.sourceTreeSha256)
    expect(committed.dirty).toBe(false)
    expect(withUntracked.dirty).toBe(true)
  })

  test('accepts only exact source and migration-head identity', () => {
    const expected = { commitSha: 'a'.repeat(40), sourceTreeSha256: 'b'.repeat(64), dirty: true }
    const actual = {
      build_sha: expected.commitSha,
      source_tree_sha256: expected.sourceTreeSha256,
      source_migration_heads: ['migration-head'],
      database_migration_heads: ['migration-head'],
    }
    expect(runtimeIdentityProblems(actual, expected)).toEqual([])
    expect(runtimeIdentityProblems(null, expected)).toEqual(['runtime identity response is missing'])
    expect(runtimeIdentityProblems({ ...actual, build_sha: 'c'.repeat(40) }, expected)).toContain(
      'commit SHA does not match the local checkout',
    )
    expect(runtimeIdentityProblems({ ...actual, database_migration_heads: [] }, expected)).toContain(
      'database migration heads do not match the served source',
    )
    expect(runtimeIdentityProblems({ ...actual, database_identity_sha256: 'd'.repeat(64) }, expected, 'd'.repeat(64))).toEqual([])
    expect(runtimeIdentityProblems(actual, expected, 'd'.repeat(64))).toContain(
      'active database identity does not match the approved test database',
    )
    expect(runtimeIdentityProblems({ ...actual, database_identity_sha256: 'e'.repeat(64) }, expected, 'd'.repeat(64))).toContain(
      'active database identity does not match the approved test database',
    )
  })

  test('guards Studytomy and full-suite runs while allowing explicit upstream-only paths', () => {
    expect(includesStudyTomySpecs(['test', 'features/studytomy/tests/example.spec.ts'])).toBe(true)
    expect(includesStudyTomySpecs(['test', 'features/courses/tests/example.spec.ts'])).toBe(false)
    expect(includesStudyTomySpecs(['test'])).toBe(true)
  })

  test('limits verified Studytomy browser runs to loopback hosts', () => {
    expect(isLoopbackUrl('http://localhost:3000')).toBe(true)
    expect(isLoopbackUrl('http://127.0.0.1:8080')).toBe(true)
    expect(isLoopbackUrl('https://example.com')).toBe(false)
    expect(isLoopbackUrl('not a URL')).toBe(false)
  })
})
