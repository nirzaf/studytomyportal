'use client'

import Link from 'next/link'
import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@components/ui/button'
import { getUriWithOrg } from '@services/config/config'
import { AdaptiveRequestError, type AdaptiveReleaseScope, type AdaptiveRequest } from '@services/studytomy/adaptive'

type Concept = {
  concept_uuid: string
  title: string
  state: 'not_assessed' | 'insufficient_evidence' | 'developing' | 'mastered'
  independent_family_count: number
  assisted_count: number
  review_due: boolean
}
type MasteryPage = { curriculum_revision_uuid?: string; concepts: Concept[]; next_offset: number | null }
type MasteryFailure = 'disabled' | 'approval' | 'no_record' | 'temporary'

function masteryFailure(error: unknown): MasteryFailure {
  if (error instanceof AdaptiveRequestError) {
    if (error.status === 403 && error.code === 'ADAPTIVE_DISABLED') return 'disabled'
    if ([403, 409].includes(error.status) && error.code === 'POLICY_NOT_APPROVED') return 'approval'
    if (error.status === 404) return 'no_record'
  }
  return 'temporary'
}
export type LessonRecommendation = {
  activity_uuid: string
  course_uuid: string
  course_release_uuid: string
  activity_title: string
  reason: 'prerequisite_gap' | 'practice_needed' | 'not_yet_assessed' | 'review_due'
}
type Recommendation = LessonRecommendation | { reason: 'no_accessible_activity'; support_action: 'return_to_lesson' | null }

export default function AdaptiveLessonProgress({ client, activityUuid, orgSlug, release, refreshKey = 0, onOpenRecommendation }: {
  client: AdaptiveRequest
  activityUuid: string
  orgSlug?: string
  release?: AdaptiveReleaseScope
  refreshKey?: number
  onOpenRecommendation?: (_recommendation: LessonRecommendation) => Promise<boolean>
}) {
  const { t } = useTranslation()
  const contentId = useId()
  const buttonId = useId()
  const [available, setAvailable] = useState(false)
  const [open, setOpen] = useState(false)
  const [offset, setOffset] = useState(0)
  const [curriculumView, setCurriculumView] = useState<{
    client: AdaptiveRequest; lessonScope: string; revision: string
  } | null>(null)
  const [pageResult, setPageResult] = useState<{ client: AdaptiveRequest; queryKey: string; page: MasteryPage } | null>(null)
  const [failedPage, setFailedPage] = useState<{ queryKey: string; reason: MasteryFailure } | null>(null)
  const [masteryRetry, setMasteryRetry] = useState(0)
  const [recommendationResult, setRecommendationResult] = useState<{
    queryKey: string; recommendations: Recommendation[]
  } | null>(null)
  const [failedRecommendationQuery, setFailedRecommendationQuery] = useState<string | null>(null)
  const [recommendationRetry, setRecommendationRetry] = useState(0)
  const [openingActivity, setOpeningActivity] = useState<string | null>(null)
  const [recommendationNotice, setRecommendationNotice] = useState('')
  const lessonScope = JSON.stringify([activityUuid, release?.course_uuid, release?.course_release_uuid])
  const curriculumRevision = curriculumView?.client === client && curriculumView.lessonScope === lessonScope
    ? curriculumView.revision : undefined
  const pageQueryKey = JSON.stringify([
    activityUuid, offset, open, refreshKey, masteryRetry, release?.course_uuid ?? null,
    release?.course_release_uuid ?? null, curriculumRevision,
  ])
  const recommendationQueryKey = JSON.stringify([
    activityUuid, open, refreshKey, recommendationRetry, orgSlug ?? null, release?.course_uuid ?? null,
    release?.course_release_uuid ?? null,
  ])
  const page = pageResult?.client === client && pageResult.queryKey === pageQueryKey ? pageResult.page : null
  const failure = failedPage?.queryKey === pageQueryKey ? failedPage.reason : null
  const setupUnavailable = failure !== null && failure !== 'temporary'
  const recommendations = recommendationResult?.queryKey === recommendationQueryKey
    ? recommendationResult.recommendations
    : null
  const recommendationError = failedRecommendationQuery === recommendationQueryKey

  useEffect(() => {
    const controller = new AbortController()
    const params = curriculumRevision
      ? new URLSearchParams({ curriculum_revision_uuid: curriculumRevision, offset: String(offset) })
      : new URLSearchParams({ activity_uuid: activityUuid, offset: String(offset), ...release })
    const endpoint = curriculumRevision ? 'me/mastery' : 'me/lesson-mastery'
    client<MasteryPage>(`${endpoint}?${params}`, 'GET', undefined, undefined, controller.signal)
      .then(result => {
        if (controller.signal.aborted) return
        if (curriculumRevision && result.curriculum_revision_uuid !== curriculumRevision) {
          setFailedPage({ queryKey: pageQueryKey, reason: 'no_record' })
          return
        }
        setAvailable(true)
        setPageResult({ client, queryKey: pageQueryKey, page: result })
        setFailedPage(current => current?.queryKey === pageQueryKey ? null : current)
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setFailedPage({ queryKey: pageQueryKey, reason: masteryFailure(error) })
      })
    return () => controller.abort()
  }, [activityUuid, client, curriculumRevision, offset, open, pageQueryKey, release])

  useEffect(() => {
    if (!open || !available || setupUnavailable || (!release && !orgSlug)) return
    const controller = new AbortController()
    const params = new URLSearchParams({ activity_uuid: activityUuid, ...release })
    client<Recommendation[]>(`me/recommendations?${params}`, 'GET', undefined, undefined, controller.signal)
      .then(result => {
        if (controller.signal.aborted) return
        setRecommendationResult({ queryKey: recommendationQueryKey, recommendations: result })
        setFailedRecommendationQuery(null)
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailedRecommendationQuery(recommendationQueryKey)
      })
    return () => controller.abort()
  }, [activityUuid, available, client, open, orgSlug, recommendationQueryKey, release, setupUnavailable])

  function requestPage(next: number) {
    setOffset(next)
  }

  function toggle() {
    setCurriculumView(null)
    requestPage(0)
    setRecommendationNotice('')
    setOpeningActivity(null)
    setOpen(!open)
  }

  async function openRecommendation(recommendation: LessonRecommendation) {
    if (!release || !onOpenRecommendation || openingActivity) return
    setOpeningActivity(recommendation.activity_uuid)
    setRecommendationNotice('')
    try {
      const opened = await onOpenRecommendation(recommendation)
      if (!opened) setRecommendationNotice(t(
        'adaptiveProgress.targetUnavailable',
        'This suggested lesson is not available in the current release. Continue with this lesson for now.',
      ))
    } catch {
      setRecommendationNotice(t(
        'adaptiveProgress.targetUnavailable',
        'This suggested lesson is not available in the current release. Continue with this lesson for now.',
      ))
    } finally {
      setOpeningActivity(null)
    }
  }

  if (!available && !failure && !open) return null
  const masteryNotAvailable = {
    disabled: t('adaptiveProgress.disabled', 'Concept tracking is not enabled for this organization.'),
    approval: t('adaptiveProgress.approvalRequired', 'Concept tracking requires current learning approval and consent.'),
    no_record: curriculumRevision
      ? t('adaptiveProgress.noCurriculumRecord', 'No concept progress record is available for this curriculum and account.')
      : t('adaptiveProgress.noRecord', 'No concept progress record is available for this lesson and account.'),
  }
  const states = {
    not_assessed: t('adaptiveProgress.notAssessed', 'Not yet assessed'),
    insufficient_evidence: t('adaptiveProgress.insufficient', 'More evidence needed'),
    developing: t('adaptiveProgress.developing', 'Developing'),
    mastered: t('adaptiveProgress.mastered', 'Mastered'),
  }
  const reasons = {
    prerequisite_gap: t('adaptiveProgress.prerequisite', 'Build a prerequisite skill'),
    practice_needed: t('adaptiveProgress.practice', 'Practise this concept'),
    not_yet_assessed: t('adaptiveProgress.try', 'Try a concept you have not yet been assessed on'),
    review_due: t('adaptiveProgress.review', 'Review this concept'),
  }
  const suggestions = recommendations?.filter((item): item is LessonRecommendation => item.reason !== 'no_accessible_activity')
  const support = recommendations?.some(item =>
    item.reason === 'no_accessible_activity' && item.support_action === 'return_to_lesson')
  return <div className="rounded-lg border bg-background p-4">
    <Button id={buttonId} type="button" variant="outline" className="min-h-11" aria-expanded={open} aria-controls={contentId}
      onClick={toggle}>
      {t('adaptiveProgress.title', 'Concept progress')}
    </Button>
    <div id={contentId} role="region" aria-labelledby={buttonId} hidden={!open} className="space-y-4 pt-3">
      {(curriculumRevision || page?.curriculum_revision_uuid) && <Button type="button" variant="outline"
        className="min-h-11" onClick={() => {
          setOffset(0)
          setCurriculumView(curriculumRevision || !page?.curriculum_revision_uuid
            ? null : { client, lessonScope, revision: page.curriculum_revision_uuid })
        }}>
        {curriculumRevision
          ? t('adaptiveProgress.lessonConcepts', 'View lesson concepts')
          : t('adaptiveProgress.curriculumConcepts', 'View curriculum concepts')}
      </Button>}
      {failure === 'temporary' ? <div className="space-y-2">
        <p role="status">{t('adaptiveProgress.unavailable', 'Concept progress is unavailable.')}</p>
        <Button type="button" variant="outline" className="min-h-11" onClick={() => setMasteryRetry(current => current + 1)}>
          {t('adaptiveProgress.retryMastery', 'Retry concept progress')}
        </Button>
      </div>
        : failure ? <div className="space-y-2">
          <p role="status">{masteryNotAvailable[failure]}</p>
          {orgSlug && <Link href={getUriWithOrg(orgSlug, '/trail')} className="inline-flex min-h-11 items-center text-sm font-medium underline">
            {t('adaptiveProgress.courseProgress', 'View course progress')}
          </Link>}
        </div>
        : !page ? <p role="status">{t('adaptiveProgress.loading', 'Loading concept progress…')}</p>
        : <>
          {page.concepts.length === 0 && <p role="status">
            {t('adaptiveProgress.noConcepts', 'There are no tracked concepts on this page.')}
          </p>}
          <ul className="space-y-3">
            {page.concepts.map(concept => <li key={concept.concept_uuid} className="border-b pb-3 [overflow-wrap:anywhere]">
              <p className="font-semibold"><bdi>{concept.title}</bdi></p>
              <p>{states[concept.state]}</p>
              <p className="text-sm">
                {concept.independent_family_count === 0
                  ? t('adaptiveProgress.noEvidence', 'No independent evidence yet.')
                  : t('adaptiveProgress.evidence', 'Independent question families: {{count}}', { count: concept.independent_family_count })}
                {' '}
                {concept.assisted_count > 0 && t('adaptiveProgress.assisted', 'Supported practice does not count toward independent mastery.')}
                {' '}
                {concept.review_due && t('adaptiveProgress.reviewDue', 'Review due.')}
              </p>
            </li>)}
          </ul>
          {(offset > 0 || page.next_offset !== null) && <div className="flex flex-wrap gap-2">
            {offset > 0 && <Button type="button" variant="outline" className="min-h-11" onClick={() => requestPage(Math.max(0, offset - 20))}>
              {t('adaptiveProgress.previous', 'Previous concepts')}
            </Button>}
            {page.next_offset !== null && <Button type="button" variant="outline" className="min-h-11"
              onClick={() => { if (page.next_offset !== null) requestPage(page.next_offset) }}>
              {t('adaptiveProgress.next', 'Next concepts')}
            </Button>}
          </div>}
          {!curriculumRevision && (release || orgSlug) && <section>
            <h3 className="font-semibold">{t('adaptiveProgress.suggestions', 'Suggested activities')}</h3>
            {recommendationError ? <div className="space-y-2">
              <p role="status">{t('adaptiveProgress.suggestionsUnavailable', 'Suggested activities are unavailable.')}</p>
              <Button type="button" variant="outline" className="min-h-11"
                onClick={() => setRecommendationRetry(current => current + 1)}>
                {t('adaptiveProgress.retrySuggestions', 'Retry suggestions')}
              </Button>
            </div>
              : recommendations === null ? <p role="status">{t('adaptiveProgress.loadingSuggestions', 'Loading suggestions…')}</p>
              : suggestions?.length ? <ul className="space-y-2">
                {suggestions.map(item => {
                  if (release) return <li key={`${item.course_release_uuid}:${item.activity_uuid}`} className="[overflow-wrap:anywhere]">
                    <Button type="button" variant="outline" className="min-h-11 h-auto whitespace-normal text-start"
                      disabled={Boolean(openingActivity)} onClick={() => void openRecommendation(item)}>
                      <bdi>{item.activity_title}</bdi>
                    </Button>
                    <p className="text-sm">{reasons[item.reason]}</p>
                  </li>
                  if (!orgSlug) return null
                  const course = encodeURIComponent(item.course_uuid.replace(/^course_/, ''))
                  const activity = encodeURIComponent(item.activity_uuid.replace(/^activity_/, ''))
                  return <li key={item.activity_uuid} className="[overflow-wrap:anywhere]">
                    <Link className="inline-flex min-h-11 items-center underline" prefetch={false}
                      href={getUriWithOrg(orgSlug, `/course/${course}/activity/${activity}`)}>
                      <bdi>{item.activity_title}</bdi>
                    </Link>
                    <p className="text-sm">{reasons[item.reason]}</p>
                  </li>
                })}
              </ul> : <p>{support
                ? t('adaptiveProgress.return', 'Continue with this lesson for now.')
                : t('adaptiveProgress.none', 'No activities to suggest right now.')}</p>}
            {recommendationNotice && <p role="status">{recommendationNotice}</p>}
          </section>}
        </>}
    </div>
  </div>
}
