'use client'

import { type FormEvent, useEffect, useId, useMemo, useRef, useState } from 'react'
import { Button } from '@components/ui/button'
import { Input } from '@components/ui/input'
import { formatDateTime } from '@/lib/format'
import { adaptiveRequest, type AdaptiveRequest } from '@services/studytomy/adaptive'

type Scope = { course_uuid: string; course_release_uuid: string; activity_uuid: string }
type Visual = { parts: number; selected: number }
type Check = {
  binding_id: string
  revision: number
  phase: 'initial' | 'recheck' | 'complete' | 'needs_review'
  question?: { prompt: string; visual: Visual } | null
  reteaching?: string | null
  teaching_visual?: Visual | null
  pdf_page: number
  outcome?: 'correct' | 'incorrect' | null
  assisted: boolean
}
type Event = {
  kind: 'initial' | 'recheck'
  prompt: string
  outcome: 'correct' | 'incorrect'
  reteaching?: string | null
  teaching_visual?: Visual | null
  assisted: boolean
  at: string
}
type Timeline = {
  activity_version: number
  concept_uuid: string
  pdf_page: number
  complete: boolean
  events: Event[]
}
type Submission = Scope & { binding_id: string; expected_revision: number; operation_uuid: string; answer: string }

function Parts({ value }: { value: Visual }) {
  return <span className="flex flex-wrap items-center gap-1" role="img"
    aria-label={`${value.selected} of ${value.parts} equal parts are selected`}>
    {Array.from({ length: value.parts }, (_, index) => (
      <span key={index} aria-hidden="true" className={`h-7 w-7 rounded-sm border border-current ${
        index < value.selected ? 'bg-primary text-primary-foreground' : 'bg-muted'}`} />
    ))}
  </span>
}

export default function ManagedPdfTopicCheck({ orgUuid, token, courseUuid, releaseUuid, activityUuid }: {
  orgUuid: string; token: string; courseUuid: string; releaseUuid: string; activityUuid: string
}) {
  const client = useMemo(() => adaptiveRequest(orgUuid, token), [orgUuid, token])
  const scope = useMemo((): Scope => ({
    course_uuid: courseUuid, course_release_uuid: releaseUuid, activity_uuid: activityUuid,
  }), [courseUuid, releaseUuid, activityUuid])
  const [check, setCheck] = useState<Check | null>(null)
  const [answer, setAnswer] = useState('')
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  const pending = useRef<Submission | null>(null)
  const currentRequest = useRef<AbortController | null>(null)
  const label = useId()

  useEffect(() => {
    const refresh = () => {
      currentRequest.current?.abort()
      currentRequest.current = null
      setBusy(false)
      setFailed(false)
      setCheck(null)
      if (document.visibilityState !== 'visible') return
      const controller = new AbortController()
      currentRequest.current = controller
      void client<Check>(`me/topic-check?${new URLSearchParams(scope)}`, 'GET', undefined, undefined, controller.signal)
        .then(result => { if (!controller.signal.aborted) setCheck(result) })
        .catch(() => { if (!controller.signal.aborted) setCheck(null) })
        .finally(() => { if (currentRequest.current === controller) currentRequest.current = null })
    }
    setAnswer('')
    pending.current = null
    refresh()
    document.addEventListener('visibilitychange', refresh)
    return () => {
      currentRequest.current?.abort()
      currentRequest.current = null
      document.removeEventListener('visibilitychange', refresh)
    }
  }, [client, scope])

  async function submit(event: FormEvent) {
    event.preventDefault()
    const clean = answer.trim()
    if (!check || check.phase === 'complete' || !clean || currentRequest.current) return
    const request = pending.current?.answer === clean && pending.current.expected_revision === check.revision
      && pending.current.binding_id === check.binding_id
      ? pending.current : { ...scope, answer: clean, binding_id: check.binding_id, expected_revision: check.revision, operation_uuid: crypto.randomUUID() }
    pending.current = request
    const controller = new AbortController()
    currentRequest.current = controller
    setBusy(true)
    setFailed(false)
    try {
      const result = await client<Check>('me/topic-check', 'POST', request, undefined, controller.signal)
      if (controller.signal.aborted) return
      pending.current = null
      setAnswer('')
      setCheck(result)
    } catch (error) {
      if (controller.signal.aborted) return
      const status = (error as { status?: number })?.status
      if (status && [401, 403, 404, 410].includes(status)) {
        pending.current = null
        setAnswer('')
        setCheck(null)
      } else if (status === 409) {
        const latest = await client<Check>(`me/topic-check?${new URLSearchParams(scope)}`, 'GET', undefined, undefined, controller.signal)
          .catch(() => null)
        if (!controller.signal.aborted) {
          pending.current = null
          setAnswer('')
          setCheck(latest)
        }
      } else setFailed(true)
    } finally {
      if (currentRequest.current === controller) {
        currentRequest.current = null
        setBusy(false)
      }
    }
  }

  if (!check) return null
  return <section aria-labelledby={label} className="space-y-3 rounded-lg border p-4">
    <h2 id={label} className="font-semibold">{check.phase === 'recheck' ? 'Try a different fractions question' : 'Check your understanding'}</h2>
    {check.reteaching && <div className="space-y-2" aria-live="polite">
      <p>Not quite. Review this explanation before trying another example.</p>
      <p className="whitespace-pre-wrap">{check.reteaching}</p>
      {check.teaching_visual && <Parts value={check.teaching_visual} />}
      <p>Educator’s reference: this lesson’s reviewed PDF, page {check.pdf_page}.</p>
    </div>}
    {check.question && <form className="space-y-3" onSubmit={submit}>
      <p>{check.question.prompt}</p>
      <Parts value={check.question.visual} />
      <label className="block">
        <span>Your answer</span>
        <Input value={answer} onChange={event => setAnswer(event.target.value)} maxLength={48} disabled={busy} required />
      </label>
      <Button disabled={busy || !answer.trim()}>{busy ? 'Checking…' : 'Check answer'}</Button>
      {failed && <p role="alert">Could not check the answer. Enter a whole number or a fraction such as 1/2 and try again.</p>}
    </form>}
    {(check.phase === 'complete' || check.phase === 'needs_review') && <p role="status">
      {check.outcome === 'correct' ? 'Correct. Topic check complete.' : 'Recheck incorrect. Revisit the reviewed PDF before moving on.'}
      {check.assisted ? ' This check used learning support.' : ''}
    </p>}
  </section>
}

export function StaffTopicTimeline({ client, courseUuid }: { client: AdaptiveRequest; courseUuid: string }) {
  const [result, setResult] = useState<Timeline | null>(null)
  const [status, setStatus] = useState<'idle' | 'loading' | 'unavailable'>('idle')
  const active = useRef<AbortController | null>(null)
  useEffect(() => {
    setResult(null)
    setStatus('idle')
    return () => active.current?.abort()
  }, [client, courseUuid])
  async function load(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    active.current?.abort()
    const operation = new AbortController()
    active.current = operation
    const form = new FormData(event.currentTarget)
    const learner = String(form.get('learner')).trim()
    const query = new URLSearchParams({
      course_uuid: courseUuid,
      course_release_uuid: String(form.get('release')).trim(),
      activity_uuid: String(form.get('activity')).trim(),
    })
    setStatus('loading')
    setResult(null)
    try {
      const value = await client<Timeline>(`staff/learners/${encodeURIComponent(learner)}/topic-check?${query}`, 'GET', undefined, undefined, operation.signal)
      if (!operation.signal.aborted) {
        setResult(value)
        setStatus('idle')
      }
    } catch {
      if (!operation.signal.aborted) setStatus('unavailable')
    }
  }
  return <section className="space-y-3 rounded-lg border p-4">
    <h2 className="font-semibold">Reviewed PDF topic check timeline</h2>
    <form className="space-y-2" onSubmit={load}>
      <label className="block">Learner reference<Input name="learner" maxLength={100} required /></label>
      <label className="block">Release reference<Input name="release" maxLength={60} required /></label>
      <label className="block">Lesson reference<Input name="activity" maxLength={89} required /></label>
      <Button disabled={status === 'loading'}>{status === 'loading' ? 'Loading…' : 'View timeline'}</Button>
    </form>
    {status === 'unavailable' && <p role="status">No authorized topic check is available for those references.</p>}
    {result && <div className="space-y-2">
      <p className="text-sm text-muted-foreground">Topic-check results do not award independent mastery.</p>
      <p>Lesson revision {result.activity_version} · PDF page {result.pdf_page} · {result.complete ? 'Complete' : 'Not complete'}</p>
      {result.events.length ? <ol className="space-y-1">{result.events.map(item => <li key={item.kind}>
        {item.kind === 'initial' ? 'Initial check' : 'Recheck'}: {item.outcome} ({item.assisted ? 'assisted' : 'unassisted'})
        <p>{item.prompt}</p>
        <time className="block text-xs text-muted-foreground" dateTime={item.at}>{new Date(item.at).toLocaleString()}</time>
        {item.reteaching && <p>Reviewed explanation (PDF page {result.pdf_page}): {item.reteaching}</p>}
        {item.teaching_visual && <Parts value={item.teaching_visual} />}
      </li>)}</ol> : <p>No attempts recorded.</p>}
    </div>}
  </section>
}
