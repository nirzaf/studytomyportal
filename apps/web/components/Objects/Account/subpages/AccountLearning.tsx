'use client'

import Link from 'next/link'
import { type ReactNode, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { Button } from '@components/ui/button'
import ManagedPdfTopicCheck from '@components/Studytomy/ManagedPdfTopicCheck'
import AssessmentIntegrityCases from '@components/Studytomy/AssessmentIntegrityCases'
import AdaptiveAttemptPanel from '@components/Objects/Activities/AdaptiveAttemptPanel'
import type { LessonRecommendation } from '@components/Objects/Activities/AdaptiveLessonProgress'
import { LessonTutorContent, useLessonTutor } from '@components/Objects/Activities/AI/LessonTutorPanel'
import DocumentPdfActivity from '@components/Objects/Activities/DocumentPdf/DocumentPdf'
import LearnHousePlayer from '@components/Objects/Activities/Video/LearnHousePlayer'
import {
  type LinkedAssignment,
  ManagedLinkedAssignmentPanel,
} from '@components/Objects/Account/subpages/ManagedLinkedAssignmentPanel'
import { getAuthenticatedSessionIdentity } from '@/lib/auth/sessionScope'
import { formatDateTime } from '@/lib/format'
import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'

type Release = { course_uuid: string; release_uuid: string; name: string }
type Lesson = {
  activity_uuid: string
  name: string
  position: number
  format: 'text' | 'pdf' | 'video' | 'virtual_lab' | 'unsupported'
  media_type: string | null
  available: boolean
  caption_codes?: string[]
  linked_assignment_activity_uuid?: string | null
  unavailable_reason: string | null
}
type Catalog = { releases: Release[]; next_cursor: string | null }
type Index = { lessons: Lesson[]; requires_first_use: boolean }
type Completion = { activity_uuid: string; activity_completed: boolean; course_completed: boolean }
type TextLesson = Lesson & { paragraphs: string[]; completion?: Completion }
type LinkedAssignmentSummary = {
  activity_uuid: string
  assignment_uuid: string
  title: string
}
type VirtualLabLesson = Release & {
  activity_uuid: string
  lab_id: string
  experiment_id: string
  build_version: string
  build_sha256: string
  entry_url: string
  units_system: string
  submission_formats_note?: string | null
  linked_assignment?: LinkedAssignmentSummary | null
}
type CertificatePage = {
  awards: {
    award_uuid: string
    award_name: string
    issuer_name: string
    course_name: string
    issued_at: string
    status: 'issued' | 'corrected' | 'withdrawn'
  }[]
  next_cursor: string | null
}
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
  | { kind: 'loading' }
  | { kind: 'unavailable'; section?: 'certificates' }
  | { kind: 'catalog'; page: Catalog; after?: string; history: boolean }
  | { kind: 'access'; window: AccessWindow }
  | { kind: 'certificates'; page: CertificatePage; after?: string }
  | { kind: 'lessons'; release: Release; index: Index }
  | { kind: 'lesson'; release: Release; lesson: TextLesson; completionRequest?: 'saving' | 'failed' }
  | { kind: 'media'; release: Release; lesson: Lesson }
  | { kind: 'virtual_lab'; release: Release; lab: VirtualLabLesson }
  | { kind: 'assignment'; release: Release; assignment: LinkedAssignment; returnLab?: VirtualLabLesson }

function isApprovedLabEntryUrl(url: string): boolean {
  try {
    const parsed = new URL(url)
    if (parsed.username || parsed.password || parsed.search) return false
    if (parsed.protocol === 'https:') return true
    return parsed.protocol === 'http:' && (parsed.hostname === 'localhost' || parsed.hostname === '127.0.0.1')
  } catch {
    return false
  }
}

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
      <LearningView orgUuid={orgUuid} token={token} learnerUuid={typeof user?.user_uuid === 'string' && user.user_uuid ? user.user_uuid : undefined} refreshSession={session.update} />
    </div>
  )
}

function LearningView({
  orgUuid,
  token,
  refreshSession,
  learnerUuid,
}: {
  learnerUuid?: string
  orgUuid: string
  token: string
  refreshSession?: (_force?: boolean) => Promise<void>
}) {
  const { i18n } = useTranslation()
  const [view, setView] = useState<View>({ kind: 'loading' })
  const lessonCourse = view.kind === 'lesson' ? view.release.course_uuid : undefined
  const lessonRelease = view.kind === 'lesson' ? view.release.release_uuid : undefined
  const adaptiveRelease = useMemo(() => lessonCourse && lessonRelease
    ? { course_uuid: lessonCourse, course_release_uuid: lessonRelease }
    : undefined, [lessonCourse, lessonRelease])
  const pending = useRef<AbortController | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const moveFocus = useRef(false)

  async function load<T>(
    path: string, project: (_data: T) => View, method = 'GET', unavailableSection?: 'certificates',
  ) {
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
      if (!controller.signal.aborted) setView({ kind: 'unavailable', section: unavailableSection })
    }
  }

  function showCatalog(after?: string, history = false) {
    const query = after ? `?${new URLSearchParams({ after })}` : ''
    return load<Catalog>(`${history ? 'access-history' : 'releases'}${query}`, page => ({ kind: 'catalog', page, after, history }))
  }

  function showCertificates(after?: string) {
    const query = after ? `?${new URLSearchParams({ after })}` : ''
    return load<CertificatePage>(
      `awards${query}`, page => ({ kind: 'certificates', page, after }), 'GET', 'certificates',
    )
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

  function showVirtualLab(release: Release, lesson: Lesson, firstUse: boolean) {
    const path = `${pathFor(release)}/labs/${encodeURIComponent(lesson.activity_uuid)}`
    return load<VirtualLabLesson>(
      firstUse ? `${path}/open` : path,
      data => ({ kind: 'virtual_lab', release, lab: data }),
      firstUse ? 'POST' : 'GET',
    )
  }

  function showLinkedAssignment(release: Release, activityUuid: string, returnLab?: VirtualLabLesson) {
    const path = `${pathFor(release)}/assignments/${encodeURIComponent(activityUuid)}`
    return load<LinkedAssignment>(
      path,
      data => ({ kind: 'assignment', release, assignment: data, returnLab }),
      'GET',
    )
  }

  async function openManagedRecommendation(recommendation: LessonRecommendation): Promise<boolean> {
    if (view.kind !== 'lesson') return false
    const { release } = view
    if (recommendation.course_uuid !== release.course_uuid || recommendation.course_release_uuid !== release.release_uuid) {
      return false
    }
    pending.current?.abort()
    const controller = new AbortController()
    pending.current = controller
    try {
      const base = `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/learning/${pathFor(release)}`
      const indexResponse = await secureFetch(
        `${base}/lessons`,
        { ...RequestBodyWithAuthHeader('GET', null, null, token), cache: 'no-store', signal: controller.signal },
      )
      if (!indexResponse.ok) return false
      const index = await indexResponse.json() as Index
      const target = index.lessons.find(lesson => lesson.activity_uuid === recommendation.activity_uuid)
      if (controller.signal.aborted || !target?.available) return false
      if (target.format === 'pdf' || target.format === 'video') {
        await showMedia(release, target, false)
        return true
      }
      if (target.format !== 'text') return false
      const response = await secureFetch(
        `${base}/lessons/${encodeURIComponent(recommendation.activity_uuid)}`,
        { ...RequestBodyWithAuthHeader('GET', null, null, token), cache: 'no-store', signal: controller.signal },
      )
      if (!response.ok) return false
      const lesson = await response.json() as Partial<TextLesson>
      if (controller.signal.aborted || lesson.activity_uuid !== recommendation.activity_uuid || !Array.isArray(lesson.paragraphs)) {
        return false
      }
      moveFocus.current = true
      setView({ kind: 'lesson', release, lesson: lesson as TextLesson })
      return true
    } catch {
      return false
    }
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
        {!view.history && <Button variant="outline" className="min-h-11"
          onClick={() => act(() => showCertificates())}>My certificates</Button>}
        {view.page.releases.length === 0 && <p>{view.history ? 'No access history is available.' : 'No reviewed courses are available.'}</p>}
        <ul className="space-y-2">
          {view.page.releases.map(release => <li key={release.release_uuid}>
            <Button variant="outline" className="min-h-11 h-auto w-full flex-col items-start whitespace-normal text-start"
              onClick={() => act(() => view.history ? showAccess(release) : showLessons(release))}>
              <span>{release.name}</span>
              {view.page.releases.some(other => other.release_uuid !== release.release_uuid && other.name === release.name) &&
                <span className="max-w-full break-all text-xs text-muted-foreground">Release {release.release_uuid}</span>}
            </Button>
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
    case 'certificates':
      title = 'My certificates'
      content = <>
        <Button variant="outline" className="min-h-11"
          onClick={() => act(() => showCatalog())}>Back to my learning</Button>
        {view.page.awards.length === 0 && <p>No certificates are available on this page.</p>}
        <ul className="space-y-2">
          {view.page.awards.map(award => <li key={award.award_uuid} className="space-y-1 rounded-lg border p-4">
            <h3 className="font-semibold">{award.award_name}</h3>
            <p>{award.course_name} · {award.issuer_name}</p>
            <p>{award.status === 'withdrawn' ? 'Withdrawn' : award.status === 'corrected' ? 'Corrected' : 'Issued'}
              {' · '}<time dateTime={award.issued_at}>
                {formatDateTime(award.issued_at, i18n.language, { timeZone: 'UTC' })} UTC
              </time>
            </p>
            <p className="break-words text-sm text-muted-foreground">Award reference {award.award_uuid}</p>
          </li>)}
        </ul>
        {(view.after || view.page.next_cursor) && <div className="flex flex-wrap gap-2">
          {view.after && <Button variant="outline" className="min-h-11"
            onClick={() => act(() => showCertificates())}>First page</Button>}
          {view.page.next_cursor && <Button variant="outline" className="min-h-11"
            onClick={() => act(() => showCertificates(view.page.next_cursor ?? undefined))}>Next page</Button>}
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
                  : lesson.format === 'virtual_lab'
                    ? showVirtualLab(view.release, lesson, view.index.requires_first_use)
                    : showMedia(view.release, lesson, view.index.requires_first_use))}
              >
                {lesson.available && view.index.requires_first_use ? `Open ${lesson.name}` : lesson.name}
                <span className="ms-auto shrink-0 text-xs font-normal text-muted-foreground">
                  {lesson.format === 'text'
                    ? 'Text'
                    : lesson.format === 'pdf'
                      ? 'PDF'
                      : lesson.format === 'video'
                        ? 'Video'
                        : lesson.format === 'virtual_lab'
                          ? 'Virtual Lab'
                          : 'Unsupported'}
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
                        : lesson.unavailable_reason === 'release_lab_unavailable'
                          ? 'This virtual laboratory build is not currently authorized in this release.'
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
        {adaptiveRelease && <ManagedLessonTutor
          orgUuid={orgUuid}
          activityUuid={view.lesson.activity_uuid}
          release={adaptiveRelease}
        />}
        {adaptiveRelease && <AdaptiveAttemptPanel orgUuid={orgUuid} activityUuid={view.lesson.activity_uuid}
          release={adaptiveRelease} onOpenRecommendation={openManagedRecommendation} />}
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
        {view.lesson.format === 'pdf' && <ManagedPdfTopicCheck key={mediaUrl} orgUuid={orgUuid} token={token} learnerUuid={learnerUuid}
          courseUuid={view.release.course_uuid} releaseUuid={view.release.release_uuid} activityUuid={view.lesson.activity_uuid} />}
      </>
      break
    }
    case 'virtual_lab': {
      const { lab } = view
      title = lab.name
      const safeUrl = isApprovedLabEntryUrl(lab.entry_url)
      content = <>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" className="min-h-11" onClick={() => act(() => showLessons(view.release))}>
            Back to lessons
          </Button>
          {lab.linked_assignment && (
            <Button
              className="min-h-11"
              onClick={() => act(() => showLinkedAssignment(view.release, lab.linked_assignment!.activity_uuid, lab))}
            >
              Open practical assignment: {lab.linked_assignment.title}
            </Button>
          )}
        </div>
        <div className="space-y-2 rounded-lg border p-3 text-sm text-muted-foreground">
          <p>
            Pinned build <code className="font-mono text-xs">{lab.build_version}</code> · SHA-256{' '}
            <code className="font-mono text-xs">{lab.build_sha256.slice(0, 12)}</code> · Units: {lab.units_system}
          </p>
          <p>
            Opening or closing this virtual laboratory does not automatically record course completion or independent mastery.
            {lab.submission_formats_note ? ` ${lab.submission_formats_note}` : ''}
          </p>
        </div>
        {safeUrl ? (
          <ManagedVirtualLabFrame lab={lab} />
        ) : (
          <p role="alert" className="rounded-lg border border-destructive p-4 text-sm text-destructive">
            This virtual laboratory entry URL did not pass client safety verification.
          </p>
        )}
      </>
      break
    }
    case 'assignment': {
      const { assignment, returnLab } = view
      title = assignment.title
      content = <>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" className="min-h-11" onClick={() => act(() => showLessons(view.release))}>
            Back to lessons
          </Button>
          {returnLab && (
            <Button variant="outline" className="min-h-11" onClick={() => setView({ kind: 'virtual_lab', release: view.release, lab: returnLab })}>
              Back to virtual lab
            </Button>
          )}
        </div>
        <ManagedLinkedAssignmentPanel
          orgUuid={orgUuid}
          token={token}
          assignment={assignment}
          onAssignmentUpdated={nextAssignment =>
            setView({ kind: 'assignment', release: view.release, assignment: nextAssignment, returnLab })
          }
        />
      </>
      break
    }
    case 'unavailable':
      if (view.section === 'certificates') title = 'My certificates'
      content = <>
        <p role="status">{view.section === 'certificates'
          ? 'Certificates are unavailable for this account.' : 'Learning is unavailable for this account.'}</p>
        <Button variant="outline" className="min-h-11"
          onClick={() => act(() => view.section === 'certificates' ? showCertificates() : showCatalog())}>Try again</Button>
        <Button variant="outline" className="min-h-11"
          onClick={() => act(() => view.section === 'certificates' ? showCatalog() : showCertificates())}>
          {view.section === 'certificates' ? 'Back to my learning' : 'My certificates'}
        </Button>
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

function ManagedLessonTutor({
  orgUuid,
  activityUuid,
  release,
}: {
  orgUuid: string
  activityUuid: string
  release: { course_uuid: string; course_release_uuid: string }
}) {
  const tutor = useLessonTutor(orgUuid, activityUuid, release)
  return <LessonTutorContent tutor={tutor} activityUuid={activityUuid} />
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

function ManagedVirtualLabFrame({ lab }: { lab: VirtualLabLesson }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [expanded, setExpanded] = useState(false)

  function toggleFullscreen() {
    if (typeof document === 'undefined') return
    if (!document.fullscreenElement) {
      void containerRef.current?.requestFullscreen?.().then(() => setExpanded(true)).catch(() => {})
    } else {
      void document.exitFullscreen?.().then(() => setExpanded(false)).catch(() => {})
    }
  }

  return (
    <div ref={containerRef} className="space-y-2 rounded-lg border bg-card p-2">
      <div className="flex flex-wrap items-center justify-between gap-2 px-1">
        <span className="text-xs font-medium text-muted-foreground">
          Isolated simulation frame ({lab.lab_id})
        </span>
        <Button type="button" variant="outline" size="sm" className="min-h-10" onClick={toggleFullscreen}>
          {expanded ? 'Exit fullscreen' : 'Fullscreen lab'}
        </Button>
      </div>
      <div className="relative h-[70vh] min-h-[480px] w-full overflow-hidden rounded-md bg-slate-950">
        <iframe
          key={`${lab.release_uuid}:${lab.activity_uuid}:${lab.build_sha256}`}
          src={lab.entry_url}
          title={lab.name}
          className="h-full w-full border-0"
          sandbox="allow-scripts allow-downloads"
          allow="fullscreen"
          allowFullScreen
          referrerPolicy="no-referrer"
        />
      </div>
    </div>
  )
}
