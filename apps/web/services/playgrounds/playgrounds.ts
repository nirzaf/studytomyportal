import { getAPIUrl } from '@services/config/config'
import type { PlaygroundLearnerAdaptation } from '@services/playgrounds/generator'
import {
  RequestBodyWithAuthHeader,
  errorHandling,
  secureFetch,
} from '@services/utils/ts/requests'

export type PlaygroundAccessType = 'public' | 'authenticated' | 'restricted'

export interface Playground {
  id: number
  org_id: number
  org_uuid?: string | null
  org_slug?: string | null
  playground_uuid: string
  name: string
  description?: string | null
  thumbnail_image?: string | null
  access_type: PlaygroundAccessType
  published: boolean
  course_uuid?: string | null
  course_id?: number | null
  html_content?: string | null
  learner_adaptation?: PlaygroundLearnerAdaptation | null
  created_by?: number | null
  author_username?: string | null
  author_first_name?: string | null
  author_last_name?: string | null
  author_user_uuid?: string | null
  author_avatar_image?: string | null
  creation_date: string
  update_date: string
}

export interface PlaygroundCreate {
  name: string
  description?: string
  thumbnail_image?: string
  access_type?: PlaygroundAccessType
  course_uuid?: string
  html_content?: string
  learner_adaptation?: PlaygroundLearnerAdaptation
}

export interface PlaygroundUpdate {
  name?: string
  description?: string
  thumbnail_image?: string
  access_type?: PlaygroundAccessType
  published?: boolean
  course_uuid?: string
  html_content?: string
  learner_adaptation?: PlaygroundLearnerAdaptation
}

export async function createPlayground(
  orgId: number,
  data: PlaygroundCreate,
  access_token: string
): Promise<Playground> {
  const result = await fetch(
    `${getAPIUrl()}playgrounds/?org_id=${orgId}`,
    RequestBodyWithAuthHeader('POST', data, null, access_token)
  )
  return errorHandling(result)
}

export async function getPlayground(
  playgroundUuid: string,
  access_token?: string
): Promise<Playground> {
  const result = await fetch(
    `${getAPIUrl()}playgrounds/${playgroundUuid}`,
    RequestBodyWithAuthHeader('GET', null, null, access_token)
  )
  return errorHandling(result)
}

export async function getOrgPlaygrounds(
  orgId: number,
  access_token?: string
): Promise<Playground[]> {
  const result = await fetch(
    `${getAPIUrl()}playgrounds/org/${orgId}`,
    RequestBodyWithAuthHeader('GET', null, null, access_token)
  )
  return errorHandling(result)
}

export async function updatePlayground(
  playgroundUuid: string,
  data: PlaygroundUpdate,
  access_token: string
): Promise<Playground> {
  const result = await fetch(
    `${getAPIUrl()}playgrounds/${playgroundUuid}`,
    RequestBodyWithAuthHeader('PUT', data, null, access_token)
  )
  return errorHandling(result)
}

export async function deletePlayground(
  playgroundUuid: string,
  access_token: string
): Promise<void> {
  const result = await fetch(
    `${getAPIUrl()}playgrounds/${playgroundUuid}`,
    RequestBodyWithAuthHeader('DELETE', null, null, access_token)
  )
  return errorHandling(result)
}

export async function duplicatePlayground(
  playgroundUuid: string,
  access_token: string
): Promise<Playground> {
  const result = await fetch(
    `${getAPIUrl()}playgrounds/${playgroundUuid}/duplicate`,
    RequestBodyWithAuthHeader('POST', null, null, access_token)
  )
  return errorHandling(result)
}

export async function updatePlaygroundThumbnail(
  playgroundUuid: string,
  file: File,
  access_token: string
): Promise<Playground> {
  const formData = new FormData()
  formData.append('thumbnail', file)
  const result = await fetch(
    `${getAPIUrl()}playgrounds/${playgroundUuid}/thumbnail`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${access_token}`,
      },
      body: formData,
    }
  )
  return errorHandling(result)
}

export async function addUserGroupToPlayground(
  playgroundUuid: string,
  usergroupUuid: string,
  access_token: string
): Promise<void> {
  const result = await fetch(
    `${getAPIUrl()}playgrounds/${playgroundUuid}/usergroups/${usergroupUuid}`,
    RequestBodyWithAuthHeader('POST', null, null, access_token)
  )
  return errorHandling(result)
}

export async function removeUserGroupFromPlayground(
  playgroundUuid: string,
  usergroupUuid: string,
  access_token: string
): Promise<void> {
  const result = await fetch(
    `${getAPIUrl()}playgrounds/${playgroundUuid}/usergroups/${usergroupUuid}`,
    RequestBodyWithAuthHeader('DELETE', null, null, access_token)
  )
  return errorHandling(result)
}

// ── Reactions ────────────────────────────────────────────────────────────────

export interface PlaygroundReactionUser {
  id: number
  user_uuid: string
  username: string
  first_name: string | null
  last_name: string | null
  avatar_image: string | null
}

export interface PlaygroundReactionSummary {
  emoji: string
  count: number
  users: PlaygroundReactionUser[]
  has_reacted: boolean
}

export async function getPlaygroundReactions(
  playgroundUuid: string,
  access_token?: string
): Promise<PlaygroundReactionSummary[]> {
  const result = await fetch(
    `${getAPIUrl()}playgrounds/${playgroundUuid}/reactions`,
    RequestBodyWithAuthHeader('GET', null, null, access_token)
  )
  return errorHandling(result)
}

export async function togglePlaygroundReaction(
  playgroundUuid: string,
  emoji: string,
  access_token: string
): Promise<{ action: 'added' | 'removed'; emoji: string }> {
  const result = await fetch(
    `${getAPIUrl()}playgrounds/${playgroundUuid}/reactions`,
    RequestBodyWithAuthHeader('POST', { emoji }, null, access_token)
  )
  return errorHandling(result)
}

// ── Studytomy independent content review ────────────────────────────────────

export interface PlaygroundReviewStatus {
  playground_uuid: string
  governed: boolean
  published: boolean
  deliverable: boolean
  content_digest: string
  latest_decision: 'approved' | 'rejected' | null
  latest_reviewed_digest: string | null
  current_revision_approved: boolean
  can_review: boolean
  is_creator: boolean
}

export interface PlaygroundReviewDecisionRequest {
  expected_digest: string
  decision: 'approved' | 'rejected'
  rights_confirmed: boolean
  accessibility_confirmed: boolean
  age_suitability_confirmed: boolean
  evidence_reference: string
}

export interface PlaygroundReviewReceipt {
  review_event_uuid: string
  playground_uuid: string
  decision: 'approved' | 'rejected'
  content_digest: string
}

export class PlaygroundReviewError extends Error {
  readonly status: number
  readonly code?: string

  constructor(status: number, message: string, code?: string) {
    super(message)
    this.name = 'PlaygroundReviewError'
    this.status = status
    this.code = code
  }
}

export async function getPlaygroundReviewStatus(
  orgUuid: string,
  playgroundUuid: string,
  accessToken: string
): Promise<PlaygroundReviewStatus> {
  const response = await secureFetch(
    `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/playgrounds/${encodeURIComponent(playgroundUuid)}/content-review-status`,
    { ...RequestBodyWithAuthHeader('GET', null, null, accessToken), cache: 'no-store' }
  )
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    const detail = body?.detail
    throw new PlaygroundReviewError(
      response.status,
      typeof detail === 'string'
        ? detail
        : detail?.message || 'Content review status is unavailable.',
      typeof detail === 'object' ? detail?.code : undefined
    )
  }
  return response.json()
}

export async function submitPlaygroundReviewDecision(
  orgUuid: string,
  playgroundUuid: string,
  command: PlaygroundReviewDecisionRequest,
  accessToken: string,
  idempotencyKey: string
): Promise<PlaygroundReviewReceipt> {
  const url = `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/playgrounds/${encodeURIComponent(playgroundUuid)}/content-review-decisions`
  const options = RequestBodyWithAuthHeader('POST', command, null, accessToken)
  const headers = new Headers(options.headers)
  headers.set('Idempotency-Key', idempotencyKey)
  options.headers = headers
  const response = await secureFetch(url, options)
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    const detail = body?.detail
    throw new PlaygroundReviewError(
      response.status,
      typeof detail === 'string'
        ? detail
        : detail?.message || 'Unable to record this content review decision.',
      typeof detail === 'object' ? detail?.code : undefined
    )
  }
  return response.json()
}
