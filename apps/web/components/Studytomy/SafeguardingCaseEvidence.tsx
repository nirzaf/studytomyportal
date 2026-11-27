'use client'

import { type FormEvent, useEffect, useRef, useState } from 'react'
import { Button } from '@components/ui/button'
import { Input } from '@components/ui/input'
import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'

const evidenceReference = 'ev_[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'

export default function SafeguardingCaseEvidence({ orgUuid, token, caseUuid, canAttach }: {
  orgUuid: string; token: string; caseUuid: string; canAttach: boolean
}) {
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ text: string; error?: boolean }>()
  const pending = useRef<AbortController | null>(null)
  const uploadKey = useRef<string | null>(null)
  const urls = useRef(new Set<string>())
  const path = `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/safeguarding/cases/${encodeURIComponent(caseUuid)}/evidence`

  useEffect(() => {
    const downloads = urls.current
    return () => {
      pending.current?.abort()
      pending.current = null
      for (const url of downloads) URL.revokeObjectURL(url)
      downloads.clear()
    }
  }, [path, token])

  async function run(operation: (_signal: AbortSignal) => Promise<string>) {
    if (pending.current) return
    const request = new AbortController()
    pending.current = request
    setBusy(true)
    setMessage(undefined)
    try {
      const text = await operation(request.signal)
      if (!request.signal.aborted) setMessage({ text })
    } catch {
      if (!request.signal.aborted) {
        setMessage({ error: true, text: 'Evidence is unavailable under current access or policy. Check the file or reference and try again.' })
      }
    } finally {
      if (pending.current === request) pending.current = null
      if (!pending.current) setBusy(false)
    }
  }

  function attach(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canAttach || pending.current) return
    const form = event.currentTarget
    const body = new FormData(form)
    const file = body.get('file')
    if (!(file instanceof File) || !file.size) return
    const key = uploadKey.current ??= crypto.randomUUID()
    void run(async (signal) => {
      const response = await secureFetch(path, {
        method: 'POST', body, signal, cache: 'no-store', redirect: 'error',
        headers: { Authorization: `Bearer ${token}`, 'Idempotency-Key': key },
      })
      if (!response.ok) throw new Error('Attachment unavailable')
      const result: { evidence_uuid: string } = await response.json()
      if (!new RegExp(`^${evidenceReference}$`).test(result.evidence_uuid)) throw new Error('Invalid receipt')
      if (!signal.aborted) {
        form.reset()
        uploadKey.current = null
      }
      return `Evidence attached. Reference ${result.evidence_uuid}`
    })
  }

  function download(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const reference = String(new FormData(event.currentTarget).get('reference') ?? '').trim()
    if (!new RegExp(`^${evidenceReference}$`).test(reference)) return
    void run(async (signal) => {
      const response = await secureFetch(`${path}/${encodeURIComponent(reference)}`, {
        ...RequestBodyWithAuthHeader('GET', null, null, token),
        signal, redirect: 'error',
      })
      if (!response.ok) throw new Error('Download unavailable')
      const content = await response.blob()
      if (signal.aborted) return ''
      const objectUrl = URL.createObjectURL(content)
      const downloads = urls.current
      downloads.add(objectUrl)
      const link = document.createElement('a')
      link.href = objectUrl
      const attachment = response.headers.get('Content-Disposition') ?? ''
      const filename = /^attachment; filename="(evidence-ev_[0-9a-f-]+\.(?:pdf|jpg|png|gif|webp|bin))"$/.exec(attachment)?.[1]
      link.download = filename?.startsWith(`evidence-${reference}.`) ? filename : reference
      link.click()
      window.setTimeout(() => {
        if (downloads.delete(objectUrl)) URL.revokeObjectURL(objectUrl)
      }, 1000)
      return 'Evidence download requested.'
    })
  }

  return <section aria-label="Safeguarding evidence" className="space-y-3 border-t pt-3">
    <h3 className="text-sm font-medium">Safeguarding evidence</h3>
    {canAttach && <form onSubmit={attach} className="flex flex-wrap items-end gap-2">
      <label className="grid gap-1 text-sm">Evidence file
        <Input name="file" type="file" required disabled={busy} onChange={() => { uploadKey.current = null }} />
      </label>
      <Button type="submit" variant="outline" disabled={busy}>Attach evidence</Button>
    </form>}
    <form onSubmit={download} className="flex flex-wrap items-end gap-2">
      <label className="grid gap-1 text-sm">Evidence reference
        <Input name="reference" required maxLength={39} pattern={evidenceReference} autoComplete="off" />
      </label>
      <Button type="submit" variant="outline" disabled={busy}>Download evidence</Button>
    </form>
    {busy && <p role="status" className="text-sm">Checking evidence…</p>}
    {message && <p role={message.error ? 'alert' : 'status'} className="break-all text-sm">{message.text}</p>}
  </section>
}
