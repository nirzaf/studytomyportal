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
  assert.equal(isSafeStudytomyTestDatabase('postgresql://tester:secret@localhost/studytomy_test#unexpected'), false)
})

test('database identity is credential-free and varies with host, port, and database', () => {
  const first = studytomyTestDatabaseIdentity('postgresql://alice:first@localhost/studytomy_test')
  const sameTarget = studytomyTestDatabaseIdentity('postgresql://bob:second@localhost:5432/studytomy_test')
  const otherPort = studytomyTestDatabaseIdentity('postgresql://alice:first@localhost:5433/studytomy_test')
  const otherHost = studytomyTestDatabaseIdentity('postgresql://alice:first@127.0.0.1/studytomy_test')
  const otherDatabase = studytomyTestDatabaseIdentity('postgresql://alice:first@localhost/other_test')
  assert.match(first ?? '', /^[0-9a-f]{64}$/)
  assert.equal(first, '5cefd14c3a81e924b9e6e36e08b483e9c6c45d022994e0a9f5169cdc979afa1b')
  assert.equal(first, sameTarget)
  assert.notEqual(first, otherPort)
  assert.notEqual(first, otherHost)
  assert.notEqual(first, otherDatabase)
})
