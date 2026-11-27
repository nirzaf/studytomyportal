'use client'

import { type FormEvent, useEffect, useRef, useState } from 'react'
import { Button } from '@components/ui/button'
import { Input } from '@components/ui/input'
import {
  approveEvidenceRetention,
  placeRecordEvidenceHold,
  previewEvidenceRetention,
  releaseRecordEvidenceHold,
  removeApprovedRecordEvidence,
  replayApprovedRecordEvidenceRemoval,
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
  const [status, setStatus] = useState('')
  const approvalRef = useRef<string | null>(null)
  const pending = useRef<AbortController | null>(null)
  const holdAttempt = useRef<{ signature: string; uuid: string } | null>(null)
  const replayAttempt = useRef<{ signature: string; uuid: string } | null>(null)
  useEffect(() => () => pending.current?.abort(), [])

  function reset() {
    setManifest(null)
    setReceipt(null)
    setError('')
    setStatus('')
    approvalRef.current = null
    holdAttempt.current = null
    replayAttempt.current = null
  }

  const hasCandidates = manifest?.files.some((file) => (
    file.disposition === 'candidate' && !manifest.completed_removals[file.evidence_reference]
  )) === true
  const hasCompletedRemovals = Object.keys(manifest?.completed_removals ?? {}).length > 0

  async function performAction(
    action: (signal: AbortSignal) => Promise<void>,
    successMessage: string,
  ) {
    if (busy || !manifest) return
    const request = new AbortController()
    pending.current = request
    setBusy(true)
    setError('')
    const previousDigest = manifest.snapshot_sha256
    try {
      await action(request.signal)
      if (request.signal.aborted) return
      const refreshed = await previewEvidenceRetention(
        orgUuid, token, manifest.request_reference, new Date(Date.now() - 60_000).toISOString(), request.signal,
      )
      if (request.signal.aborted) return
      setManifest(refreshed)
      if (refreshed.snapshot_sha256 !== previousDigest) {
        setReceipt(null)
        approvalRef.current = null
      }
      setError('')
      setStatus(successMessage)
    } catch (failure) {
      if (!request.signal.aborted) setError(failure instanceof Error ? failure.message : 'The retention action is unavailable.')
    } finally {
      if (pending.current === request) pending.current = null
      if (!request.signal.aborted) setBusy(false)
    }
  }

  async function placeHold(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!manifest || busy) return
    const fields = new FormData(event.currentTarget)
    const reasonCode = String(fields.get('hold_reason') ?? '').trim()
    const reviewDate = new Date(String(fields.get('review_due') ?? ''))
    if (!/^[a-z][a-z0-9_]{0,63}$/.test(reasonCode) || !Number.isFinite(reviewDate.getTime()) || reviewDate.getTime() <= Date.now()) {
      setError('Choose a policy reason code and a future review date.')
      return
    }
    if (!window.confirm('Place a review hold on this case evidence? The server will validate the current retention policy.')) return
    const review_due_at = reviewDate.toISOString()
    const signature = JSON.stringify([manifest.request_reference, reasonCode, review_due_at])
    if (holdAttempt.current?.signature !== signature) holdAttempt.current = { signature, uuid: crypto.randomUUID() }
    const holdReference = holdAttempt.current.uuid
    await performAction(async (signal) => {
      await placeRecordEvidenceHold(orgUuid, token, manifest.request_reference, holdReference,
        { reason_code: reasonCode, review_due_at }, signal)
    }, `Evidence hold recorded: ${holdReference}.`)
  }

  async function releaseHold(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!manifest || busy) return
    const fields = new FormData(event.currentTarget)
    const holdReference = String(fields.get('hold_reference') ?? '')
    const reasonCode = String(fields.get('release_reason') ?? '').trim()
    if (!manifest.hold_references.includes(holdReference) || !/^[a-z][a-z0-9_]{0,63}$/.test(reasonCode)) return
    if (!window.confirm(`Release evidence hold ${holdReference}? The server will verify that release is allowed.`)) return
    await performAction(async (signal) => {
      await releaseRecordEvidenceHold(orgUuid, token, manifest.request_reference, holdReference, reasonCode, signal)
    }, `Evidence hold released: ${holdReference}.`)
  }

  async function removeFile(reference: string) {
    if (!manifest || !receipt || !window.confirm(`Permanently remove approved evidence ${reference}? The server will recheck the exact retention approval and storage key.`)) return
    await performAction(async (signal) => {
      const result = await removeApprovedRecordEvidence(
        orgUuid, token, manifest.request_reference, receipt.approval_reference, reference, signal,
      )
      if (result.evidence_reference !== reference) throw new Error('Unexpected removal receipt.')
    }, `Removal verified for ${reference}.`)
  }

  async function replayRemoval(reference: string) {
    const approvalReference = manifest?.completed_removal_approvals?.[reference]
    if (!manifest || !approvalReference || !window.confirm(`Reapply the approved removal to restored evidence ${reference}? Use this only after a backup restore.`)) return
    const signature = JSON.stringify([manifest.request_reference, approvalReference, reference])
    if (replayAttempt.current?.signature !== signature) replayAttempt.current = { signature, uuid: crypto.randomUUID() }
    const replayReference = replayAttempt.current.uuid
    await performAction(async (signal) => {
      const result = await replayApprovedRecordEvidenceRemoval(
        orgUuid, token, manifest.request_reference, approvalReference, reference, replayReference, signal,
      )
      if (result.evidence_reference !== reference) throw new Error('Unexpected replay receipt.')
    }, `Removal replay verified for ${reference}.`)
  }

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
      {status && <p role="status" className="text-sm text-green-800">{status}</p>}
      {manifest && (
        <div className="space-y-3 text-sm">
          <dl className="space-y-1">
            <div><dt>Cutoff</dt><dd><time dateTime={manifest.as_of}>{new Date(manifest.as_of).toLocaleString()}</time></dd></div>
            <div><dt>Active holds</dt><dd>{manifest.hold_references.length}</dd></div>
            <div><dt>Manifest digest</dt><dd className="break-all font-mono">{manifest.snapshot_sha256}</dd></div>
          </dl>
          {manifest.files.length === 0 ? <p>No case evidence files.</p> : (
            <ul aria-label="Case evidence dispositions" className="space-y-1">
              {manifest.files.map((file) => {
                const removalRecorded = Boolean(manifest.completed_removals[file.evidence_reference])
                const approvedCandidate = Boolean(receipt) && file.disposition === 'candidate' && !removalRecorded
                return <li key={file.evidence_reference} className="break-words">
                  <span>{file.evidence_reference} · {removalRecorded ? 'Removal recorded' : dispositions[file.disposition]}</span>
                  {approvedCandidate && <Button type="button" variant="destructive" className="ms-2 min-h-11"
                    disabled={busy} onClick={() => removeFile(file.evidence_reference)}>Remove approved evidence</Button>}
                  {removalRecorded && manifest.completed_removal_approvals?.[file.evidence_reference] && <Button type="button" variant="outline" className="ms-2 min-h-11"
                    disabled={busy} onClick={() => replayRemoval(file.evidence_reference)}>Replay removal after restore</Button>}
                </li>
              })}
            </ul>
          )}
          {receipt ? (
            <div role="status" className="break-words">
              <p>Removal review approved for {receipt.candidate_count} {receipt.candidate_count === 1 ? 'file' : 'files'}. Approval reference {receipt.approval_reference}.</p>
              <p className="mt-1">A different authorized reviewer must execute removal. Unsupported storage remains blocked by the server.</p>
            </div>
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
          {manifest.hold_references.length > 0 ? (
            <form onSubmit={releaseHold} className="space-y-2 border-t pt-3">
              <h3 className="font-medium">Release an active evidence hold</h3>
              <fieldset disabled={busy} className="flex flex-wrap items-end gap-3">
                <label className="grow text-sm font-medium">Hold reference
                  <select name="hold_reference" required className="mt-1 block min-h-11 w-full rounded-md border px-3">
                    {manifest.hold_references.map((reference) => <option key={reference} value={reference}>{reference}</option>)}
                  </select>
                </label>
                <label className="grow text-sm font-medium">Policy-approved release reason code
                  <Input name="release_reason" required maxLength={64} pattern="[a-z][a-z0-9_]{0,63}" autoComplete="off" />
                </label>
                <Button type="submit" variant="outline" className="min-h-11">Release hold</Button>
              </fieldset>
            </form>
          ) : (
            <form onSubmit={placeHold} className="space-y-2 border-t pt-3">
              <h3 className="font-medium">Place a temporary evidence hold</h3>
              <p>The server checks current D-13/D-18 authority. A hold prevents removal until reviewed and released.</p>
              <fieldset disabled={busy} className="flex flex-wrap items-end gap-3">
                <label className="grow text-sm font-medium">Policy-approved hold reason code
                  <Input name="hold_reason" required maxLength={64} pattern="[a-z][a-z0-9_]{0,63}" autoComplete="off" />
                </label>
                <label className="grow text-sm font-medium">Review due
                  <Input name="review_due" required type="datetime-local" />
                </label>
                <Button type="submit" variant="outline" className="min-h-11">Place evidence hold</Button>
              </fieldset>
            </form>
          )}
          {hasCompletedRemovals && <p className="text-xs text-muted-foreground">Removal replay is available only when a matching approved removal receipt is present and current server policy permits it.</p>}
        </div>
      )}
    </section>
  )
}
