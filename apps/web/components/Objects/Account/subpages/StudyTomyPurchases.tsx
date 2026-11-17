'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { Button } from '@components/ui/button'
import { getAuthenticatedSessionIdentity } from '@/lib/auth/sessionScope'
import { formatCurrency, formatDateTime } from '@/lib/format'
import { getAPIUrl, getUriWithOrg } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'
import PurchaserRefundRequest from '@components/Studytomy/PurchaserRefundRequest'

type Order = {
  customer_ref: string
  accepted_at: string
  currency: string
  order_total_minor: number
}

type PurchasePage = { orders: Order[]; next_cursor: string | null }
type Receipt = Order & {
  confirmed_minor: number
  collection_status: 'not_required' | 'awaiting_collection' | 'pending_collection' | 'confirmed'
  finance_review_required: boolean
  allocations: {
    position: number
    courses: string[]
    access: {
      clock: 'after_purchase' | 'after_first_access' | 'fixed_period'
      duration_days: number | null
      starts_at: string | null
      ends_at: string | null
    }
    price: {
      currency: string
      total_minor: number
      lines: { kind: 'charge' | 'tax' | 'discount'; label: string; amount_minor: number }[]
    }
    customer_terms: string
    correction_status?: 'corrected' | null
  }[]
}

type View =
  | { kind: 'loading' | 'unavailable' | 'error' }
  | { kind: 'list'; page: PurchasePage }
  | { kind: 'receipt'; receipt: Receipt }

type Props = { orgUuid: string; orgSlug: string; orderRef?: string }
const orderUuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const collectionLabels: Record<Receipt['collection_status'], string> = {
  not_required: 'No payment required',
  awaiting_collection: 'Awaiting payment',
  pending_collection: 'Payment verification pending',
  confirmed: 'Payment confirmed',
}

export default function StudyTomyPurchases(props: Props) {
  const session = useLHSession()
  const token = session?.data?.tokens?.access_token
  if (session?.status !== 'authenticated' || !token) {
    return <p role="status">{session?.status === 'unauthenticated' ? 'Sign in to view purchases.' : 'Loading purchases…'}</p>
  }
  const user = session.data?.user
  const identity = getAuthenticatedSessionIdentity(user?.id ?? user?.user_uuid, session.data)
  return <PurchasesView key={JSON.stringify([identity, props.orgUuid, props.orderRef])} {...props} token={token} />
}

function PurchasesView({ orgUuid, orgSlug, orderRef, token }: Props & { token: string }) {
  const { i18n } = useTranslation()
  const [view, setView] = useState<View>({ kind: 'loading' })
  const [after, setAfter] = useState<string>()
  const [retry, setRetry] = useState(0)
  const purchasesUrl = getUriWithOrg(orgSlug, '/account/purchases')
  const isDetail = orderRef !== undefined

  useEffect(() => {
    let active: AbortController | undefined
    function clear() {
      active?.abort()
      setView({ kind: 'loading' })
    }
    async function load() {
      clear()
      if (document.visibilityState === 'hidden') return
      if (orderRef !== undefined && !orderUuid.test(orderRef)) {
        setView({ kind: 'unavailable' })
        return
      }
      const request = new AbortController()
      active = request
      const query = new URLSearchParams({ limit: '25', ...(after ? { after } : {}) })
      const path = orderRef === undefined ? `purchases?${query}` : `checkout/orders/${orderRef}/receipt`
      try {
        const response = await secureFetch(
          `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/${path}`,
          { ...RequestBodyWithAuthHeader('GET', null, null, token), cache: 'no-store', signal: request.signal },
        )
        if (!response.ok) {
          if (!request.signal.aborted) {
            setView({ kind: [401, 403, 404, 409].includes(response.status) ? 'unavailable' : 'error' })
          }
          return
        }
        if (orderRef === undefined) {
          const page: PurchasePage = await response.json()
          if (!request.signal.aborted) setView({ kind: 'list', page })
        } else {
          const receipt: Receipt = await response.json()
          if (!request.signal.aborted) {
            setView(receipt.customer_ref === orderRef ? { kind: 'receipt', receipt } : { kind: 'unavailable' })
          }
        }
      } catch {
        if (!request.signal.aborted) setView({ kind: 'error' })
      }
    }
    void load()
    document.addEventListener('visibilitychange', load)
    window.addEventListener('pageshow', load)
    window.addEventListener('pagehide', clear)
    return () => {
      active?.abort()
      document.removeEventListener('visibilitychange', load)
      window.removeEventListener('pageshow', load)
      window.removeEventListener('pagehide', clear)
    }
  }, [after, orderRef, orgUuid, retry, token])

  function money(minor: number, currency: string) {
    const digits = new Intl.NumberFormat(i18n.language, { style: 'currency', currency }).resolvedOptions().maximumFractionDigits ?? 2
    return formatCurrency(minor / (10 ** digits), currency, i18n.language, { currencyDisplay: 'code' })
  }
  const date = (value: string) => formatDateTime(value, i18n.language)

  return (
    <section aria-label={isDetail ? 'StudyTomy receipt' : 'StudyTomy purchases'} className="space-y-4">
      {isDetail && <Link href={purchasesUrl} prefetch={false} className="inline-flex min-h-11 items-center underline">Back to purchases</Link>}
      <h2 className="text-xl font-semibold">{isDetail ? 'StudyTomy receipt' : 'StudyTomy purchases'}</h2>
      {view.kind === 'loading' && <p role="status">Loading {isDetail ? 'receipt' : 'purchases'}…</p>}
      {view.kind === 'unavailable' && <p role="status">{isDetail ? 'This receipt is unavailable.' : 'StudyTomy purchase history is unavailable.'}</p>}
      {view.kind === 'error' && (
        <div className="space-y-2">
          <p role="alert">Unable to load {isDetail ? 'this receipt' : 'purchases'}.</p>
          <Button type="button" variant="outline" onClick={() => setRetry((value) => value + 1)}>Try again</Button>
        </div>
      )}
      {view.kind === 'list' && (
        <>
          {view.page.orders.length === 0 && <p role="status">No StudyTomy purchases to show.</p>}
          {view.page.orders.length > 0 && <ul className="divide-y rounded-lg border">
            {view.page.orders.map((order) => (
              <li key={order.customer_ref}>
                <Link href={`${purchasesUrl}?order=${encodeURIComponent(order.customer_ref)}`} prefetch={false}
                  className="flex min-h-11 flex-wrap items-center justify-between gap-x-4 gap-y-1 px-4 py-3 hover:bg-muted focus-visible:bg-muted">
                  <span>Receipt for <time dateTime={order.accepted_at}>{date(order.accepted_at)}</time></span>
                  <span>{money(order.order_total_minor, order.currency)}</span>
                </Link>
              </li>
            ))}
          </ul>}
          <div className="flex flex-wrap gap-2">
            {after && <Button type="button" variant="outline" onClick={() => setAfter(undefined)}>First page</Button>}
            {view.page.next_cursor && (
              <Button type="button" variant="outline" onClick={() => setAfter(view.page.next_cursor ?? undefined)}>Next page</Button>
            )}
          </div>
        </>
      )}
      {view.kind === 'receipt' && (
        <>
          <dl className="grid gap-3 rounded-lg border p-4 sm:grid-cols-2">
            <div><dt>Receipt reference</dt><dd className="break-words font-medium">{view.receipt.customer_ref}</dd></div>
            <div><dt>Order accepted</dt><dd><time dateTime={view.receipt.accepted_at}>{date(view.receipt.accepted_at)}</time></dd></div>
            <div><dt>Order total</dt><dd>{money(view.receipt.order_total_minor, view.receipt.currency)}</dd></div>
            <div><dt>Amount confirmed</dt><dd>{money(view.receipt.confirmed_minor, view.receipt.currency)}</dd></div>
            <div><dt>Payment status</dt><dd>{collectionLabels[view.receipt.collection_status]}</dd></div>
          </dl>
          {view.receipt.finance_review_required && <p role="status">Finance review is pending.</p>}
          {view.receipt.collection_status === 'confirmed' && (
            <PurchaserRefundRequest orgUuid={orgUuid} orderRef={view.receipt.customer_ref} token={token} />
          )}
          {view.receipt.allocations.map(({ position, courses, access, price, customer_terms, correction_status }) => (
            <div key={position} className="space-y-3 rounded-lg border p-4">
              <h3 className="font-semibold">{courses.join(', ')}</h3>
              {correction_status === 'corrected' && <p>Learner allocation corrected.</p>}
              {access.clock === 'fixed_period' && access.starts_at && access.ends_at && (
                <p>Access period from {date(access.starts_at)} to {date(access.ends_at)}</p>
              )}
              {access.duration_days !== null && access.clock !== 'fixed_period' && (
                <p>{access.duration_days} {access.duration_days === 1 ? 'day' : 'days'} of access from {
                  access.clock === 'after_first_access' ? 'first use' : 'purchase'
                }</p>
              )}
              <dl className="space-y-1">
                {price.lines.map((line, index) => (
                  <div key={index} className="flex justify-between gap-3">
                    <dt className="break-words">{line.label}</dt>
                    <dd className="shrink-0">{money((line.kind === 'discount' ? -1 : 1) * line.amount_minor, price.currency)}</dd>
                  </div>
                ))}
                <div className="flex justify-between gap-3 border-t pt-2 font-medium">
                  <dt>Subtotal</dt><dd>{money(price.total_minor, price.currency)}</dd>
                </div>
              </dl>
              <div>
                <h4 className="font-medium">Accepted terms</h4>
                <p className="whitespace-pre-wrap break-words">{customer_terms}</p>
              </div>
            </div>
          ))}
        </>
      )}
    </section>
  )
}
