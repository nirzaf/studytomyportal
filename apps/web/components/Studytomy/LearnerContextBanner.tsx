'use client'

import React from 'react'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { useTranslation } from 'react-i18next'
import { UserRound } from 'lucide-react'

/** Identifies the authenticated account that owns learning activity on this page. */
export default function LearnerContextBanner({ className = '' }: { className?: string }) {
  const session = useLHSession() as any
  const { t } = useTranslation()
  const user = session?.data?.user

  if (session?.status !== 'authenticated' || !user) return null

  const fullName = [user.first_name, user.last_name]
    .filter((part) => typeof part === 'string' && part.trim().length > 0)
    .join(' ')
    .trim()
  const displayName = fullName || user.username

  if (typeof displayName !== 'string' || displayName.trim().length === 0) return null

  return (
    <p className={`flex min-w-0 items-start gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2.5 text-sm leading-5 text-gray-800 ${className}`}>
      <UserRound size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-gray-600" />
      <span className="min-w-0 break-words">
        {t('lti.link.signed_in_as', { email: displayName.trim() })}
      </span>
    </p>
  )
}
