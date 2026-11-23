'use client'

import { type FormEvent, useEffect, useRef, useState } from 'react'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { useOrg } from '@components/Contexts/OrgContext'
import { Button } from '@components/ui/button'
import { Input } from '@components/ui/input'
import { getAuthenticatedSessionIdentity } from '@/lib/auth/sessionScope'
import {
  listSafeguardingTriage,
  submitSafeguardingTriage,
  type SafeguardingTriageItem,
} from '@services/studytomy/safeguarding'

const caseReference = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i
const userReference = /^user_[a-zA-Z0-9_-]{1,95}$/
const pageSize = 100

export default function SafeguardingTriageWorkspace() {
  const org = useOrg()
  const session = useLHSession()
  const token = session?.data?.tokens?.access_token
  const user = session?.data?.user
  const identity = getAuthenticatedSessionIdentity(user?.id ?? user?.user_uuid, session?.data)
  if (!org?.org_uuid || session?.status !== 'authenticated' || !token || !identity) {
    return <p role="status">Triage is available to authorized safeguarding staff.</p>
  }
  return <TriageQueue key={JSON.stringify([org.org_uuid, identity])} orgUuid={org.org_uuid} token={token} />
}

function TriageQueue({ orgUuid, token }: { orgUuid: string; token: string }) {
  const [items, setItems] = useState<SafeguardingTriageItem[]>([])
  const [selected, setSelected] = useState<SafeguardingTriageItem | null>(null)
  const [cursor, setCursor] = useState<string | null>(null)
  const [reload, setReload] = useState(0)
  const [page, setPage] = useState<'loading' | 'available' | 'denied' | 'failed'>('loading')
  const [result, setResult] = useState('')
  useEffect(() => {
    const pending = new AbortController()
    listSafeguardingTriage(orgUuid, token, cursor, pending.signal)
      .then((data) => {
        if (pending.signal.aborted) return
        setItems(data ?? [])
        setPage(data === null ? 'denied' : 'available')
      })
      .catch(() => {
        if (!pending.signal.aborted) setPage('failed')
      })
    return () => pending.abort()
  }, [orgUuid, token, cursor, reload])

  function load(after: string | null) {
    setPage('loading')
    setSelected(null)
    setResult('')
    setCursor(after)
    setReload((count) => count + 1)
  }

  if (page === 'loading') return <p role="status">Loading unassigned cases…</p>
  if (page === 'denied') return <p role="status">Triage is available to authorized safeguarding staff.</p>
  if (page === 'failed') return (
    <div className="space-y-3">
      <p role="alert">The triage queue is unavailable.</p>
      <Button variant="outline" onClick={() => load(cursor)}>Try again</Button>
    </div>
  )

  return (
    <div className="space-y-5">
      <p role="status">{result}</p>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={() => load(null)}>{cursor ? 'Back to first page' : 'Refresh queue'}</Button>
        {items.length === pageSize && (
          <Button variant="outline" onClick={() => load(items[items.length - 1].case_uuid)}>Next page</Button>
        )}
      </div>
      {items.length === 0 && <p>No unassigned cases on this page.</p>}
      <ul className="divide-y divide-slate-200">
        {items.map((item) => (
          <li key={item.case_uuid} className="flex flex-wrap items-center justify-between gap-2 py-3">
            <span className="break-all">{item.case_uuid}</span>
            <Button variant="outline" aria-pressed={selected?.case_uuid === item.case_uuid} onClick={() => setSelected(item)}>
              Select case
            </Button>
          </li>
        ))}
      </ul>
      {selected && (
        <TriageForm
          key={`${selected.case_uuid}:${selected.version}`}
          item={selected} orgUuid={orgUuid} token={token}
          onRecorded={(updated) => {
            setItems((current) => current.flatMap((item) => item.case_uuid === updated.case_uuid
              ? updated.status === 'assigned' ? [] : [updated]
              : [item]))
            setSelected(null)
            setResult(updated.status === 'assigned' ? 'Triage recorded. An independent reviewer was assigned.' : 'Triage recorded. The case remains unassigned.')
          }}
        />
      )}
    </div>
  )
}

function TriageForm({ item, orgUuid, token, onRecorded }: {
  item: SafeguardingTriageItem
  orgUuid: string
  token: string
  onRecorded: (_item: SafeguardingTriageItem & { status: string }) => void
}) {
  const [verification, setVerification] = useState('')
  const [subjects, setSubjects] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const pending = useRef<AbortController | null>(null)
  useEffect(() => () => pending.current?.abort(), [])

  async function record(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending.current) return
    const references = [...new Set(subjects.split(/[,\s]+/).filter(Boolean))]
    if (!caseReference.test(verification.trim()) || references.length > 10 || references.some((reference) => !userReference.test(reference))) {
      setMessage('Enter a valid verification reference and up to 10 organization user references.')
      return
    }
    const controller = new AbortController()
    pending.current = controller
    setBusy(true)
    setMessage('')
    try {
      const updated = await submitSafeguardingTriage(orgUuid, token, item, verification.trim(), references, controller.signal)
      if (!controller.signal.aborted) onRecorded(updated)
    } catch {
      if (!controller.signal.aborted) setMessage('Triage could not be confirmed. Refresh the queue before trying again.')
    } finally {
      pending.current = null
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  return (
    <form className="space-y-4 border-t border-slate-200 pt-5" onSubmit={record}>
      <h2 className="text-lg font-semibold">Record triage</h2>
      <p className="break-all">Case {item.case_uuid}</p>
      <label className="block space-y-2">
        <span className="block font-medium">Independent verification reference</span>
        <Input required autoComplete="off" maxLength={36} disabled={busy} value={verification} onChange={(event) => setVerification(event.target.value)} />
      </label>
      <label className="block space-y-2">
        <span className="block font-medium">Verified subject user references</span>
        <Input autoComplete="off" maxLength={1000} disabled={busy} value={subjects} onChange={(event) => setSubjects(event.target.value)} />
      </label>
      <p className="text-sm text-slate-600">Separate references with commas. Leave this blank only if independent verification identified no subjects.</p>
      {message && <p role="alert">{message}</p>}
      <Button type="submit" disabled={busy}>{busy ? 'Recording…' : 'Record triage'}</Button>
    </form>
  )
}
