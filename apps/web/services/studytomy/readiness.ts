import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader } from '@services/utils/ts/requests'
import type { StudyCourseReadiness, StudyOfferKind } from '@/types/studytomy'

export async function getCourseOfferReadiness(
  orgUuid: string,
  courseUuid: string,
  accessToken: string,
  offerKind: StudyOfferKind = 'course'
): Promise<StudyCourseReadiness> {
  const query = new URLSearchParams({ offer_kind: offerKind })
  const response = await fetch(
    `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/courses/${encodeURIComponent(courseUuid)}/readiness?${query}`,
    RequestBodyWithAuthHeader('GET', null, null, accessToken)
  )
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = body?.detail
    throw new Error(typeof detail === 'string' ? detail : detail?.message || 'Unable to load course readiness.')
  }
  return body as StudyCourseReadiness
}
