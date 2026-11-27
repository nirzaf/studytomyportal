'use client'

import { type FormEvent, useEffect, useRef, useState } from 'react'
import { Button } from '@components/ui/button'
import { AdaptiveRequestError, adaptiveRequest } from '@services/studytomy/adaptive'

type Evidence = {
  round_uuid: string
  result_uuid: string
  score_points: number
  maximum_points: number
  version: number
  appeal: {
    appeal_uuid: string; status: string; decision_uuid: string | null
    appealed_result_uuid: string; result_uuid: string
  } | null
}

type Props = {
  orgUuid: string
  token: string
  courseUuid: string
  assignmentUuid: string
  submissionUuid: string
  attempt: number
  openAttempt: boolean
  onDecision: () => void
}

export default function AssessmentModeration({
  orgUuid, token, courseUuid, assignmentUuid, submissionUuid, attempt, openAttempt, onDecision,
}: Props) {
  const [evidence, setEvidence] = useState<Evidence>()
  const [message, setMessage] = useState<{ text: string; error?: boolean }>()
  const [busy, setBusy] = useState(false)
  const active = useRef<AbortController | null>(null)
  const base = `courses/${encodeURIComponent(courseUuid)}/assignments/${encodeURIComponent(assignmentUuid)}/submissions/${encodeURIComponent(submissionUuid)}`
  const query = `?attempt_number=${attempt}`

  useEffect(() => () => {
    active.current?.abort()
    active.current = null
  }, [token, orgUuid, base, query])

  async function load(decision?: {
    action: 'confirm' | 'correct' | 'reopen' | 'retry-grants' | 'deadline-adjustments'; body?: object
  }) {
    if (active.current) return
    const controller = new AbortController()
    active.current = controller
    setBusy(true)
    setMessage(undefined)
    setEvidence(undefined)
    const request = adaptiveRequest(orgUuid, token)
    let recorded = false
    try {
      if (decision?.action === 'retry-grants' || decision?.action === 'deadline-adjustments') {
        const receipt = await request<{ adjustment_uuid: string; expires_at: string }>(
          `${base}/${decision.action}?expected_attempt_number=${attempt}`,
          'POST', decision.body, undefined, controller.signal,
        )
        if (controller.signal.aborted) return
        onDecision()
        setEvidence(evidence)
        setMessage({ text: `Recorded until ${new Date(receipt.expires_at).toLocaleString()}. Receipt ${receipt.adjustment_uuid}` })
        return
      }
      if (decision && evidence?.appeal) {
        const appeal = evidence.appeal
        const expected = appeal.status === 'reopened' && appeal.decision_uuid &&
          (decision.action === 'confirm' || decision.action === 'correct')
          ? `&expected_decision_uuid=${encodeURIComponent(appeal.decision_uuid)}` : ''
        await request(
          `${base}/appeals/${encodeURIComponent(appeal.appeal_uuid)}/${decision.action}${query}${expected}`,
          'POST', decision.body, undefined, controller.signal,
        )
        if (controller.signal.aborted) return
        recorded = true
        onDecision()
      }
      const current = await request<Evidence>(base + '/review-evidence' + query, 'GET', undefined, undefined, controller.signal)
      if (!controller.signal.aborted) {
        setEvidence(current)
        if (recorded) setMessage({ text: 'Appeal decision recorded.' })
      }
    } catch (error) {
      if (controller.signal.aborted) return
      setMessage({ error: true, text: recorded
        ? 'Decision recorded. Reload the review to see the current result.'
        : error instanceof AdaptiveRequestError && error.status === 409
          ? 'This review changed or is blocked by current policy. Refresh the submission before retrying.'
          : 'Review evidence or this action is unavailable for this submission.' })
    } finally {
      if (active.current === controller) active.current = null
      if (!active.current) setBusy(false)
    }
  }

  function correct(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const score = new FormData(event.currentTarget).get('score')
    if (typeof score !== 'string' || !/^\d+$/.test(score) || !evidence) return
    const points = Number(score)
    if (!Number.isSafeInteger(points) || points > evidence.maximum_points) return
    if (points === evidence.score_points) {
      setMessage({ text: 'Enter a score different from the recorded result.', error: true })
      return
    }
    void load({ action: 'correct', body: { score_points: points } })
  }

  function saveDeadline(event: FormEvent<HTMLFormElement>, kind: 'retry-grants' | 'deadline-adjustments') {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const value = form.get('expires_at')
    const date = typeof value === 'string' ? new Date(value) : null
    if (!date || !Number.isFinite(date.getTime()) || date.getTime() <= Date.now()) {
      setMessage({ text: 'Choose a deadline in the future.', error: true })
      return
    }
    const expires_at = date.toISOString()
    if (kind === 'deadline-adjustments' && openAttempt) {
      void load({ action: kind, body: { expires_at, reason: form.get('reason') } })
    } else if (kind === 'retry-grants' && evidence && !openAttempt) {
      void load({ action: kind, body: { expires_at, result_uuid: evidence.result_uuid } })
    }
  }

  const appeal = evidence?.appeal
  const undecided = appeal?.status === 'received' || appeal?.status === 'reopened'
  return <details className="space-y-3 rounded-xl border border-neutral-200 p-4">
    <summary className="cursor-pointer text-sm font-semibold">Assessment moderation</summary>
    {!openAttempt && <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void load()}>
      Load review evidence
    </Button>}
    {busy && <p role="status" className="text-sm">Checking assessment review…</p>}
    {message && <p role={message.error ? 'alert' : 'status'} className="text-sm">{message.text}</p>}
    {evidence && <p className="text-sm">
      Recorded result {evidence.score_points} / {evidence.maximum_points} · Version {evidence.version}
    </p>}
    {evidence && <p className="break-all text-sm">Assessment round reference {evidence.round_uuid}</p>}
    {evidence && !appeal && <p className="text-sm">No appeal for this attempt.</p>}
    {appeal && <p className="text-sm">Appeal status · {appeal.status.replaceAll('_', ' ')}</p>}
    {undecided && evidence && appeal.appealed_result_uuid !== evidence.result_uuid &&
      <p role="status" className="text-sm">The appealed result has been superseded.</p>}
    {undecided && evidence && appeal.appealed_result_uuid === evidence.result_uuid && <>
      <Button type="button" size="sm" variant="outline" disabled={busy}
        onClick={() => void load({ action: 'confirm' })}>Confirm recorded result</Button>
      <form onSubmit={correct} className="flex flex-wrap items-end gap-2">
        <label className="grid gap-1 text-sm">Corrected score
          <input name="score" type="number" min={0} max={evidence.maximum_points} step={1} required
            className="rounded border bg-background p-2" />
        </label>
        <Button type="submit" size="sm" disabled={busy}>Record score correction</Button>
      </form>
    </>}
    {appeal?.status === 'result_confirmed' && appeal.decision_uuid && appeal.result_uuid === evidence?.result_uuid &&
      <Button type="button" size="sm" variant="outline" disabled={busy}
      onClick={() => void load({ action: 'reopen', body: { decision_uuid: appeal.decision_uuid } })}>Reopen appeal</Button>}
    {(openAttempt || evidence) && <form
      className="flex flex-wrap items-end gap-2 border-t pt-3"
      onSubmit={(event) => saveDeadline(event, openAttempt ? 'deadline-adjustments' : 'retry-grants')}>
      <label className="grid gap-1 text-sm">
        {openAttempt ? 'Extended deadline (local time)' : 'Retry deadline (local time)'}
        <input name="expires_at" type="datetime-local" required disabled={busy} className="rounded border bg-background p-2" />
      </label>
      {openAttempt && <label className="grid gap-1 text-sm">Reason
        <select name="reason" required defaultValue="" disabled={busy} className="rounded border bg-background p-2">
          <option value="" disabled>Choose a reason</option>
          <option value="SERVICE_INTERRUPTION">Service interruption</option>
          <option value="APPROVED_ADJUSTMENT">Approved adjustment</option>
        </select>
      </label>}
      <Button type="submit" size="sm" variant="outline" disabled={busy}>
        {openAttempt ? 'Extend attempt deadline' : 'Grant reviewed retry'}
      </Button>
    </form>}
  </details>
}
