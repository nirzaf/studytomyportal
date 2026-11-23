import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'

export type CheckoutOffer = {
  offer_uuid: string
  offer_version_uuid: string
  title: string
}

export type CheckoutLearner = {
  learner_uuid: string
  label: string
}

export type CheckoutQuote = {
  offer_version_uuid: string
  offer_sha256: string
  price_per_learner: {
    currency: string | null
    total_minor: number | null
    lines: { kind: 'charge' | 'tax' | 'discount'; label: string; amount_minor: number }[]
  }
  learner_count: number
  total_minor: number
  learner_uuids: string[]
  learners_with_overlap: string[]
  customer_terms: string
  confirmation_sha256: string
}

export type CheckoutSelection = {
  offer_uuid: string
  offer_version_uuid: string
  learner_uuids: string[]
}

const base = (orgUuid: string) =>
  `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/checkout`

async function read<T>(response: Response, fallback: string): Promise<T> {
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = body?.detail
    throw new Error(typeof detail === 'string' ? detail : detail?.message || fallback)
  }
  return body as T
}

export async function loadCheckoutOptions(orgUuid: string, token: string, signal?: AbortSignal) {
  const root = base(orgUuid)
  const [offersResponse, learnersResponse] = await Promise.all([
    secureFetch(`${root}/offers`, {
      ...RequestBodyWithAuthHeader('GET', null, null, token), cache: 'no-store', signal,
    }),
    secureFetch(`${root}/learners`, {
      ...RequestBodyWithAuthHeader('GET', null, null, token), cache: 'no-store', signal,
    }),
  ])
  const [offers, learners] = await Promise.all([
    read<CheckoutOffer[]>(offersResponse, 'Checkout offers are unavailable.'),
    read<CheckoutLearner[]>(learnersResponse, 'Eligible learners are unavailable.'),
  ])
  return { offers, learners }
}

export async function getCheckoutQuote(
  orgUuid: string, token: string, selection: CheckoutSelection, signal?: AbortSignal,
) {
  const response = await secureFetch(
    `${base(orgUuid)}/quote`,
    { ...RequestBodyWithAuthHeader('POST', selection, null, token), cache: 'no-store', signal },
  )
  return read<CheckoutQuote>(response, 'A current checkout quote is unavailable.')
}

export async function createCheckoutDraft(
  orgUuid: string,
  token: string,
  confirmation: CheckoutSelection & {
    confirmation_sha256: string
    acknowledged_overlap: boolean
  },
  idempotencyKey: string,
  signal?: AbortSignal,
) {
  const options = RequestBodyWithAuthHeader('POST', confirmation, null, token)
  const headers = new Headers(options.headers)
  headers.set('Idempotency-Key', idempotencyKey)
  options.headers = headers
  const response = await secureFetch(`${base(orgUuid)}/orders`, { ...options, cache: 'no-store', signal })
  return read<{ customer_ref: string }>(response, 'The order draft could not be saved.')
}

export async function acceptCheckoutDraft(
  orgUuid: string,
  token: string,
  customerRef: string,
  confirmation: CheckoutSelection & {
    confirmation_sha256: string
    acknowledged_overlap: boolean
  },
  signal?: AbortSignal,
) {
  const response = await secureFetch(
    `${base(orgUuid)}/orders/${encodeURIComponent(customerRef)}/accept`,
    { ...RequestBodyWithAuthHeader('POST', confirmation, null, token), cache: 'no-store', signal },
  )
  return read<{ customer_ref: string }>(response, 'The order confirmation is unavailable.')
}
