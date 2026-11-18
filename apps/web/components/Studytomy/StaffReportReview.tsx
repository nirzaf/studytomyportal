'use client'

import { type FormEvent, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@components/ui/button'
import { Input } from '@components/ui/input'
import { Textarea } from '@components/ui/textarea'
import { AdaptiveRequestError, type AdaptiveRequest, type Definition } from '@services/studytomy/adaptive'

type ReportReview = {
  course_uuid: string
  review_state: 'current' | 'stale' | 'correction_pending' | 'withdrawn'
  report: {
    uuid: string
    version: number
    status: 'draft' | 'approved' | 'published' | 'withdrawn'
    commentary: string | null
    approved_at: string | null
    digest: string
    period_from: string
    period_to: string
    evidence_watermark: number
    summary: {
      native_completions: number
      reviewed_release_completions?: number
      concepts: {
        concept_uuid: string; title: string; independent_correct: number; independent_incorrect: number
        supported_practice: number; unknown: number; pending: number; missing_independent_evidence: boolean
      }[]
    }
  }
  review_requests: { report_version: number; reason: string; count: number }[]
}

const states = {
  current: 'Current evidence', stale: 'Stale draft or approval',
  correction_pending: 'Correction pending', withdrawn: 'Withdrawn',
}
const changeReasons = {
  evidence_corrected: 'Evidence corrected', disclosure_authority: 'Disclosure authority changed',
  report_error: 'Report error', other: 'Other concern',
}
const reviewReasons: Record<string, string> = {
  evidence_error: 'Incorrect learning evidence', report_inaccurate: 'Inaccurate report',
  privacy_concern: 'Privacy concern', other: 'Other concern',
}

export default function StaffReportReview({ client, courseUuid, curricula }: {
  client: AdaptiveRequest; courseUuid: string; curricula: Definition[]
}) {
  const { t } = useTranslation()
  const [loaded, setLoaded] = useState<{ client: AdaptiveRequest; courseUuid: string; review: ReportReview }>()
  const review = loaded?.client === client && loaded.courseUuid === courseUuid ? loaded.review : undefined
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const active = useRef<AbortController | null>(null)

  useEffect(() => {
    const clear = () => {
      active.current?.abort()
      active.current = null
      setLoaded(undefined)
      setError('')
      setNotice('')
      setBusy(false)
    }
    const hide = () => { if (document.visibilityState === 'hidden') clear() }
    window.addEventListener('pagehide', clear)
    document.addEventListener('visibilitychange', hide)
    return () => {
      active.current?.abort()
      active.current = null
      window.removeEventListener('pagehide', clear)
      document.removeEventListener('visibilitychange', hide)
    }
  }, [client, courseUuid])

  async function run(uuid: string, mutation?: { method: 'PUT' | 'POST'; suffix: string; body?: object }) {
    if (active.current) return
    const running = new AbortController()
    active.current = running
    setBusy(true)
    setError('')
    setNotice('')
    if (!mutation) setLoaded(undefined)
    const path = `staff/reports/${encodeURIComponent(uuid)}`
    try {
      const receipt = mutation && await client<{ deliveries?: { status: string; recipient_count: number }[] }>(
        path + mutation.suffix, mutation.method, mutation.body, undefined, running.signal,
      )
      const next = await client<ReportReview>(path, 'GET', undefined, undefined, running.signal)
      if (running.signal.aborted) return
      if (next.course_uuid !== courseUuid || next.report.uuid !== uuid) {
        setLoaded(undefined)
        setError(t('adaptive.staffReport.unavailable', 'This report is unavailable for this course.'))
        return
      }
      setLoaded({ client, courseUuid, review: next })
      if (receipt?.deliveries) {
        const statuses = receipt.deliveries.map(({ status, recipient_count }) => {
          const labels: Record<string, [string, string]> = {
            provider_accepted: [
              'adaptive.staffReport.delivery.providerAccepted',
              'Accepted by email provider; delivery unconfirmed',
            ],
            queued: ['adaptive.staffReport.delivery.queued', 'Queued; not yet accepted by provider'],
            retry: ['adaptive.staffReport.delivery.retry', 'Retry scheduled after provider did not accept'],
            suppressed: ['adaptive.staffReport.delivery.suppressed', 'Suppressed by current authorization checks'],
            reconciliation_required: [
              'adaptive.staffReport.delivery.outcomeUnknown',
              'Provider outcome unknown; reconciliation is required',
            ],
            dead_letter: ['adaptive.staffReport.delivery.stopped', 'Stopped after repeated delivery failures'],
          }
          const [key, fallback] = labels[status] ?? [`adaptive.staffReport.delivery.${status}`, status]
          const label = t(key, fallback)
          return `${label} ${recipient_count}`
        })
        setNotice(statuses.length
          ? t('adaptive.staffReport.deliveryOutcome', 'Email notice status — {{statuses}}', { statuses: statuses.join(', ') })
          : t('adaptive.staffReport.noNotices', 'There are no email notices for this report version.'))
      } else if (mutation) setNotice(t('adaptive.staffReport.saved', 'Report updated.'))
    } catch (error) {
      if (running.signal.aborted) return
      setLoaded(undefined)
      setError(error instanceof AdaptiveRequestError && error.status === 409
        ? t('adaptive.staffReport.conflict', 'The report changed or is blocked. Open it again. {{detail}}', { detail: error.message })
        : t('adaptive.staffReport.unavailable', 'This report is unavailable for this course.'))
    } finally {
      if (active.current === running) {
        active.current = null
        setBusy(false)
      }
    }
  }

  function open(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const reference = new FormData(event.currentTarget).get('report_reference')
    if (typeof reference === 'string') void run(reference.trim())
  }

  const report = review?.report
  return <details className="rounded-lg border bg-card p-4 sm:p-6">
    <summary className="flex min-h-11 cursor-pointer items-center font-semibold">{t('adaptive.staffReport.title', 'Progress report review')}</summary>
    <StaffReportDirectory client={client} curricula={curricula.filter(row => row.status === 'published')}
      disabled={busy} open={uuid => { void run(uuid) }} />
    <form onSubmit={open} className="my-3 flex flex-wrap items-end gap-3">
      <label className="grid min-w-0 flex-1 gap-1 text-sm font-medium">
        {t('adaptive.staffReport.reference', 'Report reference')}
        <Input name="report_reference" required pattern="report_[0-9a-f\-]{36}" maxLength={43} className="min-h-11" />
      </label>
      <Button type="submit" variant="outline" disabled={busy} className="min-h-11">{t('adaptive.staffReport.open', 'Open report')}</Button>
    </form>
    {busy && <p role="status">{t('adaptive.staffReport.loading', 'Checking report…')}</p>}
    {notice && <p role="status">{notice}</p>}
    {error && <p role="alert" className="text-destructive">{error}</p>}
    {report && review && <div key={report.version} className="space-y-4 border-t pt-4 text-sm">
      <h4 className="font-semibold">{t('adaptive.staffReport.version', 'Version {{version}}', { version: report.version })} · {t(`adaptive.staffReport.state.${review.review_state}`, states[review.review_state])}</h4>
      <p>{t('adaptive.staffReport.cutoff', 'Evidence cutoff: {{cutoff}} (UTC)', { cutoff: new Date(report.period_to).toISOString() })}</p>
      <p>{report.approved_at ? t('adaptive.staffReport.approved', 'Approved {{date}}', { date: new Date(report.approved_at).toISOString() }) : t('adaptive.staffReport.unapproved', 'Awaiting approval')}</p>
      <ReportEvidence summary={report.summary} />
      {review.review_requests.length > 0 && <section className="space-y-2">
        <h5 className="font-semibold">{t('adaptive.staffReport.requests', 'Review requests')}</h5>
        <ul className="list-disc ps-5">{review.review_requests.map(request => <li key={`${request.report_version}/${request.reason}`}>
          {t('adaptive.staffReport.requestSummary', 'Version {{version}} · {{reason}} · {{count}}', {
            version: request.report_version, count: request.count,
            reason: t(`adaptive.staffReport.requestReason.${request.reason}`, reviewReasons[request.reason] ?? request.reason),
          })}
        </li>)}</ul>
      </section>}
      {review.review_state === 'current' && ['draft', 'approved'].includes(report.status) && <form className="space-y-2" onSubmit={event => {
        event.preventDefault()
        const commentary = String(new FormData(event.currentTarget).get('commentary') ?? '').trim()
        void run(report.uuid, { method: 'PUT', suffix: '', body: { expected_version: report.version, commentary: commentary || null } })
      }}>
        <label className="grid gap-1">{t('adaptive.staffReport.commentary', 'Reviewer commentary')}
          <Textarea name="commentary" maxLength={600} defaultValue={report.commentary ?? ''} disabled={busy} />
        </label>
        <Button type="submit" variant="outline" disabled={busy} className="min-h-11">{t('adaptive.staffReport.save', 'Save commentary')}</Button>
        {report.status === 'draft' && <Button type="button" className="ms-2 min-h-11" disabled={busy} onClick={event => {
          const form = event.currentTarget.form
          if (!form || String(new FormData(form).get('commentary') ?? '').trim() !== (report.commentary ?? '')) {
            setError(t('adaptive.staffReport.unsaved', 'Save your commentary before approving this version.'))
            return
          }
          void run(report.uuid, { method: 'POST', suffix: '/approve', body: { expected_version: report.version, expected_digest: report.digest } })
        }}>{t('adaptive.staffReport.approve', 'Approve reviewed version')}</Button>}
      </form>}
      {review.review_state === 'current' && report.status === 'approved' && <RecipientRelease
        key={report.uuid + '/' + report.version} client={client} report={report} busy={busy}
        publish={grants => {
          void run(report.uuid, { method: 'POST', suffix: '/publish', body: {
            expected_version: report.version, recipient_grant_uuids: grants,
          } })
        }}
      />}
      {review.review_state === 'current' && report.status === 'published' && (
        <Button type="button" variant="outline" disabled={busy} className="min-h-11" onClick={() => {
          void run(report.uuid, { method: 'POST', suffix: `/notify?expected_version=${report.version}` })
        }}>{t('adaptive.staffReport.notify', 'Request report email notices')}</Button>
      )}
      {['published', 'withdrawn'].includes(report.status) && <form className="space-y-2" onSubmit={event => {
        event.preventDefault()
        const action = (event.nativeEvent as SubmitEvent).submitter?.getAttribute('value')
        const reason = new FormData(event.currentTarget).get('reason')
        if (action !== 'withdraw' && action !== 'supersede') return
        void run(report.uuid, { method: 'POST', suffix: `/${action}`, body: { expected_version: report.version, reason } })
      }}>
        <label className="grid gap-1">{t('adaptive.staffReport.reason', 'Reason for change')}
          <select name="reason" required defaultValue="" disabled={busy} className="min-h-11 rounded-md border bg-background px-3">
            <option value="" disabled>{t('adaptive.choose', 'Choose an option')}</option>
            {Object.entries(changeReasons).map(([key, label]) => <option key={key} value={key}>{t(`adaptive.staffReport.changeReason.${key}`, label)}</option>)}
          </select>
        </label>
        <div className="flex flex-wrap gap-2">
          <Button type="submit" value="supersede" variant="outline" disabled={busy} className="min-h-11">{t('adaptive.staffReport.supersede', 'Create replacement draft')}</Button>
          {report.status === 'published' && <Button type="submit" value="withdraw" variant="destructive" disabled={busy} className="min-h-11">{t('adaptive.staffReport.withdraw', 'Withdraw report')}</Button>}
        </div>
      </form>}
    </div>}
  </details>
}


type ReportSummary = ReportReview['report']['summary']
type RecipientPreview = {
  uuid: string; version: number; learner_name: string; course_title: string; curriculum_title: string
  period_from: string; period_to: string; evidence_cutoff: string; summary: ReportSummary
}

function ReportEvidence({ summary }: { summary: ReportSummary }) {
  const { t } = useTranslation()
  return <>
      <p>{t('adaptive.staffReport.completion', 'Completed activities updated in this period: {{count}}', { count: summary.native_completions })}</p>
      {summary.reviewed_release_completions != null && (
        <p>Reviewed course releases completed in this period: {summary.reviewed_release_completions}</p>
      )}
      <ul className="grid gap-2">
        {summary.concepts.map(concept => <li key={concept.concept_uuid} className="rounded-md bg-muted p-3">
          <bdi className="font-medium">{concept.title}</bdi>
          <p>{concept.missing_independent_evidence
            ? t('adaptive.staffReport.missing', 'No independent evidence in this period.')
            : t('adaptive.staffReport.independent', 'Independent responses: {{correct}} correct, {{incorrect}} incorrect', { correct: concept.independent_correct, incorrect: concept.independent_incorrect })}</p>
          <p>{t('adaptive.staffReport.otherEvidence', 'Supported practice: {{practice}} · Unknown: {{unknown}} · Pending: {{pending}}', { practice: concept.supported_practice, unknown: concept.unknown, pending: concept.pending })}</p>
        </li>)}
      </ul>
  </>
}

type RecipientOptions = { items: { grant_uuid: string; first_name: string }[]; next_offset: number | null }

function RecipientRelease({ client, report, busy, publish }: {
  client: AdaptiveRequest; report: ReportReview['report']; busy: boolean; publish: (_grants: string[]) => void
}) {
  const { t } = useTranslation()
  const active = useRef<AbortController | null>(null)
  const [previews, setPreviews] = useState<{ grant: string; firstName: string; projection: RecipientPreview }[]>([])
  const [options, setOptions] = useState<RecipientOptions>()
  const [choice, setChoice] = useState('')
  const [selected, setSelected] = useState('')
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState('')
  const projection = previews.find(item => item.grant === selected)?.projection

  useEffect(() => () => active.current?.abort(), [client])

  async function findRecipients(offset: number) {
    if (active.current || busy) return
    const running = new AbortController()
    active.current = running
    setChecking(true)
    setError('')
    try {
      const params = new URLSearchParams({ expected_version: String(report.version), offset: String(offset) })
      const page = await client<RecipientOptions>(
        `staff/reports/${encodeURIComponent(report.uuid)}/recipients?${params}`, 'GET',
        undefined, undefined, running.signal,
      )
      if (!running.signal.aborted) {
        setOptions(page)
        setChoice('')
      }
    } catch (failure) {
      if (running.signal.aborted) return
      setOptions(undefined)
      setPreviews([])
      setError(failure instanceof AdaptiveRequestError ? failure.message
        : t('adaptive.staffReport.optionsError', 'Recipient options could not be loaded.'))
    } finally {
      if (active.current === running) {
        active.current = null
        setChecking(false)
      }
    }
  }

  async function preview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (active.current || busy || previews.length >= 20) return
    const option = options?.items.find(item => item.grant_uuid === choice)
    if (!option) return
    const grant = option.grant_uuid
    setError('')
    const running = new AbortController()
    active.current = running
    setChecking(true)
    try {
      const candidate = await client<RecipientPreview>(
        `staff/reports/${encodeURIComponent(report.uuid)}/recipient-preview`, 'POST',
        { expected_version: report.version, recipient_grant_uuid: grant }, undefined, running.signal,
      )
      if (running.signal.aborted) return
      if (candidate.uuid !== report.uuid || candidate.version !== report.version) {
        setPreviews([])
        setError(t('adaptive.staffReport.previewStale', 'Open the report again to preview its current version.'))
        return
      }
      setPreviews(items => [...items.filter(item => item.grant !== grant), { grant, firstName: option.first_name, projection: candidate }])
      setSelected(grant)
    } catch (failure) {
      if (running.signal.aborted) return
      if (failure instanceof AdaptiveRequestError) {
        setPreviews(items => failure.code === 'REPORT_RECIPIENT_NOT_AUTHORIZED'
          ? items.filter(item => item.grant !== grant) : [])
        setError(failure.message)
      } else {
        setError(t('adaptive.staffReport.previewError', 'The recipient preview could not be loaded.'))
      }
    } finally {
      if (active.current === running) {
        active.current = null
        setChecking(false)
      }
    }
  }

  return <section className="space-y-3 border-t pt-4">
    <h5 className="font-semibold">{t('adaptive.staffReport.recipients', 'Publication recipients')}</h5>
    <div className="flex flex-wrap gap-2">
      <Button type="button" variant="outline" disabled={busy || checking} className="min-h-11"
        onClick={() => { void findRecipients(0) }}>{t('adaptive.staffReport.findRecipients', 'Find authorized recipients')}</Button>
      {options?.next_offset != null && <Button type="button" variant="outline" disabled={busy || checking} className="min-h-11"
        onClick={() => { void findRecipients(options.next_offset!) }}>{t('adaptive.staffReport.moreRecipients', 'Next recipients')}</Button>}
    </div>
    {checking && <p role="status">{t('adaptive.staffReport.previewing', 'Checking recipients…')}</p>}
    {options && !options.items.length && <p>{t('adaptive.staffReport.noRecipients', 'There are no authorized recipients on this page.')}</p>}
    {!!options?.items.length && <form onSubmit={preview} className="flex flex-wrap items-end gap-2">
      <label className="grid min-w-0 flex-1 gap-1">{t('adaptive.staffReport.recipient', 'Recipient')}
        <select name="sharing_grant" required value={choice} onChange={event => setChoice(event.target.value)}
          disabled={busy || checking || previews.length >= 20} className="min-h-11 min-w-0 rounded-md border bg-background px-3">
          <option value="" disabled>{t('adaptive.choose', 'Choose an option')}</option>
          {options.items.map(item => <option key={item.grant_uuid} value={item.grant_uuid}>
            {item.first_name} · {item.grant_uuid}
          </option>)}
        </select>
      </label>
      <Button type="submit" variant="outline" disabled={!choice || busy || checking || previews.length >= 20} className="min-h-11">
        {t('adaptive.staffReport.preview', 'Preview recipient')}
      </Button>
    </form>}
    {error && <p role="alert" className="text-destructive">{error}</p>}
    {previews.length > 0 && <>
      <ul className="space-y-2">{previews.map(item => <li key={item.grant} className="flex flex-wrap items-center gap-2">
        <Button type="button" variant="outline" aria-pressed={selected === item.grant} onClick={() => setSelected(item.grant)}
          disabled={busy || checking} className="min-h-11 max-w-full whitespace-normal break-all">{item.firstName} · {item.grant}</Button>
        <Button type="button" variant="ghost" disabled={busy || checking} className="min-h-11" onClick={() => {
          setPreviews(rows => rows.filter(row => row.grant !== item.grant))
        }}>{t('adaptive.staffReport.removeRecipient', 'Remove')}<span className="sr-only"> {item.grant}</span></Button>
      </li>)}</ul>
      {projection && <div className="space-y-3 rounded-md border p-3">
        <h6 className="font-medium">{t('adaptive.staffReport.recipientView', 'Recipient view')}</h6>
        <p><bdi>{projection.learner_name}</bdi> · <bdi>{projection.course_title}</bdi> · <bdi>{projection.curriculum_title}</bdi></p>
        <p>{t('adaptive.staffReport.previewPeriod', '{{from}} to {{to}} (UTC)', { from: projection.period_from, to: projection.period_to })}</p>
        <ReportEvidence summary={projection.summary} />
      </div>}
      <Button type="button" disabled={busy || checking} className="min-h-11" onClick={() => publish(previews.map(item => item.grant))}>
        {t('adaptive.staffReport.publish', 'Publish to selected recipients ({{count}})', { count: previews.length })}
      </Button>
    </>}
  </section>
}


type ReportDirectoryPage = {
  items: { uuid: string; version: number; status: ReportReview['report']['status']; period_from: string; period_to: string }[]
  next_cursor: string | null
}
type ReportScope = { learner: string; revision: string }

function StaffReportDirectory({ client, curricula, disabled, open }: {
  client: AdaptiveRequest; curricula: Definition[]; disabled: boolean; open: (_uuid: string) => void
}) {
  const { t } = useTranslation()
  const [scope, setScope] = useState<ReportScope>({ learner: '', revision: '' })
  const [result, setResult] = useState<{ client: AdaptiveRequest; learner: string; revision: string; page: ReportDirectoryPage }>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const active = useRef<AbortController | null>(null)
  const operation = useRef<{ input: string; uuid: string } | null>(null)
  const revision = curricula.find(row => row.uuid === scope.revision)?.uuid ?? curricula[0]?.uuid
  const learner = scope.learner.trim()
  const page = result?.client === client && result.learner === learner && result.revision === revision ? result.page : undefined
  const path = `staff/learners/${encodeURIComponent(learner)}/reports`

  useEffect(() => {
    const clear = () => {
      active.current?.abort()
      active.current = null
      operation.current = null
      setResult(undefined)
      setScope({ learner: '', revision: '' })
      setBusy(false)
      setError('')
    }
    const hide = () => { if (document.visibilityState === 'hidden') clear() }
    window.addEventListener('pagehide', clear)
    document.addEventListener('visibilitychange', hide)
    return () => {
      active.current?.abort()
      active.current = null
      operation.current = null
      window.removeEventListener('pagehide', clear)
      document.removeEventListener('visibilitychange', hide)
    }
  }, [client])

  async function request(action: 'list' | 'create', value?: string | FormData) {
    if (!learner || !revision || disabled || active.current) return
    setError('')
    const controller = new AbortController()
    active.current = controller
    setBusy(true)
    try {
      if (action === 'create' && value instanceof FormData) {
        const from = String(value.get('period_from') ?? '')
        const to = String(value.get('period_to') ?? '')
        if (!from || !to || from >= to) {
          setError(t('adaptive.staffReport.periodOrder', 'The end date must be after the start date.'))
          return
        }
        const input = JSON.stringify([learner, revision, from, to])
        if (operation.current?.input !== input) operation.current = { input, uuid: crypto.randomUUID() }
        const draft = await client<{ uuid: string }>(path, 'POST', {
          operation_uuid: operation.current.uuid, curriculum_revision_uuid: revision,
          period_from: `${from}T00:00:00Z`, period_to: `${to}T00:00:00Z`,
        }, undefined, controller.signal)
        if (!controller.signal.aborted) {
          setResult(undefined)
          open(draft.uuid)
        }
      } else {
        const params = new URLSearchParams({ curriculum_revision_uuid: revision, limit: '10' })
        if (typeof value === 'string') params.set('cursor', value)
        const next = await client<ReportDirectoryPage>(`${path}?${params}`, 'GET', undefined, undefined, controller.signal)
        if (!controller.signal.aborted) setResult({ client, learner, revision, page: next })
      }
    } catch (failure) {
      if (controller.signal.aborted) return
      setResult(undefined)
      setError(failure instanceof AdaptiveRequestError && failure.status === 409
        ? failure.message : t('adaptive.staffReport.directoryError', 'Reports are unavailable for this learner and curriculum.'))
    } finally {
      if (active.current === controller) {
        active.current = null
        setBusy(false)
      }
    }
  }

  if (!curricula.length) return null
  const locked = busy || disabled
  return <div className="my-3 space-y-3 border-b pb-4">
    <form onSubmit={event => { event.preventDefault(); void request('list') }} className="grid gap-3 sm:grid-cols-2">
      <label className="grid gap-1 text-sm font-medium">{t('adaptive.staffReport.learner', 'Learner reference')}
        <Input required maxLength={100} className="min-h-11" value={scope.learner} disabled={locked}
          onChange={event => { setScope({ ...scope, learner: event.target.value }); setResult(undefined); setError('') }} />
      </label>
      <label className="grid gap-1 text-sm font-medium">{t('adaptive.staffReport.curriculum', 'Published curriculum')}
        <select className="min-h-11 rounded-md border bg-background px-3" value={revision} disabled={locked}
          onChange={event => { setScope({ ...scope, revision: event.target.value }); setResult(undefined); setError('') }}>
          {curricula.map(row => <option key={row.uuid} value={row.uuid}>{row.label}</option>)}
        </select>
      </label>
      <Button type="submit" variant="outline" className="min-h-11 justify-self-start" disabled={locked || !learner}>
        {t('adaptive.staffReport.find', 'Find reports')}
      </Button>
    </form>
    {busy && <p role="status">{t('adaptive.staffReport.loading', 'Checking report…')}</p>}
    {error && <p role="alert" className="text-destructive">{error}</p>}
    {page && <div className="space-y-3">
      {page.items.length ? <ul className="space-y-2">{page.items.map(item => <li key={item.uuid} className="rounded-md border p-2">
        <Button type="button" variant="ghost" disabled={locked} className="min-h-11 max-w-full whitespace-normal" onClick={() => open(item.uuid)}>
          {t('adaptive.staffReport.listEntry', '{{from}} to {{to}} · Version {{version}} · {{status}}', {
            from: item.period_from.slice(0, 10), to: item.period_to.slice(0, 10),
            version: item.version, status: t(`adaptive.status.${item.status}`, item.status),
          })}
        </Button>
      </li>)}</ul> : <p>{t('adaptive.staffReport.empty', 'There are no reports for this learner and curriculum.')}</p>}
      {page.next_cursor && <Button type="button" variant="outline" className="min-h-11" disabled={locked}
        onClick={() => { void request('list', page.next_cursor ?? undefined) }}>
        {t('adaptive.staffReport.more', 'Next reports')}
      </Button>}
      <form className="grid gap-3 sm:grid-cols-2" onSubmit={event => {
        event.preventDefault()
        void request('create', new FormData(event.currentTarget))
      }}>
        <label className="grid gap-1 text-sm font-medium">{t('adaptive.staffReport.start', 'Start date (UTC)')}
          <Input type="date" name="period_from" required disabled={locked} className="min-h-11" />
        </label>
        <label className="grid gap-1 text-sm font-medium">{t('adaptive.staffReport.end', 'End date, excluded (UTC)')}
          <Input type="date" name="period_to" required disabled={locked} className="min-h-11" />
        </label>
        <Button type="submit" className="min-h-11 justify-self-start" disabled={locked}>
          {t('adaptive.staffReport.create', 'Create report draft')}
        </Button>
      </form>
    </div>}
  </div>
}
