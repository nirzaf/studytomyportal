'use client'

import Link from 'next/link'
import { type ReactNode, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { Button } from '@components/ui/button'
import AssessmentIntegrityCases from '@components/Studytomy/AssessmentIntegrityCases'
import DocumentPdfActivity from '@components/Objects/Activities/DocumentPdf/DocumentPdf'
import LearnHousePlayer from '@components/Objects/Activities/Video/LearnHousePlayer'
import { getAuthenticatedSessionIdentity } from '@/lib/auth/sessionScope'
import { formatDateTime } from '@/lib/format'
import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'

type Release = { course_uuid: string; release_uuid: string; name: string }
type Lesson = {
  activity_uuid: string
  name: string
  position: number
  format: 'text' | 'pdf' | 'video' | 'unsupported'
  media_type: string | null
  available: boolean
  caption_codes?: string[]
  unavailable_reason: string | null
}
type Catalog = { releases: Release[]; next_cursor: string | null }
type Index = { lessons: Lesson[]; requires_first_use: boolean }
type Completion = { activity_uuid: string; activity_completed: boolean; course_completed: boolean }
type TextLesson = Lesson & { paragraphs: string[]; completion?: Completion }
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
  | { kind: 'lesson'; release: Release; lesson: TextLesson; completionRequest?: 'saving' | 'failed' }
  | { kind: 'media'; release: Release; lesson: Lesson }

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
      <LearningView orgUuid={orgUuid} token={token} refreshSession={session.update} />
    </div>
  )
}

function LearningView({
  orgUuid,
  token,
  refreshSession,
}: {
  orgUuid: string
  token: string
  refreshSession?: (_force?: boolean) => Promise<void>
}) {
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

  async function completeLesson(lessonView: Extract<View, { kind: 'lesson' }>) {
    const { release, lesson } = lessonView
    pending.current?.abort()
    const controller = new AbortController()
    pending.current = controller
    setView({ ...lessonView, completionRequest: 'saving' })
    try {
      const path = `${pathFor(release)}/lessons/${encodeURIComponent(lesson.activity_uuid)}/completion`
      const response = await secureFetch(
        `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/learning/${path}`,
        { ...RequestBodyWithAuthHeader('POST', null, null, token), cache: 'no-store', signal: controller.signal },
      )
      if (!response.ok) throw new Error('Completion could not be recorded')
      const completion = await response.json() as Completion
      if (completion.activity_uuid !== lesson.activity_uuid || completion.activity_completed !== true) {
        throw new Error('Completion response did not match this lesson')
      }
      if (!controller.signal.aborted) setView({
        ...lessonView, lesson: { ...lesson, completion }, completionRequest: undefined,
      })
    } catch {
      if (!controller.signal.aborted) setView({ ...lessonView, completionRequest: 'failed' })
    }
  }

  async function showMedia(release: Release, lesson: Lesson, firstUse: boolean) {
    pending.current?.abort()
    const controller = new AbortController()
    pending.current = controller
    setView({ kind: 'loading' })
    try {
      if (firstUse) {
        const path = `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/learning/courses/${encodeURIComponent(release.course_uuid)}/releases/${encodeURIComponent(release.release_uuid)}/activities/${encodeURIComponent(lesson.activity_uuid)}/media/open`
        const request = RequestBodyWithAuthHeader('POST', null, null, token)
        const headers = new Headers(request.headers)
        headers.set('Range', 'bytes=0-0')
        const response = await secureFetch(path, {
          ...request,
          headers,
          cache: 'no-store',
          signal: controller.signal,
        })
        if (!response.ok) throw new Error('Media access is unavailable')
        await response.body?.cancel()
      }
      if (!controller.signal.aborted) setView({ kind: 'media', release, lesson })
    } catch {
      if (!controller.signal.aborted) setView({ kind: 'unavailable' })
    }
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
        {(access.status === 'expired' || access.status === 'revoked') && (
          <Link href="/help" className="font-semibold underline">Contact support</Link>
        )}
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
            <div className="space-y-1">
              <Button
                variant="outline"
                className="min-h-11 w-full justify-start whitespace-normal text-start"
                disabled={!lesson.available}
                onClick={() => act(() => lesson.format === 'text'
                  ? showLesson(view.release, lesson, view.index.requires_first_use)
                  : showMedia(view.release, lesson, view.index.requires_first_use))}
              >
                {lesson.available && view.index.requires_first_use ? `Open ${lesson.name}` : lesson.name}
                <span className="ms-auto shrink-0 text-xs font-normal text-muted-foreground">
                  {lesson.format === 'text' ? 'Text' : lesson.format === 'pdf' ? 'PDF' : lesson.format === 'video' ? 'Video' : 'Unsupported'}
                </span>
              </Button>
              {!lesson.available && <p className="px-3 text-sm text-muted-foreground">
                {lesson.unavailable_reason === 'accessibility_policy_required'
                  ? 'This resource needs an approved accessibility policy before it can be opened.'
                  : lesson.unavailable_reason === 'accessibility_policy_incomplete'
                    ? 'The approved accessibility policy does not yet define this resource.'
                    : lesson.unavailable_reason === 'media_format_not_approved'
                      ? 'This resource format or size is not approved for delivery.'
                      : lesson.unavailable_reason === 'captions_or_text_alternative_not_retained'
                        ? 'Video is unavailable until captions or an approved accessible alternative are included in the release.'
                        : lesson.unavailable_reason === 'unsupported_release_format'
                          ? 'This activity format is not supported in the managed lesson player.'
                          : lesson.unavailable_reason === 'release_format_conflict'
                            ? 'This activity has conflicting release-format metadata.'
                            : lesson.unavailable_reason === 'assessment_pass_required'
                              ? 'A verified assessment pass is required for this activity.'
                              : lesson.unavailable_reason === 'assessment_pass_delivery_pending'
                                ? 'Your assessment pass is recorded. This activity is still locked.'
                                : 'This released resource is not currently available.'}
              </p>}
            </div>
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
        {view.lesson.completion && (
          view.lesson.completion.activity_completed
            ? <p role="status">{view.lesson.completion.course_completed ? 'Course completed.' : 'Lesson completed.'}</p>
            : <div className="space-y-2">
                <Button className="min-h-11" disabled={view.completionRequest === 'saving'}
                  onClick={() => void completeLesson(view)}>
                  {view.completionRequest === 'saving' ? 'Saving completion…' : 'Mark lesson complete'}
                </Button>
                {view.completionRequest === 'failed' && <p role="alert">Completion could not be saved. Try again.</p>}
              </div>
        )}
      </>
      break
    case 'media': {
      title = view.lesson.name
      const mediaUrl = `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/learning/courses/${encodeURIComponent(view.release.course_uuid)}/releases/${encodeURIComponent(view.release.release_uuid)}/activities/${encodeURIComponent(view.lesson.activity_uuid)}/media`
      content = <>
        <Button variant="outline" className="min-h-11" onClick={() => act(() => showLessons(view.release))}>Back to lessons</Button>
        <ManagedReleaseMedia
          release={view.release}
          lesson={view.lesson}
          mediaUrl={mediaUrl}
          refreshSession={refreshSession}
        />
      </>
      break
    }
    case 'unavailable':
      content = <>
        <p role="status">Learning is unavailable for this account.</p>
        <Button variant="outline" className="min-h-11" onClick={() => act(() => showCatalog())}>Try again</Button>
        <p><Link href="/help" className="font-semibold underline">Contact support</Link></p>
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

function ManagedReleaseMedia({
  release,
  lesson,
  mediaUrl,
  refreshSession,
}: {
  release: Release
  lesson: Lesson
  mediaUrl: string
  refreshSession?: (_force?: boolean) => Promise<void>
}) {
  const [retry, setRetry] = useState(0)
  const [failed, setFailed] = useState(false)

  async function retryMedia() {
    setFailed(false)
    await refreshSession?.(true).catch(() => {})
    setRetry(value => value + 1)
  }

  const activity = {
    activity_uuid: lesson.activity_uuid,
    name: lesson.name,
    content: { filename: lesson.format === 'pdf' ? 'lesson.pdf' : 'lesson.mp4' },
  }

  return <div className="space-y-3">
    {lesson.format === 'pdf' && <>
      <DocumentPdfActivity
        key={`${mediaUrl}:${retry}`}
        activity={activity}
        course={{ course_uuid: release.course_uuid }}
        mediaUrl={mediaUrl}
        onRetry={retryMedia}
      />
      <Button type="button" variant="outline" className="min-h-11" onClick={() => void retryMedia()}>
        Refresh document access
      </Button>
    </>}
    {lesson.format === 'video' && <>
      {!failed && <div className="relative aspect-video w-full overflow-hidden rounded-lg bg-black">
        <LearnHousePlayer
          key={`${mediaUrl}:${retry}`}
          src={mediaUrl}
          title={lesson.name}
          crossOrigin="use-credentials"
          captions={lesson.caption_codes?.map(code => ({
            code, label: code, url: `${mediaUrl}/captions/${encodeURIComponent(code)}`,
          }))}
          onCaptionsUnavailable={() => setFailed(true)}
          onMediaError={() => setFailed(true)}
        />
      </div>}
      {failed && <div className="space-y-2" role="alert">
        <p>Video could not be loaded. Your access may have expired or the connection may have changed.</p>
        <Button type="button" variant="outline" className="min-h-11" onClick={() => void retryMedia()}>
          Refresh access and retry
        </Button>
      </div>}
    </>}
  </div>
}
