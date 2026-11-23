'use client'

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@components/ui/button'
import { AdaptiveRequestError, adaptiveRequest } from '@services/studytomy/adaptive'

type Review = {
  reference: string
  status: 'awaiting_review' | 'response_received' | 'dismissed'
  response: 'dispute_concern' | 'request_staff_contact' | null
}
type DismissalReason = 'insufficient_evidence' | 'concern_resolved'

export default function AssessmentIntegrityReview({ orgUuid, token, courseUuid, assignmentUuid, submissionUuid, attempt }: {
  orgUuid: string
  token: string
  courseUuid: string
  assignmentUuid: string
  submissionUuid: string
  attempt: number
}) {
  const { t } = useTranslation()
  const [canRecord, setCanRecord] = useState(false)
  const [recorded, setRecorded] = useState(false)
  const [review, setReview] = useState<Review | null>(null)
  const [reason, setReason] = useState<DismissalReason | ''>('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(false)
  const submissionPath = `courses/${encodeURIComponent(courseUuid)}/assignments/${encodeURIComponent(assignmentUuid)}/submissions/${encodeURIComponent(submissionUuid)}`
  const base = `${submissionPath}/integrity-cases`
  const query = `?attempt_number=${attempt}`

  useEffect(() => {
    const controller = new AbortController()
    const request = adaptiveRequest(orgUuid, token)
    request<Review>(base + query, 'GET', undefined, undefined, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return
        setReview(result)
        setError(false)
      })
      .catch(async (failure: unknown) => {
        if (controller.signal.aborted) return
        if (failure instanceof AdaptiveRequestError && failure.status === 404) {
          try {
            const availability = await request<{ can_record: boolean }>(
              `${submissionPath}/integrity-intake${query}`, 'GET', undefined, undefined, controller.signal,
            )
            if (!controller.signal.aborted) setCanRecord(availability.can_record)
            return
          } catch (intakeFailure) {
            failure = intakeFailure
          }
        }
        if (!controller.signal.aborted) {
          setError(!(failure instanceof AdaptiveRequestError && [401, 403, 404, 409].includes(failure.status)))
        }
      })
    return () => controller.abort()
  }, [orgUuid, token, base, query, submissionPath])

  async function recordConcern() {
    setBusy(true)
    setError(false)
    try {
      await adaptiveRequest(orgUuid, token)(base + query, 'POST', { source: 'staff_observation' })
      setCanRecord(false)
      setRecorded(true)
    } catch (failure) {
      if (failure instanceof AdaptiveRequestError && [401, 403, 404, 409].includes(failure.status)) {
        setCanRecord(false)
      }
      setError(true)
    } finally {
      setBusy(false)
    }
  }

  async function dismiss() {
    if (!review || !reason) return
    setBusy(true)
    setError(false)
    try {
      const result = await adaptiveRequest(orgUuid, token)<Pick<Review, 'reference' | 'status'>>(
        `${base}/${encodeURIComponent(review.reference)}/dismissal${query}`, 'PUT', { reason },
      )
      setReview({ ...review, ...result })
    } catch {
      setError(true)
    } finally {
      setBusy(false)
    }
  }

  if (!review && !canRecord && !recorded && !error) return null
  return (
    <section aria-label={t('assignments.integrity.title')} className="space-y-3 rounded-xl border border-neutral-200 p-4">
      <h3 className="text-sm font-semibold">{t('assignments.integrity.title')}</h3>
      {error && <p role="alert" className="text-sm text-rose-700">{t('assignments.integrity.staff_error')}</p>}
      {recorded && <p role="status" className="text-sm">{t('assignments.integrity.concern_recorded')}</p>}
      {canRecord && (
        <>
          <p className="text-sm">{t('assignments.integrity.intake_description')}</p>
          <Button type="button" variant="outline" className="min-h-11" disabled={busy} onClick={recordConcern}>
            {t('assignments.integrity.record_concern')}
          </Button>
        </>
      )}
      {review && (
        <>
          <p className="text-sm break-all">{t('assignments.integrity.reference', { reference: review.reference })}</p>
          <p role="status" className="text-sm">
            {t(`assignments.integrity.${review.status === 'dismissed' ? 'dismissed' : review.response ?? 'no_response'}`)}
          </p>
          {review.status !== 'dismissed' && (
            <div className="flex flex-wrap items-end gap-3">
              <label className="flex flex-col gap-1 text-sm">
                {t('assignments.integrity.dismissal_reason')}
                <select className="min-h-11 rounded-md border bg-background px-2" value={reason}
                  disabled={busy} onChange={(event) => setReason(event.target.value as DismissalReason | '')}>
                  <option value="">{t('assignments.integrity.choose_reason')}</option>
                  <option value="insufficient_evidence">{t('assignments.integrity.insufficient_evidence')}</option>
                  <option value="concern_resolved">{t('assignments.integrity.concern_resolved')}</option>
                </select>
              </label>
              <Button type="button" variant="outline" className="min-h-11" disabled={busy || !reason} onClick={dismiss}>
                {t('assignments.integrity.dismiss')}
              </Button>
            </div>
          )}
        </>
      )}
    </section>
  )
}
