'use client'

import Link from 'next/link'
import { type FormEvent, useEffect, useRef, useState } from 'react'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { Button } from '@components/ui/button'
import { getAuthenticatedSessionIdentity } from '@/lib/auth/sessionScope'
import { getSafeguardingReportStatus, submitSafeguardingReport } from '@services/studytomy/safeguarding'

export default function SafeguardingReportForm({
  orgUuid, reportingAvailable,
}: { orgUuid: string; reportingAvailable: boolean }) {
  const session = useLHSession()
  const token = session?.data?.tokens?.access_token
  if (session?.status === 'loading') return <p role="status">Checking your sessionâ€¦</p>
  if (session?.status !== 'authenticated' || !token) {
    return <Link className="font-semibold underline" href="/login?redirect=/help/report">{reportingAvailable ? 'Sign in to send or check a report' : 'Sign in to check a report'}</Link>
  }
  const user = session.data?.user
  const identity = getAuthenticatedSessionIdentity(user?.id ?? user?.user_uuid, session.data)
  return (
    <div key={JSON.stringify([orgUuid, identity])} className="space-y-8">
      {reportingAvailable && <ReportForm orgUuid={orgUuid} token={token} />}
      <ReportStatus orgUuid={orgUuid} token={token} />
    </div>
  )
}

function ReportForm({ orgUuid, token }: { orgUuid: string; token: string }) {
  const [narrative, setNarrative] = useState('')
  const [reference, setReference] = useState('')
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  const attempt = useRef<string | null>(null)
  const pending = useRef<AbortController | null>(null)
  useEffect(() => () => pending.current?.abort(), [])

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending.current || !narrative.trim() || reference) return
    const controller = new AbortController()
    pending.current = controller
    setBusy(true)
    setFailed(false)
    try {
      attempt.current ??= crypto.randomUUID()
      const receipt = await submitSafeguardingReport(orgUuid, token, attempt.current, narrative, controller.signal)
      if (!controller.signal.aborted) {
        setReference(receipt.case_uuid)
        setNarrative('')
      }
    } catch {
      if (!controller.signal.aborted) setFailed(true)
    } finally {
      pending.current = null
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  if (reference) return (
    <div role="status" className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-emerald-950">
      <p className="font-semibold">Your report was received.</p>
      <p className="mt-1 break-all">Keep this case reference somewhere safe. {reference}</p>
    </div>
  )

  return (
    <form className="space-y-4" onSubmit={send}>
      <label className="block space-y-2">
        <span className="block font-semibold">What happened?</span>
        <textarea
          className="block min-h-36 w-full rounded-lg border border-slate-400 bg-white p-3 text-slate-950 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
          value={narrative}
          onChange={(event) => {
            attempt.current = null
            setFailed(false)
            setNarrative(event.target.value)
          }}
          autoComplete="off"
          maxLength={8000}
          required
          disabled={busy}
        />
      </label>
      {failed && <p role="alert">We couldnâ€™t confirm that your report was received. Try again or use the contact instructions above.</p>}
      <Button type="submit" disabled={busy || !narrative.trim()}>{busy ? 'Sending reportâ€¦' : 'Send private report'}</Button>
    </form>
  )
}


const reportStatuses = new Map([
  ['acknowledged', 'Received'],
  ['assigned', 'Assigned for review'],
  ['awaiting_information', 'Awaiting information'],
  ['under_review', 'Under review'],
  ['resolved', 'Resolved'],
  ['closed', 'Closed'],
])

function ReportStatus({ orgUuid, token }: { orgUuid: string; token: string }) {
  const [reference, setReference] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const pending = useRef<AbortController | null>(null)
  useEffect(() => () => pending.current?.abort(), [])

  async function check(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending.current || !reference.trim()) return
    const valid = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(reference.trim())
    if (!valid) {
      setMessage('Enter the full case reference from your receipt.')
      return
    }
    const controller = new AbortController()
    pending.current = controller
    setBusy(true)
    setMessage('')
    try {
      const status = await getSafeguardingReportStatus(orgUuid, token, reference.trim(), controller.signal)
      if (!controller.signal.aborted) {
        setMessage(status ? reportStatuses.get(status) ?? 'Status unavailable.' : 'Report not found.')
      }
    } catch {
      if (!controller.signal.aborted) setMessage('Status is unavailable. Try again later.')
    } finally {
      pending.current = null
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  return (
    <section className="space-y-3 border-t border-slate-200 pt-6" aria-labelledby="report-status-heading">
      <h2 id="report-status-heading" className="text-lg font-semibold">Check a report</h2>
      <form className="space-y-3" onSubmit={check}>
        <label className="block space-y-2">
          <span className="block text-sm font-medium">Case reference</span>
          <input
            className="block w-full rounded-lg border border-slate-400 bg-white p-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2"
            autoComplete="off"
            maxLength={36}
            value={reference}
            onChange={(event) => { setReference(event.target.value); setMessage('') }}
            aria-describedby="case-status-message"
            disabled={busy}
            required
          />
        </label>
        <Button type="submit" variant="outline" disabled={busy || !reference.trim()}>
          {busy ? 'Checking…' : 'Check status'}
        </Button>
        <p id="case-status-message" role="status">{message}</p>
      </form>
    </section>
  )
}
