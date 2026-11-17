'use client'

import { type ReactNode, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { Button } from '@components/ui/button'
import AssessmentIntegrityCases from '@components/Studytomy/AssessmentIntegrityCases'
import { getAuthenticatedSessionIdentity } from '@/lib/auth/sessionScope'
import { formatDateTime } from '@/lib/format'
import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'

type Release = { course_uuid: string; release_uuid: string; name: string }
type Lesson = { activity_uuid: string; name: string }
type Catalog = { releases: Release[]; next_cursor: string | null }
type Index = { lessons: Lesson[]; requires_first_use: boolean }
type TextLesson = Lesson & { paragraphs: string[] }
type AccessWindow = Release & {
  status: 'active' | 'first_use_pending' | 'scheduled' | 'expired' | 'revoked'
  start_basis: 'after_purchase' | 'after_first_access' | 'fixed_period'
  starts_at: string | null
  ends_at: string | null
  duration_days: number | null
  completion_before_window?: true | null
}

const accessStatus: Record<AccessWindow['status'], string> = {
  active: 'Active', first_use_pending: 'Starts on first use', scheduled: 'Scheduled',
  expired: 'Expired', revoked: 'Access ended',
}
const startBasis: Record<AccessWindow['start_basis'], string> = {
  after_purchase: 'From purchase', after_first_access: 'From first use', fixed_period: 'Fixed period',
}

type View =
  | { kind: 'loading' | 'unavailable' }
  | { kind: 'catalog'; page: Catalog; after?: string; history: boolean }
  | { kind: 'access'; window: AccessWindow }
  | { kind: 'lessons'; release: Release; index: Index }
  | { kind: 'lesson'; release: Release; lesson: TextLesson }

export default function AccountLearning({ orgUuid }: { orgUuid: string }) {
  const session = useLHSession()
  const token = session?.data?.tokens?.access_token
  const user = session?.data?.user
  if (session?.status !== 'authenticated' || !token) {
    return <p role="status">Sign in to view your learning.</p>
  }
  const identity = getAuthenticatedSessionIdentity(user?.id ?? user?.user_uuid, session.data)
  return (
    <div key={JSON.stringify([orgUuid, identity])} className="space-y-8">
      <AssessmentIntegrityCases orgUuid={orgUuid} token={token} />
      <LearningView orgUuid={orgUuid} token={token} />
    </div>
  )
}

function LearningView({ orgUuid, token }: { orgUuid: string; token: string }) {
  const { i18n } = useTranslation()
  const [view, setView] = useState<View>({ kind: 'loading' })
  const pending = useRef<AbortController | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const moveFocus = useRef(false)

  async function load<T>(path: string, project: (_data: T) => View, method = 'GET') {
    pending.current?.abort()
    const controller = new AbortController()
    pending.current = controller
    setView({ kind: 'loading' })
    try {
      const response = await secureFetch(
        `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/learning/${path}`,
        { ...RequestBodyWithAuthHeader(method, null, null, token), cache: 'no-store', signal: controller.signal },
      )
      if (!response.ok) throw new Error('Learning request unavailable')
      const data = await response.json() as T
      if (!controller.signal.aborted) setView(project(data))
    } catch {
      if (!controller.signal.aborted) setView({ kind: 'unavailable' })
    }
  }

  function showCatalog(after?: string, history = false) {
    const query = after ? `?${new URLSearchParams({ after })}` : ''
    return load<Catalog>(`${history ? 'access-history' : 'releases'}${query}`, page => ({ kind: 'catalog', page, after, history }))
  }

  function pathFor(release: Release) {
    return `courses/${encodeURIComponent(release.course_uuid)}/releases/${encodeURIComponent(release.release_uuid)}`
  }

  function showLessons(release: Release) {
    return load<Index>(`${pathFor(release)}/lessons`, index => ({ kind: 'lessons', release, index }))
  }

  function showAccess(release: Release) {
    return load<AccessWindow>(`${pathFor(release)}/access-history`, window => ({ kind: 'access', window }))
  }

  function showLesson(release: Release, lesson: Lesson, firstUse: boolean) {
    const path = `${pathFor(release)}/lessons/${encodeURIComponent(lesson.activity_uuid)}`
    return load<TextLesson>(
      firstUse ? `${path}/open` : path,
      data => ({ kind: 'lesson', release, lesson: data }),
      firstUse ? 'POST' : 'GET',
    )
  }

  useEffect(() => {
    function clear() {
      pending.current?.abort()
      setView({ kind: 'loading' })
    }
    function refresh() {
      clear()
      if (document.visibilityState !== 'hidden') void showCatalog()
    }
    refresh()
    document.addEventListener('visibilitychange', refresh)
    window.addEventListener('pagehide', clear)
    window.addEventListener('pageshow', refresh)
    return () => {
      pending.current?.abort()
      document.removeEventListener('visibilitychange', refresh)
      window.removeEventListener('pagehide', clear)
      window.removeEventListener('pageshow', refresh)
    }
    // Refresh on credential changes and when returning to the page.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgUuid, token])

  useEffect(() => {
    if (moveFocus.current && view.kind !== 'loading') {
      heading.current?.focus()
      moveFocus.current = false
    }
  }, [view])

  function act(action: () => Promise<void>) {
    moveFocus.current = true
    void action()
  }

  let title = 'My learning'
  let content: ReactNode
  switch (view.kind) {
    case 'catalog':
      if (view.history) title = 'Access history'
      content = <>
        <Button variant="outline" className="min-h-11"
          onClick={() => act(() => showCatalog(undefined, !view.history))}>
          {view.history ? 'Back to my learning' : 'Access history'}
        </Button>
        {view.page.releases.length === 0 && <p>{view.history ? 'No access history is available.' : 'No reviewed courses are available.'}</p>}
        <ul className="space-y-2">
          {view.page.releases.map(release => <li key={release.release_uuid}>
            <Button variant="outline" className="min-h-11 w-full justify-start whitespace-normal text-start"
              onClick={() => act(() => view.history ? showAccess(release) : showLessons(release))}>{release.name}</Button>
          </li>)}
        </ul>
        {(view.after || view.page.next_cursor) && <div className="flex flex-wrap gap-2">
          {view.after && <Button variant="outline" className="min-h-11"
            onClick={() => act(() => showCatalog(undefined, view.history))}>First page</Button>}
          {view.page.next_cursor && <Button variant="outline" className="min-h-11"
            onClick={() => act(() => showCatalog(view.page.next_cursor ?? undefined, view.history))}>Next page</Button>}
        </div>}
      </>
      break
    case 'access': {
      const access = view.window
      title = access.name
      content = <>
        <Button variant="outline" className="min-h-11"
          onClick={() => act(() => showCatalog(undefined, true))}>Back to access history</Button>
        <dl className="grid gap-3 rounded-lg border p-4 sm:grid-cols-2">
          <div><dt>Status</dt><dd className="font-medium">{accessStatus[access.status]}</dd></div>
          <div><dt>Access basis</dt><dd>{startBasis[access.start_basis]}</dd></div>
          {access.starts_at && <div><dt>Start date</dt><dd><time dateTime={access.starts_at}>
            {formatDateTime(access.starts_at, i18n.language, { timeZone: 'UTC' })} UTC
          </time></dd></div>}
          {access.ends_at && <div><dt>End date</dt><dd><time dateTime={access.ends_at}>
            {formatDateTime(access.ends_at, i18n.language, { timeZone: 'UTC' })} UTC
          </time></dd></div>}
          {access.duration_days !== null && <div><dt>Duration</dt><dd>{access.duration_days} {access.duration_days === 1 ? 'day' : 'days'}</dd></div>}
          {access.completion_before_window && <div><dt>Prior course completion</dt><dd>Recorded before this access period.</dd></div>}
        </dl>
      </>
      break
    }
    case 'lessons':
      title = view.release.name
      content = <>
        <Button variant="outline" className="min-h-11" onClick={() => act(() => showCatalog())}>Back to my learning</Button>
        {view.index.requires_first_use && <p>Opening a lesson starts your access period.</p>}
        {view.index.lessons.length === 0 && <p>No lessons are available for this release.</p>}
        <ol className="space-y-2">
          {view.index.lessons.map(lesson => <li key={lesson.activity_uuid}>
            <Button variant="outline" className="min-h-11 w-full justify-start whitespace-normal text-start"
              onClick={() => act(() => showLesson(view.release, lesson, view.index.requires_first_use))}>
              {view.index.requires_first_use ? `Open ${lesson.name}` : lesson.name}
            </Button>
          </li>)}
        </ol>
      </>
      break
    case 'lesson':
      title = view.lesson.name
      content = <>
        <Button variant="outline" className="min-h-11" onClick={() => act(() => showLessons(view.release))}>Back to lessons</Button>
        <article className="max-w-prose space-y-4 whitespace-pre-wrap break-words leading-relaxed">
          {view.lesson.paragraphs.map((paragraph, index) => <p key={index}>{paragraph}</p>)}
        </article>
      </>
      break
    case 'unavailable':
      content = <>
        <p role="status">Learning is unavailable for this account.</p>
        <Button variant="outline" className="min-h-11" onClick={() => act(() => showCatalog())}>Try again</Button>
      </>
      break
    default:
      content = <p role="status">Loading learning…</p>
  }
  return <section className="space-y-4" aria-labelledby="account-learning-title">
    <h1 ref={heading} id="account-learning-title" tabIndex={-1} className="break-words text-xl font-semibold">{title}</h1>
    {content}
  </section>
}
