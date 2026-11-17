'use client'

import React, { useEffect, useState } from 'react'
import AllocationReview from './AllocationReview'
import {
  AlertCircle,
  ClipboardList,
  LoaderCircle,
  ShieldCheck,
} from 'lucide-react'
import {
  getFamilyReviewQueue,
  getRelationshipForReview,
} from '@services/studytomy/family'
import type {
  StudyFamilyReviewQueueItem,
  StudyRelationshipReview,
} from '@/types/studytomy'

interface FamilyWorkspaceProps {
  orgUuid: string | undefined
  accessToken: string | undefined
}

const STATUS_LABELS: Record<StudyFamilyReviewQueueItem['status'], string> = {
  pending: 'Pending review',
  accepted: 'Current authority',
  denied: 'Declined',
  restricted: 'Restricted',
  disputed: 'Disputed',
  revoked: 'Revoked',
  expired: 'Expired',
}

function formatDate(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? 'Date unavailable'
    : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

export default function FamilyWorkspace({ orgUuid, accessToken }: FamilyWorkspaceProps) {
  const [items, setItems] = useState<StudyFamilyReviewQueueItem[]>([])
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [reviewResult, setReviewResult] = useState<{
    key: string
    review?: StudyRelationshipReview
    error?: string
  } | null>(null)
  const [queueLoading, setQueueLoading] = useState(true)
  const [queueError, setQueueError] = useState<string | null>(null)
  const authMissing = !orgUuid || !accessToken
  const visibleQueueError = authMissing
    ? 'Sign in with an assigned family-services account to view this workspace.'
    : queueError

  useEffect(() => {
    let current = true
    if (!orgUuid || !accessToken) {
      return () => { current = false }
    }

    const loadQueue = async () => {
      await Promise.resolve()
      if (!current) return
      setQueueLoading(true)
      setQueueError(null)
      setItems([])
      setSelectedKey(null)
      try {
        const result = await getFamilyReviewQueue(orgUuid, accessToken)
        if (!current) return
        setItems(result)
        setSelectedKey((existing) =>
          existing && result.some((item) => `${item.learner_uuid}:${item.relationship_uuid}` === existing)
            ? existing
            : result[0] ? `${result[0].learner_uuid}:${result[0].relationship_uuid}` : null
        )
      } catch (error: unknown) {
        if (current) {
          setItems([])
          setQueueError(error instanceof Error ? error.message : 'Unable to load the family review queue.')
        }
      } finally {
        if (current) setQueueLoading(false)
      }
    }
    void loadQueue()

    return () => { current = false }
  }, [orgUuid, accessToken])

  const selectedItem = items.find(
    (item) => `${item.learner_uuid}:${item.relationship_uuid}` === selectedKey
  ) ?? items[0] ?? null
  const selectedLearnerUuid = selectedItem?.learner_uuid
  const selectedRelationshipUuid = selectedItem?.relationship_uuid
  const selectedReviewKey = selectedLearnerUuid && selectedRelationshipUuid
    ? `${selectedLearnerUuid}:${selectedRelationshipUuid}`
    : null
  const currentReviewResult = reviewResult?.key === selectedReviewKey ? reviewResult : null
  const review = currentReviewResult?.review ?? null
  const reviewError = currentReviewResult?.error ?? null
  const reviewLoading = selectedReviewKey !== null && currentReviewResult === null

  useEffect(() => {
    let current = true
    if (!orgUuid || !accessToken || !selectedLearnerUuid || !selectedRelationshipUuid) {
      return () => { current = false }
    }

    const key = `${selectedLearnerUuid}:${selectedRelationshipUuid}`
    getRelationshipForReview(
      orgUuid,
      selectedLearnerUuid,
      selectedRelationshipUuid,
      accessToken
    )
      .then((result) => {
        if (current) setReviewResult({ key, review: result })
      })
      .catch((error: unknown) => {
        if (current) {
          setReviewResult({
            key,
            error: error instanceof Error ? error.message : 'Unable to load this assigned review.',
          })
        }
      })

    return () => { current = false }
  }, [orgUuid, accessToken, selectedLearnerUuid, selectedRelationshipUuid])

  return (
    <main className="mx-auto w-full max-w-7xl space-y-6 px-4 py-6 sm:px-6 lg:px-8">
      <header className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm sm:p-6">
        <div className="flex items-start gap-3">
          <ShieldCheck aria-hidden="true" className="mt-1 h-6 w-6 shrink-0 text-sky-800" />
          <div>
            <h1 className="text-2xl font-semibold text-gray-950">Family services workspace</h1>
            <p className="mt-2 max-w-3xl text-base leading-6 text-gray-700">
              Review only the learner relationships assigned to your active staff scope. Purchaser status does not grant academic access.
            </p>
          </div>
        </div>
      </header>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(17rem,22rem)_minmax(0,1fr)]">
        <section aria-labelledby="family-queue-title" className="rounded-lg border border-gray-200 bg-white shadow-sm">
          <div className="border-b border-gray-200 px-4 py-4 sm:px-5">
            <h2 id="family-queue-title" className="flex items-center gap-2 text-lg font-semibold text-gray-950">
              <ClipboardList aria-hidden="true" className="h-5 w-5 text-gray-600" />
              Assigned relationships
            </h2>
            <p className="mt-1 text-sm text-gray-600">Learner and status remain visible in this list.</p>
          </div>

          {queueLoading && !authMissing ? (
            <p className="flex items-center gap-2 px-5 py-6 text-sm text-gray-700" role="status" aria-live="polite">
              <LoaderCircle aria-hidden="true" className="h-4 w-4 animate-spin motion-reduce:animate-none" />
              Loading assigned relationships…
            </p>
          ) : visibleQueueError ? (
            <div className="m-4 rounded-md border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950" role="alert">
              <p className="font-semibold">Workspace unavailable</p>
              <p className="mt-1 leading-6">{visibleQueueError}</p>
              <p className="mt-2">The server requires current family-authority, support and staffing approvals.</p>
            </div>
          ) : items.length === 0 ? (
            <p className="px-5 py-6 text-sm leading-6 text-gray-700" role="status">
              There are no relationship reviews in your assigned scope.
            </p>
          ) : (
            <ul className="divide-y divide-gray-100" aria-label="Assigned family relationships">
              {items.map((item) => {
                const key = `${item.learner_uuid}:${item.relationship_uuid}`
                const isSelected = key === (selectedKey ?? `${items[0].learner_uuid}:${items[0].relationship_uuid}`)
                return (
                  <li key={key}>
                    <button
                      type="button"
                      aria-pressed={isSelected}
                      onClick={() => setSelectedKey(key)}
                      className={`min-h-16 w-full px-4 py-3 text-start transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-sky-800 ${
                        isSelected ? 'bg-sky-50' : 'hover:bg-gray-50'
                      }`}
                    >
                      <span className="block text-sm font-semibold text-gray-950">{STATUS_LABELS[item.status]}</span>
                      <span className="mt-1 block break-all text-xs text-gray-700">Learner: {item.learner_uuid}</span>
                      <span className="mt-1 block text-xs text-gray-600">Updated {formatDate(item.updated_at)}</span>
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </section>

        <section aria-labelledby="family-review-title" aria-live="polite" className="min-w-0 rounded-lg border border-gray-200 bg-white p-5 shadow-sm sm:p-6">
          {selectedItem && !authMissing ? (
            <>
              <div className="border-b border-gray-200 pb-4">
                <p className="text-sm font-medium uppercase tracking-wide text-gray-600">Learner subject</p>
                <h2 id="family-review-title" className="mt-1 break-all text-lg font-semibold text-gray-950">
                  {selectedItem.learner_uuid}
                </h2>
                <p className="mt-2 break-all text-sm text-gray-700">Relationship: {selectedItem.relationship_uuid}</p>
                <p className="mt-2 text-sm font-medium text-gray-900">Status: {STATUS_LABELS[selectedItem.status]}</p>
              </div>

              {reviewLoading ? (
                <p className="flex items-center gap-2 py-6 text-sm text-gray-700" role="status">
                  <LoaderCircle aria-hidden="true" className="h-4 w-4 animate-spin motion-reduce:animate-none" />
                  Loading assigned review details…
                </p>
              ) : reviewError ? (
                <div className="mt-5 rounded-md border border-red-300 bg-red-50 p-4 text-sm text-red-900" role="alert">
                  <div className="flex items-start gap-2">
                    <AlertCircle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
                    <p>{reviewError}</p>
                  </div>
                </div>
              ) : review ? (
                <div className="space-y-5 pt-5">
                  <div>
                    <h3 className="text-base font-semibold text-gray-950">Authority request</h3>
                    <dl className="mt-3 grid gap-3 sm:grid-cols-2">
                      <div className="rounded-md bg-gray-50 p-3">
                        <dt className="text-xs font-semibold uppercase tracking-wide text-gray-600">Requester account</dt>
                        <dd className="mt-1 break-all text-sm text-gray-900">{review.adult_user_uuid}</dd>
                      </div>
                      <div className="rounded-md bg-gray-50 p-3">
                        <dt className="text-xs font-semibold uppercase tracking-wide text-gray-600">Submitted</dt>
                        <dd className="mt-1 text-sm text-gray-900">{formatDate(review.created_at)}</dd>
                      </div>
                    </dl>
                  </div>

                  <div>
                    <h3 className="text-sm font-semibold text-gray-900">Requested information scopes</h3>
                    <ul className="mt-2 flex flex-wrap gap-2" aria-label="Requested information scopes">
                      {review.requested_scopes.map((scope) => (
                        <li key={scope} className="rounded-full border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-800">{scope}</li>
                      ))}
                    </ul>
                  </div>

                  {review.approved_scopes.length > 0 && (
                    <div>
                      <h3 className="text-sm font-semibold text-gray-900">Currently permitted scopes</h3>
                      <ul className="mt-2 flex flex-wrap gap-2" aria-label="Currently permitted information scopes">
                        {review.approved_scopes.map((scope) => (
                          <li key={scope} className="rounded-full border border-emerald-300 bg-emerald-50 px-3 py-1.5 text-sm text-emerald-950">{scope}</li>
                        ))}
                      </ul>
                      {review.valid_to && <p className="mt-2 text-sm text-gray-700">Authority ends {formatDate(review.valid_to)}.</p>}
                    </div>
                  )}

                  <div className="rounded-md border border-sky-200 bg-sky-50 p-4">
                    <h3 className="text-sm font-semibold text-sky-950">Assigned evidence reference</h3>
                    <p className="mt-1 break-all font-mono text-sm text-sky-950">{review.request_evidence_reference}</p>
                    <p className="mt-2 text-sm leading-6 text-sky-950">
                      This workspace shows the reference only. A verified case-evidence repository is not connected, so it cannot record or approve a decision.
                    </p>
                  </div>

                  {review.status === 'pending' ? (
                    <p className="rounded-md border border-amber-300 bg-amber-50 p-4 text-sm leading-6 text-amber-950" role="status">
                      Next step: review this request in the approved case system. Decision controls stay unavailable until evidence linking and current policies are verified.
                    </p>
                  ) : review.status === 'denied' ? (
                    <p className="rounded-md border border-gray-300 bg-gray-50 p-4 text-sm leading-6 text-gray-800" role="status">
                      This request was declined. Any new request must follow the current approved family-service process.
                    </p>
                  ) : review.status === 'restricted' || review.status === 'disputed' ? (
                    <p className="rounded-md border border-amber-300 bg-amber-50 p-4 text-sm leading-6 text-amber-950" role="status">
                      This relationship is {STATUS_LABELS[review.status].toLowerCase()}. Keep it in the assigned review process; no sharing scope is active while it is restricted or disputed.
                    </p>
                  ) : null}
                </div>
              ) : (
                <p className="py-6 text-sm text-gray-700">Select an assigned relationship to review its current status.</p>
              )}
            </>
          ) : (
            <div className="py-8 text-sm leading-6 text-gray-700">
              Choose a relationship from your assigned list. No sibling or household records are loaded here.
            </div>
          )}
        </section>
      </div>
      {orgUuid && accessToken && !queueLoading && !visibleQueueError && (
        <AllocationReview key={`${orgUuid}:${accessToken}`} orgUuid={orgUuid} accessToken={accessToken} />
      )}
    </main>
  )
}
