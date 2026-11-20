'use client'

import React, { useEffect, useRef, useState } from 'react'
import AllocationReview from './AllocationReview'
import {
  AlertCircle,
  ClipboardList,
  LoaderCircle,
  ShieldCheck,
} from 'lucide-react'
import {
  activateLearnerProfile,
  createLearnerProfile,
  decideRelationship,
  fetchRelationshipEvidenceContent,
  getFamilyReviewQueue,
  getLearnerProfile,
  getRelationshipForReview,
  listRelationshipEvidence,
  recordLearnerEligibilityDecision,
  uploadRelationshipEvidence,
  type StudyEvidenceFileItem,
  type StudyLearnerProfileItem,
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
    subjectKey: string
    review?: StudyRelationshipReview
    evidenceFiles?: StudyEvidenceFileItem[]
    evidenceError?: string
    error?: string
  } | null>(null)
  const [selectedEvidence, setSelectedEvidence] = useState('')
  const [evidenceActionBusy, setEvidenceActionBusy] = useState(false)
  const [evidenceActionNotice, setEvidenceActionNotice] = useState<string | null>(null)
  const [evidenceActionError, setEvidenceActionError] = useState<string | null>(null)
  const [decisionBusy, setDecisionBusy] = useState(false)
  const [decisionNotice, setDecisionNotice] = useState<string | null>(null)
  const [decisionError, setDecisionError] = useState<string | null>(null)
  const [queueLoading, setQueueLoading] = useState(true)
  const [queueError, setQueueError] = useState<string | null>(null)
  const authMissing = !orgUuid || !accessToken
  const visibleQueueError = authMissing
    ? 'Sign in with an assigned family-services account to view this workspace.'
    : queueError

  const orgSessionKey = `${orgUuid ?? ''}|${accessToken ?? ''}`
  const orgSessionRef = useRef(orgSessionKey)
  orgSessionRef.current = orgSessionKey

  useEffect(() => {
    let current = true
    setQueueError(null)
    setItems([])
    setSelectedKey(null)
    setReviewResult(null)
    setSelectedEvidence('')
    setEvidenceActionBusy(false)
    setEvidenceActionNotice(null)
    setEvidenceActionError(null)
    setDecisionBusy(false)
    setDecisionNotice(null)
    setDecisionError(null)
    if (!orgUuid || !accessToken) {
      setQueueLoading(false)
      return () => { current = false }
    }

    const expectedOrgSession = `${orgUuid}|${accessToken}`
    const loadQueue = async () => {
      await Promise.resolve()
      if (!current || orgSessionRef.current !== expectedOrgSession) return
      setQueueLoading(true)
      try {
        const result = await getFamilyReviewQueue(orgUuid, accessToken)
        if (!current || orgSessionRef.current !== expectedOrgSession) return
        setItems(result)
        setSelectedKey((existing) =>
          existing && result.some((item) => `${item.learner_uuid}:${item.relationship_uuid}` === existing)
            ? existing
            : result[0] ? `${result[0].learner_uuid}:${result[0].relationship_uuid}` : null
        )
      } catch (error: unknown) {
        if (current && orgSessionRef.current === expectedOrgSession) {
          setItems([])
          setQueueError(error instanceof Error ? error.message : 'Unable to load the family review queue.')
        }
      } finally {
        if (current && orgSessionRef.current === expectedOrgSession) setQueueLoading(false)
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
  const selectedSubjectKey =
    orgUuid && accessToken && selectedLearnerUuid && selectedRelationshipUuid
      ? `${orgUuid}|${accessToken}|${selectedLearnerUuid}|${selectedRelationshipUuid}`
      : null
  const selectedSubjectKeyRef = useRef<string | null>(selectedSubjectKey)
  selectedSubjectKeyRef.current = selectedSubjectKey

  const currentReviewResult =
    selectedSubjectKey !== null && reviewResult?.subjectKey === selectedSubjectKey ? reviewResult : null
  const review = currentReviewResult?.review ?? null
  const evidenceFiles = currentReviewResult?.evidenceFiles ?? []
  const evidenceError = currentReviewResult?.evidenceError ?? null
  const reviewError = currentReviewResult?.error ?? null
  const reviewLoading = selectedSubjectKey !== null && currentReviewResult === null

  useEffect(() => {
    let current = true
    setDecisionNotice(null)
    setDecisionError(null)
    setDecisionBusy(false)
    setSelectedEvidence('')
    if (!orgUuid || !accessToken || !selectedLearnerUuid || !selectedRelationshipUuid) {
      setReviewResult(null)
      return () => { current = false }
    }

    const expectedSubjectKey = `${orgUuid}|${accessToken}|${selectedLearnerUuid}|${selectedRelationshipUuid}`
    const loadSelected = async () => {
      try {
        const [reviewDetail, filesOutcome] = await Promise.all([
          getRelationshipForReview(
            orgUuid,
            selectedLearnerUuid,
            selectedRelationshipUuid,
            accessToken
          ),
          listRelationshipEvidence(orgUuid, selectedRelationshipUuid, accessToken)
            .then((files) => ({ ok: true as const, files }))
            .catch((error: unknown) => ({
              ok: false as const,
              files: [] as StudyEvidenceFileItem[],
              message:
                error instanceof Error
                  ? error.message
                  : 'Unable to load bound relationship evidence.',
            })),
        ])
        if (!current || selectedSubjectKeyRef.current !== expectedSubjectKey) return
        setReviewResult({
          subjectKey: expectedSubjectKey,
          review: reviewDetail,
          evidenceFiles: filesOutcome.files,
          evidenceError: filesOutcome.ok ? undefined : filesOutcome.message,
        })
        setSelectedEvidence(filesOutcome.files[0]?.evidence_uuid || '')
      } catch (error: unknown) {
        if (!current || selectedSubjectKeyRef.current !== expectedSubjectKey) return
        setReviewResult({
          subjectKey: expectedSubjectKey,
          error: error instanceof Error ? error.message : 'Unable to load this assigned review.',
        })
      }
    }
    void loadSelected()

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

                  <div className="rounded-md border border-sky-200 bg-sky-50 p-4 space-y-3">
                    <div>
                      <h3 className="text-sm font-semibold text-sky-950">Assigned evidence reference</h3>
                      <p className="mt-1 break-all font-mono text-sm text-sky-950">{review.request_evidence_reference}</p>
                      {review.request_evidence_reference.startsWith('case_') && (
                        <p className="mt-1 text-xs text-sky-900">
                          Opaque external case reference — not an uploaded file verified inside Studytomy storage.
                        </p>
                      )}
                    </div>
                    {evidenceError ? (
                      <p className="rounded border border-amber-300 bg-amber-50 p-2.5 text-sm text-amber-950" role="alert">
                        Evidence list unavailable: {evidenceError}
                      </p>
                    ) : evidenceFiles.length > 0 ? (
                      <div className="space-y-2">
                        <p className="text-xs font-semibold uppercase tracking-wide text-sky-900">
                          Bound ready guardian-authority evidence
                        </p>
                        <ul className="space-y-1.5">
                          {evidenceFiles.map((item) => (
                            <li key={item.evidence_uuid} className="flex flex-wrap items-center justify-between gap-2 rounded bg-white px-2.5 py-2 text-xs text-gray-800">
                              <span className="font-mono break-all">{item.evidence_uuid}</span>
                              <div className="flex items-center gap-2">
                                <span>{item.media_type}</span>
                                <button
                                  type="button"
                                  disabled={evidenceActionBusy || !orgUuid || !accessToken}
                                  onClick={async () => {
                                    if (!orgUuid || !accessToken || evidenceActionBusy) return
                                    const targetKey = `${orgUuid}|${accessToken}|${review.learner_uuid}|${review.relationship_uuid}`
                                    setEvidenceActionBusy(true)
                                    setEvidenceActionError(null)
                                    setEvidenceActionNotice(null)
                                    try {
                                      const { blob } = await fetchRelationshipEvidenceContent(
                                        orgUuid,
                                        review.relationship_uuid,
                                        item.evidence_uuid,
                                        accessToken
                                      )
                                      if (selectedSubjectKeyRef.current !== targetKey) return
                                      const objectUrl = URL.createObjectURL(blob)
                                      window.open(objectUrl, '_blank', 'noopener,noreferrer')
                                      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
                                      setEvidenceActionNotice(`Opened original evidence ${item.evidence_uuid}.`)
                                    } catch (error) {
                                      if (selectedSubjectKeyRef.current === targetKey) {
                                        setEvidenceActionError(
                                          error instanceof Error ? error.message : 'Unable to open original evidence file.'
                                        )
                                      }
                                    } finally {
                                      if (selectedSubjectKeyRef.current === targetKey) {
                                        setEvidenceActionBusy(false)
                                      }
                                    }
                                  }}
                                  className="rounded border border-sky-300 bg-sky-50 px-2 py-1 font-semibold text-sky-900 hover:bg-sky-100 disabled:opacity-50"
                                >
                                  Open original file
                                </button>
                              </div>
                            </li>
                          ))}
                        </ul>
                      </div>
                    ) : (
                      <p className="text-sm leading-6 text-sky-950">
                        No ready guardian-authority evidence file is attached to this relationship yet.
                      </p>
                    )}
                    {orgUuid && accessToken && (
                      <form
                        className="flex flex-wrap items-end gap-2 border-t border-sky-200 pt-3"
                        onSubmit={async (event) => {
                          event.preventDefault()
                          if (evidenceActionBusy || !orgUuid || !accessToken) return
                          const form = event.currentTarget
                          const fileInput = form.elements.namedItem('evidence_file') as HTMLInputElement | null
                          const file = fileInput?.files?.[0]
                          if (!file) return
                          const targetKey = `${orgUuid}|${accessToken}|${review.learner_uuid}|${review.relationship_uuid}`
                          setEvidenceActionBusy(true)
                          setEvidenceActionError(null)
                          setEvidenceActionNotice(null)
                          try {
                            const uploaded = await uploadRelationshipEvidence(
                              orgUuid,
                              review.relationship_uuid,
                              file,
                              accessToken,
                              crypto.randomUUID()
                            )
                            const refreshedList = await listRelationshipEvidence(
                              orgUuid,
                              review.relationship_uuid,
                              accessToken
                            )
                            if (selectedSubjectKeyRef.current !== targetKey) return
                            setReviewResult((prev) =>
                              prev && prev.subjectKey === targetKey
                                ? { ...prev, evidenceFiles: refreshedList, evidenceError: undefined }
                                : prev
                            )
                            setSelectedEvidence(uploaded.evidence_uuid)
                            form.reset()
                            setEvidenceActionNotice(`Uploaded bound evidence ${uploaded.evidence_uuid}.`)
                          } catch (error) {
                            if (selectedSubjectKeyRef.current === targetKey) {
                              setEvidenceActionError(
                                error instanceof Error ? error.message : 'Unable to upload guardian-authority evidence.'
                              )
                            }
                          } finally {
                            if (selectedSubjectKeyRef.current === targetKey) {
                              setEvidenceActionBusy(false)
                            }
                          }
                        }}
                      >
                        <label className="grid gap-1 text-xs font-medium text-sky-950">
                          Upload bound evidence file (PDF / image)
                          <input
                            type="file"
                            name="evidence_file"
                            accept=".pdf,.png,.jpg,.jpeg,.webp,application/pdf,image/*"
                            required
                            disabled={evidenceActionBusy}
                            className="text-xs"
                          />
                        </label>
                        <button
                          type="submit"
                          disabled={evidenceActionBusy}
                          className="rounded-md bg-sky-800 px-3 py-1.5 text-xs font-semibold text-white hover:bg-sky-900 disabled:opacity-50"
                        >
                          {evidenceActionBusy ? 'Uploading…' : 'Upload evidence'}
                        </button>
                      </form>
                    )}
                    {evidenceActionNotice && (
                      <p className="rounded border border-emerald-300 bg-emerald-50 p-2 text-xs text-emerald-950" role="status">
                        {evidenceActionNotice}
                      </p>
                    )}
                    {evidenceActionError && (
                      <p className="rounded border border-red-300 bg-red-50 p-2 text-xs text-red-900" role="alert">
                        {evidenceActionError}
                      </p>
                    )}
                  </div>

                  {decisionNotice && (
                    <p className="rounded-md border border-emerald-300 bg-emerald-50 p-3 text-sm text-emerald-950" role="status">
                      {decisionNotice}
                    </p>
                  )}
                  {decisionError && (
                    <p className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-900" role="alert">
                      {decisionError}
                    </p>
                  )}

                  {review.status === 'pending' ? (
                    evidenceFiles.length > 0 && accessToken ? (
                      <form
                        className="space-y-3 rounded-md border border-gray-200 bg-gray-50 p-4"
                        onSubmit={async (event) => {
                          event.preventDefault()
                          if (decisionBusy || !orgUuid || !accessToken) return
                          const submitter = (event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null
                          const outcome = submitter?.value === 'deny' ? 'deny' : 'accept'
                          const form = new FormData(event.currentTarget)
                          const validDays = Number(form.get('valid_days') || 365)
                          const validTo = new Date(Date.now() + Math.max(1, validDays) * 86_400_000)
                          const targetLearnerUuid = review.learner_uuid
                          const targetRelationshipUuid = review.relationship_uuid
                          const requestSubjectKey = `${orgUuid}|${accessToken}|${targetLearnerUuid}|${targetRelationshipUuid}`
                          const requestOrgSession = `${orgUuid}|${accessToken}`
                          setDecisionBusy(true)
                          setDecisionError(null)
                          setDecisionNotice(null)
                          try {
                            const updated = await decideRelationship(
                              orgUuid,
                              targetLearnerUuid,
                              targetRelationshipUuid,
                              accessToken,
                              crypto.randomUUID(),
                              {
                                outcome,
                                expected_version: review.authority_version,
                                approved_scopes: outcome === 'accept' ? review.requested_scopes : [],
                                valid_to: outcome === 'accept' ? validTo.toISOString() : null,
                                evidence_reference: selectedEvidence || evidenceFiles[0].evidence_uuid,
                                reason_code: outcome === 'accept' ? 'GUARDIAN_VERIFIED' : 'GUARDIAN_DENIED',
                              }
                            )
                            if (orgSessionRef.current === requestOrgSession) {
                              setItems((prev) =>
                                prev.map((row) =>
                                  row.learner_uuid === targetLearnerUuid &&
                                  row.relationship_uuid === targetRelationshipUuid
                                    ? {
                                        ...row,
                                        status: updated.status,
                                        approved_scopes: updated.approved_scopes,
                                        authority_version: updated.authority_version,
                                        updated_at: updated.updated_at,
                                      }
                                    : row
                                )
                              )
                            }
                            if (selectedSubjectKeyRef.current !== requestSubjectKey) return
                            const refreshed = await getRelationshipForReview(
                              orgUuid,
                              targetLearnerUuid,
                              targetRelationshipUuid,
                              accessToken
                            )
                            if (selectedSubjectKeyRef.current !== requestSubjectKey) return
                            setReviewResult((prev) =>
                              prev && prev.subjectKey === requestSubjectKey
                                ? { ...prev, review: refreshed }
                                : prev
                            )
                            setDecisionNotice(`Decision recorded: ${STATUS_LABELS[refreshed.status]}.`)
                          } catch (error) {
                            if (selectedSubjectKeyRef.current === requestSubjectKey) {
                              setDecisionError(error instanceof Error ? error.message : 'Unable to record this decision.')
                            }
                          } finally {
                            if (selectedSubjectKeyRef.current === requestSubjectKey) {
                              setDecisionBusy(false)
                            }
                          }
                        }}
                      >
                        <h3 className="text-sm font-semibold text-gray-900">Record bound authority decision</h3>
                        <label className="block text-xs font-medium text-gray-700">
                          Evidence attachment
                          <select
                            value={selectedEvidence}
                            onChange={(e) => setSelectedEvidence(e.target.value)}
                            disabled={decisionBusy}
                            className="mt-1 block w-full rounded-md border border-gray-300 bg-white px-2.5 py-2 text-sm"
                          >
                            {evidenceFiles.map((file) => (
                              <option key={file.evidence_uuid} value={file.evidence_uuid}>
                                {file.evidence_uuid} ({file.media_type})
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="block text-xs font-medium text-gray-700">
                          Validity window (days, for approval)
                          <input
                            type="number"
                            name="valid_days"
                            min={1}
                            max={365}
                            defaultValue={365}
                            disabled={decisionBusy}
                            className="mt-1 block w-full rounded-md border border-gray-300 bg-white px-2.5 py-2 text-sm"
                          />
                        </label>
                        <div className="flex flex-wrap gap-2">
                          <button
                            type="submit"
                            value="accept"
                            disabled={decisionBusy}
                            className="rounded-md bg-emerald-700 px-3 py-2 text-sm font-semibold text-white hover:bg-emerald-800 disabled:opacity-50"
                          >
                            Approve requested scopes
                          </button>
                          <button
                            type="submit"
                            value="deny"
                            disabled={decisionBusy}
                            className="rounded-md border border-red-300 bg-white px-3 py-2 text-sm font-semibold text-red-700 hover:bg-red-50 disabled:opacity-50"
                          >
                            Decline request
                          </button>
                        </div>
                      </form>
                    ) : (
                      <p className="rounded-md border border-amber-300 bg-amber-50 p-4 text-sm leading-6 text-amber-950" role="status">
                        Next step: upload and verify a bound guardian-authority evidence file for this relationship before recording an approval or denial.
                      </p>
                    )
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
        <>
          <LearnerLifecyclePanel
            key={`lifecycle:${orgUuid}:${accessToken}`}
            orgUuid={orgUuid}
            accessToken={accessToken}
            initialLearnerUuid={selectedLearnerUuid}
          />
          <AllocationReview key={`${orgUuid}:${accessToken}`} orgUuid={orgUuid} accessToken={accessToken} />
        </>
      )}
    </main>
  )
}

function LearnerLifecyclePanel({
  orgUuid,
  accessToken,
  initialLearnerUuid,
}: {
  orgUuid: string
  accessToken: string
  initialLearnerUuid?: string
}) {
  const [lookupUuid, setLookupUuid] = useState(initialLearnerUuid ?? '')
  const [profile, setProfile] = useState<StudyLearnerProfileItem | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function loadProfile(learnerUuid: string) {
    if (!learnerUuid.trim() || busy) return
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const loaded = await getLearnerProfile(orgUuid, learnerUuid.trim(), accessToken)
      setProfile(loaded)
      setLookupUuid(loaded.learner_uuid)
    } catch (err) {
      setProfile(null)
      setError(err instanceof Error ? err.message : 'Unable to load learner profile.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <details className="rounded-lg border border-gray-200 bg-white p-5">
      <summary className="cursor-pointer text-base font-semibold text-gray-900">
        Learner intake, eligibility &amp; activation
      </summary>
      <p className="mt-2 text-xs text-gray-600">
        School grade does not establish learner age, legal capacity, or guardian consent. Activating a learner profile does not grant consent or course access.
      </p>
      {notice && <p className="mt-3 rounded border border-emerald-300 bg-emerald-50 p-2.5 text-sm text-emerald-950" role="status">{notice}</p>}
      {error && <p className="mt-3 rounded border border-red-300 bg-red-50 p-2.5 text-sm text-red-900" role="alert">{error}</p>}

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <form
          className="space-y-2 rounded-md border border-gray-200 p-3"
          onSubmit={async (event) => {
            event.preventDefault()
            if (busy) return
            const form = event.currentTarget
            const fd = new FormData(form)
            const user_uuid = String(fd.get('user_uuid') ?? '').trim()
            const time_zone = String(fd.get('time_zone') ?? '').trim() || null
            if (!user_uuid) return
            setBusy(true)
            setError(null)
            setNotice(null)
            try {
              const created = await createLearnerProfile(orgUuid, accessToken, crypto.randomUUID(), {
                user_uuid,
                time_zone,
              })
              setProfile(created)
              setLookupUuid(created.learner_uuid)
              form.reset()
              setNotice(`Created pending learner profile ${created.learner_uuid}.`)
            } catch (err) {
              setError(err instanceof Error ? err.message : 'Unable to create learner profile.')
            } finally {
              setBusy(false)
            }
          }}
        >
          <h3 className="text-sm font-semibold text-gray-900">Create pending learner profile</h3>
          <label className="block text-xs font-medium text-gray-700">
            User UUID
            <input name="user_uuid" required maxLength={100} disabled={busy} className="mt-1 block w-full rounded border border-gray-300 px-2.5 py-1.5 text-sm" />
          </label>
          <label className="block text-xs font-medium text-gray-700">
            IANA Time Zone (optional, e.g. Asia/Colombo)
            <input name="time_zone" maxLength={64} placeholder="Asia/Colombo" disabled={busy} className="mt-1 block w-full rounded border border-gray-300 px-2.5 py-1.5 text-sm" />
          </label>
          <button type="submit" disabled={busy} className="rounded-md bg-gray-900 px-3 py-1.5 text-xs font-semibold text-white hover:bg-gray-800 disabled:opacity-50">
            Create pending profile
          </button>
        </form>

        <form
          className="space-y-2 rounded-md border border-gray-200 p-3"
          onSubmit={(event) => {
            event.preventDefault()
            void loadProfile(lookupUuid)
          }}
        >
          <h3 className="text-sm font-semibold text-gray-900">Inspect learner profile</h3>
          <label className="block text-xs font-medium text-gray-700">
            Learner UUID
            <input
              value={lookupUuid}
              onChange={(e) => setLookupUuid(e.target.value)}
              required
              maxLength={100}
              disabled={busy}
              className="mt-1 block w-full rounded border border-gray-300 px-2.5 py-1.5 text-sm"
            />
          </label>
          <button type="submit" disabled={busy || !lookupUuid.trim()} className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-semibold text-gray-900 hover:bg-gray-50 disabled:opacity-50">
            Load learner profile
          </button>
        </form>
      </div>

      {profile && (
        <div className="mt-4 space-y-4 rounded-md border border-gray-200 bg-gray-50 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <div>
              <p className="font-mono font-semibold text-gray-900">{profile.learner_uuid}</p>
              <p className="text-xs text-gray-700">
                Status: <strong>{profile.status}</strong> · Control mode: <strong>{profile.control_mode}</strong> · Access version: <strong>{profile.access_version}</strong>
              </p>
            </div>
            {profile.status === 'pending' && (
              <button
                type="button"
                disabled={busy}
                onClick={async () => {
                  if (busy) return
                  setBusy(true)
                  setError(null)
                  setNotice(null)
                  try {
                    const activated = await activateLearnerProfile(
                      orgUuid,
                      profile.learner_uuid,
                      accessToken,
                      crypto.randomUUID(),
                      {
                        expected_access_version: profile.access_version,
                        reason_code: 'STAFF_LEARNER_ACTIVATED',
                      }
                    )
                    setProfile(activated)
                    setNotice(`Learner profile ${activated.learner_uuid} activated (version ${activated.access_version}).`)
                  } catch (err) {
                    setError(err instanceof Error ? err.message : 'Unable to activate learner profile.')
                  } finally {
                    setBusy(false)
                  }
                }}
                className="rounded-md bg-emerald-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-emerald-800 disabled:opacity-50"
              >
                Activate learner profile
              </button>
            )}
          </div>

          <form
            className="grid gap-2 rounded border border-gray-200 bg-white p-3 sm:grid-cols-2"
            onSubmit={async (event) => {
              event.preventDefault()
              if (busy) return
              const form = event.currentTarget
              const fd = new FormData(form)
              const eligibility_kind = String(fd.get('eligibility_kind')) as 'participation_eligibility' | 'independent_control_eligibility'
              const outcome = String(fd.get('outcome')) as 'eligible' | 'ineligible' | 'renewal_required'
              const evidence_reference = String(fd.get('evidence_reference') ?? '').trim()
              const control_mode_raw = String(fd.get('control_mode') ?? '').trim()
              const control_mode = control_mode_raw === 'dependent' || control_mode_raw === 'independent' ? control_mode_raw : null
              if (!evidence_reference) return
              setBusy(true)
              setError(null)
              setNotice(null)
              try {
                await recordLearnerEligibilityDecision(
                  orgUuid,
                  profile.learner_uuid,
                  accessToken,
                  crypto.randomUUID(),
                  {
                    eligibility_kind,
                    outcome,
                    evidence_reference,
                    control_mode,
                  }
                )
                const refreshed = await getLearnerProfile(orgUuid, profile.learner_uuid, accessToken)
                setProfile(refreshed)
                form.reset()
                setNotice('Eligibility decision recorded and learner profile refreshed.')
              } catch (err) {
                setError(err instanceof Error ? err.message : 'Unable to record eligibility decision.')
              } finally {
                setBusy(false)
              }
            }}
          >
            <h4 className="sm:col-span-2 text-xs font-semibold uppercase tracking-wide text-gray-700">
              Record human eligibility decision
            </h4>
            <label className="text-xs font-medium text-gray-700">
              Eligibility kind
              <select name="eligibility_kind" className="mt-1 block w-full rounded border border-gray-300 bg-white px-2 py-1.5 text-sm">
                <option value="participation_eligibility">Participation eligibility</option>
                <option value="independent_control_eligibility">Independent control eligibility</option>
              </select>
            </label>
            <label className="text-xs font-medium text-gray-700">
              Outcome
              <select name="outcome" className="mt-1 block w-full rounded border border-gray-300 bg-white px-2 py-1.5 text-sm">
                <option value="eligible">Eligible</option>
                <option value="renewal_required">Renewal required</option>
                <option value="ineligible">Ineligible</option>
              </select>
            </label>
            <label className="text-xs font-medium text-gray-700">
              Control mode (optional)
              <select name="control_mode" defaultValue="" className="mt-1 block w-full rounded border border-gray-300 bg-white px-2 py-1.5 text-sm">
                <option value="">Unchanged</option>
                <option value="dependent">Dependent</option>
                <option value="independent">Independent</option>
              </select>
            </label>
            <label className="text-xs font-medium text-gray-700">
              Evidence reference
              <input name="evidence_reference" required maxLength={500} placeholder="ev_... or case_..." className="mt-1 block w-full rounded border border-gray-300 px-2 py-1.5 text-sm" />
            </label>
            <div className="sm:col-span-2">
              <button type="submit" disabled={busy} className="rounded-md bg-sky-800 px-3 py-1.5 text-xs font-semibold text-white hover:bg-sky-900 disabled:opacity-50">
                Record eligibility decision
              </button>
            </div>
          </form>

          {profile.eligibility_decisions.length > 0 && (
            <ul className="space-y-1 text-xs text-gray-700">
              {profile.eligibility_decisions.map((decision) => (
                <li key={decision.decision_uuid} className="rounded bg-white px-2.5 py-1.5">
                  <strong>{decision.eligibility_kind}</strong>: {decision.outcome} ({decision.currently_eligible ? 'currently eligible' : 'not currently eligible'}) · ref <code className="font-mono">{decision.evidence_reference}</code>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </details>
  )
}
