'use client'

import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import { Button } from '@components/ui/button'
import { formatDateTime, formatNumber } from '@/lib/format'
import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'

type Report = {
  org_uuid: string
  period_start: string
  period_end: string
  observed_at: string
  query_version: string
  population: string
  exclusions: string
  unresolved_cases: number
  oldest_unresolved_age_seconds: number | null
  first_resolved_cases: number
  first_resolution_elapsed_seconds: number
  reopened_transitions: number
  reopened_cases: number
}

function utcInput(date: string | Date) {
  return new Date(date).toISOString().slice(0, 19)
}

export default function SupportOperatingReport({ orgUuid, token }: { orgUuid: string; token: string }) {
  const [authorized, setAuthorized] = useState(false)
  const [report, setReport] = useState<Report>()
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const pending = useRef<AbortController | null>(null)
  const load = useCallback(async (start: string, end: string) => {
    pending.current?.abort()
    const controller = new AbortController()
    pending.current = controller
    setBusy(true)
    setReport(undefined)
    setMessage('')
    const path = `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/support/staff/operating-report`
    try {
      const response = await secureFetch(`${path}?${new URLSearchParams({ start, end })}`, {
        ...RequestBodyWithAuthHeader('GET', null, null, token),
        cache: 'no-store', redirect: 'error', signal: controller.signal,
      })
      if (controller.signal.aborted) return
      if ([401, 403, 404, 409].includes(response.status)) {
        setAuthorized(false)
        return
      }
      if (!response.ok) throw new Error('Report unavailable')
      const data: Report = await response.json()
      if (data.org_uuid !== orgUuid) throw new Error('Report scope mismatch')
      if (!controller.signal.aborted) { setAuthorized(true); setReport(data) }
    } catch {
      if (!controller.signal.aborted) setMessage('Check the reporting period and try again.')
    } finally {
      if (pending.current === controller) {
        pending.current = null
        setBusy(false)
      }
    }
  }, [orgUuid, token])

  useEffect(() => {
    function clear() {
      pending.current?.abort()
      pending.current = null
      setAuthorized(false)
      setReport(undefined)
      setBusy(false)
      setMessage('')
    }
    function refresh() {
      clear()
      if (document.visibilityState === 'hidden') return
      const end = new Date()
      const start = new Date(end.getTime() - 7 * 86_400_000)
      void load(start.toISOString(), end.toISOString())
    }
    refresh()
    document.addEventListener('visibilitychange', refresh)
    window.addEventListener('pagehide', clear)
    window.addEventListener('pageshow', refresh)
    return () => {
      pending.current?.abort()
      document.removeEventListener('visibilitychange', refresh)
      window.removeEventListener('pagehide', clear)
      window.removeEventListener('pageshow', refresh)
    }
  }, [load])

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const fields = new FormData(event.currentTarget)
    const start = `${fields.get('start')}Z`
    const end = `${fields.get('end')}Z`
    if (!(Date.parse(start) < Date.parse(end))) {
      setMessage('Choose an end time after the start time.')
      return
    }
    void load(start, end)
  }

  if (!authorized) return null
  const input = 'block w-full rounded-md border border-input bg-background px-3 py-2'
  const hours = (seconds: number) => formatNumber(seconds / 3600, undefined, { maximumFractionDigits: 2 })
  return <section className="space-y-3 rounded-md border p-4" aria-labelledby="support-operations-title">
    <h2 id="support-operations-title" className="text-xl font-semibold">Support operating report</h2>
    <form onSubmit={submit}>
      <fieldset disabled={busy} className="grid gap-3 sm:grid-cols-2">
        <label>Start (UTC)<input name="start" type="datetime-local" step="1" required
          className={input} defaultValue={report && utcInput(report.period_start)} /></label>
        <label>End (UTC, excluded)<input name="end" type="datetime-local" step="1" required
          className={input} defaultValue={report && utcInput(report.period_end)} /></label>
        <Button type="submit" className="justify-self-start">{busy ? 'Loading report…' : 'Load report'}</Button>
      </fieldset>
    </form>
    {message && <p role="status">{message}</p>}
    {report && <div className="space-y-3" aria-live="polite">
      <p className="text-sm text-muted-foreground">{report.population} {report.exclusions}</p>
      <p className="text-sm text-muted-foreground">
        {formatDateTime(report.period_start, undefined, { timeZone: 'UTC' })} to{' '}
        {formatDateTime(report.period_end, undefined, { timeZone: 'UTC' })} (end excluded, UTC).
        {' '}Backlog checked {formatDateTime(report.observed_at, undefined, { timeZone: 'UTC' })} UTC.
        {' '}Query {report.query_version}.
      </p>
      <dl className="grid gap-3 sm:grid-cols-2">
        <div><dt>Current unresolved cases</dt><dd className="font-semibold">{report.unresolved_cases}</dd></div>
        {report.oldest_unresolved_age_seconds !== null && <div>
          <dt>Oldest unresolved case</dt><dd>{hours(report.oldest_unresolved_age_seconds)} calendar hours</dd>
        </div>}
        <div><dt>First resolutions in period</dt><dd className="font-semibold">{report.first_resolved_cases}</dd></div>
        {report.first_resolved_cases > 0 && <div>
          <dt>Mean time to first resolution</dt>
          <dd>{hours(report.first_resolution_elapsed_seconds / report.first_resolved_cases)} calendar hours</dd>
        </div>}
        <div><dt>Reopened cases in period</dt><dd className="font-semibold">{report.reopened_cases}</dd></div>
        <div><dt>Reopening events in period</dt><dd>{report.reopened_transitions}</dd></div>
      </dl>
    </div>}
  </section>
}
