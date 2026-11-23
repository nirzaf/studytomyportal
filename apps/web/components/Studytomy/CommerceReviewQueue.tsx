'use client'

import { type FormEvent, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { Button } from '@components/ui/button'
import { getAuthenticatedSessionIdentity } from '@/lib/auth/sessionScope'
import { formatDateTime } from '@/lib/format'
import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'

type Review = { customer_ref: string; attempt_reference: string; reason: string; opened_at: string }
type ReviewPage = { cases: Review[]; next_cursor: string | null }
type Load = { kind: 'loading' | 'unavailable' | 'error' } | { kind: 'ready'; page: ReviewPage }

const reviewReasons: Record<string, string> = {
  amount_or_currency_mismatch: 'Amount or currency mismatch',
  additional_collection: 'Additional collection',
  capacity_or_sale_unavailable: 'Capacity or sale unavailable',
}

export default function CommerceReviewQueue({ orgUuid }: { orgUuid: string }) {
  const session = useLHSession()
  const token = session?.data?.tokens?.access_token
  if (session?.status !== 'authenticated' || !token) {
    return <p role="status">Sign in to view assigned collection reviews.</p>
  }
  const user = session.data?.user
  const identity = getAuthenticatedSessionIdentity(user?.id ?? user?.user_uuid, session.data)
  return <Queue key={JSON.stringify([identity, orgUuid])} orgUuid={orgUuid} token={token} />
}

function Queue({ orgUuid, token }: { orgUuid: string; token: string }) {
  const { i18n } = useTranslation()
  const [load, setLoad] = useState<Load>({ kind: 'loading' })
  const [cursor, setCursor] = useState<string>()
  const [selected, setSelected] = useState<string>()
  const [refresh, setRefresh] = useState(0)
  const [resolved, setResolved] = useState(false)
  const base = `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/collections`

  useEffect(() => {
    let active: AbortController | undefined
    function clear() {
      active?.abort()
      setSelected(undefined)
      setLoad({ kind: 'loading' })
    }
    async function read() {
      clear()
      if (document.visibilityState === 'hidden') return
      const controller = new AbortController()
      active = controller
      const query = new URLSearchParams({ limit: '20', ...(cursor ? { after: cursor } : {}) })
      try {
        const response = await secureFetch(`${base}/reviews?${query}`, {
          ...RequestBodyWithAuthHeader('GET', null, null, token), cache: 'no-store', signal: controller.signal,
        })
        if (!response.ok) {
          if (!controller.signal.aborted) {
            setLoad({ kind: [401, 403, 404, 409].includes(response.status) ? 'unavailable' : 'error' })
          }
          return
        }
        const page: ReviewPage = await response.json()
        if (!controller.signal.aborted) setLoad({ kind: 'ready', page })
      } catch {
        if (!controller.signal.aborted) setLoad({ kind: 'error' })
      }
    }
    void read()
    document.addEventListener('visibilitychange', read)
    window.addEventListener('pagehide', clear)
    window.addEventListener('pageshow', read)
    return () => {
      active?.abort()
      document.removeEventListener('visibilitychange', read)
      window.removeEventListener('pagehide', clear)
      window.removeEventListener('pageshow', read)
    }
  }, [base, cursor, refresh, token])

  const activeReview = load.kind === 'ready' ? load.page.cases.find(item => item.attempt_reference === selected) : undefined
  return (
    <section aria-labelledby="collections-heading" className="mx-auto w-full max-w-4xl space-y-4 px-4 py-6 sm:px-6">
      <h1 id="collections-heading" className="text-2xl font-semibold">Collection reviews</h1>
      {load.kind === 'loading' && <p role="status">Loading assigned reviews…</p>}
      {load.kind === 'unavailable' && <p role="status">Collection reviews are unavailable.</p>}
      {load.kind === 'error' && (
        <div className="space-y-2">
          <p role="alert">Unable to load assigned reviews.</p>
          <Button type="button" variant="outline" onClick={() => setRefresh(value => value + 1)}>Try again</Button>
        </div>
      )}
      {resolved && load.kind === 'ready' && <p role="status">External outcome recorded.</p>}
      {load.kind === 'ready' && (
        <>
          {load.page.cases.length === 0 ? <p>No assigned collection reviews to show.</p> : (
            <ul className="divide-y rounded-lg border">
              {load.page.cases.map(item => (
                <li key={item.attempt_reference} className="flex flex-wrap items-center justify-between gap-3 p-4">
                  <div className="min-w-0">
                    <p className="font-medium">{reviewReasons[item.reason] ?? 'Collection exception'}</p>
                    <p className="break-words text-sm text-muted-foreground">Order {item.customer_ref}</p>
                    <time className="text-sm text-muted-foreground" dateTime={item.opened_at}>
                      {formatDateTime(item.opened_at, i18n.language)}
                    </time>
                  </div>
                  <Button type="button" variant="outline" aria-expanded={selected === item.attempt_reference}
                    onClick={() => {
                      setResolved(false)
                      setSelected(selected === item.attempt_reference ? undefined : item.attempt_reference)
                    }}>
                    {selected === item.attempt_reference ? 'Close review' : 'Review'}
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {activeReview && <ResolutionForm key={activeReview.attempt_reference} review={activeReview}
            base={base} token={token} onResolved={() => {
              setResolved(true)
              setSelected(undefined)
              setRefresh(value => value + 1)
            }} />}
          <div className="flex flex-wrap gap-2">
            {cursor && <Button type="button" variant="outline" onClick={() => setCursor(undefined)}>First page</Button>}
            {load.page.next_cursor && <Button type="button" variant="outline"
              onClick={() => setCursor(load.page.next_cursor ?? undefined)}>Next page</Button>}
          </div>
        </>
      )}
    </section>
  )
}

function ResolutionForm({ review, base, token, onResolved }: {
  review: Review; base: string; token: string; onResolved: () => void
}) {
  const active = useRef<AbortController | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string>()
  useEffect(() => () => active.current?.abort(), [token])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (active.current) return
    const input = new FormData(event.currentTarget)
    const evidenceReference = String(input.get('evidence_reference') ?? '').trim()
    if (!evidenceReference) {
      setError('Enter the verified evidence reference.')
      return
    }
    const controller = new AbortController()
    active.current = controller
    setError(undefined)
    setPending(true)
    try {
      const order = encodeURIComponent(review.customer_ref)
      const attempt = encodeURIComponent(review.attempt_reference)
      const response = await secureFetch(`${base}/${order}/manual-attempts/${attempt}/resolution`, {
        ...RequestBodyWithAuthHeader('PUT', {
          outcome: input.get('outcome'),
          evidence_reference: evidenceReference,
          record_sha256: input.get('record_sha256'),
        }, null, token),
        cache: 'no-store',
        signal: controller.signal,
      })
      if (controller.signal.aborted) return
      if (response.ok) {
        onResolved()
      } else if (response.status === 409) {
        setError('The current policy or review state prevented this outcome from being recorded.')
      } else if ([401, 403, 404].includes(response.status)) {
        setError('You cannot resolve this review with your current access.')
      } else {
        setError('Unable to confirm the saved outcome. Reload the queue before continuing.')
      }
    } catch {
      if (!controller.signal.aborted) setError('Unable to confirm the saved outcome. Reload the queue before continuing.')
    } finally {
      if (active.current === controller) active.current = null
      if (!controller.signal.aborted) setPending(false)
    }
  }

  return (
    <form onSubmit={submit} autoComplete="off" className="space-y-3 rounded-lg border bg-background p-4">
      <h2 className="text-lg font-semibold">Record a verified external outcome</h2>
      <p className="break-words text-sm text-muted-foreground">Attempt {review.attempt_reference}</p>
      <fieldset disabled={pending} className="grid gap-3">
        <label className="grid gap-1">
          Outcome
          <select name="outcome" required defaultValue="" className="min-h-11 rounded-md border bg-background px-3">
            <option value="" disabled>Select an outcome</option>
            <option value="returned_externally">Returned outside Studytomy</option>
            <option value="settlement_not_received">Settlement was not received</option>
          </select>
        </label>
        <label className="grid gap-1">
          Evidence reference
          <input name="evidence_reference" required maxLength={150} className="min-h-11 rounded-md border px-3" />
        </label>
        <label className="grid gap-1">
          Evidence SHA-256
          <input name="record_sha256" required minLength={64} maxLength={64} pattern="[0-9a-f]{64}"
            spellCheck={false} className="min-h-11 rounded-md border px-3" />
        </label>
        <Button type="submit" className="w-fit">{pending ? 'Recording…' : 'Record outcome'}</Button>
      </fieldset>
      {error && <p role="alert">{error}</p>}
    </form>
  )
}
