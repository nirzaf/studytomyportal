import { getAPIUrl } from '@services/config/config'
import { secureFetch } from '@services/utils/ts/requests'

export type CourseEvidence = {
  evidence_uuid: string
  revision_sha256: string
  purpose: 'content_rights' | 'content_accessibility'
  verified_at: string
}

export type CourseReviewCandidate = {
  revision_sha256: string
  evidence: CourseEvidence[]
  next_before_id: number | null
  current_review?: CourseReviewReceipt | null
}

type CourseReviewReceipt = {
  uuid: string
  revision_sha256: string
  decision: 'approved' | 'changes_requested'
}

export class CourseReviewError extends Error {
  readonly code?: string

  constructor(code?: string) {
    super('Course review request failed')
    this.code = code
  }
}

export function courseReviewClient(orgUuid: string, courseUuid: string, token: string) {
  const base = `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/courses/${encodeURIComponent(courseUuid)}`
  async function request(path: string, signal: AbortSignal, options?: RequestInit) {
    const headers = new Headers(options?.headers)
    headers.set('Authorization', `Bearer ${token}`)
    const response = await secureFetch(`${base}/${path}`, {
      ...options, headers, signal, cache: 'no-store',
    })
    if (!response.ok) {
      const data = await response.json().catch(() => null)
      throw new CourseReviewError(data?.detail?.code)
    }
    return response
  }
  return {
    async candidate(signal: AbortSignal, before?: number): Promise<CourseReviewCandidate> {
      const suffix = before === undefined ? '' : `?before_id=${before}`
      return (await request(`content-review-candidate${suffix}`, signal)).json()
    },
    async decide(body: object, key: string, signal: AbortSignal): Promise<CourseReviewReceipt> {
      return (await request('content-review-decisions', signal, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key },
        body: JSON.stringify(body),
      })).json()
    },
    async upload(body: FormData, key: string, signal: AbortSignal): Promise<CourseEvidence> {
      return (await request('evidence-files', signal, {
        method: 'POST',
        headers: { 'Idempotency-Key': key },
        body,
      })).json()
    },
    async attachment(id: string, signal: AbortSignal): Promise<Blob> {
      return (await request(`evidence-files/${encodeURIComponent(id)}/content`, signal)).blob()
    },
  }
}

export type CourseReviewClient = ReturnType<typeof courseReviewClient>
