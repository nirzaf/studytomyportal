'use client'

import { type FormEvent, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { Button } from '@components/ui/button'
import { getAuthenticatedSessionIdentity } from '@/lib/auth/sessionScope'
import { formatCurrency } from '@/lib/format'
import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'

type RequestReview = {
  refund_reference: string
  attempt_reference: string
  amount_minor: number
  currency: string
  reason_code: string
  status: 'requested' | 'awaiting_second_approval' | 'reserved' | 'settled'
}

const referencePattern = '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'

export default function RefundReview({ orgUuid }: { orgUuid: string }) {
  const session = useLHSession()
  const token = session?.data?.tokens?.access_token
  if (session?.status !== 'authenticated' || !token) return null
  const user = session.data?.user
  const identity = getAuthenticatedSessionIdentity(user?.id ?? user?.user_uuid, session.data)
  return <ReviewForm key={JSON.stringify([identity, orgUuid])} orgUuid={orgUuid} token={token} />
}

function ReviewForm({ orgUuid, token }: { orgUuid: string; token: string }) {
  const { i18n } = useTranslation()
  const [review, setReview] = useState<{ order: string; request: RequestReview }>()
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string>()
  const pending = useRef<AbortController | null>(null)
  const base = `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/collections`

  useEffect(() => {
    function clear() {
      pending.current?.abort()
      pending.current = null
      setReview(undefined)
      setMessage(undefined)
      setBusy(false)
    }
    function visibility() {
      if (document.visibilityState === 'hidden') clear()
    }
    document.addEventListener('visibilitychange', visibility)
    window.addEventListener('pagehide', clear)
    return () => {
      pending.current?.abort()
      document.removeEventListener('visibilitychange', visibility)
      window.removeEventListener('pagehide', clear)
    }
  }, [])

  async function send(path: string, onSuccess: (_data: Response, _signal: AbortSignal) => Promise<void>, body?: object) {
    if (pending.current) return
    const controller = new AbortController()
    pending.current = controller
    setMessage(undefined)
    setBusy(true)
    try {
      const result = await secureFetch(`${base}/${path}`, {
        ...RequestBodyWithAuthHeader(body ? 'PUT' : 'GET', body, null, token),
        signal: controller.signal, cache: 'no-store',
      })
      if (controller.signal.aborted) return
      if (result.ok) await onSuccess(result, controller.signal)
      else if ([401, 403, 404].includes(result.status)) {
        setReview(undefined)
        setMessage('This refund request is unavailable with your current access.')
      } else if (result.status === 409) {
        setMessage('Current policy, request terms or the remaining collection balance prevent this action.')
      } else {
        setMessage('Check the request again to confirm its status.')
      }
    } catch {
      if (!controller.signal.aborted) setMessage('Check the request again to confirm its status.')
    } finally {
      if (pending.current === controller) {
        pending.current = null
        setBusy(false)
      }
    }
  }

  function lookup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const fields = new FormData(event.currentTarget)
    const order = String(fields.get('order') ?? '').toLowerCase()
    const refund = String(fields.get('refund') ?? '').toLowerCase()
    setReview(undefined)
    return send(`${encodeURIComponent(order)}/refund-requests/${encodeURIComponent(refund)}`, async (response, signal) => {
      const request: RequestReview = await response.json()
      if (signal.aborted) return
      if (request.refund_reference !== refund) throw new Error('Refund reference mismatch')
      setReview({ order, request })
    })
  }

  function reserve() {
    if (!review || review.request.status !== 'requested') return
    const { order, request } = review
    const { amount_minor, currency, reason_code, attempt_reference, refund_reference } = request
    const path = `${encodeURIComponent(order)}/manual-attempts/${encodeURIComponent(attempt_reference)}/refund-reservations/${encodeURIComponent(refund_reference)}`
    return send(path, async (response, signal) => {
      const receipt: Pick<RequestReview, 'refund_reference' | 'status'> = await response.json()
      if (signal.aborted) return
      if (receipt.refund_reference !== refund_reference || !['reserved', 'awaiting_second_approval'].includes(receipt.status)) {
        throw new Error('Reservation receipt mismatch')
      }
      setReview({ order, request: { ...request, status: receipt.status } })
    }, { amount_minor, currency, reason_code })
  }

  const request = review?.request
  return (
    <section aria-labelledby="refund-review-heading" className="mx-auto w-full max-w-4xl space-y-4 px-4 py-6 sm:px-6">
      <h2 id="refund-review-heading" className="text-xl font-semibold">Refund requests</h2>
      <form onSubmit={lookup} autoComplete="off">
        <fieldset disabled={busy} className="flex flex-wrap items-end gap-3">
          <label className="grid min-w-0 flex-1 basis-56 gap-1">
            Order reference
            <input name="order" required pattern={referencePattern} maxLength={36} className="min-h-11 rounded-md border px-3" />
          </label>
          <label className="grid min-w-0 flex-1 basis-56 gap-1">
            Refund request reference
            <input name="refund" required pattern={referencePattern} maxLength={36} className="min-h-11 rounded-md border px-3" />
          </label>
          <Button type="submit" variant="outline" className="min-h-11">Find request</Button>
        </fieldset>
      </form>
      {busy && <p role="status">Checking refund request…</p>}
      {message && <p role="alert">{message}</p>}
      {request && (
        <div className="space-y-3 rounded-lg border p-4">
          <dl className="grid gap-3 sm:grid-cols-2">
            <div><dt>Requested amount</dt><dd>{formatCurrency(
              request.amount_minor / 10 ** (new Intl.NumberFormat(i18n.language, {
                style: 'currency', currency: request.currency,
              }).resolvedOptions().maximumFractionDigits ?? 2),
              request.currency, i18n.language, { currencyDisplay: 'code' },
            )}</dd></div>
            <div><dt>Reason</dt><dd className="break-words">{request.reason_code.replaceAll('_', ' ')}</dd></div>
          </dl>
          {request.status === 'settled'
            ? <p role="status">External money return confirmed.</p>
            : request.status === 'reserved'
            ? <p role="status">Funds reserved. Money return is pending.</p>
            : request.status === 'awaiting_second_approval'
              ? <p role="status">Approval recorded. A second approver is required before funds are reserved.</p>
              : <Button type="button" disabled={busy} className="min-h-11" onClick={() => void reserve()}>Approve refund</Button>}
        </div>
      )}
    </section>
  )
}
