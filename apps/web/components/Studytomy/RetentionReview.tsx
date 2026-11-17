'use client'

import { type FormEvent, useEffect, useRef, useState } from 'react'
import { Button } from '@components/ui/button'
import { Input } from '@components/ui/input'
import {
  approveEvidenceRetention,
  previewEvidenceRetention,
  type EvidenceRemovalApproval,
  type EvidenceRetentionManifest,
} from '@services/studytomy/privacy'

const dispositions: Record<EvidenceRetentionManifest['files'][number]['disposition'], string> = {
  held: 'On hold', case_open: 'Case open', unverified: 'Evidence needs review',
  retained: 'Within retention period', candidate: 'Eligible for removal review',
}

export default function RetentionReview({ orgUuid, token }: { orgUuid: string; token: string }) {
  const [manifest, setManifest] = useState<EvidenceRetentionManifest | null>(null)
  const [receipt, setReceipt] = useState<EvidenceRemovalApproval | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const approvalRef = useRef<string | null>(null)
  const pending = useRef<AbortController | null>(null)
  useEffect(() => () => pending.current?.abort(), [])

  function reset() {
    setManifest(null)
    setReceipt(null)
    setError('')
    approvalRef.current = null
  }

  const hasCandidates = manifest?.files.some((file) => (
    file.disposition === 'candidate' && !manifest.completed_removals[file.evidence_reference]
  )) === true

  async function preview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    const caseReference = String(new FormData(event.currentTarget).get('case') ?? '').trim()
    const cutoff = new Date(Date.now() - 60_000).toISOString()
    reset()
    const request = new AbortController()
    pending.current = request
    setBusy(true)
    try {
      const result = await previewEvidenceRetention(orgUuid, token, caseReference, cutoff, request.signal)
      if (!request.signal.aborted) setManifest(result)
    } catch {
      if (!request.signal.aborted) setError('The retention manifest is unavailable. Check the case reference and current authority.')
    } finally {
      if (!request.signal.aborted) setBusy(false)
    }
  }

  async function approve(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || !manifest || receipt || !hasCandidates) return
    const reason = String(new FormData(event.currentTarget).get('reason') ?? '').trim()
    const request = new AbortController()
    pending.current = request
    setBusy(true)
    setError('')
    try {
      approvalRef.current ??= crypto.randomUUID()
      const result = await approveEvidenceRetention(orgUuid, token, manifest, approvalRef.current, reason, request.signal)
      if (!request.signal.aborted) setReceipt(result)
    } catch {
      if (!request.signal.aborted) setError('Approval could not be confirmed. Check the approved reason and refresh the manifest if the case changed.')
    } finally {
      if (!request.signal.aborted) setBusy(false)
    }
  }

  return (
    <section aria-labelledby="evidence-retention-title" className="space-y-3 rounded-lg border bg-white p-4">
      <h2 id="evidence-retention-title" className="font-semibold">Case evidence retention</h2>
      <form onSubmit={preview}>
        <fieldset disabled={busy} className="flex flex-wrap items-end gap-3">
          <label className="grow text-sm font-medium">Case reference
            <Input name="case" required maxLength={38} autoComplete="off" spellCheck={false} onChange={reset} />
          </label>
          <Button type="submit" variant="outline" className="min-h-11">Run retention preview</Button>
        </fieldset>
      </form>
      {busy && <p role="status" className="text-sm">Checking with the server…</p>}
      {error && <p role="alert" className="text-sm text-red-800">{error}</p>}
      {manifest && (
        <div className="space-y-3 text-sm">
          <dl className="space-y-1">
            <div><dt>Cutoff</dt><dd><time dateTime={manifest.as_of}>{new Date(manifest.as_of).toLocaleString()}</time></dd></div>
            <div><dt>Active holds</dt><dd>{manifest.hold_references.length}</dd></div>
            <div><dt>Manifest digest</dt><dd className="break-all font-mono">{manifest.snapshot_sha256}</dd></div>
          </dl>
          {manifest.files.length === 0 ? <p>No case evidence files.</p> : (
            <ul aria-label="Case evidence dispositions" className="space-y-1">
              {manifest.files.map((file) => <li key={file.evidence_reference} className="break-words">
                {file.evidence_reference} · {manifest.completed_removals[file.evidence_reference]
                  ? 'Removal recorded' : dispositions[file.disposition]}
              </li>)}
            </ul>
          )}
          {receipt ? (
            <p role="status" className="break-words">Removal review approved for {receipt.candidate_count} {receipt.candidate_count === 1 ? 'file' : 'files'}. Approval reference {receipt.approval_reference}.</p>
          ) : hasCandidates && (
            <form onSubmit={approve} className="space-y-2 border-t pt-3">
              <p>Approval records the reviewed candidates. File removal is a separate step.</p>
              <fieldset disabled={busy} className="flex flex-wrap items-end gap-3">
                <label className="grow text-sm font-medium">Policy-approved removal reason code
                  <Input name="reason" required maxLength={64} pattern="[a-z][a-z0-9_]{0,63}"
                    autoComplete="off" spellCheck={false} onChange={() => { approvalRef.current = null }} />
                </label>
                <Button type="submit" className="min-h-11">Record removal approval</Button>
              </fieldset>
            </form>
          )}
        </div>
      )}
    </section>
  )
}
