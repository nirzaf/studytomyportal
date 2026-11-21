import { getAPIUrl } from '@services/config/config'
import { getResponseMetadata } from '@services/utils/ts/requests'
import type { CourseDraft, DraftApi, DraftFields, SaveResult } from '@/lib/courses/courseDraftSync'

const baseUrl = (orgId: number | string) => `${getAPIUrl()}course-drafts/orgs/${orgId}/me`

const authHeaders = (token?: string): Record<string, string> =>
  token ? { Authorization: `Bearer ${token}` } : {}

function describeError(data: any, fallback: string): string {
  const detail = data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map((e: any) => e?.msg).filter(Boolean).join(', ') || fallback
  if (detail && typeof detail.message === 'string') return detail.message
  return fallback
}

async function toSaveResult(res: Response): Promise<SaveResult> {
  const meta = await getResponseMetadata(res)
  if (meta.success) return { ok: true, draft: meta.data as CourseDraft }
  if (res.status === 409 && meta.data?.detail?.code === 'COURSE_DRAFT_CONFLICT') {
    return { ok: false, conflict: true, current: (meta.data.detail.draft as CourseDraft | null) ?? null }
  }
  return { ok: false, conflict: false, error: describeError(meta.data, res.statusText || 'Request failed') }
}

export function createCourseDraftApi(orgId: number | string, getToken: () => string | undefined): DraftApi {
  return {
    async saveFields(fields: DraftFields, baseRevision: number | null) {
      const res = await fetch(baseUrl(orgId), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', ...authHeaders(getToken()) },
        credentials: 'include',
        cache: 'no-store',
        body: JSON.stringify({ ...fields, base_revision: baseRevision }),
      })
      return toSaveResult(res)
    },
    async uploadThumbnail(file: File, baseRevision: number | null) {
      const form = new FormData()
      form.append('thumbnail', file)
      if (baseRevision !== null) form.append('base_revision', String(baseRevision))
      const res = await fetch(`${baseUrl(orgId)}/thumbnail`, {
        method: 'PUT',
        headers: authHeaders(getToken()),
        credentials: 'include',
        cache: 'no-store',
        body: form,
      })
      return toSaveResult(res)
    },
  }
}

export type LoadResult = { ok: true; draft: CourseDraft | null } | { ok: false; error: string }

export async function getCourseDraft(orgId: number | string, token?: string): Promise<LoadResult> {
  try {
    const res = await fetch(baseUrl(orgId), {
      method: 'GET',
      headers: authHeaders(token),
      credentials: 'include',
      cache: 'no-store',
    })
    const meta = await getResponseMetadata(res)
    if (!meta.success) return { ok: false, error: describeError(meta.data, res.statusText || 'Request failed') }
    return { ok: true, draft: (meta.data as CourseDraft | null) ?? null }
  } catch (_e) {
    return { ok: false, error: 'Network error' }
  }
}

export type DeleteResult =
  | { ok: true }
  | { ok: false; conflict: boolean; current?: CourseDraft | null; error?: string }

/** `baseRevision` null discards unconditionally (used after the course exists). */
export async function deleteCourseDraft(
  orgId: number | string,
  baseRevision: number | null,
  token?: string
): Promise<DeleteResult> {
  try {
    const query = baseRevision === null ? '' : `?base_revision=${baseRevision}`
    const res = await fetch(`${baseUrl(orgId)}${query}`, {
      method: 'DELETE',
      headers: authHeaders(token),
      credentials: 'include',
      cache: 'no-store',
    })
    if (res.ok) return { ok: true }
    const result = await toSaveResult(res)
    if (result.ok) return { ok: true }
    return result.conflict
      ? { ok: false, conflict: true, current: result.current }
      : { ok: false, conflict: false, error: result.error }
  } catch (_e) {
    return { ok: false, conflict: false, error: 'Network error' }
  }
}

const EXTENSION_BY_TYPE: Record<string, string> = {
  'image/jpeg': 'jpg',
  'image/png': 'png',
  'image/gif': 'gif',
  'image/webp': 'webp',
}

/** Download the saved thumbnail as a File so the unchanged create call can upload it. */
export async function fetchCourseDraftThumbnail(
  orgId: number | string,
  token?: string
): Promise<File | null> {
  const res = await fetch(`${baseUrl(orgId)}/thumbnail`, {
    method: 'GET',
    headers: authHeaders(token),
    credentials: 'include',
    cache: 'no-store',
  })
  if (res.status === 404) return null
  if (!res.ok) throw new Error(res.statusText || 'Failed to load draft thumbnail')
  const blob = await res.blob()
  const type = blob.type || 'image/png'
  return new File([blob], `draft_thumbnail.${EXTENSION_BY_TYPE[type] ?? 'png'}`, { type })
}
