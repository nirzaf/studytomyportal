'use client'

import { useState, type FormEvent } from 'react'
import { Button } from '@components/ui/button'
import { Input } from '@components/ui/input'
import {
  correctAllocation,
  reviewAllocation,
  type AllocationPreflight,
} from '@services/studytomy/family'

type Props = { orgUuid: string; accessToken: string }

export default function AllocationReview({ orgUuid, accessToken }: Props) {
  const [review, setReview] = useState<{
    learner: string; order: string; result: AllocationPreflight
  } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [receipt, setReceipt] = useState('')

  async function check(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    const form = new FormData(event.currentTarget)
    const learner = String(form.get('learner') ?? '').trim()
    const order = String(form.get('order') ?? '').trim()
    setBusy(true)
    setError('')
    setReceipt('')
    setReview(null)
    try {
      setReview({ learner, order, result: await reviewAllocation(orgUuid, learner, order, accessToken) })
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Unable to review this allocation.')
    } finally {
      setBusy(false)
    }
  }

  async function correct(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || receipt || review?.result.status !== 'unstarted') return
    const form = new FormData(event.currentTarget)
    const reason = String(form.get('reason') ?? '')
    if (!review.result.reason_codes.includes(reason)) return
    setBusy(true)
    setError('')
    try {
      const result = await correctAllocation(orgUuid, review.learner, review.order, accessToken, {
        replacement_learner_uuid: String(form.get('replacement') ?? '').trim(),
        reason_code: reason,
      })
      setReceipt(result.correction_uuid)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Unable to correct this allocation.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section aria-labelledby="allocation-review-title" className="space-y-4 rounded-lg border bg-white p-5 shadow-sm">
      <h2 id="allocation-review-title" className="text-lg font-semibold">Review a learner allocation</h2>
      <form onSubmit={check} onChange={() => { setReview(null); setReceipt(''); setError('') }}>
        <fieldset disabled={busy} className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1 text-sm font-medium">
            Original learner reference
            <Input name="learner" maxLength={100} required />
          </label>
          <label className="space-y-1 text-sm font-medium">
            Order reference
            <Input name="order" maxLength={36} required />
          </label>
          <Button type="submit" variant="outline" className="min-h-11 justify-self-start">Check allocation</Button>
        </fieldset>
      </form>

      {review?.result.status === 'used' && (
        <p role="status">Content has already been opened. This case requires exception review.</p>
      )}
      {review?.result.status === 'evidence_unavailable' && (
        <p role="status">The available evidence cannot establish that this allocation is unstarted.</p>
      )}
      {review?.result.status === 'unstarted' && !review.result.reason_codes.length && (
        <p role="status">The allocation is unstarted. Current policy has no approved correction reasons.</p>
      )}
      {review?.result.status === 'unstarted' && review.result.reason_codes.length > 0 && !receipt && (
        <form onSubmit={correct} className="space-y-3 border-t pt-4">
          <p>The allocation is unstarted. Correction changes future course access and preserves learning records.</p>
          <fieldset disabled={busy} className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1 text-sm font-medium">
              Replacement learner reference
              <Input name="replacement" maxLength={100} required />
            </label>
            <label className="space-y-1 text-sm font-medium">
              Approved reason
              <select name="reason" required defaultValue="" className="block min-h-11 w-full rounded-md border bg-background px-3">
                <option value="" disabled>Select a reason</option>
                {review.result.reason_codes.map((reason) => (
                  <option key={reason} value={reason}>{reason.replaceAll('_', ' ')}</option>
                ))}
              </select>
            </label>
            <Button type="submit" className="min-h-11 justify-self-start">Correct allocation</Button>
          </fieldset>
        </form>
      )}
      {busy && <p role="status">Checking with the server…</p>}
      {error && <p role="alert" className="text-sm text-red-800">{error}</p>}
      {receipt && <p role="status" className="break-words">Allocation corrected. Reference {receipt}.</p>}
    </section>
  )
}
