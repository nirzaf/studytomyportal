'use client'

import { type FormEvent, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@components/ui/button'
import { formatCurrency } from '@/lib/format'
import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'

type Options = { currency: string; maximum_minor: number; reason_codes: string[] }
type RequestTerms = { amount_minor: number; currency: string; reason_code: string }
type Operation = { reference: string; terms: RequestTerms }
type RefundPage = {
  requests: { refund_reference: string; status: 'requested' | 'reserved' | 'settled' }[]
  next_cursor: string | null
}
const refundStatus: Record<RefundPage['requests'][number]['status'], string> = {
  requested: 'Awaiting review',
  reserved: 'Funds reserved. Money return is pending.',
  settled: 'Money return confirmed.',
}

export default function PurchaserRefundRequest({ orgUuid, orderRef, token }: {
  orgUuid: string; orderRef: string; token: string
}) {
  const { i18n } = useTranslation()
  const [options, setOptions] = useState<Options>()
  const [operation, setOperation] = useState<Operation>()
  const [submitting, setSubmitting] = useState(false)
  const [receipt, setReceipt] = useState<string>()
  const [error, setError] = useState<string>()
  const [history, setHistory] = useState<RefundPage>()
  const [cursor, setCursor] = useState<string>()
  const [refreshHistory, setRefreshHistory] = useState(0)
  const pending = useRef<AbortController | null>(null)
  const base = `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/checkout/orders/${encodeURIComponent(orderRef)}`

  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      try {
        const response = await secureFetch(`${base}/refund-options`, {
          ...RequestBodyWithAuthHeader('GET', null, null, token),
          signal: controller.signal, cache: 'no-store',
        })
        if (!response.ok) return
        const data: Options = await response.json()
        if (!controller.signal.aborted && data.reason_codes.length > 0) setOptions(data)
      } catch {
        // Keep the receipt usable when optional refund intake is unavailable.
      }
    }
    void load()
    return () => {
      controller.abort()
      pending.current?.abort()
    }
  }, [base, token])

  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      const query = cursor ? `?${new URLSearchParams({ after: cursor })}` : ''
      try {
        const response = await secureFetch(`${base}/refund-requests${query}`, {
          ...RequestBodyWithAuthHeader('GET', null, null, token),
          signal: controller.signal, cache: 'no-store',
        })
        const page: RefundPage | undefined = response.ok ? await response.json() : undefined
        if (!controller.signal.aborted) setHistory(page)
      } catch {
        if (!controller.signal.aborted) setHistory(undefined)
      }
    }
    void load()
    return () => controller.abort()
  }, [base, token, cursor, refreshHistory])

  const digits = options ? new Intl.NumberFormat(i18n.language, { style: 'currency', currency: options.currency })
    .resolvedOptions().maximumFractionDigits ?? 2 : 2

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending.current || !options) return
    const fields = new FormData(event.currentTarget)
    const input = String(fields.get('amount') ?? '').trim()
    const reason = String(fields.get('reason') ?? '')
    const match = /^(\d{1,16})(?:[.,](\d{1,4}))?$/.exec(input)
    const fraction = match?.[2] ?? ''
    const minor = match && fraction.length <= digits ? Number(match[1] + fraction.padEnd(digits, '0')) : 0
    if (!Number.isSafeInteger(minor) || minor <= 0 || minor > options.maximum_minor || !options.reason_codes.includes(reason)) {
      setError('Enter a valid refund amount and select a reason.')
      return
    }
    const next = { reference: crypto.randomUUID(), terms: {
      amount_minor: minor, currency: options.currency, reason_code: reason,
    } }
    setOperation(next)
    void send(next)
  }

  async function send(attempt: Operation) {
    if (pending.current) return
    const controller = new AbortController()
    pending.current = controller
    setSubmitting(true)
    setError(undefined)
    try {
      const result = await secureFetch(`${base}/refund-requests/${attempt.reference}`, {
        ...RequestBodyWithAuthHeader('PUT', attempt.terms, null, token),
        signal: controller.signal, cache: 'no-store',
      })
      if (controller.signal.aborted) return
      if (!result.ok) {
        if (result.status >= 500) throw new Error('Refund response unavailable')
        setOperation(undefined)
        setError('This request could not be submitted under the current policy. Check your details and try again.')
        return
      }
      const data = await result.json() as { refund_reference?: string; status?: string }
      if (controller.signal.aborted) return
      if (data.refund_reference !== attempt.reference || data.status !== 'requested') {
        throw new Error('Refund receipt mismatch')
      }
      setReceipt(attempt.reference)
      setCursor(undefined)
      setRefreshHistory(value => value + 1)
    } catch {
      if (!controller.signal.aborted) setError('The request may have been received. Retry with the same reference.')
    } finally {
      if (pending.current === controller) {
        pending.current = null
        setSubmitting(false)
      }
    }
  }

  if (!options && !history?.requests.length) return null

  return (<>
    {options && <section aria-label="Refund request" className="space-y-3 rounded-lg border p-4">
      <h3 className="font-semibold">Request a refund</h3>
      {receipt ? (
        <div role="status">
          <p>Refund request received. It is awaiting review.</p>
          <p className="break-words">Request reference {receipt}</p>
        </div>
      ) : (
        <form onSubmit={submit} autoComplete="off" className="space-y-3">
          <fieldset disabled={submitting || operation !== undefined} className="grid gap-3">
            <label className="grid gap-1">
              Refund amount ({options.currency})
              <input name="amount" inputMode="decimal" required maxLength={21} className="min-h-11 rounded-md border px-3" />
            </label>
            <p className="text-sm text-muted-foreground">Maximum request amount {formatCurrency(
              options.maximum_minor / 10 ** digits, options.currency, i18n.language, { currencyDisplay: 'code' },
            )}</p>
            <label className="grid gap-1">
              Refund reason
              <select name="reason" required defaultValue="" className="min-h-11 rounded-md border bg-background px-3">
                <option value="" disabled>Select a reason</option>
                {options.reason_codes.map(reason => <option key={reason} value={reason}>{reason.replaceAll('_', ' ')}</option>)}
              </select>
            </label>
          </fieldset>
          {error && <p role="alert">{error}</p>}
          {operation ? (
            <>
              <p className="break-words">Request reference {operation.reference}</p>
              <Button type="button" className="min-h-11" disabled={submitting} onClick={() => void send(operation)}>
                {submitting ? 'Submitting…' : 'Retry request'}
              </Button>
            </>
          ) : (
            <Button type="submit" className="min-h-11">Submit refund request</Button>
          )}
        </form>
      )}
    </section>}
    {!!history?.requests.length && (
      <section aria-label="Refund request history" className="space-y-3 rounded-lg border p-4">
        <h3 className="font-semibold">Refund request history</h3>
        <ul className="space-y-2">
          {history.requests.map(request => (
            <li key={request.refund_reference}>
              <p className="break-words">{request.refund_reference}</p>
              <p className="text-sm text-muted-foreground">
                {refundStatus[request.status]}
              </p>
            </li>
          ))}
        </ul>
        <div className="flex flex-wrap gap-2">
          {cursor && <Button type="button" variant="outline" onClick={() => setCursor(undefined)}>First page</Button>}
          {history.next_cursor && <Button type="button" variant="outline"
            onClick={() => setCursor(history.next_cursor ?? undefined)}>Next page</Button>}
        </div>
      </section>
    )}
  </>)
}
