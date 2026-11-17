'use client'

import { type FormEvent, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import RecordCompletionRelease from './RecordCompletionRelease'
import RetentionReview from './RetentionReview'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { Button } from '@components/ui/button'
import { Input } from '@components/ui/input'
import { getAuthenticatedSessionIdentity } from '@/lib/auth/sessionScope'
import { formatDateTime } from '@/lib/format'
import {
  getRecordDecisionOptions,
  listAssignedRecordRequests,
  recordRequestDecision,
  uploadRecordRequestEvidence,
  type RecordRequestDecisionOptions,
  type RecordRequestOutcome,
  type RecordRequestReceipt,
} from '@services/studytomy/privacy'

type QueueState = { kind: 'loading' | 'unavailable' | 'error' } | {
  kind: 'ready'
  requests: RecordRequestReceipt[]
}

const outcomeLabels: Record<RecordRequestOutcome, string> = {
  approved_for_preparation: 'Approve for preparation',
  denied: 'Decline request',
}

export default function RecordRequestReviewQueue({ orgUuid }: { orgUuid: string }) {
  const session = useLHSession()
  const token = session?.data?.tokens?.access_token
  if (session?.status !== 'authenticated' || !token) {
    return <p role="status">Sign in to view assigned learning record reviews.</p>
  }
  const user = session.data?.user
  const identity = getAuthenticatedSessionIdentity(user?.id ?? user?.user_uuid, session.data)
  return <ReviewQueue key={JSON.stringify([identity, orgUuid])} orgUuid={orgUuid} token={token} />
}

function ReviewQueue({ orgUuid, token }: { orgUuid: string; token: string }) {
  const { i18n } = useTranslation()
  const [state, setState] = useState<QueueState>({ kind: 'loading' })
  const [refresh, setRefresh] = useState(0)
  const [selected, setSelected] = useState<string | null>(null)
  const [decided, setDecided] = useState(false)

  useEffect(() => {
    let pending: AbortController | undefined
    function clear() {
      pending?.abort()
      setState({ kind: 'loading' })
      setSelected(null)
    }
    async function read() {
      clear()
      if (document.visibilityState === 'hidden') return
      const controller = new AbortController()
      pending = controller
      try {
        const requests = await listAssignedRecordRequests(orgUuid, token, controller.signal)
        if (controller.signal.aborted) return
        setState(requests === null ? { kind: 'unavailable' } : { kind: 'ready', requests })
      } catch {
        if (!controller.signal.aborted) setState({ kind: 'error' })
      }
    }
    void read()
    document.addEventListener('visibilitychange', read)
    window.addEventListener('pagehide', clear)
    window.addEventListener('pageshow', read)
    return () => {
      pending?.abort()
      document.removeEventListener('visibilitychange', read)
      window.removeEventListener('pagehide', clear)
      window.removeEventListener('pageshow', read)
    }
  }, [orgUuid, token, refresh])

  return (
    <main aria-labelledby="record-reviews-title" className="mx-auto max-w-4xl space-y-5 px-4 py-6 sm:px-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 id="record-reviews-title" className="text-2xl font-semibold">Learning record reviews</h1>
        {state.kind !== 'unavailable' && (
          <Button variant="outline" className="min-h-11" disabled={state.kind === 'loading'} onClick={() => setRefresh((value) => value + 1)}>
            Refresh assigned requests
          </Button>
        )}
      </header>
      {state.kind === 'loading' && <p role="status">Loading assigned requests…</p>}
      {decided && <p role="status">Review decision recorded.</p>}
      {state.kind === 'unavailable' && <p role="alert">Learning record reviews are unavailable for this account.</p>}
      {state.kind === 'error' && <p role="alert">Unable to load assigned requests. Try refreshing the queue.</p>}
      {state.kind === 'ready' && (
        state.requests.length === 0 ? <p role="status">No pending requests are assigned to you.</p> : (
          <ul aria-label="Pending assigned requests" className="space-y-3">
            {state.requests.map((request) => (
              <li key={request.request_reference} className="rounded-lg border bg-white p-4">
                <p className="break-words font-medium">Request {request.request_reference}</p>
                <dl className="mt-2 grid gap-2 text-sm sm:grid-cols-2">
                  <div><dt>Received</dt><dd><time dateTime={request.received_at}>
                    {formatDateTime(request.received_at, i18n.language)}
                  </time></dd></div>
                  <div><dt>Review due</dt><dd><time dateTime={request.due_at}>
                    {formatDateTime(request.due_at, i18n.language)}
                  </time></dd></div>
                </dl>
                <Button type="button" variant="outline" className="mt-3 min-h-11"
                  aria-expanded={selected === request.request_reference}
                  onClick={() => {
                    setDecided(false)
                    setSelected((current) => current === request.request_reference ? null : request.request_reference)
                  }}>
                  {selected === request.request_reference ? 'Close review' : 'Review request'}
                </Button>
                {selected === request.request_reference && (
                  <DecisionForm orgUuid={orgUuid} token={token} reference={request.request_reference} onDecision={() => {
                    setDecided(true)
                    setRefresh((value) => value + 1)
                  }} />
                )}
              </li>
            ))}
          </ul>
        )
      )}
      {state.kind === 'ready' && <>
        <RecordCompletionRelease orgUuid={orgUuid} token={token} />
        <RetentionReview orgUuid={orgUuid} token={token} />
      </>}
    </main>
  )
}

function DecisionForm({ orgUuid, token, reference, onDecision }: {
  orgUuid: string; token: string; reference: string; onDecision: () => void
}) {
  const [options, setOptions] = useState<RecordRequestDecisionOptions>()
  const [outcome, setOutcome] = useState<RecordRequestOutcome | ''>('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [evidence, setEvidence] = useState('')

  useEffect(() => {
    const controller = new AbortController()
    getRecordDecisionOptions(orgUuid, token, reference, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setOptions(result) })
      .catch(() => { if (!controller.signal.aborted) setError('Decision options are unavailable. Refresh the queue to try again.') })
    return () => controller.abort()
  }, [orgUuid, token, reference])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || !outcome) return
    const fields = new FormData(event.currentTarget)
    const reason_code = String(fields.get('reason') ?? '')
    if (!options?.[outcome]?.includes(reason_code)) return
    setBusy(true)
    setError('')
    try {
      await recordRequestDecision(orgUuid, token, reference, {
        outcome, reason_code, evidence_reference: evidence.trim(),
      })
      onDecision()
    } catch {
      setError('The decision could not be confirmed. Retry the same decision or refresh the queue.')
    } finally {
      setBusy(false)
    }
  }

  if (!options) return error ? <p role="alert">{error}</p> : <p role="status">Loading decision options…</p>
  const outcomes = (Object.keys(outcomeLabels) as RecordRequestOutcome[]).filter((value) => options[value]?.length)
  if (outcomes.length === 0) return <p role="status">There are no available decisions for this request.</p>
  return (
    <div className="mt-4 space-y-4 border-t pt-4">
      <EvidenceUpload orgUuid={orgUuid} token={token} reference={reference} disabled={busy} onVerified={setEvidence} />
      <form onSubmit={submit} className="space-y-3">
      <fieldset disabled={busy} className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm font-medium">Decision
          <select required value={outcome} onChange={(event) => setOutcome(event.target.value as RecordRequestOutcome)}
            className="block min-h-11 w-full rounded-md border bg-background px-3">
            <option value="" disabled>Select a decision</option>
            {outcomes.map((value) => <option key={value} value={value}>{outcomeLabels[value]}</option>)}
          </select>
        </label>
        <label className="text-sm font-medium">Approved reason
          <select key={outcome} name="reason" required defaultValue="" disabled={!outcome}
            className="block min-h-11 w-full rounded-md border bg-background px-3">
            <option value="" disabled>Select a reason</option>
            {outcome && options[outcome]?.map((reason) => <option key={reason} value={reason}>{reason.replaceAll('_', ' ')}</option>)}
          </select>
        </label>
        <label className="text-sm font-medium">Evidence reference
          <Input name="evidence" value={evidence} onChange={(event) => setEvidence(event.target.value)}
            required pattern="ev_[A-Za-z0-9_\-]{16,80}" maxLength={83} autoComplete="off" spellCheck={false} />
        </label>
        <Button type="submit" disabled={!outcome} className="min-h-11 self-end justify-self-start">
          {busy ? 'Recording decision…' : 'Record decision'}
        </Button>
      </fieldset>
      {error && <p role="alert" className="text-sm text-red-800">{error}</p>}
      </form>
    </div>
  )
}

function EvidenceUpload({ orgUuid, token, reference, disabled, onVerified }: {
  orgUuid: string; token: string; reference: string; disabled: boolean; onVerified: (reference: string) => void
}) {
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<'verified' | 'failed'>()
  const key = useRef<string | null>(null)
  const pending = useRef<AbortController | null>(null)

  useEffect(() => () => pending.current?.abort(), [])

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || disabled) return
    const file = new FormData(event.currentTarget).get('file')
    if (!(file instanceof File) || file.size === 0) return
    const controller = new AbortController()
    pending.current = controller
    setBusy(true)
    setNotice(undefined)
    try {
      key.current ??= crypto.randomUUID()
      const uploaded = await uploadRecordRequestEvidence(orgUuid, token, reference, file, key.current, controller.signal)
      if (controller.signal.aborted) return
      onVerified(uploaded.evidence_reference)
      setNotice('verified')
    } catch {
      if (!controller.signal.aborted) setNotice('failed')
    } finally {
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  return (
    <form onSubmit={upload} className="space-y-2">
      <fieldset disabled={busy || disabled} className="flex flex-wrap items-end gap-3">
        <label className="min-w-0 grow text-sm font-medium">Private case evidence
          <Input name="file" type="file" required onChange={() => { key.current = null; setNotice(undefined) }} />
        </label>
        <Button type="submit" variant="outline" className="min-h-11">{busy ? 'Verifying evidence…' : 'Upload evidence'}</Button>
      </fieldset>
      {notice === 'verified' && <p role="status" className="text-sm">Evidence verified. The reference is filled in below.</p>}
      {notice === 'failed' && <p role="alert" className="text-sm text-red-800">Evidence could not be verified. Check the file and retry.</p>}
    </form>
  )
}
