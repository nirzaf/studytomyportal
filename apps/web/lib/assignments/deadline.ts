type ParsedDueDate = { at: Date; hasTime: boolean }

export function parseDueDate(raw?: string | null): ParsedDueDate | null {
  const value = raw?.trim()
  if (!value) return null
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?(Z|[+-]\d{2}:\d{2})?)?$/.exec(value)
  if (!match) return null
  const [, year, month, day, hours, minutes, seconds, fraction, offset] = match
  const hasTime = hours !== undefined
  const at = offset
    ? new Date(value.replace(' ', 'T'))
    : new Date(
      Number(year), Number(month) - 1, Number(day), Number(hours ?? 0),
      Number(minutes ?? 0), Number(seconds ?? 0), Number((fraction ?? '').padEnd(3, '0').slice(0, 3)),
    )
  return Number.isNaN(at.getTime()) ? null : { at, hasTime }
}

export function isAssignmentPastDue(raw?: string | null, now = Date.now()): boolean {
  const parsed = parseDueDate(raw)
  if (!parsed) return false
  // A date-only deadline includes the whole local day.
  if (!parsed.hasTime) parsed.at.setDate(parsed.at.getDate() + 1)
  return parsed.at.getTime() < now
}
