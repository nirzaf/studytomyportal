'use client'

import { useEffect, useId, useMemo, useRef, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import useSWR from 'swr'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { Button } from '@components/ui/button'
import { getAuthenticatedSessionIdentity } from '@/lib/auth/sessionScope'
import { AdaptiveRequestError, adaptiveRequest, type AdaptiveRequest } from '@services/studytomy/adaptive'

type Action = 'explain' | 'hint' | 'check_understanding'
type Options = {
  mode: 'legacy' | 'guided' | 'unavailable'
  actions: Action[]
  reason: 'assessment_in_progress' | 'guidance_unavailable' | null
  practice_attempt_uuid: string | null
}
type Session = { uuid: string; activity_uuid: string; version: number; closed_at: string | null }
type Operation = { operation_uuid: string; expected_version: number; action: Action }
type Stored = { session: Session; operation?: Operation }
const feedbackIssues = ['inaccurate', 'unclear', 'unsafe'] as const
type FeedbackIssue = typeof feedbackIssues[number]
type FeedbackReceipt = { turn_uuid: string; issue_code: FeedbackIssue; received_at: string }
type ReleaseScope = { course_uuid: string; course_release_uuid: string }

type Turn = {
  operation_uuid: string
  state: 'running' | 'completed' | 'failed' | 'reconciliation_required' | 'cancelled'
  session_version: number
  result: {
    message: string
    check_prompt: string | null
    fallback_used: boolean
    context_limited: boolean
    hint_level: number | null
  } | null
}

export function useLessonTutor(
  orgUuid: string | undefined,
  activityUuid: string,
  release?: ReleaseScope,
) {
  const session = useLHSession()
  const token = session?.data?.tokens?.access_token
  const user = session?.data?.user
  const identity = getAuthenticatedSessionIdentity(user?.id ?? user?.user_uuid, session?.data)
  const key = session?.status === 'authenticated' && token && orgUuid && activityUuid
    ? JSON.stringify([identity, orgUuid, activityUuid, release?.course_uuid, release?.course_release_uuid]) : null
  const client = useMemo(() => key && orgUuid && token ? adaptiveRequest(orgUuid, token) : null, [key, orgUuid, token])
  const optionRequests = useRef(new Map<AdaptiveRequest, Set<AbortController>>())
  useEffect(() => {
    const requests = optionRequests.current
    return () => {
      if (!client) return
      for (const request of requests.get(client) ?? []) request.abort()
      requests.delete(client)
    }
  }, [client])
  const { data, error } = useSWR<Options>(key && client ? ['lesson-tutor', key, client] : null,
    async () => {
      if (!client) throw new Error('Lesson guidance is unavailable.')
      const controller = new AbortController()
      const pending = optionRequests.current.get(client) ?? new Set<AbortController>()
      optionRequests.current.set(client, pending)
      pending.add(controller)
      try {
        return await client<Options>(
          `me/tutor-options?${new URLSearchParams({
            activity_uuid: activityUuid,
            ...(release && {
              course_uuid: release.course_uuid,
              course_release_uuid: release.course_release_uuid,
            }),
          })}`,
          'GET', undefined, undefined, controller.signal,
        )
      } finally {
        pending.delete(controller)
      }
    },
    { shouldRetryOnError: false },
  )
  return { client, key, options: error ? undefined : data, error: Boolean(error), release }
}

function loadStored(key: string, activityUuid: string): Stored | undefined {
  try {
    const value: Stored = JSON.parse(sessionStorage.getItem(key) ?? 'null')
    return value?.session.activity_uuid === activityUuid && !value.session.closed_at ? value : undefined
  } catch {
    return undefined
  }
}

export function LessonTutorContent({ tutor, activityUuid }: {
  tutor: ReturnType<typeof useLessonTutor>
  activityUuid: string
}) {
  const { t } = useTranslation()
  const { options, client, key, error, release } = tutor
  if (!client || !key || !options || options.mode !== 'guided') {
    const assessment = options?.reason === 'assessment_in_progress'
    return <p role="status" className="text-sm">
      {assessment ? t('ai.guided.assessment', 'Finish the assessment before opening guidance.')
        : error || !key || options ? t('ai.guided.unavailable', 'Lesson guidance is unavailable.')
        : t('ai.guided.loading', 'Checking lesson guidance…')}
    </p>
  }
  const sessionKey = `lesson-tutor:${key}:${options.practice_attempt_uuid ?? ''}`
  return <GuidedLesson key={sessionKey} {...{ client, activityUuid, options, sessionKey, release }} />
}

function GuidedLesson({ client, activityUuid, options, sessionKey, release }: {
  client: AdaptiveRequest; activityUuid: string; options: Options; sessionKey: string
  release?: ReleaseScope
}) {
  const { t } = useTranslation()
  const heading = useId()
  const [stored, setStored] = useState(() => loadStored(sessionKey, activityUuid))
  const [turn, setTurn] = useState<Turn>()
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const [missing, setMissing] = useState(false)
  const active = useRef<AbortController | null>(null)

  useEffect(() => () => {
    active.current?.abort()
    active.current = null
  }, [client])

  function save(value?: Stored) {
    setStored(value)
    try {
      if (value) sessionStorage.setItem(sessionKey, JSON.stringify(value))
      else sessionStorage.removeItem(sessionKey)
    } catch { /* Recovery is still available until this panel is closed. */ }
  }

  function accept(receipt: Turn, snapshot: Stored) {
    setTurn(receipt)
    setStatus('')
    if (receipt.state === 'running') return
    save({ session: { ...snapshot.session, version: receipt.session_version } })
  }

  async function execute(operation: (_signal: AbortSignal) => Promise<void>, recoverable: boolean) {
    if (active.current) return
    const controller = new AbortController()
    active.current = controller
    setBusy(true)
    setMissing(false)
    setStatus('')
    try {
      await operation(controller.signal)
    } catch (error) {
      if (controller.signal.aborted) return
      if (error instanceof AdaptiveRequestError && error.code === 'ASSESSMENT_IN_PROGRESS') {
        save()
        setTurn(undefined)
        setStatus(t('ai.guided.assessment', 'Finish the assessment before opening guidance.'))
      } else if (recoverable && error instanceof AdaptiveRequestError && error.status === 404) {
        setMissing(true)
        setStatus(t('ai.guided.missing', 'No turn receipt was found. Retry the same request or end guidance.'))
      } else {
        setStatus(recoverable
          ? t('ai.guided.unknown', 'The request could not be confirmed. Check its status before continuing.')
          : t('ai.guided.unavailable', 'Lesson guidance is unavailable.'))
      }
    } finally {
      if (!controller.signal.aborted) {
        active.current = null
        setBusy(false)
      }
    }
  }

  async function start() {
    await execute(async signal => {
      const session = await client<Session>('me/tutor-sessions', 'POST', {
        activity_uuid: activityUuid,
        ...(release && {
          course_uuid: release.course_uuid,
          course_release_uuid: release.course_release_uuid,
        }),
        ...(options.practice_attempt_uuid && { practice_attempt_uuid: options.practice_attempt_uuid }),
      }, undefined, signal)
      if (!signal.aborted) {
        setTurn(undefined)
        save({ session })
      }
    }, false)
  }

  async function submit(action: Action, retry = false) {
    if (!stored || active.current) return
    const operation = retry ? stored.operation : {
      operation_uuid: crypto.randomUUID(), expected_version: stored.session.version, action,
    }
    if (!operation) return
    const snapshot = { session: stored.session, operation }
    save(snapshot)
    setTurn(undefined)
    await execute(async signal => {
      const receipt = await client<Turn>(`me/tutor-sessions/${encodeURIComponent(snapshot.session.uuid)}/turns`,
        'POST', operation, undefined, signal)
      if (signal.aborted) return
      if (receipt.operation_uuid !== operation.operation_uuid) throw new Error('Mismatched receipt')
      accept(receipt, snapshot)
    }, true)
  }

  async function recover() {
    if (!stored?.operation) return
    const snapshot = stored
    const operationUuid = stored.operation.operation_uuid
    await execute(async signal => {
      const receipt = await client<Turn>(
        `me/tutor-sessions/${encodeURIComponent(snapshot.session.uuid)}/turns/${encodeURIComponent(operationUuid)}`,
        'GET', undefined, undefined, signal,
      )
      if (signal.aborted) return
      if (receipt.operation_uuid !== operationUuid) throw new Error('Mismatched receipt')
      accept(receipt, snapshot)
    }, true)
  }

  async function close() {
    if (!stored) return
    const snapshot = stored.session
    await execute(async signal => {
      await client<Session>(`me/tutor-sessions/${encodeURIComponent(snapshot.uuid)}/close`, 'POST',
        { expected_version: snapshot.version }, undefined, signal)
      if (!signal.aborted) {
        save()
        setTurn(undefined)
        setStatus(t('ai.guided.ended', 'Guidance ended.'))
      }
    }, false)
  }

  const labels = {
    explain: t('ai.guided.explain', 'Explain this concept'),
    hint: t('ai.guided.hint', 'Get a reviewed hint'),
    check_understanding: t('ai.guided.check', 'Check my understanding'),
  }
  function sendFeedback(operationUuid: string, issue: FeedbackIssue, signal: AbortSignal) {
    if (!stored) return Promise.reject(new Error("Missing tutor session"))
    return client<FeedbackReceipt>(
      `me/tutor-sessions/${encodeURIComponent(stored.session.uuid)}/feedback`,
      'POST', { turn_uuid: operationUuid, issue_code: issue }, undefined, signal,
    )
  }

  const result = turn?.result
  const feedback = busy ? t('ai.guided.working', 'Working on your request…') : status ||
    (turn?.state === 'running' ? t('ai.guided.running', 'Your request is still processing.') :
      turn?.state === 'reconciliation_required' ? t('ai.guided.reconciliation', 'This request could not be confirmed. You can continue with the lesson.') :
      turn?.state === 'completed' && !result ? t('ai.guided.expired', 'This response has expired.') :
      turn?.state === 'cancelled' || turn?.state === 'failed' ? t('ai.guided.unavailable', 'Lesson guidance is unavailable.') : '')
  return <section aria-labelledby={heading} className="min-h-0 flex-1 space-y-3 overflow-y-auto [overflow-wrap:anywhere]">
    <h2 id={heading} className="text-base font-semibold">{t('ai.guided.title', 'Guided lesson help')}</h2>
    <div className="flex flex-wrap gap-2">
      {!stored && <Button className="min-h-11" disabled={busy} onClick={() => void start()}>
        {t('ai.guided.start', 'Start guidance')}
      </Button>}
      {stored && !stored.operation && options.actions.map(action =>
        <Button key={action} className="min-h-11 whitespace-normal" disabled={busy} onClick={() => void submit(action)}>
          {labels[action]}
        </Button>)}
      {stored?.operation && <Button className="min-h-11" disabled={busy} onClick={() => void recover()}>
        {t('ai.guided.recover', 'Check request status')}
      </Button>}
      {missing && stored?.operation && <Button className="min-h-11" disabled={busy}
        onClick={() => { if (stored.operation) void submit(stored.operation.action, true) }}>
        {t('ai.guided.retry', 'Retry same request')}
      </Button>}
      {stored && <Button variant="secondary" className="min-h-11" disabled={busy}
        onClick={() => void close()}>
        {t('ai.guided.close', 'End guidance')}
      </Button>}
    </div>
    <p role="status" aria-atomic="true" className="text-sm">{feedback}</p>
    {result && <div aria-live="polite" className="space-y-2 rounded-lg border border-white/20 p-3 text-sm">
      {result.fallback_used && <p className="font-semibold">{t('ai.guided.reviewed', 'Reviewed guidance')}</p>}
      {result.hint_level !== null && <p>{t('ai.guided.assisted', 'Hint {{level}} · Supported practice', { level: result.hint_level })}</p>}
      {result.context_limited && <p>{t('ai.guided.limited', 'This response used a limited lesson excerpt.')}</p>}
      <p className="whitespace-pre-wrap"><bdi>{result.message}</bdi></p>
      {result.check_prompt && <p><bdi>{result.check_prompt}</bdi></p>}
      {stored && turn.state === 'completed' && (
        <TurnFeedback key={turn.operation_uuid} operationUuid={turn.operation_uuid}
          send={sendFeedback} execute={execute} busy={busy} />
      )}
    </div>}
  </section>
}

function TurnFeedback({ operationUuid, send, execute, busy }: {
  operationUuid: string
  send: (_operationUuid: string, _issue: FeedbackIssue, _signal: AbortSignal) => Promise<FeedbackReceipt>
  execute: (_operation: (_signal: AbortSignal) => Promise<void>, _recoverable: boolean) => Promise<void>
  busy: boolean
}) {
  const { t } = useTranslation()
  const fieldId = useId()
  const [open, setOpen] = useState(false)
  const [issue, setIssue] = useState<FeedbackIssue | ''>('')
  const [state, setState] = useState<'ready' | 'retry' | 'received' | 'unavailable'>('ready')
  const problem = state === 'retry'
    ? t('ai.guided.feedbackRetry', 'Feedback could not be confirmed. Retry the same report.')
    : state === 'received'
      ? t('ai.guided.feedbackReceived', 'Feedback received.')
      : state === 'unavailable'
        ? t('ai.guided.feedbackUnavailable', 'Feedback is unavailable for this response.')
        : ''

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!issue) return
    await execute(async signal => {
      try {
        const receipt = await send(operationUuid, issue, signal)
        if (signal.aborted) return
        if (receipt.turn_uuid !== operationUuid || receipt.issue_code !== issue) {
          setState('retry')
          return
        }
        setState('received')
      } catch (error) {
        if (signal.aborted) return
        setState(error instanceof AdaptiveRequestError && [403, 404, 409].includes(error.status)
          ? 'unavailable' : 'retry')
      }
    }, false)
  }

  return <div className="space-y-2 border-t border-white/20 pt-3">
    {!open && <Button type="button" variant="secondary" className="min-h-11"
      onClick={() => setOpen(true)} disabled={busy}>
      {t('ai.guided.feedbackOpen', 'Report an issue')}
    </Button>}
    {open && (state === 'ready' || state === 'retry') && <form onSubmit={submit} className="space-y-2">
      <label htmlFor={fieldId} className="block font-semibold">{t('ai.guided.feedbackIssue', 'Issue with this response')}</label>
      <select id={fieldId} value={issue} className="min-h-11 w-full rounded-md border bg-background px-3 text-foreground"
        disabled={busy || state === 'retry'}
        onChange={event => {
          const value = feedbackIssues.find(item => item === event.currentTarget.value)
          setIssue(value ?? '')
        }}>
        <option value="">{t('ai.guided.feedbackSelect', 'Choose an issue')}</option>
        <option value="inaccurate">{t('ai.guided.feedbackInaccurate', 'Inaccurate')}</option>
        <option value="unclear">{t('ai.guided.feedbackUnclear', 'Unclear')}</option>
        <option value="unsafe">{t('ai.guided.feedbackUnsafe', 'Unsafe')}</option>
      </select>
      <Button type="submit" className="min-h-11" disabled={busy || !issue}>
        {state === 'retry' ? t('ai.guided.feedbackRetryButton', 'Retry report') : t('ai.guided.feedbackSubmit', 'Send feedback')}
      </Button>
    </form>}
    {open && <p role="status" aria-atomic="true">{problem}</p>}
  </div>
}
