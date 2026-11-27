'use client'

import { type FormEvent, useEffect, useRef, useState } from 'react'
import { Button } from '@components/ui/button'
import { Input } from '@components/ui/input'
import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'

type Inventory = {
  case_uuid: string
  case_status: string
  categories: { category: string; narrative_records: number; file_records: number }[]
  active_holds: string[]
  hold_reason_codes: string[]
  release_reason_codes: string[]
}

const casePattern = '[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'
const caseReference = new RegExp(`^${casePattern}$`)
const validReason = /^[a-z][a-z0-9_]{0,63}$/

export default function SafeguardingRetentionReview({ orgUuid, token }: { orgUuid: string; token: string }) {
  const [inventory, setInventory] = useState<Inventory>()
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ text: string; error?: boolean }>()
  const running = useRef<AbortController | null>(null)
  const holdAttempt = useRef<{ signature: string; uuid: string } | null>(null)

  useEffect(() => () => {
    running.current?.abort()
    running.current = null
  }, [orgUuid, token])

  async function submit(reference: string, action?: { hold: string; release: boolean; body: object }) {
    if (running.current) return
    const controller = new AbortController()
    const { signal } = controller
    running.current = controller
    setBusy(true)
    setMessage(undefined)
    setInventory(undefined)
    const base = `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/safeguarding/cases/${encodeURIComponent(reference)}`
    let saved = false
    try {
      if (action) {
        const path = `${base}/retention-holds/${encodeURIComponent(action.hold)}${action.release ? '/release' : ''}`
        const response = await secureFetch(path, {
          ...RequestBodyWithAuthHeader('PUT', action.body, null, token), signal, redirect: 'error',
        })
        if (!response.ok) {
          if (response.status === 409) throw new Error('The hold action conflicts with current policy or case state. Reload the inventory.')
          throw new Error('The hold action is unavailable for this case.')
        }
        saved = true
        holdAttempt.current = null
      }
      const response = await secureFetch(`${base}/retention-inventory`, {
        ...RequestBodyWithAuthHeader('GET', null, null, token), signal, redirect: 'error',
      })
      if (!response.ok) throw new Error('The retention inventory is unavailable for this case.')
      const result: Inventory = await response.json()
      if (result.case_uuid !== reference) throw new Error('Unexpected case reference.')
      if (!signal.aborted) {
        setInventory(result)
        if (action) setMessage({ text: action.release ? 'Hold released.' : `Hold recorded. Reference ${action.hold}` })
      }
    } catch (error) {
      if (!signal.aborted) setMessage({ error: true, text: saved
        ? 'The action was recorded. Load the inventory again to see the change.'
        : error instanceof Error ? error.message : 'Retention review unavailable.' })
    } finally {
      if (running.current === controller) running.current = null
      if (!running.current) setBusy(false)
    }
  }

  function lookup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const reference = String(new FormData(event.currentTarget).get('case') ?? '').trim().toLowerCase()
    if (caseReference.test(reference)) void submit(reference)
  }

  function update(event: FormEvent<HTMLFormElement>, release: boolean) {
    event.preventDefault()
    if (!inventory || running.current) return
    const form = new FormData(event.currentTarget)
    const reason_code = String(form.get('reason') ?? '').trim()
    if (!validReason.test(reason_code)) return
    if (release) {
      const hold = String(form.get('hold') ?? '')
      if (!inventory.active_holds.includes(hold)) return
      void submit(inventory.case_uuid, { hold, release, body: { reason_code } })
      return
    }
    const date = new Date(String(form.get('review_due') ?? ''))
    if (!Number.isFinite(date.getTime()) || date.getTime() <= Date.now()) {
      setMessage({ text: 'Choose a review date in the future.', error: true })
      return
    }
    const body = { reason_code, review_due_at: date.toISOString() }
    const signature = JSON.stringify([inventory.case_uuid, body])
    if (holdAttempt.current?.signature !== signature) {
      holdAttempt.current = { signature, uuid: crypto.randomUUID() }
    }
    void submit(inventory.case_uuid, { hold: holdAttempt.current.uuid, release, body })
  }

  return <details className="space-y-3 rounded-lg border bg-white p-4">
    <summary className="cursor-pointer font-semibold">Safeguarding retention holds</summary>
    <form onSubmit={lookup} className="flex flex-wrap items-end gap-2">
      <label className="grid gap-1 text-sm">Safeguarding case reference
        <Input name="case" required maxLength={36} pattern={casePattern} autoComplete="off" disabled={busy} />
      </label>
      <Button type="submit" variant="outline" disabled={busy}>Load safeguarding inventory</Button>
    </form>
    {busy && <p role="status" className="text-sm">Checking retention…</p>}
    {message && <p role={message.error ? 'alert' : 'status'} className="break-words text-sm">{message.text}</p>}
    {inventory && <div className="space-y-3">
      <p className="break-all text-sm">{inventory.case_uuid} · {inventory.case_status.replaceAll('_', ' ')}</p>
      <p className="text-sm">Active holds {inventory.active_holds.length}</p>
      {inventory.categories.length > 0 && <ul className="space-y-1 text-sm">
        {inventory.categories.map((category) => <li key={category.category}>
          {category.category} · Reports {category.narrative_records} · Files {category.file_records}
        </li>)}
      </ul>}
      {inventory.hold_reason_codes.length > 0 && <form className="flex flex-wrap items-end gap-2 border-t pt-3" onSubmit={(event) => update(event, false)}>
        <label className="grid gap-1 text-sm">Policy-approved hold reason
          <select name="reason" required defaultValue="" className="h-9 rounded border bg-background px-2">
            <option value="" disabled>Choose a reason</option>
            {inventory.hold_reason_codes.map((reason) => <option key={reason} value={reason}>{reason.replaceAll('_', ' ')}</option>)}
          </select>
        </label>
        <label className="grid gap-1 text-sm">Review due (local time)
          <Input name="review_due" type="datetime-local" required />
        </label>
        <Button type="submit" variant="outline" disabled={busy}>Place hold</Button>
      </form>}
      {inventory.active_holds.length > 0 && inventory.release_reason_codes.length > 0 && <form className="flex flex-wrap items-end gap-2 border-t pt-3"
        onSubmit={(event) => update(event, true)}>
        <label className="grid gap-1 text-sm">Active hold
          <select name="hold" required defaultValue="" className="h-9 rounded border bg-background px-2">
            <option value="" disabled>Choose a hold</option>
            {inventory.active_holds.map((hold) => <option key={hold} value={hold}>{hold}</option>)}
          </select>
        </label>
        <label className="grid gap-1 text-sm">Policy-approved release reason
          <select name="reason" required defaultValue="" className="h-9 rounded border bg-background px-2">
            <option value="" disabled>Choose a reason</option>
            {inventory.release_reason_codes.map((reason) => <option key={reason} value={reason}>{reason.replaceAll('_', ' ')}</option>)}
          </select>
        </label>
        <Button type="submit" variant="outline" disabled={busy}>Release hold</Button>
      </form>}
    </div>}
  </details>
}
