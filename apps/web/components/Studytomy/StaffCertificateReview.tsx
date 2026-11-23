'use client'

import { type FormEvent, useEffect, useRef, useState } from 'react'
import { Button } from '@components/ui/button'
import { Input } from '@components/ui/input'
import { AdaptiveRequestError, type AdaptiveRequest } from '@services/studytomy/adaptive'

type Eligibility = {
  award_name: string
  issuer_name: string
  eligible: boolean
  completion_event_uuid: string | null
  assessment_result_uuids: Record<string, string>
  missing_assessment_uuids: string[]
  issuance_enabled: boolean
  evidence_sha256: string
}

export default function StaffCertificateReview({ client, courseUuid }: {
  client: AdaptiveRequest
  courseUuid: string
}) {
  const [loaded, setPreview] = useState<{ client: AdaptiveRequest; courseUuid: string; path: string; evidence: Eligibility }>()
  const preview = loaded?.client === client && loaded.courseUuid === courseUuid ? loaded : undefined
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const pending = useRef<AbortController | null>(null)
  const scopeForm = useRef<HTMLFormElement>(null)

  useEffect(() => () => pending.current?.abort(), [])

  function clearPreview() {
    setPreview(undefined)
    setError('')
    setNotice('')
  }

  async function run(operation: (_signal: AbortSignal) => Promise<void>, fallback: string) {
    if (pending.current) return
    const controller = new AbortController()
    pending.current = controller
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await operation(controller.signal)
    } catch (problem) {
      if (!controller.signal.aborted) {
        if (problem instanceof AdaptiveRequestError && problem.code === 'MANAGED_CERTIFICATE_EVIDENCE_CHANGED') {
          setPreview(undefined)
        }
        setError(problem instanceof AdaptiveRequestError ? problem.message : fallback)
      }
    } finally {
      pending.current = null
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  function loadEvidence(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setPreview(undefined)
    const path = pathFor(event.currentTarget)
    void run(async signal => {
      const evidence = await client<Eligibility>(`${path}/award-eligibility`, 'GET', undefined, undefined, signal)
      if (!signal.aborted) setPreview({ client, courseUuid, path, evidence })
    }, 'Certificate evidence could not be loaded.')
  }

  function pathFor(scope: HTMLFormElement) {
    const form = new FormData(scope)
    const release = encodeURIComponent(String(form.get('release') ?? '').trim())
    const learner = encodeURIComponent(String(form.get('learner') ?? '').trim())
    return `courses/${encodeURIComponent(courseUuid)}/releases/${release}/learners/${learner}`
  }

  function issue() {
    if (!preview?.evidence.eligible || !preview.evidence.issuance_enabled) return
    void run(async signal => {
      const receipt = await client<{ award_uuid: string }>(`${preview.path}/awards`, 'POST', {
        evidence_sha256: preview.evidence.evidence_sha256,
      }, undefined, signal)
      if (!signal.aborted) {
        setPreview(undefined)
        setNotice(`Certificate issued. Award reference ${receipt.award_uuid}`)
      }
    }, 'Certificate issuance could not be confirmed. Preview the evidence before retrying.')
  }

  function reviewChange(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const scope = scopeForm.current
    if (!scope || !scope.reportValidity()) return
    const path = pathFor(scope)
    const form = new FormData(event.currentTarget)
    const award = encodeURIComponent(String(form.get('award') ?? '').trim())
    const result = String(form.get('result') ?? '').trim()
    void run(async signal => {
      const reply = await client<{ status: 'corrected' | 'withdrawn'; version: number }>(
        `${path}/awards/${award}/review`, 'POST', { corrected_result_uuid: result }, undefined, signal,
      )
      if (!signal.aborted) {
        setPreview(undefined)
        setNotice(reply.status === 'withdrawn'
          ? `Certificate withdrawn. Version ${reply.version} recorded.`
          : `Certificate corrected. Version ${reply.version} recorded.`)
      }
    }, 'The certificate review could not be confirmed.')
  }

  return (
    <section className="space-y-4 rounded-lg border bg-card p-4 sm:p-6" aria-label="Managed certificates">
      <h2 className="font-semibold">Managed certificates</h2>
      <form ref={scopeForm} onSubmit={loadEvidence} onChange={clearPreview}>
        <fieldset disabled={busy} className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1 text-sm">
            <span>Reviewed release reference</span>
            <Input name="release" required maxLength={64} autoComplete="off" spellCheck={false} />
          </label>
          <label className="space-y-1 text-sm">
            <span>Learner reference</span>
            <Input name="learner" required maxLength={100} autoComplete="off" spellCheck={false} />
          </label>
          <Button type="submit" variant="outline" className="min-h-11 justify-self-start">Preview certificate evidence</Button>
        </fieldset>
      </form>
      {busy && <p role="status" className="text-sm">Checking with the server…</p>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {notice && <p role="status" className="break-words text-sm">{notice}</p>}
      {preview && <div className="space-y-3 border-t pt-4 text-sm">
        <h3 className="break-words font-semibold">{preview.evidence.award_name}</h3>
        <p>Issuer {preview.evidence.issuer_name}</p>
        <p>{preview.evidence.completion_event_uuid ? 'Reviewed course completion recorded.' : 'Reviewed course completion is missing.'}</p>
        {Object.entries(preview.evidence.assessment_result_uuids).map(([assignment, result]) => (
          <p key={assignment} className="break-words">Verified assessment {assignment} · Result {result}</p>
        ))}
        {preview.evidence.missing_assessment_uuids.map(assignment => (
          <p key={assignment} className="break-words">Verified pass required for {assignment}.</p>
        ))}
        {!preview.evidence.issuance_enabled && <p>Issuance requires approval for this release.</p>}
        {preview.evidence.eligible && preview.evidence.issuance_enabled && (
          <Button type="button" disabled={busy} className="min-h-11" onClick={issue}>Issue certificate</Button>
        )}
      </div>}
      <form onSubmit={reviewChange} className="text-sm">
        <fieldset disabled={busy} className="grid gap-3 border-t pt-4 sm:grid-cols-2">
          <legend className="font-semibold">Review an issued certificate</legend>
          <label className="space-y-1">
            <span>Award reference</span>
            <Input name="award" required maxLength={36} autoComplete="off" spellCheck={false} />
          </label>
          <label className="space-y-1">
            <span>Corrected assessment result reference</span>
            <Input name="result" required maxLength={36} autoComplete="off" spellCheck={false} />
          </label>
          <Button type="submit" variant="outline" className="min-h-11 justify-self-start">Record certificate review</Button>
        </fieldset>
      </form>
    </section>
  )
}
