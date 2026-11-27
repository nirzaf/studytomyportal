'use client'

import { type FormEvent, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  CourseReviewError,
  type CourseReleaseReceipt,
  type CourseReviewCandidate,
  type CourseReviewClient,
  type CourseEvidence,
} from '@services/studytomy/contentReview'

const control = 'min-h-11 rounded-md border border-gray-400 bg-white px-3 py-2 text-gray-900 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-700 disabled:opacity-60'

export default function ContentReviewPanel({ client, initial }: {
  client: CourseReviewClient
  initial: CourseReviewCandidate
}) {
  const { t } = useTranslation()
  const [candidate, setCandidate] = useState(initial)
  const [rights, setRights] = useState('')
  const [accessibility, setAccessibility] = useState('')
  const [accessible, setAccessible] = useState(false)
  const [assessment, setAssessment] = useState(false)
  const [purpose, setPurpose] = useState<CourseEvidence['purpose']>('content_rights')
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [release, setRelease] = useState<CourseReleaseReceipt | null>(null)
  const errorRef = useRef<HTMLDivElement>(null)
  const active = useRef<AbortController | null>(null)
  const attempt = useRef<{ body: string; id: string } | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const uploadAttempt = useRef<{ revision: string; purpose: string; file: File; id: string } | null>(null)

  useEffect(() => () => {
    active.current?.abort()
    active.current = null
  }, [client])

  useEffect(() => {
    if (error) errorRef.current?.focus()
  }, [error])

  async function run(action: (_signal: AbortSignal) => Promise<void>) {
    if (active.current) return
    const controller = new AbortController()
    active.current = controller
    setBusy(true)
    setError('')
    setStatus('')
    try {
      await action(controller.signal)
    } catch (failure) {
      if (!controller.signal.aborted) {
        const code = failure instanceof CourseReviewError ? failure.code : undefined
        if (code === 'RELEASE_MEDIA_UNSUPPORTED') {
          setError(t('contentReview.unsupportedMedia', 'This release cannot retain one or more lesson formats or media dependencies. Keep this version in draft and review the affected lessons.'))
        } else if (code === 'STALE_VERSION' || code === 'GROUNDED_SOURCE_REVIEW_REQUIRED' || code === 'CURRENT_COURSE_REVIEW_REQUIRED') {
          setError(t('contentReview.stale', 'The candidate changed. Reload it before making a decision.'))
        } else {
          setError(t('contentReview.error', 'The request failed. Check your review access and attachment selection, then retry.'))
        }
      }
    } finally {
      if (active.current === controller) {
        active.current = null
        setBusy(false)
      }
    }
  }

  function load(more: boolean) {
    return run(async signal => {
      let page = await client.candidate(signal, more ? candidate.next_before_id ?? undefined : undefined)
      if (signal.aborted) return
      if (more && page.revision_sha256 === candidate.revision_sha256) {
        setCandidate({ ...page, evidence: [...candidate.evidence, ...page.evidence] })
        return
      }
      if (more) page = await client.candidate(signal)
      if (signal.aborted) return
      setCandidate(page)
      setRelease(null)
      setRights('')
      setAccessibility('')
      setAccessible(false)
      setAssessment(false)
    })
  }

  function download(id: string) {
    return run(async signal => {
      const blob = await client.attachment(id, signal)
      if (signal.aborted) return
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `evidence-${id}`
      link.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    })
  }

  function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!file) return
    const revision = candidate.revision_sha256
    const body = new FormData()
    body.set('purpose', purpose)
    body.set('revision_sha256', revision)
    body.set('file', file)
    return run(async signal => {
      const previous = uploadAttempt.current
      const operation = previous?.revision === revision && previous.purpose === purpose && previous.file === file
        ? previous : { revision, purpose, file, id: crypto.randomUUID() }
      uploadAttempt.current = operation
      const saved = await client.upload(body, operation.id, signal)
      if (signal.aborted) return
      if (saved.revision_sha256 !== revision || saved.purpose !== purpose) throw new CourseReviewError()
      setCandidate(current => ({
        ...current,
        evidence: [saved, ...current.evidence.filter(item => item.evidence_uuid !== saved.evidence_uuid)],
      }))
      if (purpose === 'content_rights') setRights(saved.evidence_uuid)
      else setAccessibility(saved.evidence_uuid)
      setAccessible(false)
      setAssessment(false)
      setFile(null)
      if (fileInput.current) fileInput.current.value = ''
      uploadAttempt.current = null
      setStatus(t('contentReview.attachmentSaved', 'Evidence attached to this candidate.'))
    })
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const button = (event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null
    const decision = button?.value
    if (decision !== 'approved' && decision !== 'changes_requested') return
    if (decision === 'approved' && (candidate.grounding_review_required || candidate.media_review_blocked)) return
    const body = {
      revision_sha256: candidate.revision_sha256,
      rights_evidence_uuid: rights,
      accessibility_evidence_uuid: accessibility,
      decision,
      accessibility_confirmed: accessible,
      assessment_confirmed: assessment,
    }
    const signature = JSON.stringify(body)
    return run(async signal => {
      const verified = await client.verifyEvidence({
        revision_sha256: candidate.revision_sha256,
        rights_evidence_uuid: rights,
        accessibility_evidence_uuid: accessibility,
      }, signal)
      const verifiedRights = verified.some(item => item.evidence_uuid === rights && item.purpose === 'content_rights')
      const verifiedAccessibility = verified.some(item => item.evidence_uuid === accessibility && item.purpose === 'content_accessibility')
      if (!verifiedRights || !verifiedAccessibility) throw new CourseReviewError('REVIEW_EVIDENCE_MISMATCH')
      if (attempt.current?.body !== signature) {
        attempt.current = { body: signature, id: crypto.randomUUID() }
      }
      await client.decide(body, attempt.current.id, signal)
      const latest = await client.candidate(signal)
      if (signal.aborted) return
      setCandidate(current => ({
        ...current,
        current_review: latest.revision_sha256 === current.revision_sha256 ? latest.current_review : null,
        grounding_review_required: latest.grounding_review_required,
        media_review_blocked: latest.media_review_blocked,
      }))
      setRelease(null)
      setStatus(t('contentReview.saved', 'Review decision recorded.'))
    })
  }

  function designateManagedDraft() {
    if (busy || !window.confirm(
      'Mark this private, unpublished course as a Studytomy-managed draft? Publication will remain unavailable until the separate review and release checks pass.'
    )) return
    return run(async signal => {
      await client.designateManagedDraft(signal)
      const latest = await client.candidate(signal)
      if (signal.aborted) return
      setCandidate(latest)
      setRelease(null)
      setStatus('This draft is designated for Studytomy managed review. It has not been published.')
    })
  }

  const review = candidate.current_review
  const canRecordRelease = review?.decision === 'approved' && review.revision_sha256 === candidate.revision_sha256
    && !candidate.grounding_review_required && !candidate.media_review_blocked

  function recordRelease() {
    if (!canRecordRelease || !review) return
    const matchesReview = (receipt: { review_uuid: string; revision_sha256: string }) =>
      receipt.review_uuid === review.uuid && receipt.revision_sha256 === review.revision_sha256
    return run(async signal => {
      const preflight = await client.verifyRelease(review.uuid, signal)
      if (signal.aborted) return
      if (!matchesReview(preflight)) throw new CourseReviewError('CURRENT_COURSE_REVIEW_REQUIRED')
      const receipt = await client.recordRelease(review.uuid, signal)
      if (signal.aborted) return
      if (!matchesReview(receipt)) throw new CourseReviewError('CURRENT_COURSE_REVIEW_REQUIRED')
      setRelease(receipt)
      setStatus(t('contentReview.releaseRecorded', 'Reviewed release recorded.'))
    })
  }

  return (
    <section aria-labelledby="course-review-heading" className="mx-auto max-w-3xl space-y-5 p-4 sm:p-8">
      <h2 id="course-review-heading" className="text-xl font-semibold">{t('contentReview.title', 'Content review')}</h2>
      <p className="break-all font-mono text-sm">{candidate.revision_sha256}</p>
      {candidate.grounding_review_required && <p role="status" className="text-sm text-gray-800">
        {t('contentReview.groundingReviewRequired',
          'A lesson or quiz changed, or a cited source is unavailable. Update its source evidence and reload this candidate before approving.')}
      </p>}
      {candidate.media_review_blocked && <p role="status" className="text-sm text-gray-800">
        {t('contentReview.unsupportedMedia', 'This release cannot retain one or more lesson formats or media dependencies. Keep this version in draft and review the affected lessons.')}
      </p>}
      {candidate.current_review && <p className="text-sm font-medium text-gray-800">
        {candidate.current_review.decision === 'approved'
          ? t('contentReview.approvalRecorded', 'Approval recorded for this candidate.')
          : t('contentReview.changesRecorded', 'Changes requested for this candidate.')}
      </p>}
      <button type="button" className={control} disabled={busy} onClick={() => void load(false)}>
        {t('contentReview.reload', 'Reload candidate')}
      </button>
      <button type="button" className={control} disabled={busy} onClick={() => void designateManagedDraft()}>
        Designate private draft for managed review
      </button>
      {canRecordRelease && !release && <button type="button" className={control} disabled={busy} onClick={() => void recordRelease()}>
        {t('contentReview.recordRelease', 'Record reviewed release')}
      </button>}
      {release && <p className="break-all text-sm">{t('contentReview.releaseReference', 'Release reference {{reference}}', { reference: release.uuid })}</p>}
      {error && <div ref={errorRef} tabIndex={-1} role="alert" className="text-red-800">{error}</div>}
      {status && <p role="status">{status}</p>}
      {busy && <p role="status">{t('contentReview.pending', 'Working…')}</p>}
      <form onSubmit={upload} className="space-y-3 rounded-md border border-gray-300 p-4" aria-busy={busy}>
        <h3 className="font-semibold">{t('contentReview.addEvidence', 'Attach evidence')}</h3>
        <label className="flex flex-col gap-2">
          {t('contentReview.purpose', 'Evidence purpose')}
          <select className={control} value={purpose} disabled={busy}
            onChange={event => setPurpose(event.target.value as CourseEvidence['purpose'])}>
            <option value="content_rights">{t('contentReview.rights', 'Rights evidence')}</option>
            <option value="content_accessibility">{t('contentReview.accessibility', 'Accessibility evidence')}</option>
          </select>
        </label>
        <label className="flex min-w-0 flex-col gap-2">
          {t('contentReview.file', 'Evidence file')}
          <input ref={fileInput} className={control} type="file" disabled={busy} required
            onChange={event => setFile(event.target.files?.[0] ?? null)} />
        </label>
        <button className={control} type="submit" disabled={busy || !file}>
          {t('contentReview.attach', 'Upload attachment')}
        </button>
      </form>
      <form onSubmit={submit} className="space-y-4" aria-busy={busy}>
        {([
          ['content_rights', t('contentReview.rights', 'Rights evidence'), rights, setRights],
          ['content_accessibility', t('contentReview.accessibility', 'Accessibility evidence'), accessibility, setAccessibility],
        ] as const).map(([purpose, label, selected, setSelected]) => (
          <div key={purpose} className="space-y-2">
            <label className="flex min-w-0 flex-col gap-2">
              {label}
              <select className={control} value={selected} required disabled={busy} onChange={event => {
                setSelected(event.target.value)
                setAccessible(false)
                setAssessment(false)
              }}>
                <option value="">{t('contentReview.select', 'Select an attachment')}</option>
                {candidate.evidence.filter(item => item.purpose === purpose).map(item =>
                  <option key={item.evidence_uuid} value={item.evidence_uuid}>{item.evidence_uuid}</option>
                )}
              </select>
            </label>
            {selected && <button type="button" className={control} disabled={busy} onClick={() => void download(selected)}>
              {purpose === 'content_rights'
                ? t('contentReview.downloadRights', 'Download rights evidence')
                : t('contentReview.downloadAccessibility', 'Download accessibility evidence')}
            </button>}
          </div>
        ))}
        {candidate.next_before_id !== null && <button type="button" className={control} disabled={busy} onClick={() => void load(true)}>
          {t('contentReview.more', 'Load more attachments')}
        </button>}
        <label className="flex min-h-11 items-center gap-3">
          <input type="checkbox" checked={accessible} disabled={busy} onChange={event => setAccessible(event.target.checked)} />
          {t('contentReview.confirmAccessibility', 'I confirm the accessibility findings.')}
        </label>
        <label className="flex min-h-11 items-center gap-3">
          <input type="checkbox" checked={assessment} disabled={busy} onChange={event => setAssessment(event.target.checked)} />
          {t('contentReview.confirmAssessment', 'I confirm the assessment findings.')}
        </label>
        <div className="flex flex-wrap gap-2">
          <button className={control} type="submit" value="changes_requested" disabled={busy || !rights || !accessibility}>
            {t('contentReview.requestChanges', 'Request changes')}
          </button>
          <button className={control} type="submit" value="approved"
            disabled={busy || !rights || !accessibility || !accessible || !assessment || candidate.grounding_review_required || candidate.media_review_blocked}>
            {t('contentReview.approve', 'Record approval')}
          </button>
        </div>
      </form>
    </section>
  )
}
