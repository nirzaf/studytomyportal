'use client'

import { type FormEvent, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { Button } from '@components/ui/button'
import { getAuthenticatedSessionIdentity } from '@/lib/auth/sessionScope'
import { formatCurrency, formatDateTime } from '@/lib/format'
import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'

type Offer = { offer_uuid: string; offer_version_uuid: string; title: string; version: number }
type Cash = {
  currency: string
  collected_minor: number
  refunded_minor: number
  chargeback_minor: number
  reversed_chargeback_minor: number
  net_minor: number
  collection_count: number
  settled_refund_count: number
  chargeback_count: number
  chargeback_reversal_count: number
}
type Report = {
  org_uuid: string
  offer_uuid: string
  offer_version_uuid: string
  period_start: string
  period_end: string
  observed_at: string
  query_version: string
  population: string
  exclusions: string
  confirmed_cash: Cash[]
}

export default function PilotEvidenceDashboard({ orgUuid }: { orgUuid: string }) {
  const session = useLHSession()
  const token = session?.data?.tokens?.access_token
  if (session?.status !== 'authenticated' || !token) return null
  const user = session.data?.user
  const identity = getAuthenticatedSessionIdentity(user?.id ?? user?.user_uuid, session.data)
  return <CashReport key={JSON.stringify([orgUuid, identity])} orgUuid={orgUuid} token={token} />
}

function CashReport({ orgUuid, token }: { orgUuid: string; token: string }) {
  const { i18n } = useTranslation()
  const [offers, setOffers] = useState<Offer[]>([])
  const [report, setReport] = useState<Report>()
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const pending = useRef<AbortController | null>(null)
  const base = `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/pilot`

  useEffect(() => {
    let loading: AbortController | undefined
    function clear() {
      loading?.abort()
      pending.current?.abort()
      pending.current = null
      setOffers([])
      setReport(undefined)
      setMessage('')
      setBusy(false)
    }
    async function refresh() {
      clear()
      if (document.visibilityState === 'hidden') return
      const controller = new AbortController()
      loading = controller
      try {
        const response = await secureFetch(`${base}/cash-report-offers`, {
          ...RequestBodyWithAuthHeader('GET', null, null, token), cache: 'no-store', signal: controller.signal,
        })
        if (!response.ok) return
        const options: Offer[] = await response.json()
        if (!controller.signal.aborted) setOffers(options)
      } catch {
        // The finance workspace remains available when no authorized report can be loaded.
      }
    }
    void refresh()
    document.addEventListener('visibilitychange', refresh)
    window.addEventListener('pagehide', clear)
    window.addEventListener('pageshow', refresh)
    return () => {
      loading?.abort()
      pending.current?.abort()
      document.removeEventListener('visibilitychange', refresh)
      window.removeEventListener('pagehide', clear)
      window.removeEventListener('pageshow', refresh)
    }
  }, [base, token])

  async function loadReport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending.current) return
    setReport(undefined)
    setMessage('')
    const fields = new FormData(event.currentTarget)
    const offer = offers.find(entry => entry.offer_version_uuid === fields.get('offer'))
    const start = `${fields.get('start')}Z`
    const end = `${fields.get('end')}Z`
    if (!offer || !(Date.parse(start) < Date.parse(end))) {
      setMessage('Choose an offer and an end time after the start time.')
      return
    }
    const controller = new AbortController()
    pending.current = controller
    setBusy(true)
    try {
      const path = `offers/${encodeURIComponent(offer.offer_uuid)}/versions/${encodeURIComponent(offer.offer_version_uuid)}/confirmed-cash`
      const response = await secureFetch(`${base}/${path}?${new URLSearchParams({ start, end })}`, {
        ...RequestBodyWithAuthHeader('GET', null, null, token), cache: 'no-store', signal: controller.signal,
      })
      if (!response.ok) throw new Error('Report unavailable')
      const data: Report = await response.json()
      if (data.org_uuid !== orgUuid || data.offer_uuid !== offer.offer_uuid || data.offer_version_uuid !== offer.offer_version_uuid) {
        throw new Error('Report scope mismatch')
      }
      if (!controller.signal.aborted) setReport(data)
    } catch {
      if (!controller.signal.aborted) setMessage('Report unavailable. Check your access and the reporting period, then try again.')
    } finally {
      if (pending.current === controller) {
        pending.current = null
        setBusy(false)
      }
    }
  }

  if (!offers.length) return null
  const input = 'block w-full rounded-md border border-input bg-background px-3 py-2'
  return <section className="space-y-4 p-4" aria-labelledby="pilot-cash-title">
    <h2 id="pilot-cash-title" className="text-xl font-semibold">Pilot cash report</h2>
    <form onSubmit={loadReport} onChange={() => { setReport(undefined); setMessage('') }}>
      <fieldset disabled={busy} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <label>Offer version<select className={input} name="offer" required>
          <option value="">Choose an offer</option>
          {offers.map(offer => <option key={offer.offer_version_uuid} value={offer.offer_version_uuid}>
            {offer.title} (version {offer.version})
          </option>)}
        </select></label>
        <label>Start (UTC)<input className={input} name="start" type="datetime-local" step="1" required /></label>
        <label>End (UTC, excluded)<input className={input} name="end" type="datetime-local" step="1" required /></label>
        <Button type="submit" className="self-end">{busy ? 'Loading report…' : 'Load cash report'}</Button>
      </fieldset>
    </form>
    {message && <p role="status">{message}</p>}
    {report && <div className="space-y-3" aria-live="polite">
      <p>{report.population} {report.exclusions}</p>
      <p className="text-sm text-muted-foreground">
        {formatDateTime(report.period_start, i18n.language, { timeZone: 'UTC' })} to{' '}
        {formatDateTime(report.period_end, i18n.language, { timeZone: 'UTC' })} (end excluded, UTC).
        {' '}Checked {formatDateTime(report.observed_at, i18n.language, { timeZone: 'UTC' })} UTC.
        {' '}Query {report.query_version}.
      </p>
      {report.confirmed_cash.length === 0 && <p>No confirmed cash movements in this period.</p>}
      {report.confirmed_cash.map(row => {
        const digits = new Intl.NumberFormat(i18n.language, { style: 'currency', currency: row.currency })
          .resolvedOptions().maximumFractionDigits ?? 2
        const currency = (minor: number) => Number.isSafeInteger(minor)
          ? formatCurrency(minor / 10 ** digits, row.currency, i18n.language, { currencyDisplay: 'code' })
          : 'Amount unavailable'
        const measures = [
          ['Confirmed collections', row.collected_minor, row.collection_count],
          ['Settled refunds', row.refunded_minor, row.settled_refund_count],
          ['Chargebacks', row.chargeback_minor, row.chargeback_count],
          ['Chargeback reversals', row.reversed_chargeback_minor, row.chargeback_reversal_count],
          ['Net cash', row.net_minor, null],
        ] as const
        return <div key={row.currency} className="rounded-md border p-3">
          <h3 className="font-semibold">{row.currency}</h3>
          <dl className="divide-y">
            {measures.map(([label, amount, count]) => <div key={label} className="flex flex-wrap justify-between gap-x-4 py-2">
              <dt>{label}{count !== null && ` (${count})`}</dt>
              <dd className="tabular-nums">{currency(amount)}</dd>
            </div>)}
          </dl>
        </div>
      })}
    </div>}
  </section>
}
