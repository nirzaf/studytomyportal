'use client'

import { type FormEvent, useEffect, useRef, useState } from 'react'
import { Button } from '@components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from '@components/ui/dialog'
import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'
import GroundedLessonDraftForm from './GroundedLessonDraftForm'
import GroundedQuizDraftForm, { type QuizDestination } from './GroundedQuizDraftForm'

type Citation = { source_uuid: string; label: string; page: number; quote: string }
type SourceState = { status: 'current' | 'stale' | 'none'; current_version: number; citations: Citation[] }
type DraftSource = {
  source_uuid: string
  label: string
  readable_pages: number[]
  pages_without_readable_text?: number[]
}
type DraftSourcePage = { sources: DraftSource[]; next_cursor: string | null }
type Props = { orgUuid: string; courseUuid: string; activityUuid: string; version: number; token: string; quiz?: QuizDestination }

export default function GroundedLessonSources({ orgUuid, courseUuid, activityUuid, version, token, quiz }: Props) {
  const [open, setOpen] = useState(false)
  const [citationRevision, setCitationRevision] = useState(0)
  const route = `studytomy/orgs/${encodeURIComponent(orgUuid)}/courses/${encodeURIComponent(courseUuid)}/lessons/${encodeURIComponent(activityUuid)}`
  const title = quiz ? 'Grounded quiz sources' : 'Lesson sources'

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild><Button size="sm" variant="outline">{title}</Button></DialogTrigger>
      <DialogContent className="max-h-[80vh] overflow-y-auto">
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription>{quiz ? 'Manage assignment PDFs and review citations for quiz drafts.' : 'Manage PDF sources for this lesson and check saved citations before review.'}</DialogDescription>
        {open && (
          <>
            <DraftMaterials key={`${route}:${version}`} route={route} token={token} version={version} quiz={quiz}
              onSourceRevoked={() => setCitationRevision((value) => value + 1)} />
            {!quiz && <section className="space-y-2 border-t pt-4" aria-label="Saved citations">
              <h3 className="text-sm font-medium">Saved citations</h3>
              <SourceList key={`${route}:${version}:${citationRevision}`} route={route + '/grounded-lesson-sources'} token={token} version={version} />
            </section>}
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}

function DraftMaterials({ route, token, version, onSourceRevoked, quiz }: {
  route: string; token: string; version: number; onSourceRevoked: () => void; quiz?: QuizDestination
}) {
  const materialRoute = route + '/draft-sources'
  const [cursor, setCursor] = useState<string>()
  const [revision, setRevision] = useState(0)
  const [loaded, setLoaded] = useState<{ token: string; page: DraftSourcePage | 'error' }>()
  const [uploadState, setUploadState] = useState<{ token: string; busy: boolean; message?: string; error?: boolean }>()
  const [selection, setSelection] = useState<{ token: string; sources: string[] }>()
  const [confirmRevocation, setConfirmRevocation] = useState<{ token: string; uuid: string }>()
  const selected = selection?.token === token ? selection.sources : []
  const upload = useRef<AbortController | null>(null)
  const page = loaded?.token === token ? loaded.page : undefined
  const state = uploadState?.token === token ? uploadState : undefined

  useEffect(() => {
    const controller = new AbortController()
    const suffix = cursor ? `?after=${encodeURIComponent(cursor)}` : ''
    async function load() {
      try {
        const response = await secureFetch(getAPIUrl() + materialRoute + suffix, {
          ...RequestBodyWithAuthHeader('GET', null, null, token),
          redirect: 'error',
          signal: controller.signal,
        })
        if (!response.ok) throw new Error('Sources unavailable')
        const page: DraftSourcePage = await response.json()
        if (!controller.signal.aborted) setLoaded({ token, page })
      } catch {
        if (!controller.signal.aborted) setLoaded({ token, page: 'error' })
      }
    }
    void load()
    return () => controller.abort()
  }, [cursor, materialRoute, revision, token])

  useEffect(() => () => {
    upload.current?.abort()
    upload.current = null
  }, [token])

  async function uploadPdf(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (upload.current) return
    const form = event.currentTarget
    const rawData = new FormData(form)
    const data = new FormData()
    for (const [key, value] of rawData.entries()) {
      if (typeof value === 'string') {
        const trimmed = value.trim()
        if (!trimmed) continue
        data.append(key, trimmed)
      } else {
        data.append(key, value)
      }
    }
    // Only attach optional defaults if the author entered primary provenance identity.
    if (!data.has('parent_book_title') && !data.has('publisher_authority')) {
      data.delete('resource_type')
      data.delete('source_language')
      data.delete('rights_status')
    }
    const controller = new AbortController()
    upload.current = controller
    setUploadState({ token, busy: true })
    try {
      const response = await secureFetch(getAPIUrl() + materialRoute, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: data,
        cache: 'no-store',
        redirect: 'error',
        signal: controller.signal,
      })
      if (!response.ok) {
        const payload = await response.json().catch(() => null)
        throw new Error(typeof payload?.detail === 'string' ? payload.detail : 'The PDF could not be uploaded.')
      }
      if (!controller.signal.aborted) {
        form.reset()
        setCursor(undefined)
        setLoaded(undefined)
        setRevision((value) => value + 1)
        setUploadState({ token, busy: false, message: 'PDF source saved.' })
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        setUploadState({ token, busy: false, error: true, message: error instanceof Error ? error.message : 'The PDF could not be uploaded.' })
      }
    } finally {
      if (upload.current === controller) upload.current = null
    }
  }

  function showPage(after?: string) {
    setLoaded(undefined)
    setCursor(after)
  }

  async function revokeSource(uuid: string) {
    if (upload.current || confirmRevocation?.token !== token || confirmRevocation.uuid !== uuid) return
    const controller = new AbortController()
    upload.current = controller
    setUploadState({ token, busy: true, message: 'Revoking PDF source…' })
    try {
      const response = await secureFetch(`${getAPIUrl()}${materialRoute}/${encodeURIComponent(uuid)}/revoke`, {
        ...RequestBodyWithAuthHeader('POST', null, null, token),
        cache: 'no-store',
        redirect: 'error',
        signal: controller.signal,
      })
      if (!response.ok) {
        const payload = await response.json().catch(() => null)
        throw new Error(typeof payload?.detail === 'string' ? payload.detail : 'The PDF source could not be revoked.')
      }
      if (controller.signal.aborted) return
      setSelection((current) => current?.token === token
        ? { token, sources: current.sources.filter((source) => source !== uuid) }
        : current)
      setConfirmRevocation(undefined)
      showPage()
      setRevision((value) => value + 1)
      onSourceRevoked()
      setUploadState({ token, busy: false, message: 'PDF source revoked. Review any affected citations.' })
    } catch (error) {
      if (!controller.signal.aborted) {
        setUploadState({ token, busy: false, error: true, message: error instanceof Error ? error.message : 'The PDF source could not be revoked.' })
      }
    } finally {
      if (upload.current === controller) upload.current = null
    }
  }

  return (
    <section className="space-y-3" aria-label="Draft PDFs">
      <form onSubmit={uploadPdf} className="space-y-2">
        <label className="block space-y-1 text-sm font-medium">
          <span className="block">Searchable PDF</span>
          <input type="file" name="file" accept=".pdf,application/pdf" required disabled={state?.busy}
            aria-describedby="grounded-source-limits" className="block w-full text-sm" />
        </label>
        <details className="rounded-md border p-3 text-sm">
          <summary className="cursor-pointer font-medium">Official syllabus &amp; source provenance (optional)</summary>
          <p className="mt-1 text-xs text-muted-foreground">
            Record the official book title, authority, syllabus edition, and rights status for traceable review. Entering metadata does not itself grant distribution rights.
          </p>
          <div className="mt-2 grid gap-2 sm:grid-cols-2">
            <label className="grid gap-1 text-xs font-medium">
              Parent book / source title
              <input name="parent_book_title" maxLength={200} disabled={state?.busy} className="rounded border px-2 py-1.5 text-sm" />
            </label>
            <label className="grid gap-1 text-xs font-medium">
              Publisher / authority (e.g. NIE Sri Lanka)
              <input name="publisher_authority" maxLength={120} disabled={state?.busy} className="rounded border px-2 py-1.5 text-sm" />
            </label>
            <label className="grid gap-1 text-xs font-medium">
              Syllabus edition
              <input name="syllabus_edition" maxLength={64} disabled={state?.busy} className="rounded border px-2 py-1.5 text-sm" />
            </label>
            <label className="grid gap-1 text-xs font-medium">
              Printed page range
              <input name="printed_page_range" maxLength={64} disabled={state?.busy} className="rounded border px-2 py-1.5 text-sm" />
            </label>
            <label className="grid gap-1 text-xs font-medium">
              Resource type
              <select name="resource_type" defaultValue="unit_extract" disabled={state?.busy} className="rounded border bg-background px-2 py-1.5 text-sm">
                <option value="unit_extract">Unit extract</option>
                <option value="textbook">Textbook</option>
                <option value="practical_handbook">Practical handbook</option>
                <option value="teacher_guide">Teacher guide</option>
                <option value="syllabus">Syllabus</option>
                <option value="resource_book">Resource book</option>
                <option value="workbook">Workbook</option>
                <option value="supplementary">Supplementary</option>
              </select>
            </label>
            <label className="grid gap-1 text-xs font-medium">
              Rights status
              <select name="rights_status" defaultValue="unverified" disabled={state?.busy} className="rounded border bg-background px-2 py-1.5 text-sm">
                <option value="unverified">Unverified</option>
                <option value="verified_permitted">Verified permitted</option>
                <option value="restricted">Restricted</option>
              </select>
            </label>
            <label className="grid gap-1 text-xs font-medium">
              Source language
              <input name="source_language" defaultValue="en" maxLength={32} disabled={state?.busy} className="rounded border px-2 py-1.5 text-sm" />
            </label>
            <label className="grid gap-1 text-xs font-medium">
              Source revision (optional)
              <input name="source_revision" maxLength={120} disabled={state?.busy} className="rounded border px-2 py-1.5 text-sm" />
            </label>
          </div>
        </details>
        <p id="grounded-source-limits" className="max-w-prose text-sm text-muted-foreground">
          Searchable PDFs only; scanned PDFs need OCR first. Each file is limited to 4 MiB, 200 pages, and 100,000
          extracted characters. Topic search uses shared words without synonym expansion and sends up to six matching
          excerpts of 2,000 characters from the five selected PDFs. If no words match, the service explains the gap and
          skips AI generation.
        </p>
        <Button type="submit" size="sm" disabled={state?.busy}>{state?.busy ? 'Uploading…' : 'Upload PDF'}</Button>
        {state?.message && <p role={state.error ? 'alert' : 'status'} className="text-sm">{state.message}</p>}
      </form>
      {!page && <p role="status" className="text-sm">Loading PDFs…</p>}
      {page === 'error' && <p role="alert" className="text-sm">PDF sources could not be loaded.</p>}
      {page && page !== 'error' && (
        <>
          {page.sources.length === 0 && <p className="text-sm">No active PDF sources on this page.</p>}
          <ul className="space-y-1 text-sm">
            {page.sources.map((source) => (
              <li key={source.source_uuid} className="break-words">
                <label className="flex items-start gap-2">
                  <input type="checkbox" checked={selected.includes(source.source_uuid)}
                    disabled={selected.length >= 5 && !selected.includes(source.source_uuid)}
                    onChange={(event) => setSelection({
                      token, sources: event.target.checked ? [...selected, source.source_uuid]
                        : selected.filter((uuid) => uuid !== source.source_uuid),
                    })} />
                  <span>
                    {source.label} · {source.readable_pages.length} readable {source.readable_pages.length === 1 ? 'page' : 'pages'}
                    {!!source.pages_without_readable_text?.length && (
                      <span className="block">
                        No readable text on {source.pages_without_readable_text.length === 1 ? 'page' : 'pages'} {source.pages_without_readable_text.join(', ')}.
                      </span>
                    )}
                  </span>
                </label>
                {confirmRevocation?.token === token && confirmRevocation.uuid === source.source_uuid ? (
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <p className="basis-full text-sm">Revoking this source makes any saved citations that use it stale.</p>
                    <Button type="button" size="sm" variant="destructive" disabled={state?.busy}
                      onClick={() => void revokeSource(source.source_uuid)}>Confirm revocation</Button>
                    <Button type="button" size="sm" variant="outline" disabled={state?.busy}
                      onClick={() => setConfirmRevocation(undefined)}>Cancel</Button>
                  </div>
                ) : <Button type="button" size="sm" variant="ghost" disabled={state?.busy}
                  onClick={() => setConfirmRevocation({ token, uuid: source.source_uuid })}>
                  Revoke source<span className="sr-only"> {source.label}</span>
                </Button>}
              </li>
            ))}
          </ul>
          {(cursor || page.next_cursor) && (
            <div className="flex flex-wrap gap-2">
              {cursor && <Button type="button" size="sm" variant="outline" onClick={() => showPage()}>First page</Button>}
              {page.next_cursor && <Button type="button" size="sm" variant="outline" onClick={() => showPage(page.next_cursor ?? undefined)}>Next page</Button>}
            </div>
          )}
        </>
      )}
      <p className="text-sm">{selected.length} of 5 sources selected</p>
      {quiz
        ? <GroundedQuizDraftForm key={`${revision}:${selected.join(':')}`} route={route} token={token} selected={selected} destination={quiz} />
        : <GroundedLessonDraftForm route={route} token={token} version={version} selected={selected} />}
    </section>
  )
}

function SourceList({ route, token, version }: { route: string; token: string; version: number }) {
  const [loaded, setLoaded] = useState<{ token: string; result: SourceState | 'error' }>()
  const result = loaded?.token === token ? loaded.result : undefined
  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      try {
        const response = await secureFetch(getAPIUrl() + route, {
          ...RequestBodyWithAuthHeader('GET', null, null, token),
          redirect: 'error',
          signal: controller.signal,
        })
        if (!response.ok) throw new Error('Sources unavailable')
        const result: SourceState = await response.json()
        if (!controller.signal.aborted) setLoaded({ token, result })
      } catch {
        if (!controller.signal.aborted) setLoaded({ token, result: 'error' })
      }
    }
    void load()
    return () => controller.abort()
  }, [route, token])

  if (!result) return <p role="status">Checking lesson sources…</p>
  if (result === 'error') return <p role="alert">Sources are unavailable. Close and reopen to try again.</p>
  if (result.status === 'stale' || result.current_version !== version) {
    return <p role="status">The sources no longer match this lesson version or are unavailable.</p>
  }
  if (result.status === 'none') return <p role="status">No source citations have been saved for this lesson.</p>
  return (
    <ol className="space-y-3">
      {result.citations.map((citation, index) => (
        <li key={`${citation.source_uuid}:${citation.page}:${index}`} className="space-y-1 border-b pb-3">
          <p className="break-words text-sm font-medium">{citation.label} · Page {citation.page}</p>
          <blockquote className="whitespace-pre-wrap break-words text-sm">{citation.quote}</blockquote>
        </li>
      ))}
    </ol>
  )
}
