import assert from 'node:assert/strict'
import { test } from 'node:test'
import { isSafeStudytomyTestDatabase, studytomyTestDatabaseIdentity } from './testDatabase'

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
  assert.equal(isSafeStudytomyTestDatabase('postgresql://tester:secret@localhost/studytomy_test?host=db.example.org'), false)
  assert.equal(isSafeStudytomyTestDatabase('postgresql://tester:secret@localhost/studytomy_test?hostaddr=203.0.113.1'), false)
})

test('database identity is credential-free and varies with host, port, and database', () => {
  const first = studytomyTestDatabaseIdentity('postgresql://alice:first@localhost/studytomy_test')
  const sameTarget = studytomyTestDatabaseIdentity('postgresql://bob:second@localhost:5432/studytomy_test')
  const otherHost = studytomyTestDatabaseIdentity('postgresql://alice:first@127.0.0.1/studytomy_test')
  const otherDatabase = studytomyTestDatabaseIdentity('postgresql://alice:first@localhost/other_test')
  assert.match(first ?? '', /^[0-9a-f]{64}$/)
  assert.equal(first, sameTarget)
  assert.notEqual(first, otherHost)
  assert.notEqual(first, otherDatabase)
})
