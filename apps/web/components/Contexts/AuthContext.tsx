'use client'

import React, {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  useMemo,
  useRef,
  useLayoutEffect,
} from 'react'
import {
  getAPIUrl,
  getLEARNHOUSE_TOP_DOMAIN_VAL,
  getLEARNHOUSE_DOMAIN_VAL,
} from '@services/config/config'
import { isSubdomainOf, isSameHost, isLocalhost as isLocalhostCheck } from '@services/utils/ts/hostUtils'
import { safeRedirectUrl } from '@services/auth/redirects'
import i18n from '@/lib/i18n'
import { safeExternalUrl } from '@services/security/url'
import { AUTH_EXPIRED_EVENT, AUTH_REFRESHED_EVENT } from '@/lib/auth/events'
import {
  AUTH_BROADCAST_CHANNEL,
  AUTH_STORAGE_SYNC_KEY,
  createAuthSyncReceiver,
  publishAuthSync,
} from '@/lib/auth/authSync'

// Types matching NextAuth's session structure
export interface Session {
  user: any | undefined
  roles?: string[] | undefined
  tokens?: {
    access_token?: string | undefined
    refresh_token?: string | undefined
    expiry?: number | undefined
  } | undefined
}

export type SessionStatus = 'loading' | 'authenticated' | 'unauthenticated'

// Result of trying to trade the refresh cookie for an access token.
//
// `unauthenticated` and `transient` are kept apart on purpose: only the former
// may end a session. Treating a rate limit, a 5xx, or an offline browser as
// "signed out" destroys an httpOnly refresh cookie that still had weeks left,
// and the user has no way back except signing in again.
export type RefreshOutcome =
  | { status: 'ok'; access_token: string; expiry?: number }
  | { status: 'unauthenticated' }
  | { status: 'transient' }

export interface UseSessionReturn {
  data: Session | null
  status: SessionStatus
  isRevalidating: boolean
  update: (_force?: boolean) => Promise<void>
}

export interface SignInOptions {
  redirect?: boolean
  callbackUrl?: string
  email?: string
  password?: string
  // SSO fields
  sso?: string
  sso_access_token?: string
  sso_user?: string
  sso_expiry?: number
  // For OAuth
  orgId?: number
  orgSlug?: string
}

export interface SignInResult {
  ok: boolean
  error: string | null
  url: string | null
  status: number
  // Set when the password was correct but the account carries a second factor.
  // `ok` stays false (nothing is authenticated yet) and the caller must hand
  // `mfa_token` back to completeMfaLogin() along with a code.
  mfa_required?: boolean
  mfa_token?: string
  // Sign-in links only: where the link itself asks to land, for a link URL
  // that carries no ?next of its own.
  redirect_to?: string | null
}

export interface SignOutOptions {
  callbackUrl?: string
  redirect?: boolean
}

// Result of a passwordless (magic link) request. The backend ALWAYS answers 200
// with a generic `detail` so it never reveals whether the account exists; the
// UI treats any non-rate-limited answer as "check your email". `rateLimited`
// carries the 429 case so the caller can show a distinct retry message.
export interface MagicLinkRequestResult {
  ok: boolean
  detail: string
  rateLimited?: boolean
  retryAfter?: number
}

// Session cache for performance (similar to NextAuth's 10s cache)
interface SessionCache {
  data: Session
  timestamp: number
}

// Keep this SHORT: the cached session carries the user's org roles, which the
// admin feature-gating reads. A long TTL means revoked membership/roles linger
// in the UI. 2 min balances freshness against redundant /users/session fetches
// (the authenticated refetch interval is ~1 min).
const SESSION_CACHE_TTL = 2 * 60 * 1000 // 2 minutes
const TOKEN_REFRESH_THRESHOLD = 60 * 1000 // 1 minute before expiry
const OAUTH_STATE_COOKIE = 'LH_oauth_state'

// Context
interface AuthContextValue {
  session: Session | null
  status: SessionStatus
  isRevalidating: boolean
  accessToken: string | null
  refreshSession: (_force?: boolean) => Promise<string | null>
  signIn: (_provider: string, _options?: SignInOptions) => Promise<SignInResult | void>
  signOut: (_options?: SignOutOptions) => Promise<void>
  completeMfaLogin: (
    _mfaToken: string,
    _code: string,
    _options?: { isBackupCode?: boolean; callbackUrl?: string; redirect?: boolean }
  ) => Promise<SignInResult>
  // Passwordless (magic link) login. `requestMagicLink` sends the email; it never
  // throws on the generic 200. `completeMagicLink` consumes the token from the
  // link and either establishes a session or (for 2FA accounts) returns an
  // mfa_token the login page can pick up, mirroring completeMfaLogin.
  requestMagicLink: (_email: string, _orgSlug?: string) => Promise<MagicLinkRequestResult>
  completeMagicLink: (
    _token: string,
    _options?: { callbackUrl?: string; redirect?: boolean }
  ) => Promise<SignInResult>
}

const AuthContext = createContext<AuthContextValue | null>(null)

// Generate cryptographically secure random string for CSRF protection
function generateSecureToken(length: number = 32): string {
  const array = new Uint8Array(length)
  crypto.getRandomValues(array)
  return Array.from(array, byte => byte.toString(16).padStart(2, '0')).join('')
}

// Check if current hostname is a custom domain
function isCustomDomain(): boolean {
  if (typeof window === 'undefined') return false
  const hostname = window.location.hostname
  const domain = getLEARNHOUSE_DOMAIN_VAL()
  return !isSubdomainOf(hostname, domain) && !isSameHost(hostname, domain) && !isLocalhostCheck(hostname)
}

// Get cookie attributes based on current domain context
function getCookieAttributes(): { secureAttr: string; domainAttr: string; sameSiteAttr: string } {
  const isSecure = typeof window !== 'undefined' && window.location.protocol === 'https:'
  const secureAttr = isSecure ? '; Secure' : ''
  const topDomain = getLEARNHOUSE_TOP_DOMAIN_VAL()

  // For custom domains, don't set domain attribute (host-only cookie)
  // For localhost, don't set domain attribute
  // For subdomains of main domain, set domain to allow sharing
  let domainAttr = ''
  if (!isCustomDomain() && topDomain !== 'localhost') {
    domainAttr = `; domain=.${topDomain}`
  }

  // SameSite=Lax is generally safe and allows top-level navigation
  const sameSiteAttr = '; SameSite=Lax'

  return { secureAttr, domainAttr, sameSiteAttr }
}

// Store OAuth CSRF state in a cookie (shared across subdomains, unlike sessionStorage)
// For custom domains, cookie is host-only so it stays on the same origin.
// For subdomains, cookie is scoped to top domain so callback on main domain can read it.
function setOAuthStateCookie(csrf: string): void {
  const { secureAttr, domainAttr, sameSiteAttr } = getCookieAttributes()
  // 5 minute expiry matching the state validation window
  const expires = new Date(Date.now() + 5 * 60 * 1000).toUTCString()
  const value = JSON.stringify({ csrf, timestamp: Date.now() })
  document.cookie = `${OAUTH_STATE_COOKIE}=${encodeURIComponent(value)}; path=/${sameSiteAttr}${secureAttr}${domainAttr}; expires=${expires}`
}

function getOAuthStateCookie(): { csrf: string; timestamp: number } | null {
  try {
    const cookies = document.cookie.split(';')
    for (const cookie of cookies) {
      const [name, ...rest] = cookie.trim().split('=')
      if (name === OAUTH_STATE_COOKIE) {
        return JSON.parse(decodeURIComponent(rest.join('=')))
      }
    }
  } catch {
    /* ignore */
  }
  return null
}

function clearOAuthStateCookie(): void {
  const { secureAttr, domainAttr, sameSiteAttr } = getCookieAttributes()
  document.cookie = `${OAUTH_STATE_COOKIE}=; path=/${sameSiteAttr}${secureAttr}${domainAttr}; expires=Thu, 01 Jan 1970 00:00:00 GMT`
}

function clearSessionMarker(): void {
  if (typeof document === 'undefined') return

  const { secureAttr, domainAttr, sameSiteAttr } = getCookieAttributes()
  const expired = 'expires=Thu, 01 Jan 1970 00:00:00 GMT'
  document.cookie = `LH_session=; path=/${sameSiteAttr}${secureAttr}${domainAttr}; ${expired}`
  document.cookie = `LH_session=; path=/${sameSiteAttr}${secureAttr}; ${expired}`
}

// Session Provider Component
interface SessionProviderProps {
  children: React.ReactNode
  refetchInterval?: number
}

export function SessionProvider({
  children,
  refetchInterval = 60000,
}: SessionProviderProps) {
  const [session, setSession] = useState<Session | null>(null)
  const [status, setStatus] = useState<SessionStatus>('loading')
  const [isRevalidating, setIsRevalidatingState] = useState(false)
  const [accessToken, setAccessToken] = useState<string | null>(null)
  const [tokenExpiry, setTokenExpiry] = useState<number | null>(null)
  const sessionCacheRef = useRef<SessionCache | null>(null)
  const intervalRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const broadcastChannelRef = useRef<BroadcastChannel | null>(null)
  const sessionRef = useRef<Session | null>(session)
  const statusRef = useRef<SessionStatus>(status)
  const accessTokenRef = useRef<string | null>(accessToken)

  useLayoutEffect(() => {
    sessionRef.current = session
    statusRef.current = status
    accessTokenRef.current = accessToken
  }, [accessToken, session, status])

  const refreshSessionInternalRef = useRef<() => Promise<boolean>>(async () => false)
  const refreshSessionInternalPromiseRef = useRef<Promise<boolean> | null>(null)

  // Use ref for refresh promise to avoid issues with stale closures
  // but still deduplicate within the same tab
  const refreshPromiseRef = useRef<Promise<RefreshOutcome> | null>(null)
  const isRefreshingRef = useRef(false)
  const authFailureHandledRef = useRef(false)
  // Monotonic auth epoch: bumped at logout, login, and every session revalidation
  // boundary. A refresh that resolves after a newer boundary cannot overwrite it.
  const authEpochRef = useRef(0)

  const setSessionRevalidationPending = useCallback((pending: boolean) => {
    if (pending) {
      // Hide private content synchronously, before the React update paints. The
      // React shield below then supplies accessible status and makes the app
      // root inert while the refresh-cookie identity is checked.
      // Each new resume/login boundary supersedes every refresh already in
      // flight, including another event received while the shield is up.
      authEpochRef.current++
      if (typeof document !== 'undefined') {
        document.documentElement.dataset.sessionValidationPending = 'true'
      }
    } else {
      if (typeof document !== 'undefined') {
        delete document.documentElement.dataset.sessionValidationPending
      }
    }
    setIsRevalidatingState(pending)
  }, [])

  const clearAuthState = useCallback((clearMarker: boolean = true, broadcastLogout = false) => {
    authEpochRef.current++
    sessionRef.current = null
    setSession(null)
    setAccessToken(null)
    setTokenExpiry(null)
    setStatus('unauthenticated')
    sessionCacheRef.current = null
    setSessionRevalidationPending(false)

    if (clearMarker) {
      clearSessionMarker()
    }
    // A server-confirmed expired/revoked session must invalidate every tab,
    // just like an explicit sign-out. Received LOGOUT events use the default
    // false value so tabs do not echo the message back and forth.
    if (broadcastLogout) {
      publishAuthSync('LOGOUT', broadcastChannelRef.current)
    }
  }, [setSessionRevalidationPending])

  // Set up BroadcastChannel with a storage-event fallback for cross-tab auth.
  useEffect(() => {
    if (typeof window === 'undefined') return

    const receiveAuthSync = createAuthSyncReceiver((type) => {
      if (type === 'LOGOUT') {
        // Another tab logged out, so clear our state (and the session marker) too.
        clearAuthState()
      } else if (type === 'LOGIN') {
        // Every tab must check the shared refresh cookie. A cross-tab storage
        // throttle caused other tabs to keep rendering the previous account.
        // The same event can arrive over both transports, so dedupe its nonce
        // locally while allowing distinct login events to supersede old work.
        if (sessionRef.current?.user || statusRef.current === 'authenticated') {
          setSessionRevalidationPending(true)
        }
        refreshSessionInternalRef.current().catch((e) =>
          console.error('[auth] cross-tab session refresh failed:', e),
        )
      }
    })

    try {
      if ('BroadcastChannel' in window) {
        broadcastChannelRef.current = new BroadcastChannel(AUTH_BROADCAST_CHANNEL)
        broadcastChannelRef.current.onmessage = (event) => {
          receiveAuthSync(event.data)
        }
      }
    } catch (e) {
      // Some privacy modes throw on BroadcastChannel; the storage event remains.
      console.warn('[auth] BroadcastChannel unavailable:', e)
    }

    const onStorage = (event: StorageEvent) => {
      if (event.key !== AUTH_STORAGE_SYNC_KEY || !event.newValue) return
      try {
        receiveAuthSync(JSON.parse(event.newValue))
      } catch {
        // Ignore malformed values from this application-owned sync key.
      }
    }
    window.addEventListener('storage', onStorage)

    return () => {
      window.removeEventListener('storage', onStorage)
      broadcastChannelRef.current?.close()
      broadcastChannelRef.current = null
    }
  }, [clearAuthState, setSessionRevalidationPending])

  // Fetch user session from backend.
  //
  // Returns `null` ONLY when the backend says this token is not a valid
  // identity (401/403). Any other failure throws, so callers can tell "you are
  // signed out" apart from "the request did not get through" and avoid tearing
  // down a healthy session over a server blip.
  const fetchUserSession = useCallback(async (token: string, expiry?: number): Promise<Session | null> => {
    const response = await fetch(`${getAPIUrl()}users/session`, {
      method: 'GET',
      headers: {
        Authorization: `Bearer ${token}`,
      },
      credentials: 'include',
    })

    if (!response.ok) {
      if (response.status === 401 || response.status === 403) {
        return null
      }
      throw new Error(`Session fetch failed with status: ${response.status}`)
    }

    const data = await response.json()
    return {
      user: data.user,
      roles: data.roles,
      tokens: {
        access_token: token,
        refresh_token: undefined, // Stored in httpOnly cookie
        expiry: expiry,
      },
    }
  }, [])

  // Check if a session might exist (marker cookie is set alongside httpOnly auth cookies).
  // Match the cookie name EXACTLY: `includes('LH_session')` also matched unrelated
  // names like `LH_session_backup`, falsely reporting a session.
  const hasSessionMarker = useCallback((): boolean => {
    if (typeof document === 'undefined') return false
    return document.cookie.split('; ').some((c) => c.startsWith('LH_session='))
  }, [])

  // Refresh access token using refresh token cookie.
  //
  // The outcome is deliberately three-way. Collapsing "the server is having a
  // bad minute" into "you are logged out" is what made sessions evaporate: a
  // single 429 from the shared-IP refresh rate limit, or one 502 during an API
  // rollout, used to tear down a session that still had weeks of validity.
  // Only `unauthenticated` (the backend explicitly rejecting the refresh
  // credential) may end a session.
  const refreshAccessToken = useCallback(async (): Promise<RefreshOutcome> => {
    // Deduplicate refresh requests within this tab
    if (isRefreshingRef.current && refreshPromiseRef.current) {
      return refreshPromiseRef.current
    }

    isRefreshingRef.current = true
    refreshPromiseRef.current = (async () => {
      try {
        // Use Next.js API route to ensure cookies are set correctly
        const response = await fetch('/api/auth/refresh', {
          method: 'GET',
          credentials: 'include',
        })

        if (!response.ok) {
          // 401/403 are the only statuses that mean the refresh cookie itself
          // is dead. The proxy clears cookies on exactly these, so the two
          // layers agree on what ends a session.
          if (response.status === 401 || response.status === 403) {
            return { status: 'unauthenticated' } as const
          }
          console.warn(
            `[auth] refresh failed with ${response.status}; keeping session, will retry`,
          )
          return { status: 'transient' } as const
        }

        const data = await response.json()

        // Validate response structure
        if (!data.access_token) {
          console.error('Invalid refresh response: missing access_token')
          return { status: 'transient' } as const
        }

        return {
          status: 'ok',
          access_token: data.access_token,
          expiry: typeof data.expiry === 'number' ? data.expiry : undefined,
        } as const
      } catch (error) {
        // Network error, offline, DNS blip, aborted request. Says nothing
        // about whether the user is still signed in.
        console.warn('[auth] refresh request could not be sent:', error)
        return { status: 'transient' } as const
      } finally {
        isRefreshingRef.current = false
        refreshPromiseRef.current = null
      }
    })()

    return refreshPromiseRef.current
  }, [])

  // Check if token needs refresh
  const isTokenExpiringSoon = useCallback((expiry?: number | null): boolean => {
    if (!expiry) return false
    return Date.now() + TOKEN_REFRESH_THRESHOLD >= expiry
  }, [])

  const applySessionFromToken = useCallback(async (
    token: string,
    expiry?: number
  ): Promise<boolean> => {
    const epoch = authEpochRef.current
    setAccessToken(token)
    setTokenExpiry(expiry || null)

    let sessionData: Session | null
    try {
      sessionData = await fetchUserSession(token, expiry)
    } catch (error) {
      // A failed profile lookup cannot tell us whether the shared refresh
      // cookie belongs to this tab's previous account or a newly signed-in
      // account. Keep the previous component tree mounted so unsaved work is
      // not discarded, but keep the revalidation shield up until identity can
      // be confirmed. On cold start there is no previous identity to preserve.
      console.warn('[auth] session lookup failed, keeping session:', error)
      if (authEpochRef.current !== epoch) return false
      setAccessToken(token)
      setTokenExpiry(expiry || null)
      sessionCacheRef.current = null
      if (sessionRef.current?.user) {
        setStatus('authenticated')
      } else {
        sessionRef.current = null
        setSession(null)
        setStatus('unauthenticated')
      }
      return false
    }
    // A logout/clear that fired during the await bumped the epoch, so abort the
    // write so we don't resurrect a session that was just invalidated.
    if (authEpochRef.current !== epoch) return false
    if (!sessionData) {
      clearAuthState(true, true)
      return false
    }

    sessionRef.current = sessionData
    setSession(sessionData)
    setStatus('authenticated')
    sessionCacheRef.current = {
      data: sessionData,
      timestamp: Date.now(),
    }
    setSessionRevalidationPending(false)
    return true
  }, [clearAuthState, fetchUserSession, setSessionRevalidationPending])

  // Internal refresh session function (used by broadcast channel)
  const refreshSessionInternal = useCallback((): Promise<boolean> => {
    if (refreshSessionInternalPromiseRef.current) {
      return refreshSessionInternalPromiseRef.current
    }

    const runRefresh = async (): Promise<boolean> => {
      const requestEpoch = authEpochRef.current
      try {
        const refreshResult = await refreshAccessToken()
        // A second tab may have changed accounts while this request was in
        // flight. Do not apply even a successfully refreshed older token.
        if (authEpochRef.current !== requestEpoch) return false
        if (refreshResult.status === 'ok') {
          return applySessionFromToken(refreshResult.access_token, refreshResult.expiry)
        } else if (refreshResult.status === 'unauthenticated') {
          clearAuthState(true, true)
          return true
        }
        // 'transient': leave the current session in place. A bounded-backoff
        // retry keeps private content shielded until identity is confirmed.
        return false
      } catch (error) {
        // Never end a session because of an unexpected client-side error.
        console.error('Session refresh error:', error)
        return false
      }
    }

    let trackedPromise: Promise<boolean>
    trackedPromise = runRefresh().finally(() => {
      if (refreshSessionInternalPromiseRef.current === trackedPromise) {
        refreshSessionInternalPromiseRef.current = null
      }
    })
    refreshSessionInternalPromiseRef.current = trackedPromise
    return trackedPromise
  }, [applySessionFromToken, clearAuthState, refreshAccessToken])

  refreshSessionInternalRef.current = refreshSessionInternal

  // A refresh-cookie network failure says nothing about identity. Keep the
  // app shielded and retry at a bounded exponential interval until the server
  // either confirms a session or explicitly rejects the refresh credential.
  useEffect(() => {
    if (!isRevalidating) return

    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null
    let delay = 5_000

    const retry = async () => {
      const validated = await refreshSessionInternalRef.current()
      if (cancelled || validated) return
      timer = setTimeout(retry, delay)
      delay = Math.min(delay * 2, 60_000)
    }

    timer = setTimeout(retry, delay)
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [isRevalidating])

  // Main session refresh function
  const refreshSession = useCallback(async (force?: boolean): Promise<string | null> => {
    // Check cache first (skip if force refresh requested)
    const now = Date.now()
    if (
      !force &&
      sessionCacheRef.current &&
      now - sessionCacheRef.current.timestamp < SESSION_CACHE_TTL
    ) {
      sessionRef.current = sessionCacheRef.current.data
      setSession(sessionCacheRef.current.data)
      setStatus('authenticated')
      return accessTokenRef.current
    }

    // Invalidate cache when forcing
    if (force) {
      sessionCacheRef.current = null
    }

    try {
      const epoch = authEpochRef.current
      // Try to refresh token if we don't have one or it's expiring
      let currentToken = accessToken
      let currentExpiry = tokenExpiry

      if (!currentToken || isTokenExpiringSoon(currentExpiry)) {
        const refreshResult = await refreshAccessToken()
        if (refreshResult.status === 'ok') {
          currentToken = refreshResult.access_token
          currentExpiry = refreshResult.expiry || null
          setAccessToken(currentToken)
          setTokenExpiry(currentExpiry)
        } else if (refreshResult.status === 'unauthenticated') {
          // The backend rejected the refresh credential: genuinely signed out.
          clearAuthState(true, true)
          return null
        } else {
          // Transient failure. Keep whatever session we already have rather
          // than signing the user out over a hiccup; the next tick retries.
          return currentToken
        }
      }

      // Fetch session data with the CURRENT expiry (from refresh, not stale state)
      const sessionData = await fetchUserSession(currentToken, currentExpiry || undefined)
      // A logout/clear during the awaits bumped the epoch, so abort the write so we
      // don't resurrect a session that was just invalidated (cross-tab logout).
      if (authEpochRef.current !== epoch) return null
      if (sessionData) {
        sessionRef.current = sessionData
        setSession(sessionData)
        setStatus('authenticated')
        sessionCacheRef.current = {
          data: sessionData,
          timestamp: now,
        }
        return currentToken
      } else {
        clearAuthState(true, true)
        return null
      }
    } catch (error) {
      // Reaching here means a request could not be completed (offline, 5xx,
      // aborted). That is not a signal about the user's identity, so the
      // session stays as-is and the refetch interval retries.
      console.warn('[auth] session refresh could not complete, keeping session:', error)
      return accessTokenRef.current
    }
  }, [accessToken, tokenExpiry, fetchUserSession, isTokenExpiringSoon, refreshAccessToken, clearAuthState])

  // Initialize session on mount
  useEffect(() => {
    let isMounted = true

    const initSession = async () => {
      // Skip entirely if no session marker; no httpOnly refresh token exists
      if (!hasSessionMarker()) {
        clearAuthState(false)
        return
      }

      setStatus('loading')

      // Try to restore session from refresh token
      const refreshResult = await refreshAccessToken()

      if (!isMounted) return

      if (refreshResult.status === 'ok') {
        await applySessionFromToken(refreshResult.access_token, refreshResult.expiry)
      } else if (refreshResult.status === 'unauthenticated') {
        clearAuthState(true, true)
      } else {
        // Transient failure on a cold start. The refresh cookie is very likely
        // still good, so don't wipe it. Drop back to 'unauthenticated' status
        // WITHOUT clearing cookies, and let the refetch interval recover the
        // session once the backend is reachable again.
        setStatus('unauthenticated')
      }
    }

    initSession()

    return () => {
      isMounted = false
    }
  }, [applySessionFromToken, clearAuthState, hasSessionMarker, refreshAccessToken])

  // Set up refetch interval.
  //
  // Also runs while unauthenticated IF the session marker cookie is still
  // present. That combination means "we hold a refresh cookie but couldn't
  // turn it into a session yet" (i.e. a transient failure), and polling is
  // what lets the tab heal itself once the backend is reachable again, instead
  // of stranding the user on a logged-out UI until they reload.
  useEffect(() => {
    const shouldPoll =
      status === 'authenticated' || (status === 'unauthenticated' && hasSessionMarker())

    if (refetchInterval && shouldPoll) {
      intervalRef.current = setInterval(() => {
        refreshSession()
      }, refetchInterval)
    }

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
      }
    }
  }, [refetchInterval, status, refreshSession, hasSessionMarker])

  // Establish a client-side session from a token-bearing auth response.
  // Shared by password login and by second-factor completion so the two cannot
  // drift apart: a session established one way must be identical to the other.
  const establishSession = useCallback(
    async (data: any, callbackUrl: string, redirect: boolean): Promise<SignInResult> => {
      // A refresh still in flight for whoever was signed in before must not
      // write its (older) session over this one when it resolves.
      authEpochRef.current++
      const newSession: Session = {
        user: data.user,
        roles: [],
        tokens: {
          access_token: data.tokens.access_token,
          expiry: data.tokens.expiry,
        },
      }

      sessionRef.current = newSession
      setSession(newSession)
      setAccessToken(data.tokens.access_token)
      setTokenExpiry(data.tokens.expiry || null)
      setStatus('authenticated')
      setSessionRevalidationPending(false)
      sessionCacheRef.current = { data: newSession, timestamp: Date.now() }

      // Fetch full session with roles. The sign-in already succeeded (cookies
      // are set), so a failed lookup only means the roles arrive on the next
      // session fetch; reporting it as a failed sign-in would misdescribe it.
      try {
        const fullSession = await fetchUserSession(data.tokens.access_token, data.tokens.expiry)
        if (fullSession) {
          fullSession.tokens = newSession.tokens
          sessionRef.current = fullSession
          setSession(fullSession)
          sessionCacheRef.current = { data: fullSession, timestamp: Date.now() }
        }
      } catch (error) {
        console.warn('[auth] session lookup after sign-in failed, keeping session:', error)
      }

      // Notify other tabs
      publishAuthSync('LOGIN', broadcastChannelRef.current)

      if (redirect) {
        window.location.href = safeRedirectUrl(callbackUrl)
      }

      return { ok: true, error: null, url: callbackUrl, status: 200 }
    },
    [fetchUserSession, setSessionRevalidationPending]
  )

  // Complete a login that stopped at the second-factor challenge.
  const completeMfaLogin = useCallback(
    async (
      mfaToken: string,
      code: string,
      options: { isBackupCode?: boolean; callbackUrl?: string; redirect?: boolean } = {}
    ): Promise<SignInResult> => {
      const { isBackupCode = false, callbackUrl = '/', redirect = true } = options

      try {
        const response = await fetch('/api/auth/login/mfa', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            mfa_token: mfaToken,
            code,
            is_backup_code: isBackupCode,
          }),
          credentials: 'include',
        })

        const data = await response.json()

        if (!response.ok) {
          const errorData = data.detail || data
          return {
            ok: false,
            error: JSON.stringify({
              code: errorData?.code || 'UNKNOWN_ERROR',
              message: errorData?.message || i18n.t('auth.errors.verification_failed'),
              retry_after: errorData?.retry_after,
            }),
            url: null,
            status: response.status,
          }
        }

        if (!data.tokens?.access_token) {
          return {
            ok: false,
            error: JSON.stringify({ code: 'INVALID_RESPONSE', message: i18n.t('auth.errors.invalid_response') }),
            url: null,
            status: 500,
          }
        }

        return await establishSession(data, callbackUrl, redirect)
      } catch (error) {
        console.error('MFA verification error:', error)
        return {
          ok: false,
          error: JSON.stringify({ code: 'NETWORK_ERROR', message: i18n.t('auth.errors.network') }),
          url: null,
          status: 0,
        }
      }
    },
    [establishSession]
  )

  // Request a passwordless login link. The backend ALWAYS returns 200 with a
  // generic detail (never revealing whether the account exists), except for a
  // 429 rate-limit, so this never throws on the happy path.
  const requestMagicLink = useCallback(
    async (email: string, orgSlug?: string): Promise<MagicLinkRequestResult> => {
      try {
        const response = await fetch('/api/auth/magic-link/request', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email, ...(orgSlug ? { org_slug: orgSlug } : {}) }),
          credentials: 'include',
        })

        const data = await response.json().catch(() => ({}))

        if (response.status === 429) {
          const errorData = data.detail || data
          return {
            ok: false,
            rateLimited: true,
            detail:
              errorData?.message ||
              'Too many requests. Please wait a moment before trying again.',
            retryAfter: errorData?.retry_after,
          }
        }

        return {
          ok: response.ok,
          detail:
            typeof data.detail === 'string'
              ? data.detail
              : 'If that account exists, a login link is on its way.',
        }
      } catch (error) {
        console.error('Magic link request error:', error)
        return { ok: false, detail: 'Could not reach the server. Please try again.' }
      }
    },
    []
  )

  // Complete a passwordless login from the token embedded in the emailed link.
  // Mirrors completeMfaLogin: on a 2FA account it hands back an mfa_token instead
  // of a session; otherwise it runs the same establishSession path as every other
  // flow. Cookies are set by the /api/auth proxy on the verify response.
  const completeMagicLink = useCallback(
    async (
      token: string,
      options: { callbackUrl?: string; redirect?: boolean } = {}
    ): Promise<SignInResult> => {
      const { callbackUrl = '/', redirect = true } = options

      try {
        const response = await fetch('/api/auth/magic-link/verify', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token }),
          credentials: 'include',
        })

        // A gateway error page is not JSON; keep its status so the caller can
        // tell "try again" from "this link is dead".
        const data = await response.json().catch(() => ({}))

        if (!response.ok) {
          const detail = data.detail ?? data
          const errorData = typeof detail === 'string' ? { message: detail } : detail
          return {
            ok: false,
            error: JSON.stringify({
              code: errorData?.code || 'UNKNOWN_ERROR',
              message: errorData?.message || i18n.t('auth.errors.link_invalid'),
            }),
            url: null,
            status: response.status,
          }
        }

        // Account carries a second factor, so no session yet. Hand the pending
        // token to the caller (the /auth/magic page forwards it to /login).
        if (data.mfa_required && data.mfa_token) {
          return {
            ok: false,
            error: null,
            url: null,
            status: response.status,
            mfa_required: true,
            mfa_token: data.mfa_token,
            redirect_to: data.redirect_to ?? null,
          }
        }

        if (!data.tokens?.access_token) {
          return {
            ok: false,
            error: JSON.stringify({ code: 'INVALID_RESPONSE', message: i18n.t('auth.errors.invalid_response') }),
            url: null,
            status: 500,
          }
        }

        return {
          ...(await establishSession(data, callbackUrl, redirect)),
          redirect_to: data.redirect_to ?? null,
        }
      } catch (error) {
        console.error('Magic link verification error:', error)
        return {
          ok: false,
          error: JSON.stringify({ code: 'NETWORK_ERROR', message: i18n.t('auth.errors.network') }),
          url: null,
          status: 0,
        }
      }
    },
    [establishSession]
  )

  // Sign in function
  const handleSignIn = useCallback(
    async (provider: string, options: SignInOptions = {}): Promise<SignInResult | void> => {
      const { redirect = true, callbackUrl = '/' } = options

      try {
        if (provider === 'credentials') {
          // Handle SSO login (tokens already obtained)
          if (options.sso === 'true' && options.sso_access_token) {
            const user = options.sso_user ? JSON.parse(options.sso_user) : null
            // Use server-provided expiry or default to 8 hours
            const expiry = options.sso_expiry || (Date.now() + 8 * 60 * 60 * 1000)

            const newSession: Session = {
              user,
              roles: [],
              tokens: {
                access_token: options.sso_access_token,
                expiry,
              },
            }
            authEpochRef.current++
            sessionRef.current = newSession
            setSession(newSession)
            setAccessToken(options.sso_access_token)
            setTokenExpiry(expiry)
            setStatus('authenticated')
            setSessionRevalidationPending(false)
            sessionCacheRef.current = {
              data: newSession,
              timestamp: Date.now(),
            }

            // Resolve the real org/role list, exactly as the password login below
            // does. The SSO handoff only carries tokens and the user, so without
            // this the session stayed `roles: []`, and because that empty list
            // was written into the session cache, the user spent the cache window
            // looking like a non-member of the org they had just signed in to:
            // the "join this organization" banner instead of their courses.
            // A failure here must not undo a successful sign-in, so we keep the
            // role-less session and let the next session fetch fill it in.
            try {
              const fullSession = await fetchUserSession(options.sso_access_token, expiry)
              if (fullSession) {
                fullSession.tokens = newSession.tokens
                sessionRef.current = fullSession
                setSession(fullSession)
                sessionCacheRef.current = {
                  data: fullSession,
                  timestamp: Date.now(),
                }
              }
            } catch {
              // Transient failure; the cached role-less session is refreshed by
              // the next /users/session read rather than blocking the redirect.
            }

            // Notify other tabs
            publishAuthSync('LOGIN', broadcastChannelRef.current)

            if (redirect) {
              window.location.href = safeRedirectUrl(callbackUrl)
            }

            return { ok: true, error: null, url: callbackUrl, status: 200 }
          }

          // Regular credentials login
          // Use Next.js API route to ensure cookies are set correctly
          const response = await fetch('/api/auth/login', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: new URLSearchParams({
              username: options.email || '',
              password: options.password || '',
              // Bind the session to the org whose login page this came from, so
              // org-scoped session policies can enforce against it. Omitted on the
              // org-less apex login.
              ...(options.orgSlug ? { org_slug: options.orgSlug } : {}),
            }),
            credentials: 'include',
          })

          const data = await response.json()

          if (!response.ok) {
            // Return error in same format as NextAuth
            const errorData = data.detail || data
            return {
              ok: false,
              error: JSON.stringify({
                code: errorData?.code || 'UNKNOWN_ERROR',
                message: errorData?.message || i18n.t('auth.errors.login_failed'),
                email: errorData?.email,
                retry_after: errorData?.retry_after,
              }),
              url: null,
              status: response.status,
            }
          }

          // Password was correct but the account has a second factor. No
          // session exists yet, so hand the pending token to the caller, which
          // collects a code and calls completeMfaLogin().
          if (data.mfa_required && data.mfa_token) {
            return {
              ok: false,
              error: null,
              url: null,
              status: response.status,
              mfa_required: true,
              mfa_token: data.mfa_token,
            }
          }

          // Validate response structure
          if (!data.tokens?.access_token) {
            return {
              ok: false,
              error: JSON.stringify({ code: 'INVALID_RESPONSE', message: i18n.t('auth.errors.invalid_response') }),
              url: null,
              status: 500,
            }
          }

          return await establishSession(data, callbackUrl, redirect)
        }

        if (provider === 'google') {
          // Store org context in cookies before OAuth redirect
          const { secureAttr, domainAttr, sameSiteAttr } = getCookieAttributes()
          const baseAttributes = `; path=/${sameSiteAttr}${secureAttr}`

          if (options.orgSlug || options.orgId) {
            if (options.orgSlug) {
              document.cookie = `LH_oauth_orgslug=${options.orgSlug}${baseAttributes}${domainAttr}`
            }
            if (options.orgId) {
              document.cookie = `LH_oauth_org_id=${options.orgId}${baseAttributes}${domainAttr}`
            }
          }

          // Generate CSRF token for state parameter. The server signs the final
          // state (with returnOrigin pinned to this request's origin) so the
          // callback can trust where it bounces the code.
          const csrfToken = generateSecureToken()
          const stateData = {
            callbackUrl,
            csrf: csrfToken,
            timestamp: Date.now(),
          }

          // For custom domains, ask for returnOrigin so the main domain callback can bounce back
          const returnOrigin = isCustomDomain() ? window.location.origin : undefined

          // Store CSRF token in cookie for validation on callback
          setOAuthStateCookie(csrfToken)

          // Always use main domain for redirect URI; only one URI registered with Google
          const redirectUri = `${window.location.protocol}//${getLEARNHOUSE_DOMAIN_VAL()}/auth/callback/google`

          // Get Google OAuth URL from server (client ID lives server-side only)
          const authResponse = await fetch('/api/auth/google/authorize', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ redirect_uri: redirectUri, state: stateData, returnOrigin }),
          })

          if (!authResponse.ok) {
            const errorData = await authResponse.json().catch(() => ({}))
            console.error('Google OAuth initiation failed:', errorData)
            return {
              ok: false,
              error: errorData.error || i18n.t('auth.errors.google_not_configured'),
              url: null,
              status: authResponse.status,
            }
          }

          const { url: googleAuthUrl } = await authResponse.json()
          const safeGoogleUrl = safeExternalUrl(googleAuthUrl)
          if (safeGoogleUrl) window.location.href = safeGoogleUrl
          return
        }

        // Unknown provider
        return {
          ok: false,
          error: `Unknown provider: ${provider}`,
          url: null,
          status: 400,
        }
      } catch (error: any) {
        console.error('Sign in error:', error)
        return {
          ok: false,
          error: error.message || i18n.t('auth.errors.sign_in_failed'),
          url: null,
          status: 500,
        }
      }
    },
    [fetchUserSession, establishSession, setSessionRevalidationPending]
  )

  // Sign out function
  const handleSignOut = useCallback(async (options: SignOutOptions = {}) => {
    const { callbackUrl = '/', redirect = true } = options

    let logoutSuccess = false
    try {
      // Use Next.js API route to ensure cookies are cleared correctly
      const response = await fetch('/api/auth/logout', {
        method: 'POST',
        credentials: 'include',
      })
      logoutSuccess = response.ok
    } catch (error) {
      console.error('Logout error:', error)
    }

    // Clear local state regardless of backend response. Bump the auth epoch first
    // (like clearAuthState + the cross-tab handler) so an in-flight refresh racing
    // this same-tab logout can't resurrect the session it's clearing.
    authEpochRef.current++
    sessionRef.current = null
    setSession(null)
    setAccessToken(null)
    setTokenExpiry(null)
    setStatus('unauthenticated')
    sessionCacheRef.current = null
    setSessionRevalidationPending(false)

    // Clear refresh promise
    refreshPromiseRef.current = null
    isRefreshingRef.current = false

    // Clear any auth cookies on client side
    const { secureAttr, domainAttr } = getCookieAttributes()
    const expireAttr = '; expires=Thu, 01 Jan 1970 00:00:00 GMT'
    document.cookie = `LH_oauth_orgslug=; path=/${expireAttr}${secureAttr}${domainAttr}`
    document.cookie = `LH_oauth_org_id=; path=/${expireAttr}${secureAttr}${domainAttr}`

    // Clear OAuth state
    clearOAuthStateCookie()
    clearSessionMarker()

    // Notify other tabs about logout
    publishAuthSync('LOGOUT', broadcastChannelRef.current)

    if (redirect) {
      window.location.href = safeRedirectUrl(callbackUrl)
    }

    // If backend logout failed, log a warning (user is still logged out locally)
    if (!logoutSuccess) {
      console.warn('Backend logout may have failed. User logged out locally.')
    }
  }, [setSessionRevalidationPending])

  useEffect(() => {
    if (typeof window === 'undefined') return

    const onAuthExpired = (event: Event) => {
      if (authFailureHandledRef.current) return
      // Never force a sign-out/redirect for a visitor who was never signed in.
      // Anonymous users on public content get expected 401s from auth-only
      // endpoints; only an ACTUAL expired session (marker present) should redirect.
      if (!hasSessionMarker()) return
      authFailureHandledRef.current = true

      const detail = (event as CustomEvent<{ callbackUrl?: string }>).detail
      const callbackUrl = detail?.callbackUrl
        || (window.location.pathname.startsWith('/admin') ? '/admin/login' : '/login')

      handleSignOut({ callbackUrl, redirect: true }).catch((error) => {
        console.error('Forced sign-out failed:', error)
        authFailureHandledRef.current = false
      })
    }

    const onAuthRefreshed = (event: Event) => {
      authFailureHandledRef.current = false
      const detail = (event as CustomEvent<{ access_token?: string; expiry?: number }>).detail

      if (detail?.access_token) {
        if (sessionRef.current?.user || statusRef.current === 'authenticated') {
          setSessionRevalidationPending(true)
        }
        applySessionFromToken(detail.access_token, detail.expiry).catch((error) => {
          console.error('Post-refresh session sync failed:', error)
        })
        return
      }

      if (sessionRef.current?.user || statusRef.current === 'authenticated') {
        setSessionRevalidationPending(true)
      }
      refreshSessionInternalRef.current().catch((error) => {
        console.error('Post-refresh session sync failed:', error)
      })
    }

    window.addEventListener(AUTH_EXPIRED_EVENT, onAuthExpired as EventListener)
    window.addEventListener(AUTH_REFRESHED_EVENT, onAuthRefreshed)

    return () => {
      window.removeEventListener(AUTH_EXPIRED_EVENT, onAuthExpired as EventListener)
      window.removeEventListener(AUTH_REFRESHED_EVENT, onAuthRefreshed)
    }
  }, [applySessionFromToken, handleSignOut, hasSessionMarker, setSessionRevalidationPending])

  // A tab restored from the back/forward cache can hold a visually complete
  // pre-logout page while JavaScript was frozen. Revalidate the refresh cookie
  // on resume so an expired or cross-tab-revoked session clears that view.
  const lastResumeSyncRef = useRef(0)
  useEffect(() => {
    if (typeof window === 'undefined') return

    const refreshOnResume = () => {
      if (
        document.visibilityState !== 'visible'
        || (status !== 'authenticated' && !hasSessionMarker())
      ) return
      const now = Date.now()
      if (now - lastResumeSyncRef.current < 2000) return
      lastResumeSyncRef.current = now
      if (sessionRef.current?.user || statusRef.current === 'authenticated') {
        setSessionRevalidationPending(true)
      }
      refreshSessionInternalRef.current().catch((error) => {
        console.error('[auth] session refresh after tab resume failed:', error)
      })
    }
    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') refreshOnResume()
    }
    const onPageShow = (event: PageTransitionEvent) => {
      if (event.persisted) refreshOnResume()
    }
    const onFocus = () => refreshOnResume()

    document.addEventListener('visibilitychange', onVisibilityChange)
    window.addEventListener('pageshow', onPageShow)
    window.addEventListener('focus', onFocus)
    return () => {
      document.removeEventListener('visibilitychange', onVisibilityChange)
      window.removeEventListener('pageshow', onPageShow)
      window.removeEventListener('focus', onFocus)
    }
  }, [hasSessionMarker, setSessionRevalidationPending, status])

  // Memoized so consumers only re-render when auth state actually changes,
  // not on every render of the provider.
  const contextValue: AuthContextValue = useMemo(
    () => ({
      session,
      status,
      isRevalidating,
      accessToken,
      refreshSession,
      signIn: handleSignIn,
      signOut: handleSignOut,
      completeMfaLogin,
      requestMagicLink,
      completeMagicLink,
    }),
    [
      session,
      status,
      isRevalidating,
      accessToken,
      refreshSession,
      handleSignIn,
      handleSignOut,
      completeMfaLogin,
      requestMagicLink,
      completeMagicLink,
    ]
  )

  return (
    <AuthContext.Provider value={contextValue}>{children}</AuthContext.Provider>
  )
}

// useSession hook - matches NextAuth's API exactly
export function useSession(): UseSessionReturn {
  const context = useContext(AuthContext)
  const refreshSession = context?.refreshSession

  // Stable across renders so callers can put it in effect deps.
  const update = useCallback(
    async (force?: boolean) => {
      await refreshSession?.(force)
    },
    [refreshSession]
  )

  const session = context?.session ?? null
  const status: SessionStatus = context ? context.status : 'unauthenticated'
  const isRevalidating = context?.isRevalidating ?? false

  // Same object until the session, status, or revalidation state changes, so context consumers
  // downstream (LHSessionProvider) don't re-render on every parent render.
  return useMemo(
    () => ({
      data: session,
      status,
      isRevalidating,
      update,
    }),
    [session, status, isRevalidating, update]
  )
}

// Decode the payload half of a server-signed OAuth state (`payload.sig`,
// base64url). This does NOT verify the signature; /api/auth/google/state does.
export function decodeOAuthStatePayload(state: string): Record<string, any> | null {
  try {
    const body = state.split('.')[0]
    const b64 = body.replace(/-/g, '+').replace(/_/g, '/')
    const json = new TextDecoder().decode(
      Uint8Array.from(atob(b64 + '='.repeat((4 - (b64.length % 4)) % 4)), c => c.charCodeAt(0))
    )
    return JSON.parse(json)
  } catch {
    return null
  }
}

// Validate OAuth state parameter (call this on callback page, after the
// server has verified its signature)
export function validateOAuthState(state: string): { valid: boolean; callbackUrl: string } {
  const defaultResult = { valid: false, callbackUrl: '/redirect_from_auth' }

  try {
    const stateData = decodeOAuthStatePayload(state)
    if (!stateData) return defaultResult
    const stored = getOAuthStateCookie()

    if (!stored) {
      console.error('No stored OAuth state found')
      return defaultResult
    }

    // Single-use nonce: consume it as soon as it's read, so it can't be replayed
    // on any subsequent (including failed) validation within its 5-minute TTL.
    clearOAuthStateCookie()

    // Validate CSRF token matches
    if (stateData.csrf !== stored.csrf) {
      console.error('OAuth CSRF token mismatch')
      return defaultResult
    }

    // Validate state is not too old (5 minutes max)
    const stateAge = Date.now() - stateData.timestamp
    if (stateAge > 5 * 60 * 1000) {
      console.error('OAuth state expired')
      return defaultResult
    }

    return {
      valid: true,
      callbackUrl: stateData.callbackUrl || '/redirect_from_auth',
    }
  } catch (error) {
    console.error('OAuth state validation error:', error)
    return defaultResult
  }
}

// signIn function - matches NextAuth's API
export async function signIn(
  provider: string,
  options?: SignInOptions
): Promise<SignInResult | void> {
  // This needs to be called from within a component that has access to the context
  // For now, we'll handle it differently for Google OAuth which needs redirect

  if (provider === 'google') {
    // Store org context from cookies if present (for compatibility)
    // The options should contain orgSlug and orgId if needed

    // Generate CSRF token for state parameter
    const csrfToken = generateSecureToken()
    const callbackUrl = options?.callbackUrl || '/'
    const stateData: Record<string, any> = {
      callbackUrl,
      csrf: csrfToken,
      timestamp: Date.now(),
    }

    // For custom domains, embed returnOrigin so the main domain callback can bounce back
    if (isCustomDomain()) {
      stateData.returnOrigin = window.location.origin
    }

    const state = btoa(JSON.stringify(stateData))

    // Store CSRF token in cookie for validation on callback
    setOAuthStateCookie(csrfToken)

    // Always use main domain for redirect URI; only one URI registered with Google
    const redirectUri = `${window.location.protocol}//${getLEARNHOUSE_DOMAIN_VAL()}/auth/callback/google`

    // Get Google OAuth URL from server (client ID lives server-side only)
    const authResponse = await fetch('/api/auth/google/authorize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ redirect_uri: redirectUri, state, scope: 'openid email profile' }),
    })

    if (!authResponse.ok) {
      const errorData = await authResponse.json().catch(() => ({}))
      console.error('Google OAuth initiation failed:', errorData)
      return {
        ok: false,
        error: errorData.error || i18n.t('auth.errors.google_not_configured'),
        url: null,
        status: authResponse.status,
      }
    }

    const { url: googleAuthUrl } = await authResponse.json()
    const safeGoogleUrl = safeExternalUrl(googleAuthUrl)
    if (safeGoogleUrl) window.location.href = safeGoogleUrl
    return
  }

  // For credentials, we need context - this will be handled by the component
  console.warn('signIn should be called with useAuth() context for credentials provider')
  return {
    ok: false,
    error: 'Use useAuth().signIn() for credentials provider',
    url: null,
    status: 400,
  }
}

// signOut function - matches NextAuth's API
export async function signOut(options?: SignOutOptions): Promise<void> {
  const { callbackUrl = '/', redirect = true } = options || {}

  try {
    // Use Next.js API route to ensure cookies are cleared correctly
    await fetch('/api/auth/logout', {
      method: 'POST',
      credentials: 'include',
    })
  } catch (error) {
    console.error('Logout error:', error)
  }

  // Clear cookies
  const { secureAttr, domainAttr } = getCookieAttributes()
  const expireAttr = '; expires=Thu, 01 Jan 1970 00:00:00 GMT'
  document.cookie = `LH_oauth_orgslug=; path=/${expireAttr}${secureAttr}${domainAttr}`
  document.cookie = `LH_oauth_org_id=; path=/${expireAttr}${secureAttr}${domainAttr}`

  // Clear OAuth state
  clearOAuthStateCookie()
  clearSessionMarker()

  // Notify same-origin tabs through storage even when BroadcastChannel is absent.
  let channel: BroadcastChannel | null = null
  try {
    channel = new BroadcastChannel(AUTH_BROADCAST_CHANNEL)
  } catch {
    // BroadcastChannel not available
  }
  publishAuthSync('LOGOUT', channel)
  channel?.close()

  if (redirect) {
    window.location.href = safeRedirectUrl(callbackUrl)
  }
}

// Hook for components that need full auth control
export function useAuth() {
  const context = useContext(AuthContext)

  if (!context) {
    throw new Error('useAuth must be used within a SessionProvider')
  }

  return {
    session: context.session,
    status: context.status,
    accessToken: context.accessToken,
    signIn: context.signIn,
    signOut: context.signOut,
    completeMfaLogin: context.completeMfaLogin,
    requestMagicLink: context.requestMagicLink,
    completeMagicLink: context.completeMagicLink,
    refreshSession: context.refreshSession,
    // Convenience method to get valid access token (refreshes if needed)
    getAccessToken: async (): Promise<string | null> => {
      if (context.accessToken) {
        const expiry = context.session?.tokens?.expiry
        if (expiry && Date.now() + TOKEN_REFRESH_THRESHOLD >= expiry) {
          const refreshed = await context.refreshSession()
          return refreshed ?? context.accessToken
        }
        return context.accessToken
      }
      return null
    },
  }
}

export default SessionProvider
