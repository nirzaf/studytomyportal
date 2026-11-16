export const AUTH_BROADCAST_CHANNEL = 'learnhouse_auth_sync'
export const AUTH_STORAGE_SYNC_KEY = 'lh_auth_sync_event'

const LEGACY_LOGIN_DEDUPE_WINDOW_MS = 1000
const SEEN_NONCE_TTL_MS = 60_000

export type AuthSyncType = 'LOGIN' | 'LOGOUT'

type AuthSyncMessage = {
  type: AuthSyncType
  at: number
  nonce: string
}

export function publishAuthSync(
  type: AuthSyncType,
  channel?: Pick<BroadcastChannel, 'postMessage'> | null,
): void {
  const message: AuthSyncMessage = {
    type,
    at: Date.now(),
    nonce: Math.random().toString(36).slice(2),
  }

  try {
    channel?.postMessage(message)
  } catch {
    // The storage-event path below remains available if a channel has closed.
  }

  if (typeof window === 'undefined') return
  try {
    // The event carries no identity or credential data. Storage events cover
    // browsers where BroadcastChannel is unavailable.
    window.localStorage.setItem(AUTH_STORAGE_SYNC_KEY, JSON.stringify(message))
  } catch {
    // Local storage can be disabled; the current tab and channel still work.
  }
}

export function createAuthSyncReceiver(
  onEvent: (_type: AuthSyncType) => void,
  now: () => number = Date.now,
): (_message: unknown) => void {
  const seenNonces = new Map<string, number>()
  let lastLegacyLoginAt: number | null = null

  return (message) => {
    const payload = message && typeof message === 'object'
      ? message as { type?: unknown; nonce?: unknown }
      : { type: message }
    const type = payload.type
    if (type !== 'LOGIN' && type !== 'LOGOUT') return

    const currentTime = now()
    if (typeof payload.nonce === 'string') {
      if (seenNonces.has(payload.nonce)) return
      seenNonces.set(payload.nonce, currentTime)
      for (const [nonce, timestamp] of seenNonces) {
        if (currentTime - timestamp > SEEN_NONCE_TTL_MS) seenNonces.delete(nonce)
      }
    } else if (type === 'LOGIN') {
      // Older tabs emit bare LOGIN messages over BroadcastChannel only.
      if (
        lastLegacyLoginAt !== null
        && currentTime - lastLegacyLoginAt < LEGACY_LOGIN_DEDUPE_WINDOW_MS
      ) return
      lastLegacyLoginAt = currentTime
    }

    onEvent(type)
  }
}
