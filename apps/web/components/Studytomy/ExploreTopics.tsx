'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { BookOpenText, BookMarked, Compass, ExternalLink, LoaderCircle, Search, ShieldCheck } from 'lucide-react'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { useOrg } from '@components/Contexts/OrgContext'
import { Button } from '@components/ui/button'
import { Input } from '@components/ui/input'
import { Textarea } from '@components/ui/textarea'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@components/ui/select'
import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'

type Curriculum = {
  uuid: string
  curriculum_key: string
  revision: number
  subject: string
  level: string
  language: string
  jurisdiction: string | null
  status: string
}

type Citation = {
  reference: number
  source_uuid: string
  version_uuid: string
  title: string
  publisher: string
  source_type: string
  jurisdiction: string
  grade: string
  subject: string
  syllabus_edition: string
  page: number
  excerpt: string
  pdf_url: string
  coverage: string
}

type ExploreResult = {
  status: 'grounded_answer' | 'sources_found' | 'insufficient_evidence' | 'ai_unavailable'
  explanation: string | null
  answer_blocks: { text: string; citations: number[] }[]
  sources: Citation[]
  coverage: string
  selected_curriculum_uuid: string | null
  selected_curriculum_label: string | null
  ai_unavailable_reason: string | null
}

const coverageLabels: Record<string, string> = {
  confirmed_selected_syllabus: 'Confirmed in selected syllabus',
  other_approved_syllabus: 'Mapped to another approved syllabus',
  supplementary: 'Supplementary evidence',
  unverified: 'Syllabus coverage unverified',
}

const sourceTypes: Record<string, string> = {
  syllabus: 'Official syllabus',
  textbook: 'Textbook',
  resource_book: 'Resource book',
  supplementary: 'Supplementary resource',
  teacher_guide: 'Teacher guide',
}

export default function ExploreTopics() {
  const session = useLHSession() as any
  const org = useOrg() as any
  const token = session?.data?.tokens?.access_token as string | undefined
  const orgUuid = org?.org_uuid as string | undefined
  const [curricula, setCurricula] = useState<Curriculum[]>([])
  const [curriculaState, setCurriculaState] = useState('Loading approved syllabuses…')
  const [query, setQuery] = useState('')
  const [selectedCurriculum, setSelectedCurriculum] = useState('')
  const [grade, setGrade] = useState('')
  const [subject, setSubject] = useState('')
  const [jurisdiction, setJurisdiction] = useState('')
  const [result, setResult] = useState<ExploreResult | null>(null)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [followUp, setFollowUp] = useState(false)
  const pdfUrlsRef = useRef<string[]>([])
  const base = useMemo(() => orgUuid ? `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}` : '', [orgUuid])

  useEffect(() => {
    if (!base || !token) return
    const controller = new AbortController()
    secureFetch(`${base}/explore/curricula`, {
      ...RequestBodyWithAuthHeader('GET', null, null, token),
      cache: 'no-store',
      redirect: 'error',
      signal: controller.signal,
    }).then(async (response) => {
      if (!response.ok) throw new Error('Approved syllabuses could not be loaded.')
      const items = await response.json() as Curriculum[]
      if (controller.signal.aborted) return
      setCurricula(items)
      setCurriculaState(items.length ? '' : 'No published curriculum is available yet. You can still search approved PDFs.')
    }).catch(() => {
      if (!controller.signal.aborted) setCurriculaState('Published syllabuses are temporarily unavailable. You can still search approved PDFs.')
    })
    return () => controller.abort()
  }, [base, token])

  useEffect(() => () => pdfUrlsRef.current.forEach((url) => URL.revokeObjectURL(url)), [])

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!base || !token || busy) return
    setBusy(true)
    setMessage('Searching educator-approved PDFs…')
    setResult(null)
    try {
      const selected = curricula.find((item) => item.uuid === selectedCurriculum)
      const payload = {
        query: query.trim(),
        selected_curriculum_uuid: !selectedCurriculum || selectedCurriculum === 'any' ? null : selectedCurriculum,
        grade: grade.trim() || selected?.level || null,
        subject: subject.trim() || selected?.subject || null,
        jurisdiction: jurisdiction.trim() || selected?.jurisdiction || null,
        source_version_uuids: followUp && result ? result.sources.map((source) => source.version_uuid) : [],
      }
      const response = await secureFetch(`${base}/explore/topics`, {
        ...RequestBodyWithAuthHeader('POST', payload, null, token),
        cache: 'no-store',
        redirect: 'error',
      })
      const body = await response.json().catch(() => null)
      if (!response.ok) throw new Error(typeof body?.detail === 'string' ? body.detail : 'Topic search could not be completed.')
      setResult(body as ExploreResult)
      setFollowUp(false)
      setMessage('')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Topic search could not be completed. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  async function openPdf(citation: Citation) {
    if (!token) return
    const popup = window.open('', '_blank')
    if (popup) popup.opener = null
    try {
      const path = `${base}/syllabus-sources/${encodeURIComponent(citation.version_uuid)}/pdf`
      const response = await secureFetch(path, {
        ...RequestBodyWithAuthHeader('GET', null, null, token),
        cache: 'no-store',
        redirect: 'error',
      })
      if (!response.ok) throw new Error('This approved PDF is no longer available.')
      const url = URL.createObjectURL(await response.blob()) + `#page=${citation.page}`
      const objectUrl = url.split('#')[0]
      pdfUrlsRef.current = [...pdfUrlsRef.current, objectUrl]
      if (popup) popup.location.href = url
      else window.location.assign(url)
    } catch (error) {
      popup?.close()
      setMessage(error instanceof Error ? error.message : 'The PDF could not be opened.')
    }
  }

  const selectedLabel = curricula.find((item) => item.uuid === selectedCurriculum)

  if (!orgUuid || !token) {
    return <main className="mx-auto max-w-5xl px-4 py-10"><p role="status" className="rounded-xl border border-slate-200 bg-white p-5 text-sm text-slate-600">Sign in to explore approved syllabus materials.</p></main>
  }

  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
      <header className="mb-7 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="mb-2 inline-flex items-center gap-2 rounded-full bg-blue-50 px-3 py-1 text-xs font-semibold text-blue-800">
            <Compass size={15} aria-hidden /> Explore Topics
          </p>
          <h1 className="text-3xl font-bold tracking-tight text-slate-900">Learn from approved sources</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">Search across educator-reviewed PDFs. Every result shows its source edition and original page so you can check the evidence yourself.</p>
        </div>
        <div className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600">
          <ShieldCheck size={16} className="text-teal-700" aria-hidden /> Only approved, permitted PDFs appear
        </div>
      </header>

      <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-6" aria-labelledby="topic-search-title">
        <h2 id="topic-search-title" className="text-lg font-semibold text-slate-900">What would you like to understand?</h2>
        <p className="mt-1 text-sm text-slate-500">Choose a syllabus to check its reviewed coverage. Leave it unselected to search all available approved materials.</p>
        {curriculaState && <p role="status" className="mt-3 text-sm text-slate-500">{curriculaState}</p>}
        <form onSubmit={search} className="mt-5 space-y-4">
          <label htmlFor="topic-question" className="sr-only">Topic or question</label>
          <Textarea
            id="topic-question"
            value={query}
            onChange={(event) => { setQuery(event.target.value); setFollowUp(false) }}
            maxLength={500}
            minLength={2}
            required
            placeholder="For example: How do carbon-12 and carbon-14 differ?"
            className="min-h-24 resize-y rounded-xl border-slate-300 text-base"
          />
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <label className="grid gap-1.5 text-xs font-semibold text-slate-700">
              Syllabus context
              <Select value={selectedCurriculum} onValueChange={(value) => { setSelectedCurriculum(value); setFollowUp(false) }}>
                <SelectTrigger aria-label="Syllabus context"><SelectValue placeholder="Any approved syllabus" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="any">Any approved syllabus</SelectItem>
                  {curricula.map((item) => <SelectItem key={item.uuid} value={item.uuid}>{item.subject} · {item.level} · v{item.revision}</SelectItem>)}
                </SelectContent>
              </Select>
            </label>
            <label className="grid gap-1.5 text-xs font-semibold text-slate-700">
              Grade
              <Input value={grade} onChange={(event) => setGrade(event.target.value)} placeholder={selectedLabel?.level || 'Any grade'} maxLength={32} />
            </label>
            <label className="grid gap-1.5 text-xs font-semibold text-slate-700">
              Subject
              <Input value={subject} onChange={(event) => setSubject(event.target.value)} placeholder={selectedLabel?.subject || 'Any subject'} maxLength={120} />
            </label>
            <label className="grid gap-1.5 text-xs font-semibold text-slate-700">
              Jurisdiction
              <Input value={jurisdiction} onChange={(event) => setJurisdiction(event.target.value)} placeholder={selectedLabel?.jurisdiction || 'Any jurisdiction'} maxLength={120} />
            </label>
          </div>
          <div className="flex flex-wrap items-center gap-3 pt-1">
            <Button type="submit" disabled={busy || query.trim().length < 2} className="min-w-36 bg-blue-700 hover:bg-blue-800">
              {busy ? <LoaderCircle size={16} className="animate-spin" aria-hidden /> : <Search size={16} aria-hidden />}
              {busy ? 'Searching…' : 'Explore topic'}
            </Button>
            {result?.sources?.length ? (
              <Button type="button" variant="outline" disabled={busy} onClick={() => setFollowUp(true)}>
                Ask a follow-up using these {result.sources.length} sources
              </Button>
            ) : null}
            {followUp && <span className="text-xs font-medium text-teal-800">Next search is limited to the sources above.</span>}
          </div>
        </form>
        {message && <p role="status" className="mt-4 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-700">{message}</p>}
      </section>

      {result && (
        <section className="mt-6 space-y-4" aria-live="polite" aria-label="Topic results">
          <div className="flex flex-col gap-3 rounded-2xl border border-slate-200 bg-white p-4 sm:flex-row sm:items-center sm:justify-between sm:p-5">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <span className={`rounded-full px-3 py-1 text-xs font-semibold ${result.coverage === 'confirmed_selected_syllabus' ? 'bg-teal-50 text-teal-800' : 'bg-amber-50 text-amber-800'}`}>
                  {coverageLabels[result.coverage] || coverageLabels.unverified}
                </span>
                {result.selected_curriculum_label && <span className="text-xs text-slate-500">Compared with {result.selected_curriculum_label}</span>}
              </div>
              {result.explanation && <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-700">{result.explanation}</p>}
              {result.ai_unavailable_reason && <p className="mt-2 text-xs text-slate-500">{result.ai_unavailable_reason}</p>}
            </div>
            <div className="shrink-0 text-xs text-slate-500">{result.sources.length} approved passage{result.sources.length === 1 ? '' : 's'}</div>
          </div>

          {result.answer_blocks.length > 0 && (
            <article className="rounded-2xl border border-blue-100 bg-blue-50/70 p-5 sm:p-6">
              <h2 className="flex items-center gap-2 text-base font-semibold text-slate-900"><BookOpenText size={18} className="text-blue-700" aria-hidden /> Source-grounded explanation</h2>
              <div className="mt-3 space-y-3 text-sm leading-7 text-slate-800">
                {result.answer_blocks.map((block, index) => (
                  <p key={`${index}-${block.text}`}>{block.text}{' '}{block.citations.map((reference) => <a key={reference} href={`#source-${reference}`} className="ms-1 rounded bg-white px-1.5 py-0.5 text-xs font-semibold text-blue-800 underline underline-offset-2">[{reference}]</a>)}</p>
                ))}
              </div>
            </article>
          )}

          {result.sources.length > 0 ? (
            <div className="space-y-3">
              <h2 className="px-1 text-base font-semibold text-slate-900">Source passages</h2>
              {result.sources.map((citation) => (
                <article id={`source-${citation.reference}`} key={`${citation.version_uuid}:${citation.page}`} className="scroll-mt-20 rounded-xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="grid size-7 place-items-center rounded-full bg-blue-50 text-xs font-bold text-blue-800">{citation.reference}</span>
                        <h3 className="font-semibold text-slate-900">{citation.title}</h3>
                      </div>
                      <p className="mt-2 text-xs text-slate-500">{sourceTypes[citation.source_type] || 'Approved PDF'} · {citation.publisher || citation.jurisdiction} {citation.syllabus_edition} · {citation.subject}, Grade {citation.grade} · page {citation.page}</p>
                    </div>
                    <Button type="button" variant="outline" size="sm" onClick={() => void openPdf(citation)} className="shrink-0">
                      <ExternalLink size={14} aria-hidden /> Open PDF · p. {citation.page}
                    </Button>
                  </div>
                  <blockquote className="mt-4 border-s-2 border-teal-600 ps-4 text-sm leading-6 text-slate-700">{citation.excerpt}</blockquote>
                  <p className="mt-3 text-xs font-medium text-slate-500">{coverageLabels[citation.coverage] || coverageLabels.unverified}</p>
                </article>
              ))}
            </div>
          ) : (
            <div className="rounded-xl border border-amber-200 bg-amber-50 p-5 text-sm leading-6 text-amber-950">
              <p className="font-semibold">No sufficient approved source found</p>
              <p className="mt-1">This search did not find approved PDF passages that support an answer. That does not establish whether the topic is in or outside the syllabus.</p>
            </div>
          )}
        </section>
      )}
      {!result && <p className="mt-5 flex items-center gap-2 px-1 text-xs text-slate-500"><BookMarked size={14} aria-hidden /> Searching does not enroll you in a course or change learning records.</p>}
    </main>
  )
}
