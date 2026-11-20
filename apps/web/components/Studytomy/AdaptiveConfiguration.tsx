'use client'

import { useEffect, useId, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@components/ui/button'
import { Input } from '@components/ui/input'
import { Textarea } from '@components/ui/textarea'
import StaffReportReview from '@components/Studytomy/StaffReportReview'
import {
  adaptiveRequest, type AdaptiveConfiguration as Catalog, type CurriculumReview, type Definition,
  type DefinitionKind, type DiagnosticReview, type ItemReview, type NativePreview, type Question,
} from '@services/studytomy/adaptive'

const selectClass = 'min-h-11 max-w-full rounded-md border border-input bg-background p-2 text-sm focus-visible:outline-ring'
const card = 'rounded-lg border bg-card p-4 sm:p-6'
const grid = 'mt-4 grid gap-4 sm:grid-cols-2'

function Field({ name, label, children, type = 'text', min, max, defaultValue }: {
  name: string; label: string; children?: ReactNode; type?: string; min?: number; max?: number
  defaultValue?: string | number
}) {
  const id = useId()
  const { t } = useTranslation()
  return (
    <label htmlFor={children ? undefined : id} className="grid min-w-0 gap-1.5 text-sm font-medium">
      {t(`adaptive.fields.${name}`, label)}
      {children ?? <Input id={id} name={name} type={type} min={min} max={max} defaultValue={defaultValue} required className="min-h-11" />}
    </label>
  )
}

function Choice({ name, label, options, required = true }: {
  name: string; label: string; options: { value: string; label: string }[]; required?: boolean
}) {
  const { t } = useTranslation()
  return <Field name={name} label={label}>
    <select name={name} required={required} className={selectClass} defaultValue="">
      <option value="">{t('adaptive.choose', 'Choose an option')}</option>
      {options.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
    </select>
  </Field>
}

function Preview({ question }: { question: Question }) {
  return <div className="my-3 rounded-md border bg-muted p-3 text-sm">
    <p className="whitespace-pre-wrap break-words">{question.prompt}</p>
    {question.options && <ul className="mt-2 list-disc ps-5">
      {question.options.map(option => <li key={option.option_uuid}>{option.text}</li>)}
    </ul>}
    {question.unit && <p className="mt-2">{question.unit}</p>}
  </div>
}

function CurriculumEditor({ snapshot, catalog, courseUuid, busy, save }: {
  snapshot: CurriculumReview; catalog: Catalog; courseUuid: string; busy: boolean
  save: (_path: string, _method: 'PUT', _body: object) => void
}) {
  const { t } = useTranslation()
  const concepts = snapshot.concepts.filter(concept => !concept.archived)
  const [mappingConcept, setMappingConcept] = useState('')
  const mappings = snapshot.mappings.filter(mapping => mapping.concept_uuid === mappingConcept && !mapping.archived)
  return <div className="grid gap-4">
    <form className={card} onSubmit={event => {
      event.preventDefault()
      const values = new FormData(event.currentTarget)
      const code = String(values.get('code')).trim()
      const prior = String(values.get('prerequisite'))
      save(`curriculum-revisions/${snapshot.curriculum.uuid}/concepts`, 'PUT', {
        expected_version: snapshot.curriculum.version,
        concepts: [...concepts.map(({ code, title, objective, sequence }) => ({ code, title, objective, sequence })), {
          code, title: values.get('title'), objective: values.get('objective'),
          sequence: Math.max(-1, ...concepts.map(concept => concept.sequence)) + 1,
        }],
        prerequisites: prior ? [...snapshot.prerequisites, { concept_code: code, prerequisite_code: prior }] : snapshot.prerequisites,
      })
    }}>
      <h3 className="font-semibold">{t('adaptive.concepts', 'Concepts and prerequisites')}</h3>
      {concepts.length > 0 && <ul className="mt-2 list-disc ps-5 text-sm">
        {concepts.map(concept => <li key={concept.uuid}>{concept.title}</li>)}
      </ul>}
      <div className={grid}>
        <Field name="code" label="Concept code" />
        <Field name="title" label="Concept title" />
        <Field name="objective" label="Learning objective"><Textarea name="objective" required /></Field>
        <Choice name="prerequisite" label="Prerequisite (optional)" required={false} options={concepts.map(c => ({ value: c.code, label: c.title }))} />
      </div>
      <Button className="mt-4 min-h-11" disabled={busy}>{t('adaptive.addConcept', 'Add concept')}</Button>
    </form>
    <form className={card} onSubmit={event => {
      event.preventDefault()
      const values = new FormData(event.currentTarget)
      const activity = catalog.activities.find(row => row.uuid === values.get('activity'))
      if (!mappingConcept || !activity) return
      const usage = values.get('usage')
      save(`concepts/${mappingConcept}/activities`, 'PUT', {
        expected_version: snapshot.curriculum.version,
        mappings: [
          ...mappings.filter(row => row.activity_uuid !== activity.uuid || row.usage !== usage)
            .map(({ activity_uuid, activity_version, course_uuid, usage }) => ({ activity_uuid, activity_version, course_uuid, usage })),
          { activity_uuid: activity.uuid, activity_version: activity.version, course_uuid: courseUuid, usage },
        ],
      })
    }}>
      <h3 className="font-semibold">{t('adaptive.mappings', 'Lesson mappings')}</h3>
      <div className={grid}>
        <Field name="mappingConcept" label="Concept">
          <select className={selectClass} value={mappingConcept} onChange={event => setMappingConcept(event.target.value)} required>
            <option value="">{t('adaptive.chooseConcept', 'Choose a concept')}</option>
            {concepts.map(c => <option value={c.uuid} key={c.uuid}>{c.title}</option>)}
          </select>
        </Field>
        <Choice name="activity" label="Course activity" options={catalog.activities.map(a => ({ value: a.uuid, label: a.name }))} />
        <Choice name="usage" label="Use" options={[
          { value: 'teach', label: t('adaptive.teach', 'Teach') },
          { value: 'practice', label: t('adaptive.practice', 'Practice') },
          { value: 'review', label: t('adaptive.review', 'Review') },
        ]} />
      </div>
      {mappings.length > 0 && <ul className="my-3 list-disc ps-5 text-sm">
        {mappings.map(mapping => {
          const activity = catalog.activities.find(row => row.uuid === mapping.activity_uuid)
          const stale = activity && activity.version !== mapping.activity_version
          return <li key={`${mapping.activity_uuid}:${mapping.usage}`}>
            {activity?.name || mapping.activity_uuid} · {t(`adaptive.${mapping.usage}`, mapping.usage)}
            {stale && <span className="ms-2 text-amber-800">{t('adaptive.staleMapping', 'Activity changed. Save a fresh mapping.')}</span>}
          </li>
        })}
      </ul>}
      <Button className="mt-4 min-h-11" disabled={busy || !mappingConcept}>{t('adaptive.saveMapping', 'Save mapping')}</Button>
    </form>
  </div>
}

type ReviewSelection = {
  kind: DefinitionKind; row: Definition; digest: string
  item?: ItemReview; curriculum?: CurriculumReview; diagnostic?: DiagnosticReview
}
type Save = (
  _path: string, _method: 'POST' | 'PUT', _body: object, _message: string,
  _after?: (_result: unknown) => void,
) => void

function ItemEditor({ choices, busy, client, save, reportError }: {
  choices: { value: string; label: string }[]; busy: boolean; client: ReturnType<typeof adaptiveRequest>
  save: Save; reportError: (_error: unknown) => void
}) {
  const { t } = useTranslation()
  const [native, setNative] = useState<{ preview: NativePreview; concept: string; task: string; question: string }>()
  const [loading, setLoading] = useState(false)
  return <section className={card}>
    <h3 className="font-semibold">{t('adaptive.items', 'Reviewed items')}</h3>
    <details className="mt-3">
      <summary className="flex min-h-11 cursor-pointer items-center text-sm font-medium">{t('adaptive.nativeItem', 'Import from an assignment task')}</summary>
      <form onSubmit={async event => {
        event.preventDefault()
        const values = new FormData(event.currentTarget)
        const task = String(values.get('task')).trim()
        const question = String(values.get('question') ?? '').trim()
        setNative(undefined)
        setLoading(true)
        try {
          const preview = await client<NativePreview>(`item-sources/${encodeURIComponent(task)}${question ? `?question_uuid=${encodeURIComponent(question)}` : ''}`)
          setNative({ preview, concept: String(values.get('concept')), task, question })
        } catch (error) {
          reportError(error)
        } finally {
          setLoading(false)
        }
      }}>
        <div className={grid}>
          <Choice name="concept" label="Concept" options={choices} />
          <Field name="task" label="Assignment task ID" />
          <Field name="question" label="Question ID (for a quiz task)"><Input name="question" className="min-h-11" /></Field>
        </div>
        <Button type="submit" variant="outline" className="mt-4 min-h-11" disabled={busy || loading}>{t('adaptive.previewTask', 'Preview question')}</Button>
      </form>
      {native && <>
        <Preview question={native.preview.question} />
        <Button onClick={() => save('item-revisions', 'POST', {
          concept_uuid: native.concept, source_task_uuid: native.task,
          source_question_uuid: native.question || null, expected_source_digest: native.preview.source_digest,
          difficulty: 1,
        }, t('adaptive.itemSaved', 'Item draft saved.'), () => setNative(undefined))} disabled={busy}>
          {t('adaptive.saveItem', 'Save item draft')}
        </Button>
      </>}
    </details>
    <details className="mt-3 border-t pt-2">
      <summary className="flex min-h-11 cursor-pointer items-center text-sm font-medium">{t('adaptive.originalItem', 'Create an original numeric item')}</summary>
      <form onSubmit={event => {
        event.preventDefault()
        const form = event.currentTarget
        const values = new FormData(form)
        save('item-revisions', 'POST', {
          concept_uuid: values.get('concept'), difficulty: Number(values.get('difficulty')),
          original_question: { kind: 'number', prompt: values.get('prompt'), unit: values.get('unit') },
          original_answer: { kind: 'number', expected: values.get('expected'), tolerance: values.get('tolerance') },
          original_rights_reference: values.get('rights_reference'),
        }, t('adaptive.itemSaved', 'Item draft saved.'), () => form.reset())
      }}>
        <div className={grid}>
          <Choice name="concept" label="Concept" options={choices} />
          <Field name="difficulty" label="Difficulty (1–5)" type="number" min={1} max={5} defaultValue={1} />
          <Field name="prompt" label="Question"><Textarea name="prompt" required /></Field>
          <Field name="unit" label="Unit (optional)"><Input name="unit" className="min-h-11" /></Field>
          <Field name="expected" label="Private expected answer" />
          <Field name="tolerance" label="Absolute tolerance" defaultValue="0" />
          <Field name="rights_reference" label="Rights reference" />
        </div>
        <Button disabled={busy} className="mt-4 min-h-11">{t('adaptive.saveItem', 'Save item draft')}</Button>
      </form>
    </details>
  </section>
}

function DiagnosticEditor({ concepts, pool, curriculum, busy, save }: {
  concepts: Catalog['concepts']; pool: Definition[]; curriculum: Definition; busy: boolean; save: Save
}) {
  const { t } = useTranslation()
  return <form className={card} onSubmit={event => {
    event.preventDefault()
    const values = new FormData(event.currentTarget)
    save('diagnostic-revisions', 'POST', {
      curriculum_uuid: curriculum.uuid, name: values.get('name'),
      required_concepts: values.getAll('required_concepts'), item_uuids: values.getAll('items'),
      selector_version: 'adaptive_coverage_v1', mastery_policy_uuid: values.get('mastery_policy_uuid'),
      limits: {
        total_items: Number(values.get('total_items')), items_per_concept: Number(values.get('items_per_concept')),
        minimum_scored_per_concept: Number(values.get('minimum_scored_per_concept')), expires_after_minutes: Number(values.get('expires_after_minutes')),
      },
      rules: {
        allow_help: false, reveal: values.get('reveal'), max_attempts: Number(values.get('max_attempts')),
        retake_wait_minutes: Number(values.get('retake_wait_minutes')),
      },
    }, t('adaptive.diagnosticSaved', 'Diagnostic draft saved.'))
  }}>
    <h3 className="font-semibold">{t('adaptive.createDiagnostic', 'Create a diagnostic draft')}</h3>
    <div className={grid}>
      <Field name="name" label="Diagnostic name" />
      <Field name="mastery_policy_uuid" label="Approved mastery policy ID" />
      <fieldset className="min-w-0"><legend className="font-medium">{t('adaptive.requiredConcepts', 'Required concepts')}</legend>
        {concepts.map(c => <label key={c.uuid} className="flex min-h-11 items-center gap-2 text-sm">
          <input type="checkbox" name="required_concepts" value={c.uuid} />
          {c.title}
        </label>)}
      </fieldset>
      <fieldset className="min-w-0"><legend className="font-medium">{t('adaptive.itemPool', 'Published diagnostic items')}</legend>
        {pool.length === 0 && <p className="text-sm text-muted-foreground">{t('adaptive.emptyPool', 'Publish an original diagnostic item first.')}</p>}
        {pool.map(item => <label key={item.uuid} className="flex min-h-11 items-center gap-2 break-words text-sm">
          <input type="checkbox" name="items" value={item.uuid} />
          {item.label}
        </label>)}
      </fieldset>
      <Field name="total_items" label="Maximum total items" type="number" min={1} />
      <Field name="items_per_concept" label="Maximum items per concept" type="number" min={1} />
      <Field name="minimum_scored_per_concept" label="Minimum scored per concept" type="number" min={1} />
      <Field name="expires_after_minutes" label="Expires after (minutes)" type="number" min={1} />
      <Field name="max_attempts" label="Maximum attempts" type="number" min={1} max={20} />
      <Field name="retake_wait_minutes" label="Retake wait (minutes)" type="number" min={0} max={43200} />
      <Choice name="reveal" label="Score reveal" options={[
        { value: 'never', label: t('adaptive.revealNever', 'Never') },
        { value: 'after_close', label: t('adaptive.revealAfterClose', 'After the attempt closes') },
      ]} />
    </div>
    <Button className="mt-4 min-h-11" disabled={busy || pool.length === 0}>{t('adaptive.saveDiagnostic', 'Save diagnostic draft')}</Button>
  </form>
}

export default function AdaptiveConfiguration({ orgUuid, courseUuid, accessToken, initial }: {
  orgUuid: string; courseUuid: string; accessToken: string; initial: Catalog
}) {
  const { t } = useTranslation()
  const client = useMemo(() => adaptiveRequest(orgUuid, accessToken), [orgUuid, accessToken])
  const [catalog, setCatalog] = useState(initial)
  const [selected, select] = useState('')
  const [snapshot, setSnapshot] = useState<CurriculumReview>()
  const [review, setReview] = useState<ReviewSelection>()
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const mutex = useRef(false)
  const failure = useRef<HTMLDivElement>(null)
  const retry = useRef<{ signature: string; key: string } | null>(null)
  const curriculum = catalog.curricula.find(row => row.uuid === selected) ?? catalog.curricula[0]
  const curriculumUuid = curriculum?.uuid
  const draftVersion = curriculum?.status === 'draft' && curriculum.version
  const concepts = catalog.concepts.filter(concept => concept.curriculum_uuid === curriculumUuid)
  const items = catalog.items.filter(item => item.curriculum_uuid === curriculumUuid)
  const diagnostics = catalog.diagnostics.filter(item => item.curriculum_uuid === curriculumUuid)
  const title = useId()

  function reportError(reason: unknown) {
    setError(reason instanceof Error ? reason.message : t('adaptive.requestFailed', 'The request could not be completed.'))
  }

  useEffect(() => {
    if (error) failure.current?.focus()
  }, [error])

  useEffect(() => {
    if (!curriculumUuid || !draftVersion) return
    const controller = new AbortController()
    client<CurriculumReview>(`curriculum-revisions/${curriculumUuid}/review`, 'GET', undefined, undefined, controller.signal)
      .then(value => { if (!controller.signal.aborted) setSnapshot(value) })
      .catch(error => { if (!controller.signal.aborted) setError(error.message) })
    return () => controller.abort()
  }, [client, curriculumUuid, draftVersion])

  const save: Save = async (path, method, body, message, after) => {
    if (mutex.current) return
    mutex.current = true
    setBusy(true)
    setError('')
    setStatus('')
    try {
      const signature = JSON.stringify([path, body])
      if (method === 'POST' && retry.current?.signature !== signature) retry.current = { signature, key: crypto.randomUUID() }
      const result = await client<unknown>(path, method, body, method === 'POST' ? retry.current?.key : undefined)
      retry.current = null
      setReview(undefined)
      setStatus(message)
      setCatalog(await client<Catalog>(`courses/${encodeURIComponent(courseUuid)}/configuration`))
      after?.(result)
    } catch (reason) {
      reportError(reason)
    } finally {
      mutex.current = false
      setBusy(false)
    }
  }

  async function openReview(kind: DefinitionKind, row: Definition) {
    if (mutex.current) return
    mutex.current = true
    setBusy(true)
    setError('')
    setReview(undefined)
    try {
      if (kind === 'diagnostic') {
        const diagnostic = await client<DiagnosticReview>(`diagnostic-revisions/${row.uuid}`)
        setReview({ kind, row, digest: diagnostic.content_digest, diagnostic })
      } else if (kind === 'item' && row.status === 'draft') {
        const preview = await client<ItemReview>(`item-revisions/${row.uuid}/${catalog.can_review ? 'grading-review' : 'review'}`)
        setReview({ kind, row, digest: preview.content_digest, item: preview })
      } else if (kind === 'curriculum' && row.status === 'draft') {
        const preview = await client<CurriculumReview>(`curriculum-revisions/${row.uuid}/review`)
        setReview({ kind, row, digest: preview.content_digest, curriculum: preview })
      } else if (row.content_digest) {
        setReview({ kind, row, digest: row.content_digest })
      }
    } catch (reason) {
      reportError(reason)
    } finally {
      mutex.current = false
      setBusy(false)
    }
  }

  function createCurriculum(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const fields = Object.fromEntries(new FormData(form))
    const pmAuthority = String(fields.programme_authority ?? '').trim()
    const pmEdition = String(fields.programme_syllabus_edition ?? '').trim()
    const pmCohort = String(fields.programme_effective_cohort ?? '').trim()
    const pmStage = String(fields.programme_stage ?? 'other').trim()
    const pmCoverage = String(fields.programme_coverage_status ?? 'unverified').trim()
    const pmLanguage = String(fields.programme_source_language ?? '').trim()
    const hasProgramme = Boolean(pmAuthority || pmEdition || pmCohort)
    const programme_metadata = hasProgramme
      ? {
          authority: pmAuthority,
          syllabus_edition: pmEdition,
          effective_cohort: pmCohort,
          stage: pmStage || 'other',
          coverage_status: pmCoverage || 'unverified',
          source_language: pmLanguage || null,
        }
      : null
    save('curriculum-revisions', 'POST', {
      curriculum_key: fields.curriculum_key,
      revision: Number(fields.revision),
      subject: fields.subject,
      level: fields.level,
      language: fields.language,
      owner_course_uuid: courseUuid,
      jurisdiction: String(fields.jurisdiction ?? '').trim() || null,
      source_reference: fields.source_reference,
      rights_reference: fields.rights_reference,
      ...(programme_metadata ? { programme_metadata } : {}),
    }, t('adaptive.curriculumSaved', 'Curriculum draft saved.'), result => {
      form.reset()
      const createdUuid = (result as { uuid?: string } | null)?.uuid
      if (createdUuid) select(createdUuid)
    })
  }

  const rows: readonly (readonly [DefinitionKind, Definition])[] = [
    ...(curriculum ? [['curriculum', curriculum] as const] : []),
    ...items.map(row => ['item', row] as const), ...diagnostics.map(row => ['diagnostic', row] as const),
  ]

  return <section aria-labelledby={title} aria-busy={busy} className="mx-auto grid w-full max-w-6xl gap-4 p-4 sm:p-8">
    <h2 id={title} className="text-xl font-semibold">{t('adaptive.title', 'Adaptive learning')}</h2>
    <p className="sr-only" role="status">{busy ? t('adaptive.working', 'Working…') : status}</p>
    {status && !busy && <p className="text-sm font-medium">{status}</p>}
    {error && <div role="alert" tabIndex={-1} ref={failure} className="rounded-md border border-destructive p-3 text-sm text-destructive">{error}</div>}
    {catalog.can_author && <details className={card}>
      <summary className="flex min-h-11 cursor-pointer items-center font-semibold">{t('adaptive.newCurriculum', 'Create a curriculum draft')}</summary>
      <form onSubmit={createCurriculum}>
        <div className={grid}>
          <Field name="curriculum_key" label="Curriculum key" />
          <Field name="revision" label="Revision number" type="number" min={1} defaultValue={1} />
          <Field name="subject" label="Subject" />
          <Field name="level" label="Learner level" />
          <Field name="language" label="Language code" />
          <Field name="jurisdiction" label="Jurisdiction (optional)"><Input name="jurisdiction" className="min-h-11" /></Field>
          <Field name="source_reference" label="Curriculum source reference" />
          <Field name="rights_reference" label="Rights reference" />
        </div>
        <details className="mt-3 rounded-md border p-3 text-sm">
          <summary className="cursor-pointer font-medium">Official programme metadata (optional, e.g. NIE Sri Lanka)</summary>
          <div className={`mt-2 ${grid}`}>
            <Field name="programme_authority" label="Syllabus authority"><Input name="programme_authority" maxLength={120} className="min-h-11" /></Field>
            <Field name="programme_syllabus_edition" label="Syllabus edition"><Input name="programme_syllabus_edition" maxLength={64} className="min-h-11" /></Field>
            <Field name="programme_effective_cohort" label="Effective cohort"><Input name="programme_effective_cohort" maxLength={64} className="min-h-11" /></Field>
            <Field name="programme_stage" label="School stage">
              <select name="programme_stage" defaultValue="other" className={selectClass}>
                <option value="other">Other</option>
                <option value="junior_secondary">Junior Secondary (Grades 6–9)</option>
                <option value="gce_ol">G.C.E. O/L (Grades 10–11)</option>
                <option value="gce_al">G.C.E. A/L (Grades 12–13)</option>
              </select>
            </Field>
            <Field name="programme_coverage_status" label="Coverage status">
              <select name="programme_coverage_status" defaultValue="unverified" className={selectClass}>
                <option value="unverified">Unverified</option>
                <option value="partial">Partial</option>
                <option value="supplementary_only">Supplementary only</option>
                <option value="verified_complete">Verified complete</option>
              </select>
            </Field>
            <Field name="programme_source_language" label="Source language"><Input name="programme_source_language" defaultValue="en" maxLength={32} className="min-h-11" /></Field>
          </div>
        </details>
        <Button disabled={busy} className="mt-4 min-h-11">{t('adaptive.saveCurriculum', 'Save curriculum draft')}</Button>
      </form>
    </details>}
    {curriculum ? <Field name="curriculum" label="Curriculum revision">
      <select value={curriculum.uuid} onChange={event => { select(event.target.value); setReview(undefined) }} className={selectClass} disabled={busy}>
        {catalog.curricula.map(row => <option key={row.uuid} value={row.uuid}>{row.label} · {t(`adaptive.status.${row.status}`, row.status)}</option>)}
      </select>
    </Field> : <p className="text-sm text-muted-foreground">{t('adaptive.noCurriculum', 'No curriculum revisions for this course.')}</p>}
    {catalog.can_author && draftVersion && snapshot?.curriculum.uuid === curriculumUuid && snapshot.curriculum.version === draftVersion &&
      <CurriculumEditor key={curriculumUuid} snapshot={snapshot} courseUuid={courseUuid} catalog={catalog} busy={busy}
        save={(path, method, body) => save(path, method, body, t('adaptive.curriculumSaved', 'Curriculum draft saved.'))} />}
    {catalog.can_author && curriculum?.status !== 'retired' && concepts.length > 0 &&
      <ItemEditor key={curriculumUuid} choices={concepts.map(c => ({ value: c.uuid, label: c.title }))} client={client} busy={busy} save={save} reportError={reportError} />}
    {catalog.can_author && curriculum?.status === 'published' && <DiagnosticEditor
      concepts={concepts} curriculum={curriculum} pool={items.filter(item => item.status === 'published' && item.independent)} busy={busy} save={save} />}
    {rows.length > 0 && <section className={card}>
      <h3 className="font-semibold">{t('adaptive.definitions', 'Definitions and review')}</h3>
      <ul className="mt-3 grid gap-2">
        {rows.map(([kind, row]) => <li key={row.uuid} className="flex flex-wrap items-center justify-between gap-2 border-b pb-2 text-sm">
          <span className="min-w-0 break-words">{row.label} · {t(`adaptive.status.${row.status}`, row.status)}</span>
          {row.status !== 'retired' && <Button type="button" variant="outline" className="min-h-11" disabled={busy} onClick={() => openReview(kind, row)}>
            {t('adaptive.openReview', 'Open review')}<span className="sr-only"> {row.label}</span>
          </Button>}
        </li>)}
      </ul>
      {review && <div className="mt-4 grid gap-3 border-t pt-4">
        <h4 className="font-semibold">{review.row.label}</h4>
        <p className="break-all text-sm"><span className="font-medium">{t('adaptive.reviewDigest', 'Review digest')}</span> <code>{review.digest}</code></p>
        {review.curriculum && <>
          <dl className="grid gap-2 break-words text-sm">
            <div><dt className="font-medium">{t('adaptive.fields.source_reference', 'Curriculum source reference')}</dt><dd>{review.curriculum.source_reference}</dd></div>
            <div><dt className="font-medium">{t('adaptive.fields.rights_reference', 'Rights reference')}</dt><dd>{review.curriculum.rights_reference}</dd></div>
          </dl>
          <ul className="grid gap-2 text-sm">
            {review.curriculum.concepts.filter(concept => !concept.archived).map(concept => <li key={concept.uuid} className="rounded-md bg-muted p-3">
              <p className="font-medium">{concept.title}</p>
              <p className="whitespace-pre-wrap break-words">{concept.objective}</p>
              {review.curriculum?.mappings.filter(mapping => mapping.concept_uuid === concept.uuid && !mapping.archived).map(mapping => (
                <p key={`${mapping.activity_uuid}:${mapping.usage}`} className="break-all">
                  {catalog.activities.find(activity => activity.uuid === mapping.activity_uuid)?.name ?? mapping.activity_uuid}
                  {' · '}{t(`adaptive.${mapping.usage}`, mapping.usage)}
                </p>
              ))}
            </li>)}
          </ul>
        </>}
        {review.item && <>
          <Preview question={review.item.item.question} />
          <p className="break-words text-sm">{t('adaptive.fields.rights_reference', 'Rights reference')} · {review.item.rights_reference}</p>
          <p className="text-sm">{review.item.allowed_use === 'practice_only'
            ? t('adaptive.practiceOnly', 'Practice only') : t('adaptive.independent', 'Independent diagnostic')}</p>
          {review.item.private_answer && <div className="space-y-1 rounded-md border border-amber-500/50 bg-amber-50 p-3 text-sm text-amber-950">
            <h5 className="font-semibold">Private grading rule · version {review.item.item_version}</h5>
            <p>Evaluator: <code>{review.item.evaluator_version}</code></p>
            {review.item.private_answer.kind === 'number' ? (
              <p>Expected value: <code>{review.item.private_answer.expected}</code> ± <code>{review.item.private_answer.tolerance}</code>{review.item.item.question.unit ? ` ${review.item.item.question.unit}` : ''}</p>
            ) : review.item.private_answer.correct_option_uuid ? (
              <p>Correct option: {review.item.item.question.options?.find(option => option.option_uuid === review.item?.private_answer?.correct_option_uuid)?.text ?? 'See option reference'} · <code>{review.item.private_answer.correct_option_uuid}</code></p>
            ) : <pre className="whitespace-pre-wrap break-words">{JSON.stringify(review.item.private_answer, null, 2)}</pre>}
            <p className="text-xs">This grading rule is visible only to an assigned reviewer for this draft.</p>
          </div>}
        </>}
        {review.diagnostic && <ul className="list-disc ps-5 text-sm">
          {review.diagnostic.manifest.required_concepts.map(uuid => <li key={uuid}>
            {concepts.find(c => c.uuid === uuid)?.title || uuid} · {t('adaptive.poolCoverage', '{{count}} reviewed items', {
              count: review.diagnostic!.manifest.pool.filter(item => item.concept_uuid === uuid).length,
            })}
          </li>)}
        </ul>}
        {catalog.can_review && <form key={review.row.uuid} onSubmit={event => {
          event.preventDefault()
          const values = new FormData(event.currentTarget)
          const publish = review.row.status === 'draft'
          save(`definitions/${review.kind}/${review.row.uuid}/${publish ? 'publish' : 'retire'}`, 'POST', publish ? {
            expected_version: review.row.version, expected_digest: review.digest, review_evidence: values.get('review_evidence'),
          } : { expected_version: review.row.version, reason: values.get('reason') }, publish
            ? t('adaptive.published', 'Definition published.') : t('adaptive.retired', 'Definition retired.'))
        }}>
          {review.row.status === 'draft' ? <Field name="review_evidence" label="Approved review evidence reference" />
            : <Choice name="reason" label="Reason for retirement" options={[
              { value: 'SUPERSEDED', label: t('adaptive.superseded', 'Superseded') },
              { value: 'CONTENT_WITHDRAWN', label: t('adaptive.withdrawn', 'Content withdrawn') },
              { value: 'REVIEW_REQUIRED', label: t('adaptive.reviewRequired', 'Review required') },
            ]} />}
          <Button className="mt-4 min-h-11" disabled={busy}>{review.row.status === 'draft' ? t('adaptive.publish', 'Publish definition') : t('adaptive.retire', 'Retire definition')}</Button>
        </form>}
      </div>}
    </section>}
    <StaffReportReview client={client} courseUuid={courseUuid} curricula={catalog.curricula} />
  </section>
}
