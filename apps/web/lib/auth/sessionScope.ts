const ORGANIZATION_SLUG = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i
const unknownSessionIdentities = new WeakMap<object, number>()
let nextUnknownSessionIdentity = 0

/**
 * Return a private cache partition for the authenticated account. If an
 * authenticated session is missing its normal user identifier, use the
 * session object's identity so separate accounts cannot share an "unknown"
 * cache key. The fallback is deliberately not an authorization input.
 */
export function getAuthenticatedSessionIdentity(
  userId: unknown,
  sessionData: object | null | undefined,
): string {
  if (typeof userId === 'string' && userId.length > 0) {
    return `authenticated:string:${userId}`
  }
  if (typeof userId === 'number' && Number.isFinite(userId)) {
    return `authenticated:number:${userId}`
  }
  if (!sessionData) return 'authenticated:unknown:missing-session'

  let opaqueIdentity = unknownSessionIdentities.get(sessionData)
  if (opaqueIdentity === undefined) {
    opaqueIdentity = ++nextUnknownSessionIdentity
    unknownSessionIdentities.set(sessionData, opaqueIdentity)
  }
  return `authenticated:unknown:${opaqueIdentity}`
}

/**
 * Resolve the organization partition for browser-only session state.
 *
 * The App Router params describe the matched route, including tenant routes
 * reached through the proxy rewrite. The browser pathname can still be the
 * public, pre-rewrite URL, so it is only a fallback. This is a cache key, not
 * an authorization input; API permissions must use server-validated tenancy.
 */
export function getOrganizationScope(
  pathname: string,
  routeParams?: Record<string, string | string[]>,
): string {
  const routeSlug = routeParams?.orgslug
  if (typeof routeSlug === 'string' && ORGANIZATION_SLUG.test(routeSlug)) {
    return routeSlug
  }

  const pathSlug = pathname.match(/^\/(?:orgs|embed)\/([^/]+)/)?.[1]
  return pathSlug && ORGANIZATION_SLUG.test(pathSlug) ? pathSlug : 'global'
}


// Tutor recovery stores opaque operation references only. Drop them on logout.
export function clearAdaptiveTutorRecovery(storage: Storage): void {
  for (let index = storage.length - 1; index >= 0; index--) {
    const key = storage.key(index)
    if (key?.startsWith('lesson-tutor:')) storage.removeItem(key)
  }
}
