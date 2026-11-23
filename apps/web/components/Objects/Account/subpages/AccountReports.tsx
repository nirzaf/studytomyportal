'use client'

import Link from 'next/link'
import { type FormEvent, useEffect, useId, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { Button } from '@components/ui/button'
import { getAuthenticatedSessionIdentity } from '@/lib/auth/sessionScope'
import { getUriWithOrg } from '@services/config/config'
import { type AdaptiveRequest, AdaptiveRequestError, adaptiveRequest } from '@services/studytomy/adaptive'

type ReportMetadata = {
  uuid: string
  version: number
  period_from: string
  period_to: string
  curriculum_title: string
  course_title: string
}

type Observation = {
  concept_uuid: string
  title: string
  independent_correct: number
  independent_incorrect: number
  supported_practice: number
  unknown: number
  pending: number
  missing_independent_evidence: boolean
}

type Report = ReportMetadata & {
  learner_name: string
  can_request_review: boolean
  evidence_cutoff: string
  commentary: string | null
  summary: { native_completions: number; reviewed_release_completions?: number; concepts: Observation[] }
}

type Page = { items: ReportMetadata[]; next_cursor: string | null }
type Load =
  | { kind: 'loading' | 'unavailable' | 'error' }
  | { kind: 'list'; page: Page }
  | { kind: 'detail'; report: Report }

const reportId = /^report_[0-9a-f-]{36}$/

export default function AccountReports({ orgUuid, orgSlug, selected }: {
  orgUuid: string; orgSlug: string; selected?: string
}) {
  const { t } = useTranslation()
  const session = useLHSession()
  const token = session?.data?.tokens?.access_token
  if (session?.status === 'unauthenticated') {
    return <p role="status">{t('account.report_sign_in', 'Sign in to view progress reports.')}</p>
  }
  if (session?.status !== 'authenticated' || !token) {
    return <p role="status">{t('account.report_loading', 'Loading reports…')}</p>
  }
  const user = session.data?.user
  const identity = getAuthenticatedSessionIdentity(user?.id ?? user?.user_uuid, session.data)
  return <ReportsView key={JSON.stringify([identity, orgUuid, selected])}
    {...{ orgUuid, orgSlug, selected, token }} />
}

function ReportsView({ orgUuid, orgSlug, selected, token }: {
  orgUuid: string; orgSlug: string; selected?: string; token: string
}) {
  const { t, i18n } = useTranslation()
  const client = useMemo(() => adaptiveRequest(orgUuid, token), [orgUuid, token])
  const [load, setLoad] = useState<Load>({ kind: 'loading' })
  const [cursor, setCursor] = useState<string>()
  const [refresh, setRefresh] = useState(0)
  const reportsUrl = getUriWithOrg(orgSlug, '/account/reports')

  useEffect(() => {
    let request: AbortController | undefined
    function clear() {
      request?.abort()
      setLoad({ kind: 'loading' })
    }
    async function fetchCurrent() {
      if (document.visibilityState === 'hidden') return
      request?.abort()
      const running = new AbortController()
      request = running
      try {
        if (selected) {
          if (!reportId.test(selected)) {
            setLoad({ kind: 'unavailable' })
            return
          }
          const result = await client<Report>(`reports/${encodeURIComponent(selected)}`, 'GET', undefined, undefined, running.signal)
          if (!running.signal.aborted) {
            setLoad(result.uuid === selected && typeof result.learner_name === 'string'
              ? { kind: 'detail', report: result } : { kind: 'unavailable' })
          }
        } else {
          const params = new URLSearchParams({ limit: '10', ...(cursor ? { cursor } : {}) })
          const page = await client<Page>(`me/reports?${params}`, 'GET', undefined, undefined, running.signal)
          if (!running.signal.aborted) setLoad({ kind: 'list', page })
        }
      } catch (error) {
        if (running.signal.aborted) return
        setLoad(error instanceof AdaptiveRequestError && [401, 403, 404, 409].includes(error.status)
          ? { kind: 'unavailable' } : { kind: 'error' })
      }
    }
    function refreshVisible() {
      clear()
      void fetchCurrent()
    }
    void fetchCurrent()
    document.addEventListener('visibilitychange', refreshVisible)
    window.addEventListener('pagehide', clear)
    window.addEventListener('pageshow', refreshVisible)
    return () => {
      request?.abort()
      document.removeEventListener('visibilitychange', refreshVisible)
      window.removeEventListener('pagehide', clear)
      window.removeEventListener('pageshow', refreshVisible)
    }
  }, [client, cursor, refresh, selected])

  const date = (value: string) => new Intl.DateTimeFormat(i18n.language, {
    dateStyle: 'medium', timeZone: 'UTC',
  }).format(new Date(value))
  const period = (value: ReportMetadata) => t('account.report_period', 'From {{from}} until {{to}} (UTC)', {
    from: date(value.period_from), to: date(value.period_to),
  })
  const title = t('account.reports', 'Progress reports')
  return <section aria-labelledby="account-reports-title" className="space-y-5">
    <header className="space-y-2">
      {selected && <Link href={reportsUrl} prefetch={false} className="inline-flex min-h-11 items-center underline">
        {t('account.report_back', 'Back to reports')}
      </Link>}
      <h1 id="account-reports-title" className="text-xl font-semibold">{title}</h1>
    </header>
    {load.kind === 'loading' && <p role="status">{t('account.report_loading', 'Loading reports…')}</p>}
    {load.kind === 'unavailable' && <p role="status">{t('account.report_unavailable', 'This report is unavailable.')}</p>}
    {load.kind === 'error' && <div className="space-y-2">
      <p role="status">{t('account.report_error', 'Reports could not be loaded.')}</p>
      <Button type="button" variant="outline" className="min-h-11" onClick={() => {
        setLoad({ kind: 'loading' })
        setRefresh(value => value + 1)
      }}>{t('account.report_retry', 'Try again')}</Button>
    </div>}
    {load.kind === 'list' && (load.page.items.length > 0
      ? <ul className="space-y-3">
        {load.page.items.map(item => <li key={item.uuid} className="rounded-lg border p-4">
          <Link href={`${reportsUrl}?${new URLSearchParams({ report: item.uuid })}`} prefetch={false}
            className="inline-flex min-h-11 items-center font-semibold underline [overflow-wrap:anywhere]">
            <bdi>{item.course_title}</bdi>
          </Link>
          <p className="text-sm">{period(item)}</p>
        </li>)}
      </ul>
      : <p>{t('account.report_empty', 'There are no progress reports available.')}</p>)}
    {load.kind === 'list' && (cursor || load.page.next_cursor) && <div className="flex flex-wrap gap-3">
      {cursor && <Button variant="outline" className="min-h-11" onClick={() => {
        setLoad({ kind: 'loading' })
        setCursor(undefined)
      }}>{t('account.report_first', 'First page')}</Button>}
      {load.page.next_cursor && <Button variant="outline" className="min-h-11" onClick={() => {
        setLoad({ kind: 'loading' })
        setCursor(load.page.next_cursor ?? undefined)
      }}>{t('account.report_next', 'Next page')}</Button>}
    </div>}
    {load.kind === 'detail' && <article className="space-y-4 rounded-lg border p-4 [overflow-wrap:anywhere]">
      <h2 className="text-lg font-semibold"><bdi>{load.report.course_title}</bdi></h2>
      <p><bdi>{load.report.learner_name}</bdi> · <bdi>{load.report.curriculum_title}</bdi></p>
      <p>{period(load.report)}</p>
      <p>{t('account.report_completion', 'Completed activities updated in this period: {{count}}', {
        count: load.report.summary.native_completions,
      })}</p>
      {load.report.summary.reviewed_release_completions != null && (
        <p>Reviewed course releases completed in this period: {load.report.summary.reviewed_release_completions}</p>
      )}
      <section className="space-y-3" aria-label={t('account.report_evidence', 'Learning evidence')}>
        <h3 className="font-semibold">{t('account.report_evidence', 'Learning evidence')}</h3>
        {load.report.summary.concepts.map(concept => <div key={concept.concept_uuid} className="space-y-1 border-t pt-3">
          <h4 className="font-semibold"><bdi>{concept.title}</bdi></h4>
          {concept.missing_independent_evidence
            ? <p>{t('account.report_missing', 'No independent evidence in this period.')}</p>
            : <p>{t('account.report_independent', 'Independent responses: {{correct}} correct, {{incorrect}} incorrect', {
              correct: concept.independent_correct, incorrect: concept.independent_incorrect,
            })}</p>}
          <p>{t('account.report_practice', 'Supported practice: {{count}}', { count: concept.supported_practice })}</p>
          <p>{t('account.report_pending', 'Unknown: {{unknown}} · Pending: {{pending}}', {
            unknown: concept.unknown, pending: concept.pending,
          })}</p>
        </div>)}
      </section>
      {load.report.commentary && <p className="whitespace-pre-wrap"><bdi>{load.report.commentary}</bdi></p>}
      {load.report.can_request_review && <ReportReviewForm
        key={load.report.version} report={load.report} client={client}
        reload={() => {
          setLoad({ kind: 'loading' })
          setRefresh(value => value + 1)
        }}
      />}
    </article>}
  </section>
}

type ReviewState = 'ready' | 'sending' | 'received' | 'error' | 'stale'

function ReportReviewForm({ report, client, reload }: {
  report: ReportMetadata
  client: AdaptiveRequest
  reload: () => void
}) {
  const { t } = useTranslation()
  const reasonId = useId()
  const [state, setState] = useState<ReviewState>('ready')
  const request = useRef<AbortController | null>(null)

  useEffect(() => {
    const abort = () => request.current?.abort()
    window.addEventListener('pagehide', abort)
    return () => {
      window.removeEventListener('pagehide', abort)
      abort()
    }
  }, [client])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (request.current || state === 'received' || state === 'stale') return
    const reason = new FormData(event.currentTarget).get('reason')
    if (typeof reason !== 'string' || !reason) return
    const running = new AbortController()
    request.current = running
    setState('sending')
    try {
      const receipt = await client<{ status: string; report_version: number }>(
        `reports/${encodeURIComponent(report.uuid)}/review-requests`, 'POST',
        { reason, expected_version: report.version }, undefined, running.signal,
      )
      if (!running.signal.aborted) {
        setState(receipt?.status === 'received' && receipt.report_version === report.version ? 'received' : 'error')
      }
    } catch (error) {
      if (running.signal.aborted) return
      if (error instanceof AdaptiveRequestError && [401, 403, 404].includes(error.status)) reload()
      else setState(error instanceof AdaptiveRequestError && error.status === 409 ? 'stale' : 'error')
    } finally {
      if (request.current === running) request.current = null
    }
  }

  return <section className="space-y-3 border-t pt-4" aria-labelledby={`${reasonId}-title`}>
    <h3 id={`${reasonId}-title`} className="font-semibold">{t('account.report_request_review', 'Request a review')}</h3>
    {state === 'received' ? <p role="status">{t('account.report_review_received', 'Your review request was received.')}</p>
      : state === 'stale' ? <div className="space-y-2">
        <p role="status">{t('account.report_review_stale', 'This report changed. Reload it before requesting a review.')}</p>
        <Button type="button" variant="outline" className="min-h-11" onClick={reload}>
          {t('account.report_reload', 'Reload report')}
        </Button>
      </div> : <form className="space-y-3" onSubmit={submit}>
        <div className="space-y-1">
          <label htmlFor={reasonId} className="block text-sm font-medium">{t('account.report_review_reason', 'Reason for review')}</label>
          <select id={reasonId} name="reason" required defaultValue="" disabled={state === 'sending'}
            className="min-h-11 w-full max-w-md rounded-md border bg-background px-3">
            <option value="" disabled>{t('account.report_review_select', 'Choose a reason')}</option>
            <option value="evidence_error">{t('account.report_review_evidence', 'Incorrect learning evidence')}</option>
            <option value="report_inaccurate">{t('account.report_review_inaccurate', 'Inaccurate report')}</option>
            <option value="privacy_concern">{t('account.report_review_privacy', 'Privacy concern')}</option>
            <option value="other">{t('account.report_review_other', 'Other concern')}</option>
          </select>
        </div>
        {state === 'error' && <p role="alert">{t('account.report_review_error', 'Your request could not be confirmed. Try again.')}</p>}
        <Button type="submit" variant="outline" className="min-h-11" disabled={state === 'sending'}>
          {state === 'sending' ? t('account.report_review_sending', 'Sending request…')
            : t('account.report_review_send', 'Send review request')}
        </Button>
      </form>}
  </section>
}
