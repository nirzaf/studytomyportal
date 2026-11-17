import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'

export type DefinitionKind = 'curriculum' | 'item' | 'diagnostic'
export type Definition = {
  uuid: string
  label: string
  status: 'draft' | 'published' | 'retired'
  version: number
  curriculum_uuid: string
  concept_uuid: string | null
  independent: boolean
  content_digest: string | null
}

export type AdaptiveConfiguration = {
  can_author: boolean
  can_review: boolean
  curricula: Definition[]
  concepts: { uuid: string; title: string; curriculum_uuid: string }[]
  items: Definition[]
  diagnostics: Definition[]
  activities: { uuid: string; name: string; version: number }[]
}

export type Mapping = {
  activity_uuid: string
  activity_version: number
  course_uuid: string
  usage: 'teach' | 'practice' | 'review'
}

type ConceptDraft = { code: string; title: string; objective: string; sequence: number }
type Prerequisite = { concept_code: string; prerequisite_code: string }

export type CurriculumReview = {
  curriculum: { uuid: string; version: number }
  source_reference: string
  rights_reference: string
  concepts: (ConceptDraft & { uuid: string; archived: boolean })[]
  prerequisites: Prerequisite[]
  mappings: (Mapping & { concept_uuid: string; archived: boolean })[]
  content_digest: string
}

export type Question = {
  prompt: string
  kind: 'number' | 'single_choice'
  unit?: string
  options?: { option_uuid: string; text: string }[]
}

export type AttemptPurpose = 'diagnostic' | 'practice'
export type IssuedQuestion = {
  uuid: string
  ordinal: number
  question: Question
  allowed_actions: ('answer' | 'skip')[]
}
export type AdaptiveAttempt = {
  uuid: string
  purpose: AttemptPurpose
  status: 'active' | 'completed' | 'abandoned' | 'expired' | 'invalidated'
  version: number
  answered_count: number
  skipped_count: number
  current_item: IssuedQuestion | null
}

export function getCurrentAdaptiveAttempt(
  request: AdaptiveRequest, activityUuid: string, purpose: AttemptPurpose, signal?: AbortSignal,
): Promise<AdaptiveAttempt | null> {
  const params = new URLSearchParams({ source_activity_uuid: activityUuid, purpose })
  return request<AdaptiveAttempt | null>(`me/attempts/current?${params}`, 'GET', undefined, undefined, signal)
}

export type NativePreview = {
  source_digest: string
  question: Question
}

export type ItemReview = {
  item: { question: Question }
  content_digest: string
  rights_reference: string
  allowed_use: 'practice_only' | 'independent_diagnostic'
}
export type DiagnosticReview = {
  content_digest: string
  manifest: {
    required_concepts: string[]
    pool: { uuid: string; concept_uuid: string }[]
    limits: { minimum_scored_per_concept: number }
  }
}

export class AdaptiveRequestError extends Error {
  constructor(readonly status: number, readonly code: string | undefined, message: string) {
    super(message)
  }
}

export type AdaptiveRequest = <T>(
  path: string, method?: 'GET' | 'POST' | 'PUT', body?: object, key?: string, signal?: AbortSignal
) => Promise<T>

export function adaptiveRequest(orgUuid: string, accessToken: string): AdaptiveRequest {
  return async <T,>(
    path: string, method = 'GET', body?: object, key?: string, signal?: AbortSignal,
  ): Promise<T> => {
    const options = RequestBodyWithAuthHeader(method, body ?? null, null, accessToken)
    const headers = new Headers(options.headers)
    if (key) headers.set('Idempotency-Key', key)
    const response = await secureFetch(
      `${getAPIUrl()}studytomy/adaptive/orgs/${encodeURIComponent(orgUuid)}/${path}`,
      { ...options, headers, cache: 'no-store', signal },
    )
    const data = await response.json().catch(() => null)
    if (!response.ok) {
      const detail = data?.detail
      const validation = Array.isArray(detail)
        ? detail.map(({ loc, msg }) => `${loc?.slice(1).join(' · ')} — ${msg}`).join(' ')
        : undefined
      throw new AdaptiveRequestError(
        response.status,
        detail?.code,
        (typeof detail === 'string' ? detail : detail?.message) ||
          validation || 'Unable to complete the adaptive configuration request.',
      )
    }
    return data as T
  }
}
