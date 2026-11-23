'use client'

import React, { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  getMyConsentNoticeForSelf,
  recordMyConsentChoiceForSelf,
  StudyConsentRequestError,
  withdrawMyConsentChoiceForSelf,
} from '@services/studytomy/privacy'
import type {
  StudyConsentChoice,
  StudyConsentEventRead,
  StudyConsentNoticeRead,
  StudyConsentPurpose,
} from '@/types/studytomy'

const PURPOSES: StudyConsentPurpose[] = [
  'learning_terms',
  'ai_assistance',
  'community',
  'sharing',
  'marketing',
  'publicity',
]
const EMPTY_NOTICES: StudyConsentNoticeRead[] = []

type ConsentSettingsProps = {
  accessToken: string
  orgUuid: string
  events: StudyConsentEventRead[]
  historyReady: boolean
  historyError: boolean
  onEventRecorded: (_event: StudyConsentEventRead) => void
  onEventsReload: () => Promise<void>
}

function isCurrentNoticeEvent(
  event: StudyConsentEventRead,
  notice: StudyConsentNoticeRead
): boolean {
  const eventPolicies = [...event.policy_version_ids].sort((a, b) => a - b)
  const noticePolicies = [...notice.policy_version_ids].sort((a, b) => a - b)
  return (
    event.purpose === notice.purpose &&
    event.notice_version === notice.notice_version &&
    JSON.stringify(eventPolicies) === JSON.stringify(noticePolicies)
  )
}

function currentChoice(
  events: StudyConsentEventRead[],
  notice: StudyConsentNoticeRead
): { choice: StudyConsentChoice | 'mixed' | null; withdrawEventUuid?: string } {
  const latestByScope = new Map<string, StudyConsentEventRead>()
  const sorted = events
    .filter((event) => isCurrentNoticeEvent(event, notice))
    .sort((left, right) => Date.parse(right.effective_at) - Date.parse(left.effective_at))

  for (const event of sorted) {
    for (const scope of event.scope_codes) {
      if (!latestByScope.has(scope)) latestByScope.set(scope, event)
    }
  }

  const scopeChoices = notice.scope_codes.map((scope) => latestByScope.get(scope))
  if (scopeChoices.every((event) => !event)) return { choice: null }
  if (scopeChoices.some((event) => !event)) return { choice: 'mixed' }

  const choices = new Set(scopeChoices.map((event) => event!.choice))
  if (choices.size !== 1) return { choice: 'mixed' }
  const choice = scopeChoices[0]!.choice
  const sameEvent = scopeChoices.every((event) => event!.event_uuid === scopeChoices[0]!.event_uuid)
  return {
    choice,
    withdrawEventUuid:
      choice === 'accept' && sameEvent ? scopeChoices[0]!.event_uuid : undefined,
  }
}

function isCurrentAcceptedChoice(
  events: StudyConsentEventRead[],
  source: StudyConsentEventRead
): boolean {
  if (source.choice !== 'accept') return false
  const sourceEffectiveAt = Date.parse(source.effective_at)
  if (!Number.isFinite(sourceEffectiveAt)) return false

  const latestByScope = new Map<string, StudyConsentEventRead>()
  const sorted = events
    .filter((event) => event.purpose === source.purpose && Date.parse(event.effective_at) >= sourceEffectiveAt)
    .sort((left, right) => Date.parse(right.effective_at) - Date.parse(left.effective_at))

  for (const event of sorted) {
    for (const scope of event.scope_codes) {
      if (!latestByScope.has(scope)) latestByScope.set(scope, event)
    }
  }

  return source.scope_codes.every((scope) => latestByScope.get(scope)?.event_uuid === source.event_uuid)
}

const ConsentSettings = ({
  accessToken,
  orgUuid,
  events,
  historyReady,
  historyError,
  onEventRecorded,
  onEventsReload,
}: ConsentSettingsProps) => {
  const { t } = useTranslation()
  const [noticeState, setNoticeState] = useState<{
    token: string
    orgUuid: string
    refreshKey: number
    notices: StudyConsentNoticeRead[]
    error: boolean
  } | null>(null)
  const [pendingPurpose, setPendingPurpose] = useState<StudyConsentPurpose | null>(null)
  const [feedback, setFeedback] = useState<{ kind: 'success' | 'error'; purpose?: StudyConsentPurpose } | null>(null)
  const [noticeRefreshKey, setNoticeRefreshKey] = useState(0)

  useEffect(() => {
    let active = true

    Promise.allSettled(
      PURPOSES.map((purpose) => getMyConsentNoticeForSelf(accessToken, orgUuid, purpose))
    ).then((results) => {
      if (!active) return
      const notices: StudyConsentNoticeRead[] = []
      let error = false
      for (const result of results) {
        if (result.status === 'fulfilled') {
          if (result.value) notices.push(result.value)
        } else {
          error = true
        }
      }
      setNoticeState({ token: accessToken, orgUuid, refreshKey: noticeRefreshKey, notices, error })
    })

    return () => { active = false }
  }, [accessToken, orgUuid, noticeRefreshKey])

  const currentNotices = noticeState?.token === accessToken &&
    noticeState.orgUuid === orgUuid &&
    noticeState.refreshKey === noticeRefreshKey
    ? noticeState
    : null
  const notices = currentNotices?.notices ?? EMPTY_NOTICES
  const noticesByPurpose = useMemo(
    () => new Map(notices.map((notice) => [notice.purpose, notice])),
    [notices]
  )

  const saveChoice = async (
    purpose: StudyConsentPurpose,
    choice: Extract<StudyConsentChoice, 'accept' | 'decline'>
  ) => {
    const notice = noticesByPurpose.get(purpose)
    if (!notice || !historyReady || pendingPurpose) return
    setPendingPurpose(purpose)
    setFeedback(null)
    try {
      const event = await recordMyConsentChoiceForSelf(
        accessToken,
        orgUuid,
        {
          purpose,
          choice,
          notice_version: notice.notice_version,
          scope_codes: notice.scope_codes,
        },
        `consent-${crypto.randomUUID()}`
      )
      onEventRecorded(event)
      setFeedback({ kind: 'success', purpose })
    } catch (error) {
      setFeedback({ kind: 'error', purpose })
      if (error instanceof StudyConsentRequestError && [403, 404, 409].includes(error.status)) {
        setNoticeRefreshKey((current) => current + 1)
      }
      void onEventsReload()
    } finally {
      setPendingPurpose(null)
    }
  }

  const withdrawChoice = async (purpose: StudyConsentPurpose, eventUuid: string) => {
    if (!historyReady || pendingPurpose) return
    setPendingPurpose(purpose)
    setFeedback(null)
    try {
      const event = await withdrawMyConsentChoiceForSelf(
        accessToken,
        orgUuid,
        eventUuid,
        `consent-withdraw-${crypto.randomUUID()}`
      )
      onEventRecorded(event)
      setFeedback({ kind: 'success', purpose })
    } catch {
      setFeedback({ kind: 'error', purpose })
      void onEventsReload()
    } finally {
      setPendingPurpose(null)
    }
  }

  const renderPurpose = (purpose: StudyConsentPurpose) => {
    const notice = noticesByPurpose.get(purpose)
    if (!notice) return null
    const state = currentChoice(events, notice)
    const saving = pendingPurpose === purpose

    return (
      <article key={purpose} className="rounded-md border border-gray-200 p-4">
        <h3 className="font-semibold text-gray-900">
          {t(`account.privacy_purpose.${purpose}`)}
        </h3>
        <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-gray-700">
          {notice.notice_text}
        </p>
        <p className="mt-2 text-xs text-gray-500">
          {t('account.privacy_notice_version', { version: notice.notice_version })}
        </p>
        <p className="mt-3 text-sm text-gray-700" aria-live="polite">
          {historyError
            ? t('account.privacy_history_error')
            : !historyReady
              ? t('account.privacy_history_loading')
              : state.choice
                ? t('account.privacy_consent_current', {
                    choice: state.choice === 'mixed'
                      ? t('account.privacy_consent_mixed')
                      : t(`account.privacy_choice.${state.choice}`),
                  })
                : t('account.privacy_consent_no_choice')}
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            className="rounded-md bg-emerald-700 px-3 py-2 text-sm font-medium text-white hover:bg-emerald-800 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={!historyReady || historyError || !!pendingPurpose}
            aria-busy={saving}
            onClick={() => void saveChoice(purpose, 'accept')}
          >
            {saving ? t('account.privacy_consent_saving') : t('account.privacy_consent_accept')}
          </button>
          <button
            type="button"
            className="rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-800 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={!historyReady || historyError || !!pendingPurpose}
            aria-busy={saving}
            onClick={() => void saveChoice(purpose, 'decline')}
          >
            {t('account.privacy_consent_decline')}
          </button>
          {state.withdrawEventUuid && (
            <button
              type="button"
              className="rounded-md border border-amber-300 bg-white px-3 py-2 text-sm font-medium text-amber-900 hover:bg-amber-50 disabled:cursor-not-allowed disabled:opacity-60"
              disabled={!historyReady || historyError || !!pendingPurpose}
              aria-busy={saving}
              onClick={() => void withdrawChoice(purpose, state.withdrawEventUuid!)}
            >
              {t('account.privacy_consent_withdraw')}
            </button>
          )}
        </div>
        {feedback?.purpose === purpose && (
          <p
            className={`mt-3 text-sm ${feedback.kind === 'success' ? 'text-emerald-800' : 'text-red-800'}`}
            role={feedback.kind === 'error' ? 'alert' : 'status'}
            aria-live="polite"
          >
            {t(feedback.kind === 'success' ? 'account.privacy_consent_saved' : 'account.privacy_consent_save_error')}
          </p>
        )}
      </article>
    )
  }

  const requiredNotice = noticesByPurpose.has('learning_terms')
  const optionalPurposes = PURPOSES.filter((purpose) => purpose !== 'learning_terms')
  const hasOptionalNotices = optionalPurposes.some((purpose) => noticesByPurpose.has(purpose))
  const historicalWithdrawals = events.filter((event) => {
    if (!isCurrentAcceptedChoice(events, event)) return false
    const currentNotice = noticesByPurpose.get(event.purpose)
    if (!currentNotice) return true
    return currentChoice(events, currentNotice).withdrawEventUuid !== event.event_uuid
  })

  return (
    <section aria-labelledby="studytomy-consent-settings-title" className="rounded-lg border border-gray-200 bg-white p-5">
      <h2 id="studytomy-consent-settings-title" className="font-semibold text-gray-900">
        {t('account.privacy_consent_title')}
      </h2>
      <p className="mt-2 text-sm leading-6 text-gray-600">
        {t('account.privacy_consent_intro')}
      </p>

      {!currentNotices ? (
        <p role="status" aria-live="polite" className="mt-3 text-sm text-gray-600">
          {t('account.privacy_consent_loading')}
        </p>
      ) : (
        <>
          {currentNotices.error && (
            <p role="alert" className="mt-3 text-sm text-red-800">
              {t('account.privacy_consent_load_error')}
            </p>
          )}
          {!notices.length && !currentNotices.error && (
            <p role="status" className="mt-3 text-sm text-gray-600">
              {t('account.privacy_consent_unavailable')}
            </p>
          )}
          {requiredNotice && (
            <div className="mt-4 space-y-3">
              <h3 className="text-sm font-semibold text-gray-900">
                {t('account.privacy_consent_learning_heading')}
              </h3>
              {renderPurpose('learning_terms')}
            </div>
          )}
          {hasOptionalNotices && (
            <div className="mt-5 space-y-3">
              <div>
                <h3 className="text-sm font-semibold text-gray-900">
                  {t('account.privacy_consent_optional_heading')}
                </h3>
                <p className="mt-1 text-sm text-gray-600">
                  {t('account.privacy_consent_optional_separate')}
                </p>
              </div>
              {optionalPurposes.map(renderPurpose)}
            </div>
          )}
          {historicalWithdrawals.length > 0 && (
            <div className="mt-5 space-y-3">
              <h3 className="text-sm font-semibold text-gray-900">
                {t('account.privacy_consent_prior_heading')}
              </h3>
              {historicalWithdrawals.map((event) => (
                <article key={event.event_uuid} className="rounded-md border border-gray-200 p-4">
                  <h4 className="font-semibold text-gray-900">
                    {t(`account.privacy_purpose.${event.purpose}`)}
                  </h4>
                  <p className="mt-2 text-sm text-gray-700">
                    {t('account.privacy_consent_prior_body', { version: event.notice_version })}
                  </p>
                  <p className="mt-1 text-xs text-gray-500">
                    {t('account.privacy_scope_codes', { scopes: event.scope_codes.join(', ') })}
                  </p>
                  <button
                    type="button"
                    className="mt-3 rounded-md border border-amber-300 bg-white px-3 py-2 text-sm font-medium text-amber-900 hover:bg-amber-50 disabled:cursor-not-allowed disabled:opacity-60"
                    disabled={!historyReady || historyError || !!pendingPurpose}
                    aria-busy={pendingPurpose === event.purpose}
                    onClick={() => void withdrawChoice(event.purpose, event.event_uuid)}
                  >
                    {pendingPurpose === event.purpose
                      ? t('account.privacy_consent_saving')
                      : t('account.privacy_consent_withdraw')}
                  </button>
                  {feedback?.purpose === event.purpose && (
                    <p
                      className={`mt-3 text-sm ${feedback.kind === 'success' ? 'text-emerald-800' : 'text-red-800'}`}
                      role={feedback.kind === 'error' ? 'alert' : 'status'}
                      aria-live="polite"
                    >
                      {t(feedback.kind === 'success' ? 'account.privacy_consent_saved' : 'account.privacy_consent_save_error')}
                    </p>
                  )}
                </article>
              ))}
            </div>
          )}
          {currentNotices.error && notices.length === 0 && (
            <p className="mt-3 text-sm text-gray-600">
              {t('account.privacy_consent_unavailable')}
            </p>
          )}
        </>
      )}
      {!historyReady && !historyError && currentNotices && notices.length > 0 && (
        <p className="mt-3 text-sm text-gray-600" role="status" aria-live="polite">
          {t('account.privacy_history_loading')}
        </p>
      )}
    </section>
  )
}

export default ConsentSettings
