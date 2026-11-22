'use client'

import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { BookCopy, FileCheck2, FileSearch, Plus, RefreshCw, ShieldCheck, UploadCloud } from 'lucide-react'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { useOrg } from '@components/Contexts/OrgContext'
import { Button } from '@components/ui/button'
import { Input } from '@components/ui/input'
import { Textarea } from '@components/ui/textarea'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@components/ui/select'
import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'

type SourceVersion = {
  source_uuid: string
  version_uuid: string
  title: string
  version_number: number
  jurisdiction: string
  grade: string
  subject: string
  language: string
  syllabus_edition: string
  effective_cohort: string
  publisher: string
  source_type: string
  source_revision: string
  rights_status: string
  rights_reference: string
  original_filename: string
  content_sha256: string
  page_count: number
  ingestion_status: string
  ingestion_error_code: string | null
  ingestion_error_message: string | null
  ingestion_attempts: number
  review_status: string
  uploaded_by_user_id: number
  reviewed_by_user_id: number | null
  review_evidence: string | null
}
type Curriculum = { uuid: string; curriculum_key: string; revision: number; subject: string; level: string; language: string; jurisdiction: string | null; status: string }
type Concept = { uuid: string; code: string; title: string; objective: string }
type Mapping = { mapping_uuid: string; version_uuid: string; source_title: string; source_type: string; source_publisher: string; source_edition: string; curriculum_uuid: string; curriculum_key: string; curriculum_revision: number; curriculum_subject: string; curriculum_level: string; curriculum_jurisdiction: string | null; concept_uuid: string; concept_code: string; concept_title: string; page_start: number; page_end: number; evidence_kind: string; evidence_quote: string; status: string; created_by_user_id: number; reviewed_by_user_id: number | null; review_evidence: string | null }
type Page = { page: number; text: string }

const statusStyle: Record<string, string> = {
  approved: 'bg-teal-50 text-teal-800',
  pending_review: 'bg-amber-50 text-amber-800',
  rejected: 'bg-rose-50 text-rose-800',
  revoked: 'bg-slate-100 text-slate-600',
  superseded: 'bg-slate-100 text-slate-600',
  ready: 'bg-teal-50 text-teal-800',
  failed: 'bg-rose-50 text-rose-800',
}

const emptyMetadata = {
  title: '', jurisdiction: 'Sri Lanka', grade: '', subject: '', language: 'en',
  syllabus_edition: '', effective_cohort: '', publisher: '', source_type: 'syllabus',
  source_revision: '1', rights_status: 'unverified', rights_reference: '', source_uuid: '',
}

export default function SyllabusLibraryManager() {
  const session = useLHSession() as any
  const org = useOrg() as any
  const token = session?.data?.tokens?.access_token as string | undefined
  const orgUuid = org?.org_uuid as string | undefined
  const currentUserId = Number(session?.data?.user?.id)
  const base = useMemo(() => orgUuid ? `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}` : '', [orgUuid])
  const [versions, setVersions] = useState<SourceVersion[]>([])
  const [curricula, setCurricula] = useState<Curriculum[]>([])
  const [conceptsState, setConceptsState] = useState<{ curriculumUuid: string; values: Concept[] }>({ curriculumUuid: '', values: [] })
  const [mappings, setMappings] = useState<Mapping[]>([])
  const [metadata, setMetadata] = useState(emptyMetadata)
  const [curriculumUuid, setCurriculumUuid] = useState('')
  const [conceptUuid, setConceptUuid] = useState('')
  const [versionUuid, setVersionUuid] = useState('')
  const [evidenceKind, setEvidenceKind] = useState('official')
  const [pageStart, setPageStart] = useState('1')
  const [pageEnd, setPageEnd] = useState('1')
  const [evidenceQuote, setEvidenceQuote] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('Loading source library…')
  const concepts = conceptsState.curriculumUuid === curriculumUuid ? conceptsState.values : []

  useEffect(() => {
    if (!base || !token) return
    const controller = new AbortController()
    Promise.all([
      secureFetch(`${base}/syllabus-sources`, { ...RequestBodyWithAuthHeader('GET', null, null, token), cache: 'no-store', redirect: 'error', signal: controller.signal }),
      secureFetch(`${base}/explore/curricula`, { ...RequestBodyWithAuthHeader('GET', null, null, token), cache: 'no-store', redirect: 'error', signal: controller.signal }),
      secureFetch(`${base}/syllabus-mappings`, { ...RequestBodyWithAuthHeader('GET', null, null, token), cache: 'no-store', redirect: 'error', signal: controller.signal }),
    ]).then(async ([sourceResponse, curriculumResponse, mappingResponse]) => {
      if (!sourceResponse.ok || !curriculumResponse.ok || !mappingResponse.ok) throw new Error('This account cannot access the educator source library.')
      const [sourceBody, curriculumBody, mappingBody] = await Promise.all([sourceResponse.json(), curriculumResponse.json(), mappingResponse.json()])
      if (controller.signal.aborted) return
      setVersions(sourceBody.sources || [])
      setCurricula(curriculumBody || [])
      setMappings(mappingBody || [])
      setMessage('')
    }).catch((error) => {
      if (!controller.signal.aborted) setMessage(error instanceof Error ? error.message : 'The source library is unavailable.')
    })
    return () => controller.abort()
  }, [base, token, refresh])

  useEffect(() => {
    if (!base || !token || !curriculumUuid) return
    const controller = new AbortController()
    secureFetch(`${base}/syllabus-curricula/${encodeURIComponent(curriculumUuid)}/concepts`, {
      ...RequestBodyWithAuthHeader('GET', null, null, token), cache: 'no-store', redirect: 'error', signal: controller.signal,
    }).then(async (response) => {
      if (!response.ok) throw new Error('Published concepts could not be loaded.')
      const values = await response.json()
      if (!controller.signal.aborted) {
        setConceptsState({ curriculumUuid, values })
        setConceptUuid(values[0]?.uuid || '')
      }
    }).catch(() => { if (!controller.signal.aborted) setConceptsState({ curriculumUuid, values: [] }) })
    return () => controller.abort()
  }, [base, token, curriculumUuid])

  function update<K extends keyof typeof emptyMetadata>(key: K, value: string) {
    setMetadata((current) => ({ ...current, [key]: value }))
  }

  async function callJson(path: string, method: string, payload?: unknown) {
    if (!token) throw new Error('Sign in to continue.')
    const response = await secureFetch(`${base}${path}`, {
      ...RequestBodyWithAuthHeader(method, payload ?? null, null, token),
      cache: 'no-store', redirect: 'error',
    })
    const body = await response.json().catch(() => null)
    if (!response.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : 'The requested change could not be saved.')
    return body
  }

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!token || busy) return
    const form = event.currentTarget
    const file = (new FormData(form).get('file')) as File | null
    if (!file) return
    setBusy(true)
    setMessage('Validating PDF and building its page index…')
    try {
      const data = new FormData()
      data.append('file', file)
      data.append('metadata', JSON.stringify({
        ...metadata,
        source_uuid: metadata.source_uuid || null,
      }))
      const response = await secureFetch(`${base}/syllabus-sources`, {
        method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: data,
        cache: 'no-store', redirect: 'error',
      })
      const body = await response.json().catch(() => null)
      if (!response.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : 'The PDF could not be uploaded.')
      form.reset()
      setMessage(body.duplicate
        ? `This PDF is already in the library as version ${body.version_number}; no duplicate was created.`
        : body.ingestion_status === 'ready'
          ? 'PDF saved and indexed. It remains unavailable to students until independent review and approval.'
          : body.ingestion_error_message || 'PDF saved with an indexing failure. Review the status and retry if appropriate.')
      // Default the next upload to a new source. Reusing a logical source for
      // another version must be an explicit selection to avoid accidental grouping.
      setMetadata((current) => ({ ...current, source_uuid: '' }))
      setRefresh((value) => value + 1)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'The PDF could not be uploaded.')
    } finally {
      setBusy(false)
    }
  }

  async function reviewSource(version: SourceVersion, decision: 'approve' | 'reject', evidence: string, rightsReference?: string) {
    setBusy(true)
    try {
      await callJson(`/syllabus-sources/${encodeURIComponent(version.version_uuid)}/review`, 'POST', { decision, evidence, rights_reference: rightsReference })
      setMessage(decision === 'approve' ? 'Source approved. Any previously approved version is now superseded.' : 'Source version rejected.')
      setRefresh((value) => value + 1)
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Review could not be saved.') }
    finally { setBusy(false) }
  }

  async function revokeSource(version: SourceVersion) {
    const evidence = window.prompt('Record why this source version is being revoked (at least 8 characters).')
    if (!evidence?.trim()) return
    setBusy(true)
    try {
      await callJson(`/syllabus-sources/${encodeURIComponent(version.version_uuid)}/revoke`, 'POST', { evidence })
      setMessage('Source revoked and removed from student retrieval.')
      setRefresh((value) => value + 1)
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Source could not be revoked.') }
    finally { setBusy(false) }
  }

  async function retryIndex(version: SourceVersion) {
    setBusy(true)
    setMessage('Retrying PDF index…')
    try {
      const body = await callJson(`/syllabus-sources/${encodeURIComponent(version.version_uuid)}/retry-index`, 'POST')
      setMessage(body.ingestion_status === 'ready' ? 'PDF indexing completed.' : body.ingestion_error_message || 'Indexing is still unavailable.')
      setRefresh((value) => value + 1)
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Index retry failed.') }
    finally { setBusy(false) }
  }

  async function submitMapping(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    try {
      await callJson('/syllabus-mappings', 'POST', {
        version_uuid: versionUuid,
        curriculum_uuid: curriculumUuid,
        concept_uuid: conceptUuid,
        page_start: Number(pageStart),
        page_end: Number(pageEnd),
        evidence_kind: evidenceKind,
        evidence_quote: evidenceQuote.trim(),
      })
      setEvidenceQuote('')
      setMessage('Curriculum mapping saved for independent educator review.')
      setRefresh((value) => value + 1)
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Mapping could not be saved.') }
    finally { setBusy(false) }
  }

  async function reviewMapping(mapping: Mapping, decision: 'approve' | 'reject') {
    const evidence = window.prompt(`Record the review basis for ${decision} (at least 8 characters).`)
    if (!evidence?.trim()) return
    setBusy(true)
    try {
      await callJson(`/syllabus-mappings/${encodeURIComponent(mapping.mapping_uuid)}/review`, 'POST', { decision, evidence })
      setMessage(`Curriculum mapping ${decision}d.`)
      setRefresh((value) => value + 1)
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Mapping review could not be saved.') }
    finally { setBusy(false) }
  }

  if (!orgUuid || !token) return <main className="mx-auto max-w-6xl p-8"><p role="status">Sign in with an educator account to manage syllabus sources.</p></main>

  const distinctSources = [...versions.reduce((sources, version) => {
    if (!sources.has(version.source_uuid)) sources.set(version.source_uuid, version)
    return sources
  }, new Map<string, SourceVersion>()).values()]
  const approvedVersions = versions.filter((item) => item.review_status === 'approved' && item.ingestion_status === 'ready')

  return (
    <main className="mx-auto w-full max-w-6xl space-y-6 px-4 py-8 sm:px-6 lg:px-8">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="mb-2 inline-flex items-center gap-2 rounded-full bg-teal-50 px-3 py-1 text-xs font-semibold text-teal-900"><BookCopy size={15} aria-hidden /> Educator workspace</p>
          <h1 className="text-3xl font-bold tracking-tight text-slate-900">Syllabus source library</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">Keep official syllabuses and supporting PDFs in one organization library. Uploads stay private until an independent educator confirms rights and approves the indexed version.</p>
        </div>
        <Button variant="outline" onClick={() => setRefresh((value) => value + 1)} disabled={busy}><RefreshCw size={15} aria-hidden /> Refresh library</Button>
      </header>

      <section className="grid gap-6 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)]">
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6" aria-labelledby="source-upload-title">
          <div className="mb-5 flex items-start gap-3">
            <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-blue-50 text-blue-800"><UploadCloud size={20} aria-hidden /></span>
          <div><h2 id="source-upload-title" className="text-lg font-semibold text-slate-900">Add a PDF source</h2><p className="mt-1 text-sm text-slate-500">Searchable PDFs up to 4 MB and 200 pages. Image-only scans are held with OCR guidance. Uploaded rights values are claims until an independent reviewer verifies permission.</p></div>
          </div>
          <form onSubmit={upload} className="space-y-4">
            <label className="grid gap-1.5 text-sm font-medium text-slate-700">Source title
              <Input required maxLength={200} value={metadata.title} onChange={(event) => update('title', event.target.value)} placeholder="e.g. Grade 12 Chemistry Resource Book" />
            </label>
            <label className="grid gap-1.5 text-sm font-medium text-slate-700">Add as a new source or a new version
              <Select value={metadata.source_uuid || 'new'} onValueChange={(value) => {
                const source = distinctSources.find((item) => item.source_uuid === value)
                setMetadata((current) => source ? {
                  ...current, source_uuid: source.source_uuid, title: source.title, jurisdiction: source.jurisdiction,
                  grade: source.grade, subject: source.subject, language: source.language,
                  syllabus_edition: source.syllabus_edition, effective_cohort: source.effective_cohort,
                  publisher: source.publisher, source_type: source.source_type,
                  source_revision: String(source.version_number + 1), rights_status: 'unverified', rights_reference: '',
                } : { ...current, source_uuid: '' })
              }}>
                <SelectTrigger aria-label="Source version"><SelectValue /></SelectTrigger><SelectContent>
                  <SelectItem value="new">Create a new reusable source</SelectItem>
                  {distinctSources.map((source) => <SelectItem key={source.source_uuid} value={source.source_uuid}>New version · {source.title}</SelectItem>)}
                </SelectContent>
              </Select>
            </label>
            <div className="grid gap-3 sm:grid-cols-2">
              <TextField label="Jurisdiction / country" value={metadata.jurisdiction} onChange={(v) => update('jurisdiction', v)} maxLength={120} required />
              <TextField label="Grade / level" value={metadata.grade} onChange={(v) => update('grade', v)} maxLength={32} required placeholder="Grade 12" />
              <TextField label="Subject" value={metadata.subject} onChange={(v) => update('subject', v)} maxLength={120} required />
              <TextField label="Language" value={metadata.language} onChange={(v) => update('language', v)} maxLength={32} required placeholder="en" />
              <TextField label="Syllabus edition" value={metadata.syllabus_edition} onChange={(v) => update('syllabus_edition', v)} maxLength={64} required placeholder="2017" />
              <TextField label="Effective cohort" value={metadata.effective_cohort} onChange={(v) => update('effective_cohort', v)} maxLength={64} required placeholder="2026" />
              <TextField label="Publisher / authority" value={metadata.publisher} onChange={(v) => update('publisher', v)} maxLength={200} required placeholder="National Institute of Education" />
              <TextField label="Document revision" value={metadata.source_revision} onChange={(v) => update('source_revision', v)} maxLength={120} required />
            </div>
            <label className="grid gap-1.5 text-sm font-medium text-slate-700">Source type
              <Select value={metadata.source_type} onValueChange={(value) => update('source_type', value)}><SelectTrigger aria-label="Source type"><SelectValue /></SelectTrigger><SelectContent>
                <SelectItem value="syllabus">Official syllabus</SelectItem><SelectItem value="textbook">Textbook</SelectItem><SelectItem value="resource_book">Resource book</SelectItem><SelectItem value="supplementary">Supplementary material</SelectItem><SelectItem value="teacher_guide">Teacher guide</SelectItem>
              </SelectContent></Select>
            </label>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="grid gap-1.5 text-sm font-medium text-slate-700">Usage permission status
                <Select value={metadata.rights_status} onValueChange={(value) => update('rights_status', value)}><SelectTrigger aria-label="Usage permission status"><SelectValue /></SelectTrigger><SelectContent>
                  <SelectItem value="unverified">Unverified</SelectItem><SelectItem value="verified_permitted">Verified permitted</SelectItem><SelectItem value="restricted">Restricted</SelectItem>
                </SelectContent></Select>
              </label>
              <TextField label="Rights evidence reference" value={metadata.rights_reference} onChange={(v) => update('rights_reference', v)} maxLength={500} placeholder="License, permission record, or policy reference" />
            </div>
            <label className="grid gap-1.5 text-sm font-medium text-slate-700">PDF file
              <Input type="file" name="file" accept=".pdf,application/pdf" required disabled={busy} className="h-auto py-2" />
            </label>
            <Button type="submit" disabled={busy} className="bg-blue-700 hover:bg-blue-800"><UploadCloud size={16} aria-hidden /> Upload and index PDF</Button>
          </form>
        </section>

        <section className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6" aria-labelledby="source-list-title">
          <div className="flex items-start gap-3">
            <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-teal-50 text-teal-800"><FileSearch size={20} aria-hidden /></span>
            <div><h2 id="source-list-title" className="text-lg font-semibold text-slate-900">Source versions</h2><p className="mt-1 text-sm text-slate-500">{versions.length} version{versions.length === 1 ? '' : 's'} · students can retrieve only approved and permitted versions</p></div>
          </div>
          <p role="status" className="min-h-5 text-sm text-slate-600">{message}</p>
          {versions.length === 0 ? <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-7 text-center text-sm text-slate-500">No PDF sources uploaded yet.</div> : (
            <ul className="space-y-3">
              {versions.map((version) => <SourceVersionCard key={version.version_uuid} version={version} currentUserId={currentUserId} busy={busy}
                onReview={reviewSource} onRetry={retryIndex} onRevoke={revokeSource} base={base} token={token} />)}
            </ul>
          )}
        </section>
      </section>

      <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6" aria-labelledby="mapping-title">
        <div className="mb-5 flex items-start gap-3">
          <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-indigo-50 text-indigo-800"><FileCheck2 size={20} aria-hidden /></span>
          <div><h2 id="mapping-title" className="text-lg font-semibold text-slate-900">Map page evidence to curriculum concepts</h2><p className="mt-1 text-sm text-slate-500">Official inclusion needs an official syllabus PDF and a separate reviewer. Resource-book mentions can only be supplementary evidence.</p></div>
        </div>
        <form onSubmit={submitMapping} className="grid gap-3 md:grid-cols-2 xl:grid-cols-6">
          <label className="grid gap-1 text-xs font-semibold text-slate-700 xl:col-span-2">Approved source
            <Select value={versionUuid} onValueChange={setVersionUuid}><SelectTrigger aria-label="Approved source"><SelectValue placeholder="Select source" /></SelectTrigger><SelectContent>
              {approvedVersions.map((version) => <SelectItem key={version.version_uuid} value={version.version_uuid}>{version.title} · v{version.version_number}</SelectItem>)}
            </SelectContent></Select>
          </label>
          <label className="grid gap-1 text-xs font-semibold text-slate-700 xl:col-span-2">Published curriculum
            <Select value={curriculumUuid} onValueChange={setCurriculumUuid}><SelectTrigger aria-label="Published curriculum"><SelectValue placeholder="Select syllabus" /></SelectTrigger><SelectContent>
              {curricula.filter((item) => item.status === 'published').map((item) => <SelectItem key={item.uuid} value={item.uuid}>{item.subject} · {item.level} · v{item.revision}</SelectItem>)}
            </SelectContent></Select>
          </label>
          <label className="grid gap-1 text-xs font-semibold text-slate-700 xl:col-span-2">Concept
            <Select value={conceptUuid} onValueChange={setConceptUuid} disabled={!concepts.length}><SelectTrigger aria-label="Curriculum concept"><SelectValue placeholder="Select concept" /></SelectTrigger><SelectContent>
              {concepts.map((item) => <SelectItem key={item.uuid} value={item.uuid}>{item.code} · {item.title}</SelectItem>)}
            </SelectContent></Select>
          </label>
          <label className="grid gap-1 text-xs font-semibold text-slate-700">First PDF page<Input type="number" min={1} max={200} required value={pageStart} onChange={(event) => setPageStart(event.target.value)} /></label>
          <label className="grid gap-1 text-xs font-semibold text-slate-700">Last PDF page<Input type="number" min={1} max={200} required value={pageEnd} onChange={(event) => setPageEnd(event.target.value)} /></label>
          <label className="grid gap-1 text-xs font-semibold text-slate-700">Evidence kind
            <Select value={evidenceKind} onValueChange={setEvidenceKind}><SelectTrigger aria-label="Evidence kind"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="official">Official syllabus evidence</SelectItem><SelectItem value="supplementary">Supplementary evidence</SelectItem></SelectContent></Select>
          </label>
          <label className="grid gap-1 text-xs font-semibold text-slate-700 md:col-span-2 xl:col-span-3">Verbatim evidence quote
            <Textarea value={evidenceQuote} onChange={(event) => setEvidenceQuote(event.target.value)} minLength={15} maxLength={1000} required placeholder="Paste the exact passage from the selected PDF pages." className="min-h-16" />
          </label>
          <div className="flex items-end"><Button type="submit" disabled={busy || !versionUuid || !curriculumUuid || !conceptUuid || !evidenceQuote.trim()}><Plus size={15} aria-hidden /> Submit mapping for review</Button></div>
        </form>
        <div className="mt-6 border-t border-slate-200 pt-5">
          <h3 className="mb-3 text-sm font-semibold text-slate-900">Mapping review queue</h3>
          {mappings.length === 0 ? <p className="text-sm text-slate-500">No page-to-concept mappings submitted yet.</p> : <ul className="space-y-2">
            {mappings.map((mapping) => <li key={mapping.mapping_uuid} className="flex flex-col gap-3 rounded-lg border border-slate-200 p-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0"><p className="text-sm font-semibold text-slate-800">{mapping.concept_code} · {mapping.concept_title}</p><p className="mt-1 text-xs text-slate-500">Target: {mapping.curriculum_subject} · {mapping.curriculum_level} · {mapping.curriculum_key} v{mapping.curriculum_revision}{mapping.curriculum_jurisdiction ? ` · ${mapping.curriculum_jurisdiction}` : ''}</p><p className="mt-1 text-xs text-slate-500">Source: {mapping.source_type.replaceAll('_', ' ')} · {mapping.source_title} · {mapping.source_publisher} · edition {mapping.source_edition}</p><p className="mt-1 line-clamp-2 text-xs text-slate-500">{mapping.evidence_kind} · PDF pages {mapping.page_start}–{mapping.page_end} · “{mapping.evidence_quote}”</p><a href={`#source-version-${encodeURIComponent(mapping.version_uuid)}`} className="mt-2 inline-block text-xs font-semibold text-blue-700 underline underline-offset-2">Inspect mapped source pages</a></div>
              <div className="flex shrink-0 items-center gap-2"><StatusPill value={mapping.status} />
                {mapping.status === 'pending_review' && mapping.created_by_user_id !== currentUserId && <><Button size="sm" variant="outline" disabled={busy} onClick={() => void reviewMapping(mapping, 'approve')}>Approve</Button><Button size="sm" variant="ghost" disabled={busy} onClick={() => void reviewMapping(mapping, 'reject')}>Reject</Button></>}
              </div>
            </li>)}
          </ul>}
        </div>
      </section>

      <aside className="flex gap-3 rounded-xl border border-blue-100 bg-blue-50/70 p-4 text-sm leading-6 text-blue-950">
        <ShieldCheck size={19} className="mt-0.5 shrink-0 text-blue-800" aria-hidden />
        <p>Uploaded files remain in private storage. A successful index does not grant reuse rights; student retrieval starts only after a different educator approves the source under the active content-rights policy.</p>
      </aside>
    </main>
  )
}

function TextField({ label, value, onChange, maxLength, required = false, placeholder }: { label: string; value: string; onChange: (_value: string) => void; maxLength: number; required?: boolean; placeholder?: string }) {
  return <label className="grid gap-1.5 text-sm font-medium text-slate-700">{label}<Input value={value} onChange={(event) => onChange(event.target.value)} maxLength={maxLength} required={required} placeholder={placeholder} /></label>
}

function StatusPill({ value }: { value: string }) {
  return <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ${statusStyle[value] || 'bg-slate-100 text-slate-700'}`}>{value.replaceAll('_', ' ')}</span>
}

function SourceVersionCard({ version, currentUserId, busy, onReview, onRetry, onRevoke, base, token }: {
  version: SourceVersion; currentUserId: number; busy: boolean;
  onReview: (_version: SourceVersion, _decision: 'approve' | 'reject', _evidence: string, _rightsReference?: string) => void;
  onRetry: (_version: SourceVersion) => void; onRevoke: (_version: SourceVersion) => void;
  base: string; token: string;
}) {
  const [pages, setPages] = useState<Page[] | null>(null)
  const [pageState, setPageState] = useState('')
  const [evidence, setEvidence] = useState('')
  const [rightsReference, setRightsReference] = useState('')
  const canReview = version.uploaded_by_user_id !== currentUserId
  const canApprove = version.ingestion_status === 'ready' && rightsReference.trim().length > 0

  async function loadPages() {
    if (pages) { setPages(null); setPageState(''); return }
    setPageState('Loading extracted pages…')
    try {
      const response = await secureFetch(`${base}/syllabus-sources/${encodeURIComponent(version.version_uuid)}/pages`, {
        ...RequestBodyWithAuthHeader('GET', null, null, token), cache: 'no-store', redirect: 'error',
      })
      if (!response.ok) throw new Error('Extracted pages are unavailable.')
      const body = await response.json()
      setPages(body.pages || [])
      setPageState('')
    } catch (error) { setPageState(error instanceof Error ? error.message : 'Pages unavailable.') }
  }

  return <li id={`source-version-${version.version_uuid}`} className="scroll-mt-24 rounded-xl border border-slate-200 p-4">
    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold text-slate-900">{version.title}</h3><span className="text-xs text-slate-500">v{version.version_number}</span><StatusPill value={version.review_status} /><StatusPill value={version.ingestion_status} /></div>
        <p className="mt-2 text-xs leading-5 text-slate-500">{version.source_type.replaceAll('_', ' ')} · {version.publisher} · {version.jurisdiction} · Grade {version.grade} {version.subject} · edition {version.syllabus_edition} · cohort {version.effective_cohort} · {version.language} · {version.page_count} pages</p>
        <p className="mt-1 break-all text-[11px] text-slate-400">SHA-256 {version.content_sha256}</p>
        <p className="mt-1 text-xs text-slate-600">Rights: {version.rights_status}{version.rights_reference ? ` · ${version.rights_reference}` : ''}</p>
        {version.ingestion_error_message && <p className="mt-2 rounded-md bg-rose-50 px-2.5 py-2 text-xs leading-5 text-rose-800">{version.ingestion_error_message}</p>}
        {version.review_evidence && <p className="mt-2 text-xs text-slate-500">Review note: {version.review_evidence}</p>}
      </div>
      <div className="flex shrink-0 flex-wrap gap-2">
        <Button type="button" variant="outline" size="sm" onClick={() => void loadPages()}><FileSearch size={14} aria-hidden /> {pages ? 'Hide pages' : 'Inspect pages'}</Button>
        {version.ingestion_status === 'failed' && version.ingestion_error_code === 'embedding_failed' && version.ingestion_attempts < 5 && <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => onRetry(version)}><RefreshCw size={14} aria-hidden /> Retry index</Button>}
      </div>
    </div>
    {pageState && <p role="status" className="mt-3 text-xs text-slate-500">{pageState}</p>}
    {pages && <div className="mt-3 max-h-64 space-y-3 overflow-auto rounded-lg bg-slate-50 p-3">{pages.length === 0 ? <p className="text-xs text-slate-500">No text extracted. Use OCR and upload a searchable PDF.</p> : pages.map((page) => <div key={page.page} className="border-b border-slate-200 pb-2 last:border-0"><p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">PDF page {page.page}</p><p className="whitespace-pre-wrap text-xs leading-5 text-slate-700">{page.text || 'No readable text on this page.'}</p></div>)}</div>}
    {version.review_status === 'pending_review' && <div className="mt-4 rounded-lg bg-amber-50/70 p-3">
      {canReview ? <>
        <label className="grid gap-1 text-xs font-medium text-slate-700">Independent review evidence
          <Input value={evidence} onChange={(event) => setEvidence(event.target.value)} maxLength={500} minLength={8} placeholder="Record the content and source review basis" />
        </label>
        <label className="mt-2 grid gap-1 text-xs font-medium text-slate-700">Independently verified license / permission reference
          <Input value={rightsReference} onChange={(event) => setRightsReference(event.target.value)} maxLength={500} placeholder="Publisher permission record, license, or rights decision reference" />
        </label>
        <div className="mt-2 flex flex-wrap gap-2"><Button size="sm" disabled={busy || !canApprove || evidence.trim().length < 8} onClick={() => onReview(version, 'approve', evidence, rightsReference.trim())}>Approve for student retrieval</Button><Button size="sm" variant="outline" disabled={busy || evidence.trim().length < 8} onClick={() => onReview(version, 'reject', evidence)}>Reject source</Button></div>
        {!canApprove && <p className="mt-2 text-xs text-amber-900">Student access stays blocked until indexing is ready and the independent reviewer records permitted-use evidence.</p>}
      </> : <p className="text-xs leading-5 text-amber-950">A different educator must review this upload. Student access remains blocked.</p>}
    </div>}
    {version.review_status !== 'revoked' && version.review_status !== 'superseded' && <div className="mt-3 flex justify-end"><Button variant="ghost" size="sm" disabled={busy} onClick={() => onRevoke(version)}>Revoke this version</Button></div>}
  </li>
}
