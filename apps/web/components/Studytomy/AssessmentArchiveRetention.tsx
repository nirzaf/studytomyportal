'use client'

import { type FormEvent, useEffect, useRef, useState } from 'react'
import { Button } from '@components/ui/button'
import { Input } from '@components/ui/input'
import { AdaptiveRequestError, adaptiveRequest } from '@services/studytomy/adaptive'

type Receipt = { reference: string; round_uuid: string }
type Stage = 'approval' | 'execution'

export default function AssessmentArchiveRetention({ orgUuid, token }: { orgUuid: string; token: string }) {
  const [stage, setStage] = useState<Stage>('approval')
  const [confirmed, setConfirmed] = useState(false)
  const [receipt, setReceipt] = useState<{ stage: Stage; result: Receipt }>()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const active = useRef<AbortController | null>(null)

  useEffect(() => () => {
    active.current?.abort()
    active.current = null
  }, [orgUuid, token])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (active.current) return
    const fields = new FormData(event.currentTarget)
    const round = String(fields.get('round') ?? '').trim()
    const approval = String(fields.get('approval') ?? '').trim()
    const execute = stage === 'execution'
    if (!/^round_[0-9a-f]{58}$/.test(round)) return
    if (execute && (!approval || fields.get('confirm') !== 'yes')) return
    setBusy(true)
    setReceipt(undefined)
    setError('')
    const controller = new AbortController()
    active.current = controller
    const base = `assessment-archives/${encodeURIComponent(round)}/removal-approvals`
    try {
      const result = await adaptiveRequest(orgUuid, token)<Receipt>(
        execute ? `${base}/${encodeURIComponent(approval)}/execute` : base,
        'POST', undefined, undefined, controller.signal,
      )
      if (controller.signal.aborted) return
      if (result.round_uuid !== round) throw new Error('Unexpected archive receipt')
      setReceipt({ stage, result })
      setConfirmed(false)
    } catch (failure) {
      if (controller.signal.aborted) return
      const detail = failure instanceof AdaptiveRequestError && failure.status === 409
        ? failure.message : 'This archive or action is unavailable under your current access.'
      setError(detail)
    } finally {
      if (active.current === controller) active.current = null
      if (!active.current) setBusy(false)
    }
  }

  return <details className="space-y-3 rounded-lg border bg-white p-4">
    <summary className="cursor-pointer font-semibold">Assessment archive retention</summary>
    <p className="text-sm">An authorized privacy reviewer records approval. A different authorized reviewer executes it.</p>
    <form className="space-y-3" onSubmit={submit}>
      <fieldset disabled={busy} className="space-y-3">
        <label className="grid gap-1 text-sm">Action
          <select className="rounded border bg-background p-2" value={stage}
            onChange={(event) => {
              setStage(event.target.value === 'execution' ? 'execution' : 'approval')
              setConfirmed(false)
              setReceipt(undefined)
              setError('')
            }}>
            <option value="approval">Record approval</option>
            <option value="execution">Execute an existing approval</option>
          </select>
        </label>
        <label className="grid gap-1 text-sm">Assessment round reference
          <Input name="round" required maxLength={64} pattern="round_[0-9a-f]{58}"
            autoComplete="off" spellCheck={false} onChange={() => {
              setReceipt(undefined)
              setConfirmed(false)
            }} />
        </label>
        {stage === 'execution' && <>
          <label className="grid gap-1 text-sm">Approval reference
            <Input name="approval" required maxLength={36} autoComplete="off" spellCheck={false}
              pattern="[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
              onChange={() => {
                setReceipt(undefined)
                setConfirmed(false)
              }} />
          </label>
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" name="confirm" value="yes" required checked={confirmed}
              onChange={(event) => setConfirmed(event.target.checked)} />
            I confirm permanent removal of the archived responses for this assessment round.
          </label>
        </>}
        <Button type="submit" variant={stage === 'execution' ? 'destructive' : 'outline'}>
          {stage === 'execution' ? 'Remove archived responses' : 'Record archive removal approval'}
        </Button>
      </fieldset>
    </form>
    {busy && <p role="status" className="text-sm">Checking archive retention…</p>}
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {receipt && <p role="status" className="break-words text-sm">
      {receipt.stage === 'execution' ? 'Removal receipt' : 'Approval reference'} {receipt.result.reference}
    </p>}
  </details>
}
