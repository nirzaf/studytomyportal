'use client'

import { type FormEvent, useEffect, useRef, useState } from 'react'
import { Button } from '@components/ui/button'
import { Input } from '@components/ui/input'
import { AdaptiveRequestError, type AdaptiveRequest, type Definition } from '@services/studytomy/adaptive'

type Mastery = {
  mastery_policy_uuid: string
  next_offset: number | null
  concepts: { concept_uuid: string; title: string; state: string; independent_family_count: number }[]
}
type Integrity = {
  status: 'current' | 'missing' | 'mismatch' | 'repaired'
  evidence_watermark: number
  projection_version: number | null
  projection: { state: string }
}
type Lookup = { learner: string; curriculum: string; offset: number }
type Review = { concept: string; path: string; snapshot: Integrity; key: string }

export default function StaffMasteryReview({ client, curricula }: {
  client: AdaptiveRequest; curricula: Definition[]
}) {
  const [page, setPage] = useState<{ lookup: Lookup; data: Mastery; client: AdaptiveRequest }>()
  const [review, setReview] = useState<Review>()
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ text: string; error?: boolean }>()
  const active = useRef<AbortController | null>(null)
  const visible = page?.client === client ? page : undefined

  useEffect(() => {
    function abort() {
      active.current?.abort()
      active.current = null
    }
    function clear() {
      if (document.visibilityState !== 'hidden') return
      abort()
      setPage(undefined)
      setReview(undefined)
      setBusy(false)
      setMessage(undefined)
    }
    document.addEventListener('visibilitychange', clear)
    return () => {
      abort()
      document.removeEventListener('visibilitychange', clear)
    }
  }, [client])

  async function run(operation: (_signal: AbortSignal) => Promise<void>) {
    if (active.current) return
    const pending = new AbortController()
    active.current = pending
    setBusy(true)
    setMessage(undefined)
    try {
      await operation(pending.signal)
    } catch (error) {
      if (pending.signal.aborted) return
      if (error instanceof AdaptiveRequestError && [401, 403, 404, 409].includes(error.status)) {
        setPage(undefined)
        setReview(undefined)
      }
      setMessage({
        error: true,
        text: error instanceof AdaptiveRequestError && error.status === 409
          ? 'The evidence changed or the review is blocked. Load mastery and run a new preview.'
          : 'Mastery review is unavailable. Check access and try again.',
      })
    } finally {
      if (active.current === pending) active.current = null
      if (!active.current) setBusy(false)
    }
  }

  async function fetchPage(lookup: Lookup, signal: AbortSignal) {
    const query = new URLSearchParams({ curriculum_revision_uuid: lookup.curriculum, offset: String(lookup.offset) })
    const data = await client<Mastery>(`staff/learners/${encodeURIComponent(lookup.learner)}/mastery?${query}`,
      'GET', undefined, undefined, signal)
    if (!signal.aborted) setPage({ lookup, data, client })
  }

  function open(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const values = new FormData(event.currentTarget)
    const learner = String(values.get('learner') ?? '').trim()
    const curriculum = String(values.get('curriculum') ?? '')
    if (!learner || !curricula.some((item) => item.uuid === curriculum && item.status === 'published')) return
    setPage(undefined)
    setReview(undefined)
    void run((signal) => fetchPage({ learner, curriculum, offset: 0 }, signal))
  }

  function preview(concept: string) {
    if (!visible) return
    const path = `staff/learners/${encodeURIComponent(visible.lookup.learner)}/concepts/${encodeURIComponent(concept)}/mastery-reconciliation`
    const query = new URLSearchParams({ policy_uuid: visible.data.mastery_policy_uuid })
    setReview(undefined)
    void run(async (signal) => {
      const snapshot = await client<Integrity>(`${path}?${query}`, 'GET', undefined, undefined, signal)
      if (!signal.aborted) setReview({ concept, snapshot, path: `${path}/repair?${query}`, key: crypto.randomUUID() })
    })
  }

  function repair() {
    if (!visible || !review || !['missing', 'mismatch'].includes(review.snapshot.status)) return
    void run(async (signal) => {
      await client<Integrity>(review.path, 'POST', {
        expected_evidence_watermark: review.snapshot.evidence_watermark,
        expected_projection_version: review.snapshot.projection_version ?? 0,
      }, review.key, signal)
      if (signal.aborted) return
      setReview(undefined)
      setPage(undefined)
      try {
        await fetchPage(visible.lookup, signal)
        if (!signal.aborted) setMessage({ text: 'Mastery projection reconciled from existing evidence.' })
      } catch {
        if (!signal.aborted) setMessage({ text: 'Reconciliation recorded. Load mastery again to see the result.' })
      }
    })
  }

  return <details className="space-y-3 rounded-lg border bg-card p-4 sm:p-6">
    <summary className="cursor-pointer font-semibold">Learner mastery review</summary>
    <form onSubmit={open} className="flex flex-wrap items-end gap-2">
      <label className="grid gap-1 text-sm">Learner reference<Input name="learner" required maxLength={100} /></label>
      <label className="grid gap-1 text-sm">Curriculum revision
        <select name="curriculum" required defaultValue="" className="h-9 rounded border bg-background px-2">
          <option value="" disabled>Choose a published revision</option>
          {curricula.filter((item) => item.status === 'published').map((item) =>
            <option key={item.uuid} value={item.uuid}>{item.label} · v{item.version}</option>)}
        </select>
      </label>
      <Button type="submit" size="sm" variant="outline" disabled={busy}>Load mastery</Button>
    </form>
    {busy && <p role="status" className="text-sm">Checking mastery…</p>}
    {message && <p role={message.error ? 'alert' : 'status'} className="text-sm">{message.text}</p>}
    {visible && !visible.data.concepts.length && <p className="text-sm">No concepts on this page.</p>}
    {visible && <ul className="space-y-2">
      {visible.data.concepts.map((concept) => <li key={concept.concept_uuid} className="space-y-2 rounded border p-3">
        <h3 className="text-sm font-medium">{concept.title}</h3>
        <p className="text-sm">{concept.state.replaceAll('_', ' ')} · Independent evidence families {concept.independent_family_count}</p>
        <Button type="button" size="sm" variant="outline" disabled={busy}
          onClick={() => preview(concept.concept_uuid)}>Preview reconciliation</Button>
        {review?.concept === concept.concept_uuid && <div className="space-y-2 text-sm">
          <p>Projection {review.snapshot.status} · Recalculated state {review.snapshot.projection.state.replaceAll('_', ' ')}</p>
          <p>Evidence watermark {review.snapshot.evidence_watermark} · Projection version {review.snapshot.projection_version ?? 'none'}</p>
          {review.snapshot.status !== 'current' && <Button type="button" size="sm" disabled={busy}
            onClick={repair}>Rebuild projection from evidence</Button>}
        </div>}
      </li>)}
    </ul>}
    {visible && (visible.lookup.offset > 0 || visible.data.next_offset !== null) && <div className="flex gap-2">
      {visible.lookup.offset > 0 && <Button type="button" size="sm" variant="outline" disabled={busy}
        onClick={() => { setReview(undefined); void run((signal) => fetchPage({ ...visible.lookup, offset: Math.max(0, visible.lookup.offset - 20) }, signal)) }}>Previous</Button>}
      {visible.data.next_offset !== null && visible.data.next_offset <= 100 && <Button type="button" size="sm" variant="outline" disabled={busy}
        onClick={() => {
          const offset = visible.data.next_offset
          if (offset === null) return
          setReview(undefined)
          void run((signal) => fetchPage({ ...visible.lookup, offset }, signal))
        }}>Next</Button>}
    </div>}
  </details>
}
