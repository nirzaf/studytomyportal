'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { BookOpenText, ExternalLink } from 'lucide-react'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { useOrg } from '@components/Contexts/OrgContext'
import { Button } from '@components/ui/button'
import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'

type Source = {
  source_uuid: string
  version_uuid: string
  title: string
  publisher: string
  source_type: string
  syllabus_edition: string
  page_start: number
  page_end: number
  evidence_kind: 'official' | 'supplementary'
  evidence_quote: string
  curriculum_label: string
  pdf_url: string
}

export default function ActivitySyllabusSources({ activityUuid }: { activityUuid: string }) {
  const session = useLHSession() as any
  const org = useOrg() as any
  const token = session?.data?.tokens?.access_token as string | undefined
  const orgUuid = org?.org_uuid as string | undefined
  const [sources, setSources] = useState<Source[]>([])
  const [message, setMessage] = useState('')
  const [opening, setOpening] = useState('')
  const objectUrls = useRef<string[]>([])
  const endpoint = useMemo(() => orgUuid && activityUuid
    ? `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/course-activities/${encodeURIComponent(activityUuid)}/syllabus-sources`
    : '', [orgUuid, activityUuid])

  useEffect(() => {
    if (!endpoint || !token) return
    const controller = new AbortController()
    secureFetch(endpoint, {
      ...RequestBodyWithAuthHeader('GET', null, null, token),
      cache: 'no-store', redirect: 'error', signal: controller.signal,
    }).then(async (response) => {
      if (!response.ok) return
      const body = await response.json()
      if (!controller.signal.aborted) setSources(Array.isArray(body) ? body : [])
    }).catch(() => {})
    return () => {
      controller.abort()
      objectUrls.current.forEach((url) => URL.revokeObjectURL(url))
      objectUrls.current = []
    }
  }, [endpoint, token])

  async function openPdf(source: Source) {
    if (!token || opening) return
    const popup = window.open('', '_blank')
    if (popup) popup.opener = null
    setOpening(source.version_uuid)
    setMessage('')
    try {
      const path = `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid || '')}/syllabus-sources/${encodeURIComponent(source.version_uuid)}/pdf`
      const response = await secureFetch(path, {
        ...RequestBodyWithAuthHeader('GET', null, null, token),
        cache: 'no-store', redirect: 'error',
      })
      if (!response.ok) throw new Error('This approved source is no longer available.')
      const url = URL.createObjectURL(await response.blob())
      objectUrls.current.push(url)
      if (popup) popup.location.href = `${url}#page=${source.page_start}`
      else {
        const link = document.createElement('a')
        link.href = `${url}#page=${source.page_start}`
        link.target = '_blank'
        link.rel = 'noopener noreferrer'
        link.click()
      }
    } catch (error) {
      popup?.close()
      setMessage(error instanceof Error ? error.message : 'The source PDF could not be opened.')
    } finally {
      setOpening('')
    }
  }

  if (sources.length === 0 && !message) return null

  return <aside className="mt-5 rounded-xl border border-blue-100 bg-blue-50/60 p-4" aria-labelledby="lesson-syllabus-sources">
    <div className="flex items-start gap-3">
      <BookOpenText size={19} className="mt-0.5 shrink-0 text-blue-800" aria-hidden />
      <div className="min-w-0 flex-1">
        <h2 id="lesson-syllabus-sources" className="font-semibold text-slate-900">Syllabus sources linked to this lesson</h2>
        <p className="mt-1 text-xs leading-5 text-slate-600">These page references are linked through the reviewed curriculum map.</p>
        {message && <p role="status" className="mt-3 text-sm text-rose-800">{message}</p>}
        {sources.length > 0 && <ul className="mt-3 space-y-3">
          {sources.map((source) => <li key={`${source.version_uuid}-${source.page_start}-${source.page_end}`} className="rounded-lg border border-blue-100 bg-white p-3">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0">
                <h3 className="font-medium text-slate-900">{source.title}</h3>
                <p className="mt-1 text-xs text-slate-600">{source.evidence_kind === 'official' ? 'Official syllabus evidence' : 'Supplementary evidence'} · {source.publisher} · {source.syllabus_edition} · PDF page {source.page_start}{source.page_end > source.page_start ? `–${source.page_end}` : ''}</p>
                <p className="mt-1 text-xs text-slate-500">{source.curriculum_label}</p>
                <blockquote className="mt-2 border-s-2 border-blue-200 ps-3 text-sm leading-6 text-slate-700">{source.evidence_quote}</blockquote>
              </div>
              <Button type="button" variant="outline" size="sm" disabled={Boolean(opening)} onClick={() => void openPdf(source)}>
                <ExternalLink size={14} aria-hidden /> {opening === source.version_uuid ? 'Opening…' : `Open PDF · p. ${source.page_start}`}
              </Button>
            </div>
          </li>)}
        </ul>}
      </div>
    </div>
  </aside>
}
