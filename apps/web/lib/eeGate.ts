import 'server-only'
import type { InstanceMode } from './instanceMode'

export { fetchInstanceMode } from './instanceMode'
export type { InstanceMode } from './instanceMode'

/**
 * The whole gate policy, in one pure function so it can be tested directly.
 *
 * Blocks only on a definitive 'oss'. A null mode means the lookup failed, and
 * that renders the dashboard: failing closed would lock real SaaS superadmins
 * out of /admin during any brief API blip, which is exactly when they need
 * it. The API-side check is what actually enforces this; the web gate exists
 * to show a clean message instead of dead chrome.
 */
export function isSuperadminSurfaceBlocked(mode: InstanceMode | null): boolean {
  return mode === 'oss'
}
