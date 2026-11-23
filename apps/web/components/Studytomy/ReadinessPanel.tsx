'use client'

import React from 'react'
import { getCourseOfferReadiness } from '@services/studytomy/readiness'
import type { StudyCourseReadiness, StudyOfferKind } from '@/types/studytomy'

interface ReadinessPanelProps {
  orgUuid: string
  courseUuid: string
  accessToken: string
  offerKind?: StudyOfferKind
}

export default function ReadinessPanel({
  orgUuid,
  courseUuid,
  accessToken,
  offerKind = 'course',
}: ReadinessPanelProps) {
  const [readiness, setReadiness] = React.useState<StudyCourseReadiness | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [loading, setLoading] = React.useState(true)

  React.useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    getCourseOfferReadiness(orgUuid, courseUuid, accessToken, offerKind)
      .then((result) => {
        if (!cancelled) setReadiness(result)
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : 'Unable to load course readiness.')
          setReadiness(null)
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [orgUuid, courseUuid, accessToken, offerKind])

  return (
    <section aria-labelledby="studytomy-readiness-title" aria-live="polite" className="rounded-lg border border-gray-200 bg-white p-5">
      <h2 id="studytomy-readiness-title" className="text-lg font-semibold text-gray-900">
        Course offer readiness
      </h2>
      {loading ? (
        <p className="mt-3 text-sm text-gray-600">Checking current approvals and evidence…</p>
      ) : error ? (
        <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>
      ) : readiness ? (
        <>
          <p className={`mt-3 text-sm font-medium ${readiness.ready_to_sell ? 'text-green-700' : 'text-amber-800'}`}>
            {readiness.ready_to_sell ? 'Ready for sale review' : 'Not ready for sale'}
          </p>
          {readiness.blockers.length > 0 && (
            <ul className="mt-4 space-y-3">
              {readiness.blockers.map((blocker, index) => (
                <li key={`${blocker.code}-${blocker.policy_key ?? 'general'}-${index}`} className="rounded-md bg-gray-50 p-3">
                  <p className="text-sm font-medium text-gray-900">{blocker.message}</p>
                  <p className="mt-1 text-sm text-gray-700">{blocker.next_step}</p>
                  <p className="mt-1 text-xs text-gray-500">
                    {blocker.code}{blocker.policy_key ? ` · ${blocker.policy_key}` : ''}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </>
      ) : null}
    </section>
  )
}
