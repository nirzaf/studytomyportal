import assert from 'node:assert/strict'
import { test } from 'node:test'
import { isSafeStudytomyTestDatabase } from './testDatabase'

test('test database guard accepts loopback PostgreSQL databases with the required suffix', () => {
  assert.equal(isSafeStudytomyTestDatabase('postgresql://tester:secret@localhost/studytomy_test'), true)
  assert.equal(isSafeStudytomyTestDatabase('postgresql+asyncpg://tester:secret@127.0.0.2/studytomy_test'), true)
  assert.equal(isSafeStudytomyTestDatabase('postgresql://tester:secret@[::1]/studytomy_test'), true)
})

test('test database guard rejects missing, remote, non-PostgreSQL, and non-test databases', () => {
  assert.equal(isSafeStudytomyTestDatabase(undefined), false)
  assert.equal(isSafeStudytomyTestDatabase('postgresql://tester:secret@db.example.org/studytomy_test'), false)
  assert.equal(isSafeStudytomyTestDatabase('postgresql://tester:secret@localhost/studytomy'), false)
  assert.equal(isSafeStudytomyTestDatabase('mysql://tester:secret@localhost/studytomy_test'), false)
  assert.equal(isSafeStudytomyTestDatabase('not a database URL'), false)
})
