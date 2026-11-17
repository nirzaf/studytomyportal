'use client'

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@components/ui/button'
import { AdaptiveRequestError, adaptiveRequest } from '@services/studytomy/adaptive'

type CaseReceipt = { reference: string; status: 'awaiting_review' | 'response_received' | 'dismissed' }
type LearnerCase = CaseReceipt & { assessment_title?: string | null; attempt_number?: number | null }
type ResponseChoice = 'dispute_concern' | 'request_staff_contact'
const casesPath = 'me/assessment-integrity-cases'

export default function AssessmentIntegrityCases({ orgUuid, token }: { orgUuid: string; token: string }) {
  const { t } = useTranslation()
  const [cases, setCases] = useState<LearnerCase[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const [refreshing, setRefreshing] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    adaptiveRequest(orgUuid, token)<LearnerCase[]>(casesPath, 'GET', undefined, undefined, controller.signal)
      .then((rows) => {
        if (controller.signal.aborted) return
        setCases(rows)
        setError(false)
      })
      .finally(() => {
        if (!controller.signal.aborted) setRefreshing(false)
      })
      .catch((failure: unknown) => {
        if (controller.signal.aborted) return
        setCases([])
        setError(!(failure instanceof AdaptiveRequestError && [401, 403, 404].includes(failure.status)))
      })
    return () => controller.abort()
  }, [orgUuid, token, refresh])

  async function respond(reference: string, response: ResponseChoice) {
    setBusy(reference)
    setError(false)
    try {
      const receipt = await adaptiveRequest(orgUuid, token)<CaseReceipt>(
        `${casesPath}/${encodeURIComponent(reference)}/response`, 'PUT', { response },
      )
      setCases((current) => current.map((row) => row.reference === reference ? { ...row, ...receipt } : row))
    } catch {
      setError(true)
    } finally {
      setBusy(null)
    }
  }

  if (!cases.length && !error) return null
  return (
    <section aria-labelledby="assessment-integrity-heading" className="space-y-4">
      <h2 id="assessment-integrity-heading" className="text-lg font-semibold">{t('assignments.integrity.title')}</h2>
      {error && <p role="alert" className="text-sm text-rose-700">{t('assignments.integrity.error')}</p>}
      <ul className="space-y-3">
        {cases.map((row) => (
          <li className="space-y-2 rounded-lg border border-gray-200 p-4" key={row.reference}>
            {row.assessment_title && <h3 className="text-sm font-semibold">{row.assessment_title}</h3>}
            {row.attempt_number && (
              <p className="text-sm">{t('assignments.integrity.attempt', { attempt: row.attempt_number })}</p>
            )}
            <p role="status" className="text-sm font-medium">{t(`assignments.integrity.${row.status}`)}</p>
            <p className="text-sm break-all">{t('assignments.integrity.reference', { reference: row.reference })}</p>
            {row.status === 'awaiting_review' && (
              <div className="flex flex-wrap gap-2">
                <Button type="button" variant="outline" className="min-h-11" disabled={busy !== null || refreshing}
                  onClick={() => respond(row.reference, 'dispute_concern')}>
                  {t('assignments.integrity.dispute')}
                </Button>
                <Button type="button" variant="outline" className="min-h-11" disabled={busy !== null || refreshing}
                  onClick={() => respond(row.reference, 'request_staff_contact')}>
                  {t('assignments.integrity.contact')}
                </Button>
              </div>
            )}
          </li>
        ))}
      </ul>
      <Button type="button" variant="outline" className="min-h-11" disabled={busy !== null || refreshing} onClick={() => {
        setRefreshing(true)
        setRefresh((value) => value + 1)
      }}>
        {t('assignments.integrity.refresh')}
      </Button>
    </section>
  )
}
