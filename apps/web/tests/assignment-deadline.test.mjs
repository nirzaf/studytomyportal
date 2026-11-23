import { expect, test } from 'bun:test'
import { formatAssignmentDueDate, isAssignmentPastDue, parseDueDate } from '../lib/assignments/deadline.ts'

test('explicit offsets disambiguate an exact deadline during the repeated DST hour', () => {
  const now = Date.parse('2026-04-04T16:00:00Z')
  expect(isAssignmentPastDue('2026-04-05T02:30:00+11:00', now)).toBe(true)
  expect(isAssignmentPastDue('2026-04-05T02:30:00+10:00', now)).toBe(false)
  expect(isAssignmentPastDue('2026-04-04T16:00:00Z', now)).toBe(false)
})

test('a date-only deadline includes the entire local due day', () => {
  const date = '2026-04-05'
  expect(parseDueDate(date)?.hasTime).toBe(false)
  expect(isAssignmentPastDue(date, new Date(2026, 3, 5, 23, 59).getTime())).toBe(false)
  expect(isAssignmentPastDue(date, new Date(2026, 3, 6, 0, 1).getTime())).toBe(true)
  expect(formatAssignmentDueDate(date, 'en-AU', 'Australia/Perth'))
    .toBe(formatAssignmentDueDate(date, 'en-AU', 'Australia/Sydney'))
})

test('a saved IANA zone changes the exact deadline label without moving the instant', () => {
  const raw = '2026-11-18T12:30:00Z'
  expect(formatAssignmentDueDate(raw, 'en-AU', 'Australia/Sydney')).toMatch(/11:30\s*pm/i)
  expect(formatAssignmentDueDate(raw, 'en-AU', 'Australia/Perth')).toMatch(/08:30\s*pm/i)
  expect(parseDueDate(raw)?.at.toISOString()).toBe('2026-11-18T12:30:00.000Z')
})
