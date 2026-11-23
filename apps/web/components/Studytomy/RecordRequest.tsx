'use client'

import { type FormEvent, useEffect, useRef, useState } from 'react'
import { Button } from '@components/ui/button'
import { Input } from '@components/ui/input'
import {
  checkRecordRequest,
  downloadRecordCompletionExport,
  getRecordRequestStatus,
  submitRecordRequest,
  type RecordRequestReceipt,
  type RecordRequestStatus,
} from '@services/studytomy/privacy'

const statusLabels: Record<RecordRequestStatus['status'], string> = {
  received: 'Received for privacy review',
  approved_for_preparation: 'Approved for record preparation',
  denied: 'Request declined',
}

export default function RecordRequest({ orgUuid, accessToken }: { orgUuid: string; accessToken: string }) {
  const [available, setAvailable] = useState(false)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  const [receipt, setReceipt] = useState<RecordRequestReceipt | null>(null)
  const [lookupReference, setLookupReference] = useState('')
  const [lookupBusy, setLookupBusy] = useState(false)
  const [lookupError, setLookupError] = useState('')
  const [status, setStatus] = useState<RecordRequestStatus | null>(null)
  const [downloading, setDownloading] = useState(false)
  const reference = useRef<string | null>(null)
  const downloadRequest = useRef<AbortController | null>(null)

  useEffect(() => () => downloadRequest.current?.abort(), [])

  useEffect(() => {
    const controller = new AbortController()
    checkRecordRequest(orgUuid, accessToken, controller.signal)
      .then((approved) => { if (!controller.signal.aborted) setAvailable(approved) })
      .catch(() => { if (!controller.signal.aborted) setAvailable(false) })
    return () => controller.abort()
  }, [orgUuid, accessToken])

  async function submit() {
    if (!available || busy || receipt) return
    setBusy(true)
    setFailed(false)
    try {
      reference.current ??= crypto.randomUUID()
      const result = await submitRecordRequest(orgUuid, accessToken, reference.current)
      setReceipt(result)
      setLookupReference((current) => current || result.request_reference)
    } catch {
      setFailed(true)
    } finally {
      setBusy(false)
    }
  }

  async function lookup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (lookupBusy) return
    const ref = lookupReference.trim()
    setStatus(null)
    setLookupError('')
    if (!/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(ref)) {
      setLookupError('Enter the reference shown on your request receipt.')
      return
    }
    setLookupBusy(true)
    try {
      setStatus(await getRecordRequestStatus(orgUuid, accessToken, ref))
    } catch {
      setLookupError('Status is unavailable. Check your receipt reference and try again.')
    } finally {
      setLookupBusy(false)
    }
  }

  async function download() {
    if (downloading || status?.status !== 'approved_for_preparation') return
    const controller = new AbortController()
    downloadRequest.current = controller
    setDownloading(true)
    setLookupError('')
    try {
      const blob = await downloadRecordCompletionExport(orgUuid, accessToken, status.request_reference, controller.signal)
      if (controller.signal.aborted) return
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = 'learning-completions.json'
      link.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch {
      if (!controller.signal.aborted) setLookupError('The reviewed export is not available for download.')
    } finally {
      if (!controller.signal.aborted) setDownloading(false)
    }
  }

  return (
    <section aria-labelledby="studytomy-record-request-title" className="space-y-3 rounded-lg border bg-white p-5">
      <h2 id="studytomy-record-request-title" className="font-semibold">Learning record requests</h2>
      {receipt ? (
        <div role="status" className="space-y-1 text-sm">
          <p>Request received for privacy review.</p>
          <p className="break-words">Reference {receipt.request_reference}</p>
          <p>Review due <time dateTime={receipt.due_at}>{new Date(receipt.due_at).toLocaleDateString()}</time>.</p>
        </div>
      ) : available && (
        <>
          <p className="text-sm">You can request eligible retained records even after course access ends.</p>
          <Button type="button" variant="outline" className="min-h-11" disabled={busy} onClick={() => void submit()}>
            {busy ? 'Sending request…' : 'Request records'}
          </Button>
          {failed && <p role="alert" className="text-sm text-red-800">The request could not be confirmed. Try again to check the same request.</p>}
        </>
      )}
      <form onSubmit={lookup} className="space-y-3">
        <label className="block space-y-1 text-sm font-medium">
          Request reference
          <Input
            name="request_reference"
            value={lookupReference}
            onChange={(event) => {
              setLookupReference(event.target.value)
              setStatus(null)
              setLookupError('')
            }}
            spellCheck={false}
            autoComplete="off"
            maxLength={38}
            required
            disabled={lookupBusy || downloading}
            aria-describedby={lookupError ? 'studytomy-record-request-status-error' : undefined}
          />
        </label>
        <Button type="submit" variant="outline" className="min-h-11" disabled={lookupBusy || downloading}>
          {lookupBusy ? 'Checking status…' : 'Check request status'}
        </Button>
        {lookupError && <p id="studytomy-record-request-status-error" role="alert" className="text-sm text-red-800">{lookupError}</p>}
        {status && <div role="status" className="space-y-1 text-sm">
          <p>{statusLabels[status.status]}</p>
          {status.status === 'received' && <p>Review due <time dateTime={status.due_at}>
            {new Date(status.due_at).toLocaleDateString()}
          </time>.</p>}
        </div>}
        {status?.status === 'approved_for_preparation' && (
          <Button type="button" variant="outline" className="min-h-11" disabled={downloading} onClick={() => void download()}>
            {downloading ? 'Checking download…' : 'Download reviewed completions'}
          </Button>
        )}
      </form>
    </section>
  )
}
