'use client'

import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useSWRConfig } from 'swr'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { Button } from '@components/ui/button'
import AdaptiveLessonProgress, { type LessonRecommendation } from './AdaptiveLessonProgress'
import { Input } from '@components/ui/input'
import { getAuthenticatedSessionIdentity } from '@/lib/auth/sessionScope'
import {
  AdaptiveRequestError, adaptiveRequest, getCurrentAdaptiveAttempt,
  type AdaptiveAttempt, type AdaptiveReleaseScope, type AttemptPurpose,
} from '@services/studytomy/adaptive'

const purposes = ['diagnostic', 'practice'] as const
type StartOption = { purpose: AttemptPurpose; uuid: string; label: string }
type Answer = { kind: 'number'; value: string } | { kind: 'single_choice'; option_uuid: string } | { kind: 'skip' }
type Submission = { key: string } & ({ answer: Answer } | { end: true })
type Receipt = { version: number; status: AdaptiveAttempt['status']; next_item: AdaptiveAttempt['current_item'] }
type Summary = { coverage_complete: boolean; correct_count: number | null; scored_count: number | null }

export default function AdaptiveAttemptPanel({ orgUuid, orgSlug, activityUuid, release, onOpenRecommendation }: {
  orgUuid?: string; orgSlug?: string; activityUuid: string; release?: AdaptiveReleaseScope
  onOpenRecommendation?: (_recommendation: LessonRecommendation) => Promise<boolean>
}) {
  const session = useLHSession()
  const token = session?.data?.tokens?.access_token
  if (session?.status !== 'authenticated' || !token || !orgUuid || !activityUuid) return null
  const user = session.data?.user
  const identity = getAuthenticatedSessionIdentity(user?.id ?? user?.user_uuid, session.data)
  const tutorScope = JSON.stringify([identity, orgUuid, activityUuid])
  return <RecoveredAttempts key={JSON.stringify([tutorScope, orgSlug, release?.course_uuid, release?.course_release_uuid])}
    {...{ orgUuid, orgSlug, activityUuid, token, tutorScope, release, onOpenRecommendation }} />
}

function RecoveredAttempts({ orgUuid, orgSlug, activityUuid, token, tutorScope, release, onOpenRecommendation }: {
  orgUuid: string; orgSlug?: string; activityUuid: string; token: string; tutorScope: string; release?: AdaptiveReleaseScope
  onOpenRecommendation?: (_recommendation: LessonRecommendation) => Promise<boolean>
}) {
  const client = useMemo(() => adaptiveRequest(orgUuid, token), [orgUuid, token])
  const [options, setOptions] = useState<StartOption[]>([])
  const [progressRefreshKey, setProgressRefreshKey] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    const params = new URLSearchParams({ source_activity_uuid: activityUuid, ...release })
    client<StartOption[]>(`me/attempts/options?${params}`, 'GET', undefined, undefined, controller.signal)
      .then(value => { if (!controller.signal.aborted) setOptions(value) })
      .catch(() => { if (!controller.signal.aborted) setOptions([]) })
    return () => controller.abort()
  }, [client, activityUuid, release])
  return <><AdaptiveLessonProgress {...{ client, activityUuid, orgSlug, release, onOpenRecommendation }} refreshKey={progressRefreshKey} />{purposes.map(purpose => <RecoveredAttempt key={purpose}
    options={options.filter(option => option.purpose === purpose)} onCompleted={() => setProgressRefreshKey(value => value + 1)}
    {...{ client, activityUuid, purpose, tutorScope, release }} />)}</>
}

function RecoveredAttempt({ client, activityUuid, purpose, options, tutorScope, release, onCompleted }: {
  client: ReturnType<typeof adaptiveRequest>; activityUuid: string; purpose: AttemptPurpose; options: StartOption[]; tutorScope: string
  onCompleted: () => void
  release?: AdaptiveReleaseScope
}) {
  const { t } = useTranslation()
  const { mutate } = useSWRConfig()
  const refreshTutor = () => void mutate(
    key => Array.isArray(key) && key[0] === 'lesson-tutor' && key[1] === tutorScope, undefined, { revalidate: true },
  )
  const [attempt, setAttempt] = useState<AdaptiveAttempt | null>(null)
  const [recovered, setRecovered] = useState(false)
  const [unconfirmedStart, setUnconfirmedStart] = useState<{ uuid: string; key: string } | null>(null)
  const [answer, setAnswer] = useState('')
  const [help, setHelp] = useState(false)
  const [busy, setBusy] = useState(false)
  const [retry, setRetry] = useState<Submission | null>(null)
  const [confirmEnd, setConfirmEnd] = useState(false)
  const [summary, setSummary] = useState<Summary>()
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const requests = useRef<AbortController | null>(null)
  const sending = useRef(false)
  const alert = useRef<HTMLDivElement>(null)
  const keepWorking = useRef<HTMLButtonElement>(null)
  const endButton = useRef<HTMLButtonElement>(null)
  const label = useId()

  useEffect(() => {
    const lifetime = new AbortController()
    requests.current = lifetime
    queueMicrotask(() => {
      if (lifetime.signal.aborted) return
      setBusy(false)
      setRecovered(false)
      setUnconfirmedStart(null)
      setAnswer('')
      setHelp(false)
      setRetry(null)
      setConfirmEnd(false)
      setSummary(undefined)
      setError('')
      setNotice('')
    })
    getCurrentAdaptiveAttempt(client, activityUuid, purpose, lifetime.signal, release?.course_release_uuid).then(current => {
      if (!lifetime.signal.aborted) { setAttempt(current); setRecovered(true) }
    }).catch(() => {
      if (!lifetime.signal.aborted) setAttempt(null)
    })
    return () => { lifetime.abort(); requests.current = null; sending.current = false }
  }, [client, activityUuid, purpose, release])
  useEffect(() => { if (error) alert.current?.focus() }, [error])

  async function start(uuid: string) {
    const lifetime = requests.current
    if (!lifetime || sending.current || attempt || !options.some(option => option.uuid === uuid)) return
    const key = unconfirmedStart?.uuid === uuid ? unconfirmedStart.key : crypto.randomUUID()
    sending.current = true
    setBusy(true)
    setError('')
    try {
      const fields = purpose === 'diagnostic'
        ? { diagnostic_revision_uuid: uuid, course_release_uuid: release?.course_release_uuid }
        : { concept_uuid: uuid, mode: 'supported', ...release }
      const current = await client<AdaptiveAttempt>(`me/${purpose}-attempts`, 'POST', {
        source_activity_uuid: activityUuid, ...fields,
      }, key, lifetime.signal)
      if (lifetime.signal.aborted) return
      setAttempt(current)
      refreshTutor()
      setUnconfirmedStart(null)
    } catch (reason) {
      if (lifetime.signal.aborted) return
      if (reason instanceof AdaptiveRequestError && [401, 403, 404, 409, 422].includes(reason.status)) {
        setUnconfirmedStart(null)
        const current = await getCurrentAdaptiveAttempt(client, activityUuid, purpose, lifetime.signal, release?.course_release_uuid).catch(() => null)
        if (lifetime.signal.aborted) return
        setAttempt(current)
        if (!current) setError(reason.message)
      } else {
        setUnconfirmedStart({ uuid, key })
        setError(t('adaptiveLearner.startUnconfirmed', 'The start could not be confirmed. Retry the same attempt.'))
      }
    } finally {
      if (!lifetime.signal.aborted) { sending.current = false; setBusy(false) }
    }
  }

  const item = attempt?.current_item
  async function submit(operation: Submission) {
    const lifetime = requests.current
    if (!attempt || attempt.status !== 'active' || (!item && 'answer' in operation) || sending.current || !lifetime) return
    sending.current = true
    setBusy(true)
    setError('')
    setNotice('')
    try {
      if ('end' in operation) {
        const ended = await client<AdaptiveAttempt>(`me/attempts/${encodeURIComponent(attempt.uuid)}/abandon`, 'POST', {
          expected_version: attempt.version, reason: 'leave_assessment',
        }, operation.key, lifetime.signal)
        if (lifetime.signal.aborted) return
        setAttempt(ended)
        refreshTutor()
        setAnswer('')
        setHelp(false)
        setRetry(null)
        setConfirmEnd(false)
        setNotice(t('adaptiveLearner.ended', 'Attempt ended.'))
        return
      }
      const receipt = await client<Receipt>(`me/attempts/${encodeURIComponent(attempt.uuid)}/responses`, 'POST', {
        item_uuid: item?.uuid, expected_version: attempt.version,
        response: operation.answer, declared_external_help: help,
      }, operation.key, lifetime.signal)
      if (lifetime.signal.aborted) return
      if (receipt.status === 'active' && !receipt.next_item) {
        const current = await getCurrentAdaptiveAttempt(client, activityUuid, purpose, lifetime.signal, release?.course_release_uuid).catch(() => null)
        if (lifetime.signal.aborted) return
        setAttempt(current)
      } else {
        setAttempt({ ...attempt, version: receipt.version, status: receipt.status, current_item: receipt.next_item })
        if (receipt.status !== 'active') refreshTutor()
      }
      setAnswer('')
      setHelp(false)
      setRetry(null)
      setNotice(t('adaptiveLearner.saved', 'Response saved.'))
      if (receipt.status === 'completed') {
        onCompleted()
        const result = await client<Summary>(`me/attempts/${encodeURIComponent(attempt.uuid)}/summary`, 'GET', undefined, undefined, lifetime.signal).catch(() => null)
        if (!lifetime.signal.aborted && result) setSummary(result)
      }
    } catch (reason) {
      if (lifetime.signal.aborted) return
      if (reason instanceof AdaptiveRequestError && [401, 403, 404, 409].includes(reason.status)) {
        setAttempt(null)
        setAnswer('')
        setRetry(null)
        setConfirmEnd(false)
        const current = await getCurrentAdaptiveAttempt(client, activityUuid, purpose, lifetime.signal, release?.course_release_uuid).catch(() => null)
        if (!lifetime.signal.aborted) setAttempt(current)
      } else if (!(reason instanceof AdaptiveRequestError) || reason.status >= 500 || reason.status === 429) {
        setRetry(operation)
        setError('end' in operation
          ? t('adaptiveLearner.endUnconfirmed', 'Ending the attempt could not be confirmed. Retry the same request.')
          : t('adaptiveLearner.unconfirmed', 'Save could not be confirmed. Retry the same response.'))
      } else {
        setRetry(null)
        setError(t('adaptiveLearner.invalid', 'Check your response and try again.'))
      }
    } finally {
      if (!lifetime.signal.aborted) { sending.current = false; setBusy(false) }
    }
  }

  if (!attempt && (!recovered || options.length === 0)) return null
  const canAnswer = item?.allowed_actions.includes('answer')
  return <section aria-labelledby={label} className="mt-4 rounded-lg border bg-card p-4 text-card-foreground">
    <h2 id={label} className="font-semibold">{purpose === 'diagnostic'
      ? t('adaptiveLearner.diagnostic', 'Lesson diagnostic')
      : t('adaptiveLearner.practice', 'Lesson practice')}</h2>
    <p role="status" className="text-sm">{busy ? t('adaptiveLearner.working', 'Updating attempt…') : notice}</p>
    {error && <div ref={alert} role="alert" tabIndex={-1} className="my-2 text-sm text-destructive">{error}</div>}
    {summary && <p className="mt-2 text-sm">
      {purpose === 'diagnostic' && !summary.coverage_complete
        ? t('adaptiveLearner.partial', 'The diagnostic ended with incomplete coverage.')
        : t('adaptiveLearner.completed', 'Attempt completed.')}
      {summary.correct_count !== null && summary.scored_count !== null && <span className="ms-2">
        {t('adaptiveLearner.result', '{{correct}} of {{scored}} scored responses were correct.', {
          correct: summary.correct_count, scored: summary.scored_count,
        })}
      </span>}
    </p>}
    {!attempt && <ul className="mt-3 grid gap-2">
      {options.map(option => <li key={option.uuid}>
        <Button type="button" variant="outline" className="min-h-11 h-auto whitespace-normal text-start"
          disabled={busy || Boolean(unconfirmedStart && unconfirmedStart.uuid !== option.uuid)} onClick={() => void start(option.uuid)}>
          {unconfirmedStart?.uuid === option.uuid
            ? t('adaptiveLearner.retryStart', 'Retry starting {{label}}', { label: option.label })
            : t('adaptiveLearner.start', 'Start {{label}}', { label: option.label })}
        </Button>
      </li>)}
    </ul>}
    {attempt?.status === 'active' && item && <form key={item.uuid} className="mt-3" onSubmit={event => {
      event.preventDefault()
      if (!canAnswer || !answer.trim() || retry || confirmEnd) return
      void submit({ key: crypto.randomUUID(), answer: item.question.kind === 'number'
        ? { kind: 'number', value: answer.trim() } : { kind: 'single_choice', option_uuid: answer } })
    }}>
      <fieldset disabled={busy || Boolean(retry) || confirmEnd} className="space-y-2">
        <legend className="whitespace-pre-wrap break-words font-medium">{item.question.prompt}</legend>
        {item.question.kind === 'number' ? <label className="block text-sm">
          {t('adaptiveLearner.answer', 'Your answer')} {item.question.unit && <span>({item.question.unit})</span>}
          <Input className="mt-1 min-h-11" inputMode="decimal" value={answer} onChange={event => setAnswer(event.target.value)} />
        </label> : item.question.options?.map(option => <label key={option.option_uuid} className="flex min-h-11 items-center gap-2">
          <input type="radio" name={`answer-${item.uuid}`} value={option.option_uuid}
            checked={answer === option.option_uuid} onChange={() => setAnswer(option.option_uuid)} />
          <span>{option.text}</span>
        </label>)}
        <label className="flex min-h-11 items-center gap-2 text-sm">
          <input type="checkbox" checked={help} onChange={event => setHelp(event.target.checked)} />
          {t('adaptiveLearner.help', 'I used help outside this activity.')}
        </label>
        <div className="flex flex-wrap gap-2">
          {canAnswer && <Button className="min-h-11" disabled={!answer.trim()}>{t('adaptiveLearner.save', 'Save response')}</Button>}
          {item.allowed_actions.includes('skip') && <Button type="button" variant="outline" className="min-h-11"
            onClick={() => void submit({ key: crypto.randomUUID(), answer: { kind: 'skip' } })}>
            {t('adaptiveLearner.skip', 'Skip question')}
          </Button>}
        </div>
      </fieldset>
    </form>}
    {attempt?.status === 'active' && <div className="mt-3">
      {retry ? <Button type="button" variant="outline" className="min-h-11" disabled={busy} onClick={() => void submit(retry)}>
        {'end' in retry ? t('adaptiveLearner.retryEnd', 'Retry ending attempt') : t('adaptiveLearner.retry', 'Retry save')}
      </Button> : confirmEnd ? <div className="space-y-2">
        <p className="text-sm">{t('adaptiveLearner.endConfirm', 'Ending this attempt closes it. Saved responses will be kept.')}</p>
        <div className="flex flex-wrap gap-2">
          <Button ref={keepWorking} type="button" variant="outline" className="min-h-11" disabled={busy} onClick={() => {
            setConfirmEnd(false)
            requestAnimationFrame(() => endButton.current?.focus())
          }}>
            {t('adaptiveLearner.keepAttempt', 'Keep working')}
          </Button>
          <Button type="button" variant="destructive" className="min-h-11" disabled={busy} onClick={() => void submit({ key: crypto.randomUUID(), end: true })}>
            {t('adaptiveLearner.confirmEnd', 'End this attempt')}
          </Button>
        </div>
      </div> : <Button ref={endButton} type="button" variant="outline" className="min-h-11" disabled={busy} onClick={() => {
        setConfirmEnd(true)
        requestAnimationFrame(() => keepWorking.current?.focus())
      }}>
        {t('adaptiveLearner.endAttempt', 'End attempt')}
      </Button>}
    </div>}
  </section>
}
