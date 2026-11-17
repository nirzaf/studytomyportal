'use client'

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AdaptiveRequestError, adaptiveRequest } from '@services/studytomy/adaptive'

type Receipt = {
  appealed_result_uuid: string
  result_uuid: string
  status: 'received' | 'reopened' | 'result_confirmed' | 'result_corrected'
}

type Props = {
  orgUuid: string
  courseUuid: string
  assignmentUuid: string
  submissionUuid: string
  resultUuid: string
  accessToken: string
  onRefreshGrade: () => Promise<boolean>
}

export default function AssessmentAppeal({
  orgUuid, courseUuid, assignmentUuid, submissionUuid, resultUuid, accessToken, onRefreshGrade,
}: Props) {
  const { t } = useTranslation()
  const path = `courses/${encodeURIComponent(courseUuid)}/assignments/${encodeURIComponent(assignmentUuid)}/submissions/${encodeURIComponent(submissionUuid)}/appeals`
  const [receipt, setReceipt] = useState<Receipt | null>(null)
  const [loading, setLoading] = useState(true)
  const [available, setAvailable] = useState(false)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState(false)
  const [refresh, setRefresh] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    adaptiveRequest(orgUuid, accessToken)<{ receipt: Receipt | null; can_request_review: boolean }>(
      path, 'GET', undefined, undefined, controller.signal,
    ).then((data) => {
      if (controller.signal.aborted) return
      setReceipt(data.receipt)
      setAvailable(data.can_request_review)
    }).catch((failure: unknown) => {
      if (controller.signal.aborted) return
      setReceipt(null)
      setAvailable(false)
      setError(!(failure instanceof AdaptiveRequestError && [403, 404, 409].includes(failure.status)))
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false)
    })
    return () => controller.abort()
  }, [orgUuid, accessToken, path, refresh])

  async function requestReview() {
    setSending(true)
    setError(false)
    try {
      const response = await adaptiveRequest(orgUuid, accessToken)<Receipt>(
        path, 'POST', { result_uuid: resultUuid },
      )
      setReceipt(response)
    } catch {
      setError(true)
    } finally {
      setSending(false)
    }
  }

  if (!loading && !available && !receipt && !error) return null
  const newerGrade = receipt?.status === 'result_corrected' && receipt.result_uuid !== resultUuid
  const buttonClass = 'min-h-11 rounded-md border border-gray-300 bg-white px-3 text-sm font-semibold text-gray-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-700 disabled:cursor-wait disabled:opacity-60'

  return (
    <section aria-label={t('assignments.appeal.label')} className="space-y-2 border-t border-gray-200 pt-4">
      <h3 className="text-sm font-semibold text-gray-900">{t('assignments.appeal.label')}</h3>
      <div role="status" className="text-sm text-gray-700">
        {loading && t('assignments.appeal.loading')}
        {!loading && receipt && t(`assignments.appeal.${receipt.status}`)}
        {!loading && available && !receipt && t('assignments.appeal.description')}
      </div>
      {error && <p role="alert" className="text-sm text-rose-700">{t('assignments.appeal.error')}</p>}
      {!loading && available && (!receipt || receipt.appealed_result_uuid !== resultUuid) && (
        <button type="button" disabled={sending} onClick={requestReview} className={buttonClass}>
          {t(sending ? 'assignments.appeal.sending' : 'assignments.appeal.request')}
        </button>
      )}
      {!loading && newerGrade && (
        <button type="button" disabled={sending} className={buttonClass} onClick={async () => {
          setSending(true)
          setError(false)
          try {
            if (!await onRefreshGrade()) setError(true)
          } catch {
            setError(true)
          } finally {
            setSending(false)
          }
        }}>
          {t('assignments.appeal.refresh_grade')}
        </button>
      )}
      {!loading && (receipt || !available) && (
        <button type="button" className={buttonClass} onClick={() => {
          setLoading(true)
          setError(false)
          setRefresh((value) => value + 1)
        }}>
          {t('assignments.appeal.refresh')}
        </button>
      )}
    </section>
  )
}
