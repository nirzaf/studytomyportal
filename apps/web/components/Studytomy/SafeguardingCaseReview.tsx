'use client'

import { type FormEvent, useEffect, useRef, useState } from 'react'
import { Button } from '@components/ui/button'
import { Input } from '@components/ui/input'
import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'
import SafeguardingCaseEvidence from './SafeguardingCaseEvidence'

type Summary = {
  case_uuid: string
  status: string
  version: number
  assigned_to_me: boolean
  can_assign: boolean
  allowed_transitions: string[]
}
const referencePattern = '[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'

export default function SafeguardingCaseReview({ orgUuid, token }: { orgUuid: string; token: string }) {
  const [summary, setSummary] = useState<Summary>()
  const [narrative, setNarrative] = useState<string>()
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ text: string; error?: boolean }>()
  const active = useRef<AbortController | null>(null)

  useEffect(() => {
    function abort() {
      active.current?.abort()
      active.current = null
    }
    function clear() {
      abort()
      setSummary(undefined)
      setNarrative(undefined)
      setBusy(false)
      setMessage(undefined)
    }
    function hide() {
      if (document.visibilityState === 'hidden') clear()
    }
    document.addEventListener('visibilitychange', hide)
    window.addEventListener('pagehide', clear)
    return () => {
      abort()
      document.removeEventListener('visibilitychange', hide)
      window.removeEventListener('pagehide', clear)
    }
  }, [orgUuid, token])

  async function request<T>(caseUuid: string, suffix: string, payload: object | null, show: (_result: T) => void) {
    if (active.current) return
    const pending = new AbortController()
    active.current = pending
    setBusy(true)
    setMessage(undefined)
    try {
      const path = `studytomy/orgs/${encodeURIComponent(orgUuid)}/safeguarding/cases/${encodeURIComponent(caseUuid)}`
      const response = await secureFetch(getAPIUrl() + path + suffix, {
        ...RequestBodyWithAuthHeader(payload ? 'POST' : 'GET', payload, null, token),
        redirect: 'error', signal: pending.signal,
      })
      if (!response.ok) {
        if (response.status === 409) throw new Error('The case changed or the operation is unavailable. Reload this case.')
        throw new Error('The case or action is unavailable under your current access.')
      }
      const data: T = await response.json()
      if (!pending.signal.aborted) show(data)
    } catch (error) {
      if (pending.signal.aborted) return
      setSummary(undefined)
      setNarrative(undefined)
      setMessage({ error: true, text: error instanceof Error ? error.message : 'Case review unavailable.' })
    } finally {
      if (active.current === pending) active.current = null
      if (!active.current) setBusy(false)
    }
  }

  function open(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (active.current) return
    const reference = String(new FormData(event.currentTarget).get('case') ?? '').trim().toLowerCase()
    if (!new RegExp(`^${referencePattern}$`).test(reference)) return
    setSummary(undefined)
    setNarrative(undefined)
    void request<Summary>(reference, '', null, (result) => {
      if (result.case_uuid !== reference) throw new Error('Unexpected case reference.')
      setSummary(result)
    })
  }

  function mutate(event: FormEvent<HTMLFormElement>, kind: 'assignment' | 'transition') {
    event.preventDefault()
    if (!summary || active.current) return
    const form = new FormData(event.currentTarget)
    const data = kind === 'assignment'
      ? { next_owner_uuid: String(form.get('owner') ?? '').trim() || null }
      : { next_status: form.get('status') }
    setNarrative(undefined)
    void request<Summary>(summary.case_uuid, `/${kind}`, { ...data, expected_version: summary.version }, (result) => {
      if (result.case_uuid !== summary.case_uuid) throw new Error('Unexpected case reference.')
      setSummary(result.assigned_to_me ? result : undefined)
      setMessage({ text: result.assigned_to_me ? 'Case updated.' : 'Case assigned to the selected reviewer.' })
    })
  }

  return <details className="space-y-3 rounded-lg border p-4" onToggle={(event) => {
    if (!event.currentTarget.open) {
      active.current?.abort()
      active.current = null
      setSummary(undefined)
      setNarrative(undefined)
      setMessage(undefined)
      setBusy(false)
    }
  }}>
    <summary className="cursor-pointer font-semibold">Safeguarding case review</summary>
    <form onSubmit={open} className="flex flex-wrap items-end gap-2">
      <label className="grid gap-1 text-sm">Case reference
        <Input name="case" required maxLength={36} pattern={referencePattern} autoComplete="off" />
      </label>
      <Button type="submit" variant="outline" disabled={busy}>Open safeguarding case</Button>
    </form>
    {busy && <p role="status">Contacting the case service…</p>}
    {message && <p role={message.error ? 'alert' : 'status'}>{message.text}</p>}
    {summary && <>
      <p className="break-all text-sm">{summary.case_uuid} · {summary.status.replaceAll('_', ' ')} · Version {summary.version}</p>
      <Button type="button" variant="outline" disabled={busy} onClick={() => {
        if (narrative !== undefined) setNarrative(undefined)
        else void request<{ narrative: string }>(summary.case_uuid, '/narrative', null, (result) => setNarrative(result.narrative))
      }}>{narrative === undefined ? 'Show confidential report' : 'Hide confidential report'}</Button>
      {narrative !== undefined && <p className="whitespace-pre-wrap break-words rounded border p-3 text-sm">{narrative}</p>}
      {summary.can_assign && <form onSubmit={(event) => mutate(event, 'assignment')} className="flex flex-wrap items-end gap-2">
        <label className="grid gap-1 text-sm">Independent reviewer reference
          <Input name="owner" required={summary.assigned_to_me} maxLength={100}
            pattern={String.raw`user_[a-zA-Z0-9_\-]{1,95}`} autoComplete="off" />
        </label>
        <Button type="submit" variant="outline" disabled={busy}>{summary.assigned_to_me ? 'Transfer case' : 'Assign case'}</Button>
      </form>}
      {summary.allowed_transitions.length > 0 && <form key={summary.version}
        onSubmit={(event) => mutate(event, 'transition')} className="flex flex-wrap items-end gap-2">
        <label className="grid gap-1 text-sm">Next case status
          <select name="status" defaultValue="" required className="rounded border bg-background p-2">
            <option value="" disabled>Choose a status</option>
            {summary.allowed_transitions.map((status) => <option value={status} key={status}>{status.replaceAll('_', ' ')}</option>)}
          </select>
        </label>
        <Button type="submit" disabled={busy}>Update safeguarding case</Button>
      </form>}
      <SafeguardingCaseEvidence key={summary.case_uuid} orgUuid={orgUuid} token={token}
        caseUuid={summary.case_uuid} canAttach={summary.assigned_to_me && !['resolved', 'closed'].includes(summary.status)} />
    </>}
  </details>
}
