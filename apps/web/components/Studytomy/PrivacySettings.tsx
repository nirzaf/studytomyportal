'use client'

import React, { useEffect, useState } from 'react'
import { ShieldCheck } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import ConsentSettings from '@components/Studytomy/ConsentSettings'
import RecordRequest from '@components/Studytomy/RecordRequest'
import { getMyConsentEventsForSelf, getMyPrivacyStatus } from '@services/studytomy/privacy'
import type { StudyConsentEventRead, StudyPrivacyStatus } from '@/types/studytomy'

const PrivacySettings = ({ orgUuid }: { orgUuid: string }) => {
  const session = useLHSession() as any
  const accessToken = session?.data?.tokens?.access_token as string | undefined
  const { t, i18n } = useTranslation()
  const [result, setResult] = useState<{
    token: string
    status?: StudyPrivacyStatus
    error?: boolean
  } | null>(null)
  const [historyResult, setHistoryResult] = useState<{
    token: string
    orgUuid: string
    events?: StudyConsentEventRead[]
    error?: boolean
  } | null>(null)

  useEffect(() => {
    let active = true
    if (!accessToken) return () => { active = false }

    getMyPrivacyStatus(accessToken)
      .then((status) => {
        if (active) setResult({ token: accessToken, status })
      })
      .catch(() => {
        if (active) setResult({ token: accessToken, error: true })
      })

    return () => { active = false }
  }, [accessToken])

  const currentResult = accessToken
    ? result?.token === accessToken ? result : null
    : { token: '', error: true }
  const status = currentResult?.status
  const hasProtectedProfile = status?.learner_profile_protected === true

  useEffect(() => {
    let active = true
    if (!accessToken || !orgUuid || !hasProtectedProfile) {
      return () => { active = false }
    }

    getMyConsentEventsForSelf(accessToken, orgUuid)
      .then((events) => {
        if (active) setHistoryResult({ token: accessToken, orgUuid, events })
      })
      .catch(() => {
        if (active) setHistoryResult({ token: accessToken, orgUuid, error: true })
      })

    return () => { active = false }
  }, [accessToken, orgUuid, hasProtectedProfile])

  const currentHistory = accessToken && historyResult?.token === accessToken && historyResult.orgUuid === orgUuid
    ? historyResult
    : null

  const refreshConsentEvents = async () => {
    if (!accessToken || !orgUuid) return
    try {
      const events = await getMyConsentEventsForSelf(accessToken, orgUuid)
      setHistoryResult({ token: accessToken, orgUuid, events })
    } catch {
      setHistoryResult({ token: accessToken, orgUuid, error: true })
    }
  }

  const recordConsentEventInHistory = (event: StudyConsentEventRead) => {
    setHistoryResult((current) => {
      if (!accessToken || !orgUuid) return current
      if (current?.token !== accessToken || current.orgUuid !== orgUuid || !current.events) {
        return current
      }
      return {
        token: accessToken,
        orgUuid,
        events: [event, ...current.events.filter((item) => item.event_uuid !== event.event_uuid)],
      }
    })
  }

  return (
    <section aria-labelledby="studytomy-privacy-title" className="space-y-4">
      <header className="rounded-md bg-gray-50 px-5 py-4">
        <h1 id="studytomy-privacy-title" className="text-xl font-bold text-gray-900">
          {t('account.privacy')}
        </h1>
        <p className="mt-1 text-sm text-gray-600">
          {t('account.privacy_description')}
        </p>
      </header>

      {currentResult?.error && (
        <p role="alert" className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-800">
          {t('account.privacy_load_error')}
        </p>
      )}

      {!currentResult?.error && !status && (
        <p role="status" aria-live="polite" className="rounded-md border border-gray-200 bg-white p-4 text-sm text-gray-600">
          {t('account.privacy_loading')}
        </p>
      )}

      {status?.learner_profile_protected ? (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-5">
          <div className="flex items-start gap-3">
            <ShieldCheck aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0 text-emerald-700" />
            <div>
              <h2 className="font-semibold text-emerald-950">{t('account.privacy_private_title')}</h2>
              <p className="mt-2 text-sm leading-6 text-emerald-900">
                {t('account.privacy_private_body')}
              </p>
              <p className="mt-2 text-sm leading-6 text-emerald-900">
                {t('account.privacy_controls_unavailable')}
              </p>
            </div>
          </div>
        </div>
      ) : status ? (
        <div className="rounded-lg border border-gray-200 bg-white p-5">
          <h2 className="font-semibold text-gray-900">{t('account.privacy_profile_title')}</h2>
          <p className="mt-2 text-sm leading-6 text-gray-600">
            {t('account.privacy_profile_body')}
          </p>
        </div>
      ) : null}

      {hasProtectedProfile && accessToken && orgUuid && (
        <RecordRequest key={JSON.stringify([orgUuid, accessToken])} orgUuid={orgUuid} accessToken={accessToken} />
      )}

      {status?.learner_profile_protected && (
        accessToken ? (
          <ConsentSettings
            accessToken={accessToken}
            orgUuid={orgUuid}
            events={currentHistory?.events ?? []}
            historyReady={!!currentHistory?.events && !currentHistory.error}
            historyError={currentHistory?.error === true}
            onEventRecorded={recordConsentEventInHistory}
            onEventsReload={refreshConsentEvents}
          />
        ) : null
      )}

      {status?.learner_profile_protected && (
        <section aria-labelledby="studytomy-consent-history-title" className="rounded-lg border border-gray-200 bg-white p-5">
          <h2 id="studytomy-consent-history-title" className="font-semibold text-gray-900">
            {t('account.privacy_history_title')}
          </h2>
          {currentHistory?.error ? (
            <p role="alert" className="mt-3 text-sm text-red-800">
              {t('account.privacy_history_error')}
            </p>
          ) : !currentHistory?.events ? (
            <p role="status" aria-live="polite" className="mt-3 text-sm text-gray-600">
              {t('account.privacy_history_loading')}
            </p>
          ) : currentHistory.events.length === 0 ? (
            <p className="mt-3 text-sm text-gray-600">
              {t('account.privacy_history_empty')}
            </p>
          ) : (
            <ol className="mt-3 divide-y divide-gray-100">
              {currentHistory.events.map((event) => (
                <li key={event.event_uuid} className="py-3 first:pt-0 last:pb-0">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-medium text-gray-900">
                      {t(`account.privacy_purpose.${event.purpose}`)}
                    </span>
                    <span className="rounded-full bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-700">
                      {t(`account.privacy_choice.${event.choice}`)}
                    </span>
                  </div>
                  <p className="mt-1 text-sm text-gray-600">
                    {t('account.privacy_notice_version', { version: event.notice_version })}
                  </p>
                  <p className="mt-1 text-sm text-gray-600">
                    {t('account.privacy_scope_codes', { scopes: event.scope_codes.join(', ') })}
                  </p>
                  <time className="mt-1 block text-xs text-gray-500" dateTime={event.effective_at}>
                    {t('account.privacy_recorded_at', {
                      date: new Date(event.effective_at).toLocaleString(i18n.language),
                    })}
                  </time>
                </li>
              ))}
            </ol>
          )}
        </section>
      )}
    </section>
  )
}

export default PrivacySettings
