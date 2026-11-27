import { getServerAPIUrl } from '@services/config/config'

export type InstanceMode = 'saas' | 'oss' | 'ee'

/**
 * Resolve the deployment mode without trusting anything client-supplied.
 *
 * The URL here comes from server env, never from the request, so nothing on
 * this path is caller-controlled. lib/saas.ts::getInstanceMode() caches on top
 * of this for the SaaS-only integrations.
 */
export async function fetchInstanceMode(): Promise<InstanceMode | null> {
  try {
    const res = await fetch(`${getServerAPIUrl()}instance/info`, {
      cache: 'no-store',
      signal: AbortSignal.timeout(4000),
    })
    if (!res.ok) return null
    const info = await res.json()
    const mode = info?.mode
    return mode === 'saas' || mode === 'oss' || mode === 'ee' ? mode : null
  } catch {
    return null
  }
}
