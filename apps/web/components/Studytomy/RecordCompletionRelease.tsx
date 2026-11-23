'use client'

import { type FormEvent, useEffect, useRef, useState } from 'react'
import { Button } from '@components/ui/button'
import { Input } from '@components/ui/input'
import {
  previewRecordCompletionExport,
  releaseRecordCompletionExport,
  type CompletionExportPreview,
} from '@services/studytomy/privacy'

export default function RecordCompletionRelease({ orgUuid, token }: { orgUuid: string; token: string }) {
  const [preview, setPreview] = useState<{ reference: string; snapshot: CompletionExportPreview } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [expiry, setExpiry] = useState('')
  const pending = useRef<AbortController | null>(null)

  useEffect(() => () => pending.current?.abort(), [])

  async function prepare(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    const reference = String(new FormData(event.currentTarget).get('reference') ?? '').trim()
    const controller = new AbortController()
    pending.current = controller
    setPreview(null)
    setExpiry('')
    setError('')
    setBusy(true)
    try {
      const snapshot = await previewRecordCompletionExport(orgUuid, token, reference, controller.signal)
      if (!controller.signal.aborted) setPreview({ reference, snapshot })
    } catch {
      if (!controller.signal.aborted) setError('Preview is unavailable. Check the case reference and its preparation approval.')
    } finally {
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  async function release() {
    if (!preview || busy || expiry) return
    const controller = new AbortController()
    pending.current = controller
    setBusy(true)
    setError('')
    try {
      const result = await releaseRecordCompletionExport(
        orgUuid, token, preview.reference, preview.snapshot.snapshot_sha256, controller.signal,
      )
      if (!controller.signal.aborted) setExpiry(result.available_until)
    } catch {
      if (!controller.signal.aborted) setError('Release could not be confirmed. Preview the current records before trying again.')
    } finally {
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  return (
    <section aria-labelledby="completion-release-title" className="space-y-3 rounded-lg border bg-white p-4">
      <h2 id="completion-release-title" className="font-semibold">Release reviewed course completions</h2>
      <form onSubmit={prepare}>
        <fieldset disabled={busy} className="flex flex-wrap items-end gap-3">
          <label className="grow text-sm font-medium">Approved request reference
            <Input name="reference" required maxLength={38} autoComplete="off" spellCheck={false}
              onChange={() => { setPreview(null); setExpiry(''); setError('') }} />
          </label>
          <Button type="submit" variant="outline" className="min-h-11">Preview export</Button>
        </fieldset>
      </form>
      {busy && <p role="status" className="text-sm">Checking with the server…</p>}
      {error && <p role="alert" className="text-sm text-red-800">{error}</p>}
      {preview && (
        <div className="space-y-3 text-sm">
          {preview.snapshot.completions.length ? (
            <ul aria-label="Course completions to release" className="space-y-1">
              {preview.snapshot.completions.map((row, index) => (
                <li key={index} className="break-words">
                  {row.course_uuid}
                  {row.release_uuid ? ` · Reviewed release ${row.release_uuid}` : ' · Historical course record'}
                  {' · '}<time dateTime={row.recorded_at}>{new Date(row.recorded_at).toLocaleString()}</time>
                </li>
              ))}
            </ul>
          ) : <p>No eligible completion records in this snapshot.</p>}
          {expiry ? (
            <p role="status">Released until <time dateTime={expiry}>{new Date(expiry).toLocaleString()}</time>.</p>
          ) : <Button type="button" disabled={busy} className="min-h-11" onClick={() => void release()}>
            Release this snapshot
          </Button>}
        </div>
      )}
    </section>
  )
}
