'use client'

import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { useOrg } from '@components/Contexts/OrgContext'
import { Button } from '@components/ui/button'
import SupportOperatingReport from '@components/Studytomy/SupportOperatingReport'
import { getAuthenticatedSessionIdentity } from '@/lib/auth/sessionScope'
import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'

type StaffCase = {
  case_uuid: string
  topic: string
  status: string
  version: number
  allowed_transitions: string[]
}
type Queue = { cases: StaffCase[]; next_cursor: string | null }
type QueueView = 'assigned' | 'recoverable'

const labels = new Map([
  ['assigned', 'Assigned'],
  ['awaiting_information', 'Awaiting information'],
  ['under_review', 'Under review'],
  ['resolved', 'Resolved'],
  ['closed', 'Closed'],
])

export default function SupportStaffWorkspace() {
  const org = useOrg()
  const session = useLHSession()
  const token = session?.data?.tokens?.access_token
  const user = session?.data?.user
  if (!org?.org_uuid || session?.status !== 'authenticated' || !token) {
    return <p role="status">Sign in with an authorized support account.</p>
  }
  const identity = getAuthenticatedSessionIdentity(user?.id ?? user?.user_uuid, session.data)
  const url = `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(org.org_uuid)}/support/staff/cases`
  return <div key={JSON.stringify([org.org_uuid, identity])} className="space-y-5">
    <StaffQueue url={url} token={token} />
    <SupportOperatingReport orgUuid={org.org_uuid} token={token} />
  </div>
}

function StaffQueue({ url, token }: { url: string; token: string }) {
  const [queue, setQueue] = useState<Queue | null>(null)
  const [view, setView] = useState<QueueView>('assigned')
  const [cursor, setCursor] = useState<string | null>(null)
  const [reload, setReload] = useState(0)
  const [message, setMessage] = useState('Loading support cases…')

  useEffect(() => {
    const controller = new AbortController()
    const query = new URLSearchParams({ view })
    if (cursor) query.set('after', cursor)
    const path = `${url}?${query}`
    secureFetch(path, { ...RequestBodyWithAuthHeader('GET', null, null, token), redirect: 'error', signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error('Unavailable')
        const result: Queue = await response.json()
        if (!controller.signal.aborted) { setQueue(result); setMessage('') }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setQueue(null)
          setMessage('The assigned support queue is unavailable.')
        }
      })
    return () => controller.abort()
  }, [url, token, cursor, reload, view])

  function load(after: string | null) {
    setQueue(null)
    setMessage('Loading support cases…')
    setCursor(after)
    setReload((value) => value + 1)
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" aria-pressed={view === 'assigned'} onClick={() => { setView('assigned'); load(null) }}>Assigned to me</Button>
        <Button variant="outline" aria-pressed={view === 'recoverable'} onClick={() => { setView('recoverable'); load(null) }}>Cases needing an owner</Button>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={() => load(null)}>{cursor ? 'Back to first page' : 'Refresh queue'}</Button>
        {queue?.next_cursor && <Button variant="outline" onClick={() => load(queue.next_cursor)}>Next page</Button>}
      </div>
      <p role="status">{message || (queue?.cases.length === 0 ? (view === 'assigned' ? 'No open cases assigned to you.' : 'No cases need a new owner.') : '')}</p>
      <ul className="divide-y divide-slate-200">
        {queue?.cases.map((item) => (
          <SupportCaseRow key={`${item.case_uuid}:${item.version}`} item={item} url={url} token={token} view={view} onUpdated={(updated) => {
            setQueue((current) => current && {
              ...current,
              cases: current.cases.flatMap((entry) => entry.case_uuid === updated.case_uuid
                ? updated.status === 'closed' || view === 'recoverable' ? [] : [updated]
                : [entry]),
            })
            setMessage(view === 'recoverable'
              ? 'Case recovered. It now appears under Assigned to me.'
              : `Case updated to ${labels.get(updated.status) ?? updated.status}.`)
          }} onHandedOff={(reference) => {
            setQueue((current) => current && {
              ...current, cases: current.cases.filter((entry) => entry.case_uuid !== reference),
            })
            setMessage('Case handed off to the approved alternate.')
          }} />
        ))}
      </ul>
    </div>
  )
}

function SupportCaseRow({ item, url, token, view, onUpdated, onHandedOff }: {
  item: StaffCase; url: string; token: string; view: QueueView;
  onUpdated: (_item: StaffCase) => void; onHandedOff: (_reference: string) => void
}) {
  const [choice, setChoice] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [confirmHandoff, setConfirmHandoff] = useState(false)
  const pending = useRef<AbortController | null>(null)
  useEffect(() => () => pending.current?.abort(), [])

  async function update(action: 'transition' | 'handoff' | 'recover') {
    if (pending.current || (action === 'transition' && !item.allowed_transitions.includes(choice))) return
    const controller = new AbortController()
    pending.current = controller
    setBusy(true)
    setError('')
    try {
      const payload = action === 'transition'
        ? { expected_version: item.version, next_status: choice }
        : { expected_version: item.version }
      const response = await secureFetch(`${url}/${encodeURIComponent(item.case_uuid)}/${action}`, {
        ...RequestBodyWithAuthHeader('POST', payload, null, token),
        redirect: 'error', signal: controller.signal,
      })
      if (!response.ok) throw new Error('Update unavailable')
      if (action === 'handoff') {
        if (!controller.signal.aborted) onHandedOff(item.case_uuid)
      } else {
        const next: StaffCase = await response.json()
        if (!controller.signal.aborted) onUpdated(next)
      }
    } catch {
      if (!controller.signal.aborted) setError('The update could not be confirmed. Refresh the queue before retrying.')
    } finally {
      pending.current = null
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  return (
    <li className="space-y-3 py-4">
      <h2 className="font-semibold">{item.topic}</h2>
      <p className="break-all text-sm text-slate-600">{item.case_uuid}</p>
      <p>{labels.get(item.status) ?? 'Status unavailable'}</p>
      {item.allowed_transitions.length > 0 && (
        <form onSubmit={(event: FormEvent<HTMLFormElement>) => { event.preventDefault(); void update('transition') }} className="flex flex-wrap items-end gap-2">
          <label className="space-y-1">
            <span className="block text-sm font-medium">Next status</span>
            <select className="rounded-md border border-slate-400 bg-white p-2" required value={choice}
              disabled={busy} onChange={(event) => setChoice(event.target.value)}>
              <option value="">Choose a status</option>
              {item.allowed_transitions.map((status) => <option key={status} value={status}>{labels.get(status) ?? status}</option>)}
            </select>
          </label>
          <Button type="submit" disabled={busy || !choice}>{busy ? 'Updating…' : 'Update case'}</Button>
        </form>
      )}
      {view === 'recoverable' && <Button type="button" disabled={busy} onClick={() => void update('recover')}>Recover case</Button>}
      {view === 'assigned' && item.status !== 'closed' && (confirmHandoff ? (
        <div className="flex flex-wrap items-center gap-2">
          <p>The approved alternate will take over this case.</p>
          <Button type="button" disabled={busy} onClick={() => void update('handoff')}>Confirm handoff</Button>
          <Button type="button" variant="outline" disabled={busy} onClick={() => setConfirmHandoff(false)}>Cancel</Button>
        </div>
      ) : (
        <Button type="button" variant="outline" disabled={busy} onClick={() => setConfirmHandoff(true)}>Hand off case</Button>
      ))}
      {error && <p role="alert">{error}</p>}
    </li>
  )
}
