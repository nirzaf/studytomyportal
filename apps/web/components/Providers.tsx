'use client'
import React, { useLayoutEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { Loader2 } from 'lucide-react'
import '../lib/i18n'
import { SessionProvider } from '@components/Contexts/AuthContext'
import LHSessionProvider from '@components/Contexts/LHSessionContext'
import AuthFetchInterceptor from '@components/Contexts/AuthFetchInterceptor'
import PostHogProvider from '@components/Contexts/PostHogProvider'
import I18nProvider from '@components/Contexts/I18nContext'
import DirectionProvider from '@components/Contexts/DirectionProvider'
import { BackgroundTasksProvider } from '@components/Contexts/BackgroundTasksContext'
import BackgroundTasksPanel from '@components/BackgroundTasks/BackgroundTasksPanel'
import { QueryClientProvider } from '@tanstack/react-query'
import { ReactQueryDevtools } from '@tanstack/react-query-devtools'
import { makeQueryClient } from '@/lib/query/client'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { useParams, usePathname } from 'next/navigation'
import {
  getAuthenticatedSessionIdentity,
  getOrganizationScope,
} from '@/lib/auth/sessionScope'

function SessionScopedProviders({
  children,
  organizationSlug,
}: {
  children: React.ReactNode
  organizationSlug: string
}) {
  const session = useLHSession()
  const user = session?.data?.user
  const userId = user?.id ?? user?.user_uuid
  const accountIdentity = session?.status === 'authenticated'
    ? getAuthenticatedSessionIdentity(userId, session.data)
    : 'anonymous'
  const identity = `${accountIdentity}:${organizationSlug}`

  return (
    <SessionScopedQueryClient key={identity}>
      {children}
    </SessionScopedQueryClient>
  )
}

function SessionScopedQueryClient({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(() => makeQueryClient())

  // The old client is no longer observable after identity/scope changes; clear
  // its query and mutation stores as the previous provider is replaced.
  useLayoutEffect(() => {
    return () => queryClient.clear()
  }, [queryClient])

  return (
    <QueryClientProvider client={queryClient}>
      {children}
      {process.env.NODE_ENV === 'development' && <ReactQueryDevtools initialIsOpen={false} />}
    </QueryClientProvider>
  )
}

function SessionRevalidationShield() {
  const session = useLHSession()
  const { t } = useTranslation()
  const isRevalidating = session?.isRevalidating ?? false

  useLayoutEffect(() => {
    const content = document.querySelector<HTMLElement>('[data-session-content]')
    if (!content) return

    content.inert = isRevalidating
    if (isRevalidating) {
      content.setAttribute('aria-hidden', 'true')
      content.setAttribute('aria-busy', 'true')
    } else {
      content.removeAttribute('aria-hidden')
      content.removeAttribute('aria-busy')
    }

    return () => {
      content.inert = false
      content.removeAttribute('aria-hidden')
      content.removeAttribute('aria-busy')
    }
  }, [isRevalidating])

  if (!isRevalidating || typeof document === 'undefined') return null

  return createPortal(
    <div
      className="fixed inset-0 z-[2147483647] flex items-center justify-center bg-white text-gray-700"
      role="status"
      aria-live="polite"
      aria-label={t('auth.session_revalidating', 'Checking your session...')}
    >
      <div className="flex items-center gap-3">
        <Loader2 className="h-6 w-6 animate-spin text-gray-500" aria-hidden="true" />
        <span>{t('auth.session_revalidating', 'Checking your session...')}</span>
      </div>
    </div>,
    document.body,
  )
}

export default function Providers({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() || '/'
  const routeParams = useParams<Record<string, string | string[]>>()
  const organizationSlug = getOrganizationScope(pathname, routeParams)

  return (
    <SessionProvider refetchInterval={600000}>
      <LHSessionProvider>
        <SessionScopedProviders organizationSlug={organizationSlug}>
          <AuthFetchInterceptor />
          <PostHogProvider>
            <I18nProvider>
              {/* Inside I18nProvider so it re-renders when the language changes. */}
              <DirectionProvider>
                <BackgroundTasksProvider scopeKey={organizationSlug}>
                  {children}
                  <BackgroundTasksPanel />
                  <SessionRevalidationShield />
                </BackgroundTasksProvider>
              </DirectionProvider>
            </I18nProvider>
          </PostHogProvider>
        </SessionScopedProviders>
      </LHSessionProvider>
    </SessionProvider>
  )
}
