'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import { useTranslation } from 'react-i18next'
import { Button } from '@components/ui/button'
import { formatCurrency } from '@/lib/format'
import { getUriWithOrg } from '@services/config/config'
import {
  acceptCheckoutDraft,
  createCheckoutDraft,
  getCheckoutQuote,
  loadCheckoutOptions,
  type CheckoutLearner,
  type CheckoutOffer,
  type CheckoutQuote,
  type CheckoutSelection,
} from '@services/studytomy/checkout'

type Proposal = { selection: CheckoutSelection; quote: CheckoutQuote }
type PendingOrder = {
  idempotencyKey: string
  confirmation: CheckoutSelection & {
    confirmation_sha256: string
    acknowledged_overlap: boolean
  }
  quote: CheckoutQuote
  customerRef?: string
}
type Options = { offers: CheckoutOffer[]; learners: CheckoutLearner[] }

const orderUuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function readPending(value: string | null): PendingOrder | undefined {
  if (!value) return undefined
  try {
    const parsed = JSON.parse(value) as PendingOrder
    if (
      typeof parsed.idempotencyKey === 'string'
      && typeof parsed.confirmation?.confirmation_sha256 === 'string'
      && orderUuid.test(parsed.confirmation.offer_uuid)
      && orderUuid.test(parsed.confirmation.offer_version_uuid)
      && Array.isArray(parsed.confirmation.learner_uuids)
      && parsed.confirmation.learner_uuids.every(id => orderUuid.test(id))
      && typeof parsed.quote?.customer_terms === 'string'
    ) return parsed
  } catch {
    // A damaged tab-local retry marker cannot authorize a checkout action.
  }
  return undefined
}

export default function StudytomyCheckout({ orgUuid, orgSlug, sessionIdentity, token }: {
  orgUuid: string; orgSlug: string; sessionIdentity: string; token: string
}) {
  const { i18n } = useTranslation()
  const storageKey = `studytomy-checkout:${orgUuid}:${sessionIdentity}`
  const receiptUrl = (ref: string) => `${getUriWithOrg(orgSlug, '/account/purchases')}?order=${encodeURIComponent(ref)}`
  const [options, setOptions] = useState<Options>()
  const [offerUuid, setOfferUuid] = useState('')
  const [selectedLearners, setSelectedLearners] = useState<string[]>([])
  const [proposal, setProposal] = useState<Proposal>()
  const [pendingOrder, setPendingOrder] = useState<PendingOrder>()
  const [acceptedRef, setAcceptedRef] = useState<string>()
  const [acknowledgedOverlap, setAcknowledgedOverlap] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string>()
  const [retry, setRetry] = useState(0)
  const controller = useRef<AbortController | null>(null)
  const operationLock = useRef(false)

  useEffect(() => {
    const pending = readPending(sessionStorage.getItem(storageKey))
    let active = true
    queueMicrotask(() => {
      if (!active) return
      if (pending) {
        setPendingOrder(pending)
        setProposal({ selection: {
          offer_uuid: pending.confirmation.offer_uuid,
          offer_version_uuid: pending.confirmation.offer_version_uuid,
          learner_uuids: pending.confirmation.learner_uuids,
        }, quote: pending.quote })
        setOfferUuid(pending.confirmation.offer_uuid)
        setSelectedLearners(pending.confirmation.learner_uuids)
        setAcknowledgedOverlap(pending.confirmation.acknowledged_overlap)
      } else {
        sessionStorage.removeItem(storageKey)
      }
    })
    return () => { active = false }
  }, [storageKey])

  useEffect(() => {
    const request = new AbortController()
    controller.current?.abort()
    controller.current = request
    queueMicrotask(() => {
      if (!request.signal.aborted) {
        setLoading(true)
        setError(undefined)
      }
    })
    void loadCheckoutOptions(orgUuid, token, request.signal)
      .then((loaded) => {
        if (!request.signal.aborted) {
          setOptions(loaded)
          setOfferUuid((current) => current || loaded.offers[0]?.offer_uuid || '')
        }
      })
      .catch(() => {
        if (!request.signal.aborted) setError('Current checkout options are unavailable.')
      })
      .finally(() => {
        if (!request.signal.aborted) setLoading(false)
      })
    return () => request.abort()
  }, [orgUuid, token, retry])

  const learnerLabels = useMemo(
    () => new Map((options?.learners ?? []).map(learner => [learner.learner_uuid, learner.label])),
    [options],
  )
  const digits = proposal?.quote.price_per_learner.currency
    ? new Intl.NumberFormat(i18n.language, {
      style: 'currency', currency: proposal.quote.price_per_learner.currency,
    }).resolvedOptions().maximumFractionDigits ?? 2
    : 2
  const money = (minor: number, currency: string | null) => currency
    ? formatCurrency(minor / (10 ** digits), currency, i18n.language, { currencyDisplay: 'code' })
    : 'Price unavailable'

  function changeOffer(value: string) {
    setOfferUuid(value)
    setProposal(undefined)
    setAcceptedRef(undefined)
    setAcknowledgedOverlap(false)
  }

  function toggleLearner(uuid: string, checked: boolean) {
    setSelectedLearners(current => checked
      ? [...new Set([...current, uuid])]
      : current.filter(value => value !== uuid))
    setProposal(undefined)
    setAcceptedRef(undefined)
    setAcknowledgedOverlap(false)
  }

  async function reviewQuote() {
    if (!options || pendingOrder || busy || !offerUuid || selectedLearners.length === 0) return
    controller.current?.abort()
    const request = new AbortController()
    controller.current = request
    setBusy(true)
    setError(undefined)
    const offer = options.offers.find(item => item.offer_uuid === offerUuid)
    if (!offer) {
      setBusy(false)
      setError('Select a current offer and try again.')
      return
    }
    const selection = {
      offer_uuid: offer.offer_uuid,
      offer_version_uuid: offer.offer_version_uuid,
      learner_uuids: [...selectedLearners].sort(),
    }
    try {
      const quote = await getCheckoutQuote(orgUuid, token, selection, request.signal)
      if (request.signal.aborted) return
      if (
        quote.offer_version_uuid !== selection.offer_version_uuid
        || quote.learner_count !== selection.learner_uuids.length
        || [...quote.learner_uuids].sort().join('|') !== selection.learner_uuids.join('|')
        || !quote.customer_terms.trim()
        || !Number.isSafeInteger(quote.total_minor)
      ) throw new Error('The quote does not match the selected offer and learners.')
      setProposal({ selection, quote })
      setAcknowledgedOverlap(false)
    } catch {
      if (!request.signal.aborted) setError('The quote changed or the selected learners are no longer eligible. Review the current options and try again.')
    } finally {
      if (!request.signal.aborted) setBusy(false)
    }
  }

  async function confirmOrder(retryPending = false) {
    if (operationLock.current || busy) return
    let operation = retryPending ? pendingOrder : undefined
    if (!operation) {
      if (!proposal || (proposal.quote.learners_with_overlap.length > 0 && !acknowledgedOverlap)) return
      operation = {
        idempotencyKey: crypto.randomUUID(),
        confirmation: {
          ...proposal.selection,
          confirmation_sha256: proposal.quote.confirmation_sha256,
          acknowledged_overlap: acknowledgedOverlap,
        },
        quote: proposal.quote,
      }
      sessionStorage.setItem(storageKey, JSON.stringify(operation))
      setPendingOrder(operation)
    }
    operationLock.current = true
    const request = new AbortController()
    controller.current?.abort()
    controller.current = request
    setBusy(true)
    setError(undefined)
    try {
      let customerRef = operation.customerRef
      if (!customerRef) {
        const draft = await createCheckoutDraft(
          orgUuid, token, operation.confirmation, operation.idempotencyKey, request.signal,
        )
        if (!orderUuid.test(draft.customer_ref)) throw new Error('The order reference could not be verified.')
        customerRef = draft.customer_ref
        operation = { ...operation, customerRef }
        sessionStorage.setItem(storageKey, JSON.stringify(operation))
        setPendingOrder(operation)
      }
      const accepted = await acceptCheckoutDraft(
        orgUuid, token, customerRef, operation.confirmation, request.signal,
      )
      if (request.signal.aborted) return
      if (accepted.customer_ref !== customerRef) throw new Error('The order receipt did not match.')
      sessionStorage.removeItem(storageKey)
      setPendingOrder(undefined)
      setAcceptedRef(customerRef)
    } catch {
      if (!request.signal.aborted) setError('The order response may be delayed. Retry this same confirmation to recover its receipt.')
    } finally {
      operationLock.current = false
      if (!request.signal.aborted) setBusy(false)
    }
  }

  if (loading && !options) return <p role="status">Loading checkout options…</p>

  return (
    <section aria-labelledby="studytomy-checkout-title" className="space-y-4 rounded-lg border p-4">
      <h3 id="studytomy-checkout-title" className="text-lg font-semibold">Course checkout</h3>
      {error && <p role="alert">{error}</p>}
      {acceptedRef ? (
        <div className="space-y-2">
          <p role="status">Order accepted. No online payment was taken. The receipt shows whether payment is required and the current collection status.</p>
          <Link className="inline-flex min-h-11 items-center underline" href={receiptUrl(acceptedRef)} prefetch={false}>
            Open order receipt
          </Link>
        </div>
      ) : pendingOrder ? (
        <div className="space-y-3">
          <p role="status">A confirmation may already be in progress. Review the retained terms, then continue or retry to recover the order receipt.</p>
          <QuoteDetails proposal={{ selection: {
            offer_uuid: pendingOrder.confirmation.offer_uuid,
            offer_version_uuid: pendingOrder.confirmation.offer_version_uuid,
            learner_uuids: pendingOrder.confirmation.learner_uuids,
          }, quote: pendingOrder.quote }} learnerLabels={learnerLabels} money={money} />
          <Button type="button" className="min-h-11" disabled={busy} onClick={() => void confirmOrder(true)}>
            {busy ? 'Confirming…' : 'Continue or retry this order'}
          </Button>
        </div>
      ) : options?.offers.length && options.learners.length ? (
        <div className="space-y-4">
          <label className="grid gap-1 text-sm font-medium">
            Course offer
            <select className="min-h-11 rounded-md border bg-background px-3" value={offerUuid} onChange={event => changeOffer(event.target.value)}>
              {options.offers.map(offer => <option key={offer.offer_uuid} value={offer.offer_uuid}>{offer.title}</option>)}
            </select>
          </label>
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">Learners eligible for this purchase</legend>
            {options.learners.map(learner => (
              <label key={learner.learner_uuid} className="flex min-h-11 items-center gap-3 rounded-md border px-3">
                <input
                  type="checkbox"
                  checked={selectedLearners.includes(learner.learner_uuid)}
                  onChange={event => toggleLearner(learner.learner_uuid, event.target.checked)}
                />
                <span>{learner.label}</span>
              </label>
            ))}
          </fieldset>
          <Button type="button" variant="outline" className="min-h-11" disabled={busy || selectedLearners.length === 0} onClick={() => void reviewQuote()}>
            {busy ? 'Checking current price…' : 'Review current price and terms'}
          </Button>
          {proposal && <div className="space-y-3 border-t pt-4">
            <QuoteDetails proposal={proposal} learnerLabels={learnerLabels} money={money} />
            {proposal.quote.learners_with_overlap.length > 0 && <label className="flex min-h-11 items-start gap-3 rounded-md border p-3">
              <input className="mt-1" type="checkbox" checked={acknowledgedOverlap} onChange={event => setAcknowledgedOverlap(event.target.checked)} />
              <span>Some selected learners already have overlapping course access. I have reviewed this before continuing.</span>
            </label>}
            <Button type="button" className="min-h-11" disabled={busy || (proposal.quote.learners_with_overlap.length > 0 && !acknowledgedOverlap)} onClick={() => void confirmOrder()}>
              {busy ? 'Saving order…' : 'Accept order'}
            </Button>
          </div>}
        </div>
      ) : (
        <p role="status">Self-service checkout is unavailable for this account or current offer.</p>
      )}
      <Button type="button" variant="ghost" className="min-h-11" onClick={() => setRetry(value => value + 1)}>
        Refresh checkout options
      </Button>
    </section>
  )
}

function QuoteDetails({ proposal, learnerLabels, money }: {
  proposal: Proposal
  learnerLabels: Map<string, string>
  money: (_minor: number, _currency: string | null) => string
}) {
  const { quote, selection } = proposal
  const currency = quote.price_per_learner.currency
  return (
    <div className="space-y-3 rounded-md bg-muted/40 p-3">
      <p className="font-medium">{selection.learner_uuids.map(uuid => learnerLabels.get(uuid) || 'Selected learner').join(', ')}</p>
      <dl className="space-y-1">
        {quote.price_per_learner.lines.map((line, index) => (
          <div key={`${line.label}-${index}`} className="flex justify-between gap-3 text-sm">
            <dt>{line.label}{quote.learner_count > 1 ? ` × ${quote.learner_count}` : ''}</dt>
            <dd className="shrink-0">{money((line.kind === 'discount' ? -1 : 1) * line.amount_minor * quote.learner_count, currency)}</dd>
          </div>
        ))}
        <div className="flex justify-between gap-3 border-t pt-2 font-semibold">
          <dt>Total</dt><dd>{money(quote.total_minor, currency)}</dd>
        </div>
      </dl>
      <div>
        <h4 className="font-medium">Customer terms</h4>
        <p className="whitespace-pre-wrap break-words text-sm">{quote.customer_terms}</p>
      </div>
      <p className="text-sm">Payment is handled outside this portal. Paid orders remain pending until collection is confirmed.</p>
    </div>
  )
}
