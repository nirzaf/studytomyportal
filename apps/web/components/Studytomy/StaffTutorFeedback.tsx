'use client'

import { type FormEvent, useEffect, useRef, useState } from 'react'
import { Button } from '@components/ui/button'
import { Input } from '@components/ui/input'
import { AdaptiveRequestError, type AdaptiveRequest } from '@services/studytomy/adaptive'

type Feedback = {
  turn_uuid: string
  session_uuid: string
  issue_code: 'inaccurate' | 'unclear' | 'unsafe'
  received_at: string
  disposition: 'confirmed' | 'dismissed' | 'escalated' | null
  review_reference: string | null
}

const issueLabels = { inaccurate: 'Inaccurate', unclear: 'Unclear', unsafe: 'Unsafe' }
const dispositions = { confirmed: 'Confirmed', dismissed: 'Dismissed', escalated: 'Escalated' }
const PAGE_SIZE = 20

export default function StaffTutorFeedback({ client, courseUuid }: {
  client: AdaptiveRequest
  courseUuid: string
}) {
  const [loaded, setLoaded] = useState<{
    client: AdaptiveRequest; courseUuid: string; learner: string; offset: number; rows: Feedback[]
  }>()
  const page = loaded?.client === client && loaded.courseUuid === courseUuid ? loaded : undefined
  const [work, setWork] = useState<{ client: AdaptiveRequest; courseUuid: string; saving: boolean }>()
  const busy = work?.client === client && work.courseUuid === courseUuid
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const active = useRef<AbortController | null>(null)

  useEffect(() => {
    function abort() {
      active.current?.abort()
      active.current = null
    }
    function clear() {
      abort()
      setLoaded(undefined)
      setWork(undefined)
      setError('')
      setNotice('')
    }
    function hide() {
      if (document.visibilityState === 'hidden') clear()
    }
    window.addEventListener('pagehide', clear)
    document.addEventListener('visibilitychange', hide)
    return () => {
      abort()
      window.removeEventListener('pagehide', clear)
      document.removeEventListener('visibilitychange', hide)
    }
  }, [client, courseUuid])

  async function load(learner: string, offset: number, review?: {
    turn_uuid: string; session_uuid: string; disposition: string; evidence_reference: string
  }) {
    if (active.current) return
    const pending = new AbortController()
    active.current = pending
    setWork({ client, courseUuid, saving: Boolean(review) })
    setError('')
    setNotice('')
    if (!review) setLoaded(undefined)
    const path = `staff/learners/${encodeURIComponent(learner)}/courses/${encodeURIComponent(courseUuid)}/tutor-feedback`
    try {
      const rows = review && page
        ? await client<Feedback>(path, 'POST', review, undefined, pending.signal).then((receipt) =>
          page.rows.map((entry) => entry.turn_uuid === review.turn_uuid && entry.session_uuid === review.session_uuid
            ? receipt : entry))
        : await client<Feedback[]>(`${path}?offset=${offset}`, 'GET', undefined, undefined, pending.signal)
      if (pending.signal.aborted) return
      setLoaded({ client, courseUuid, learner, offset, rows })
      if (review) setNotice('Review recorded.')
    } catch (cause) {
      if (pending.signal.aborted) return
      setLoaded(undefined)
      setError(cause instanceof AdaptiveRequestError && cause.status === 409
        ? 'This feedback already has a review. Load the learner’s feedback again.'
        : 'Tutor feedback could not be loaded or reviewed for this learner and course.')
    } finally {
      if (active.current === pending) {
        active.current = null
        setWork(undefined)
      }
    }
  }

  function open(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const learner = new FormData(event.currentTarget).get('learner')
    if (typeof learner === 'string' && learner.trim()) void load(learner.trim(), 0)
  }

  function submit(event: FormEvent<HTMLFormElement>, feedback: Feedback) {
    event.preventDefault()
    if (!page) return
    const fields = new FormData(event.currentTarget)
    const disposition = fields.get('disposition')
    const evidence = fields.get('evidence')
    if (typeof disposition !== 'string' || typeof evidence !== 'string') return
    void load(page.learner, page.offset, {
      turn_uuid: feedback.turn_uuid, session_uuid: feedback.session_uuid,
      disposition, evidence_reference: evidence.trim(),
    })
  }

  return <details className="rounded-lg border bg-card p-4 sm:p-6">
    <summary className="cursor-pointer font-semibold text-foreground">Tutor feedback review</summary>
    <form className="mt-4 flex flex-wrap items-end gap-2" onSubmit={open}>
      <label className="grid gap-1 text-sm">
        Learner reference
        <Input name="learner" required maxLength={100} />
      </label>
      <Button type="submit" variant="outline" disabled={busy}>Load feedback</Button>
    </form>
    {busy && <p role="status" className="mt-3 text-sm">
      {work.saving ? 'Recording review…' : 'Loading tutor feedback…'}
    </p>}
    {error && <p role="alert" className="mt-3 text-sm text-destructive">{error}</p>}
    {notice && <p role="status" className="mt-3 text-sm">{notice}</p>}
    {page && !page.rows.length && <p className="mt-3 text-sm">No feedback on this page.</p>}
    {!!page?.rows.length && <ul className="mt-4 space-y-3">
      {page.rows.map((entry) => <li key={`${entry.session_uuid}:${entry.turn_uuid}`} className="rounded-md border p-3">
        <p className="text-sm font-medium">{issueLabels[entry.issue_code]}</p>
        <time dateTime={entry.received_at} className="text-sm text-muted-foreground">
          {new Date(entry.received_at).toLocaleString()}
        </time>
        {entry.disposition ? <p className="mt-2 break-all text-sm">
          {dispositions[entry.disposition]} · Review receipt {entry.review_reference}
        </p> : <form className="mt-3 flex flex-wrap items-end gap-2" onSubmit={(event) => submit(event, entry)}>
          <label className="grid gap-1 text-sm">
            Disposition
            <select name="disposition" required defaultValue="" className="h-9 rounded-md border bg-background px-2">
              <option value="" disabled>Choose outcome</option>
              {Object.entries(dispositions).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label className="grid gap-1 text-sm">
            Evidence reference
            <Input name="evidence" required maxLength={120} pattern={String.raw`[A-Za-z0-9][A-Za-z0-9_.:\/\-]{0,119}`} />
          </label>
          <Button type="submit" disabled={busy}>Record review</Button>
        </form>}
      </li>)}
    </ul>}
    {page && (page.offset > 0 || page.rows.length === PAGE_SIZE) && <div className="mt-3 flex gap-2">
      <Button type="button" variant="outline" disabled={busy || page.offset === 0}
        onClick={() => void load(page.learner, page.offset - PAGE_SIZE)}>Previous</Button>
      <Button type="button" variant="outline" disabled={busy || page.rows.length < PAGE_SIZE || page.offset >= 1000}
        onClick={() => void load(page.learner, page.offset + PAGE_SIZE)}>Next</Button>
    </div>}
  </details>
}
