'use client'

import Link from 'next/link'
import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@components/ui/button'
import { getUriWithOrg } from '@services/config/config'
import type { AdaptiveRequest } from '@services/studytomy/adaptive'

type Concept = {
  concept_uuid: string
  title: string
  state: 'not_assessed' | 'insufficient_evidence' | 'developing' | 'mastered'
  independent_family_count: number
  assisted_count: number
  review_due: boolean
}
type MasteryPage = { concepts: Concept[]; next_offset: number | null }
type Recommendation = {
  activity_uuid: string
  course_uuid: string
  activity_title: string
  reason: 'prerequisite_gap' | 'practice_needed' | 'not_yet_assessed' | 'review_due'
} | { reason: 'no_accessible_activity'; support_action: 'return_to_lesson' | null }

export default function AdaptiveLessonProgress({ client, activityUuid, orgSlug }: {
  client: AdaptiveRequest
  activityUuid: string
  orgSlug: string
}) {
  const { t } = useTranslation()
  const contentId = useId()
  const buttonId = useId()
  const [available, setAvailable] = useState(false)
  const [open, setOpen] = useState(false)
  const [offset, setOffset] = useState(0)
  const [page, setPage] = useState<MasteryPage | null>(null)
  const [error, setError] = useState(false)
  const [recommendations, setRecommendations] = useState<Recommendation[] | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    const params = new URLSearchParams({ activity_uuid: activityUuid, offset: String(offset) })
    client<MasteryPage>(`me/lesson-mastery?${params}`, 'GET', undefined, undefined, controller.signal)
      .then(result => {
        if (controller.signal.aborted) return
        setAvailable(true)
        setPage(result)
      })
      .catch(() => {
        if (controller.signal.aborted) return
        setError(true)
      })
    return () => controller.abort()
  }, [activityUuid, client, offset, open])

  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    const params = new URLSearchParams({ activity_uuid: activityUuid })
    client<Recommendation[]>(`me/recommendations?${params}`, 'GET', undefined, undefined, controller.signal)
      .then(result => { if (!controller.signal.aborted) setRecommendations(result) })
      .catch(() => { if (!controller.signal.aborted) setRecommendations([]) })
    return () => controller.abort()
  }, [activityUuid, client, open])

  function requestPage(next: number) {
    setPage(null)
    setError(false)
    setOffset(next)
  }

  function toggle() {
    requestPage(0)
    setRecommendations(null)
    setOpen(!open)
  }

  if (!available) return null
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
  const suggestions = recommendations?.filter(item => item.reason !== 'no_accessible_activity')
  const support = recommendations?.some(item =>
    item.reason === 'no_accessible_activity' && item.support_action === 'return_to_lesson')
  return <div className="rounded-lg border bg-background p-4">
    <Button id={buttonId} type="button" variant="outline" className="min-h-11" aria-expanded={open} aria-controls={contentId}
      onClick={toggle}>
      {t('adaptiveProgress.title', 'Concept progress')}
    </Button>
    <div id={contentId} role="region" aria-labelledby={buttonId} hidden={!open} className="space-y-4 pt-3">
      {error ? <p role="status">{t('adaptiveProgress.unavailable', 'Concept progress is unavailable.')}</p>
        : !page ? <p role="status">{t('adaptiveProgress.loading', 'Loading concept progress…')}</p>
        : <>
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
          <section>
            <h3 className="font-semibold">{t('adaptiveProgress.suggestions', 'Suggested activities')}</h3>
            {recommendations === null ? <p role="status">{t('adaptiveProgress.loadingSuggestions', 'Loading suggestions…')}</p>
              : suggestions?.length ? <ul className="space-y-2">
                {suggestions.map(item => {
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
          </section>
        </>}
    </div>
  </div>
}
