'use client'

import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  getGuardianConsentEvents,
  getGuardianConsentNotice,
  listMyGuardianRelationships,
  recordGuardianConsentChoice,
  StudyConsentRequestError,
  withdrawGuardianConsentChoice,
  type GuardianRelationshipSummary,
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

type GuardianConsentPanelProps = {
  accessToken: string
  orgUuid: string
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

export default function GuardianConsentPanel({
  accessToken,
  orgUuid,
}: GuardianConsentPanelProps) {
  const { t } = useTranslation()
  const [relationships, setRelationships] = useState<GuardianRelationshipSummary[]>([])
  const [relationshipsLoaded, setRelationshipsLoaded] = useState(false)
  const [selectedLearnerUuid, setSelectedLearnerUuid] = useState<string>('')
  const [notices, setNotices] = useState<StudyConsentNoticeRead[]>([])
  const [noticesLoading, setNoticesLoading] = useState(false)
  const [noticesError, setNoticesError] = useState(false)
  const [events, setEvents] = useState<StudyConsentEventRead[]>([])
  const [historyReady, setHistoryReady] = useState(false)
  const [historyError, setHistoryError] = useState(false)
  const [pendingPurpose, setPendingPurpose] = useState<StudyConsentPurpose | null>(null)
  const [feedback, setFeedback] = useState<{
    kind: 'success' | 'error'
    purpose: StudyConsentPurpose
  } | null>(null)
  const [refreshTick, setRefreshTick] = useState(0)

  useEffect(() => {
    let active = true
    listMyGuardianRelationships(accessToken, orgUuid)
      .then((items) => {
        if (!active) return
        const eligible = items.filter(
          (rel) =>
            rel.status === 'accepted' &&
            Array.isArray(rel.approved_scopes) &&
            rel.approved_scopes.includes('consent_management')
        )
        setRelationships(eligible)
        setSelectedLearnerUuid((current) =>
          eligible.some((rel) => rel.learner_uuid === current)
            ? current
            : eligible[0]?.learner_uuid ?? ''
        )
        setRelationshipsLoaded(true)
      })
      .catch(() => {
        if (!active) return
        setRelationships([])
        setRelationshipsLoaded(true)
      })
    return () => {
      active = false
    }
  }, [accessToken, orgUuid])

  const loadDependentState = useCallback(async () => {
    if (!selectedLearnerUuid) return
    setNoticesLoading(true)
    setNoticesError(false)
    setHistoryReady(false)
    setHistoryError(false)

    const [noticeResults, historyResult] = await Promise.all([
      Promise.allSettled(
        PURPOSES.map((purpose) =>
          getGuardianConsentNotice(accessToken, orgUuid, selectedLearnerUuid, purpose)
        )
      ),
      getGuardianConsentEvents(accessToken, orgUuid, selectedLearnerUuid)
        .then((list) => ({ ok: true as const, list }))
        .catch(() => ({ ok: false as const, list: [] as StudyConsentEventRead[] })),
    ])

    const nextNotices: StudyConsentNoticeRead[] = []
    let hadNoticeError = false
    for (const res of noticeResults) {
      if (res.status === 'fulfilled') {
        if (res.value) nextNotices.push(res.value)
      } else {
        hadNoticeError = true
      }
    }
    setNotices(nextNotices)
    setNoticesError(hadNoticeError)
    setNoticesLoading(false)

    setEvents(historyResult.list)
    setHistoryError(!historyResult.ok)
    setHistoryReady(historyResult.ok)
  }, [accessToken, orgUuid, selectedLearnerUuid])

  useEffect(() => {
    if (!selectedLearnerUuid) return
    void loadDependentState()
  }, [loadDependentState, selectedLearnerUuid, refreshTick])

  const selectedRelationship = useMemo(
    () => relationships.find((rel) => rel.learner_uuid === selectedLearnerUuid) ?? null,
    [relationships, selectedLearnerUuid]
  )

  const noticesByPurpose = useMemo(
    () => new Map(notices.map((notice) => [notice.purpose, notice])),
    [notices]
  )

  if (!relationshipsLoaded || relationships.length === 0 || !selectedRelationship) {
    return null
  }

  const saveChoice = async (
    purpose: StudyConsentPurpose,
    choice: Extract<StudyConsentChoice, 'accept' | 'decline'>
  ) => {
    const notice = noticesByPurpose.get(purpose)
    if (!notice || !historyReady || pendingPurpose) return
    setPendingPurpose(purpose)
    setFeedback(null)
    try {
      const event = await recordGuardianConsentChoice(
        accessToken,
        orgUuid,
        selectedRelationship.learner_uuid,
        {
          purpose,
          choice,
          notice_version: notice.notice_version,
          scope_codes: notice.scope_codes,
        },
        `guardian-consent-${crypto.randomUUID()}`
      )
      setEvents((current) => [
        event,
        ...current.filter((item) => item.event_uuid !== event.event_uuid),
      ])
      setFeedback({ kind: 'success', purpose })
    } catch (error) {
      setFeedback({ kind: 'error', purpose })
      if (error instanceof StudyConsentRequestError && [403, 404, 409].includes(error.status)) {
        setRefreshTick((tick) => tick + 1)
      }
    } finally {
      setPendingPurpose(null)
    }
  }

  const withdrawChoice = async (purpose: StudyConsentPurpose, eventUuid: string) => {
    if (!historyReady || pendingPurpose) return
    setPendingPurpose(purpose)
    setFeedback(null)
    try {
      const event = await withdrawGuardianConsentChoice(
        accessToken,
        orgUuid,
        selectedRelationship.learner_uuid,
        eventUuid,
        `guardian-withdraw-${crypto.randomUUID()}`
      )
      setEvents((current) => [
        event,
        ...current.filter((item) => item.event_uuid !== event.event_uuid),
      ])
      setFeedback({ kind: 'success', purpose })
    } catch {
      setFeedback({ kind: 'error', purpose })
      setRefreshTick((tick) => tick + 1)
    } finally {
      setPendingPurpose(null)
    }
  }

  return (
    <section
      aria-labelledby="studytomy-guardian-consent-title"
      className="rounded-lg border border-gray-200 bg-white p-5 space-y-4"
    >
      <div>
        <h2 id="studytomy-guardian-consent-title" className="font-semibold text-gray-900">
          Guardian Consent for Dependent Learners
        </h2>
        <p className="mt-1 text-sm leading-6 text-gray-600">
          Review server-approved notices and record or withdraw your guardian consent choices for each authorized dependent learner.
        </p>
      </div>

      <div className="rounded-md bg-gray-50 p-3 space-y-2">
        <label
          htmlFor="guardian-dependent-select"
          className="block text-xs font-semibold uppercase tracking-wide text-gray-600"
        >
          Authorized dependent learner
        </label>
        <select
          id="guardian-dependent-select"
          value={selectedRelationship.learner_uuid}
          onChange={(e) => {
            setFeedback(null)
            setSelectedLearnerUuid(e.target.value)
          }}
          className="w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900"
        >
          {relationships.map((rel) => (
            <option key={rel.relationship_uuid} value={rel.learner_uuid}>
              {rel.learner_uuid} (authority v{rel.authority_version})
            </option>
          ))}
        </select>
        <p className="text-xs text-gray-500">
          Approved scopes: {selectedRelationship.approved_scopes.join(', ')}
          {selectedRelationship.valid_to
            ? ` · Valid until ${new Date(selectedRelationship.valid_to).toLocaleDateString()}`
            : ''}
        </p>
      </div>

      {noticesLoading ? (
        <p role="status" aria-live="polite" className="text-sm text-gray-600">
          {t('account.privacy_consent_loading')}
        </p>
      ) : (
        <div className="space-y-3">
          {noticesError && (
            <p role="alert" className="text-sm text-red-800">
              {t('account.privacy_consent_load_error')}
            </p>
          )}
          {!notices.length && !noticesError && (
            <p role="status" className="text-sm text-gray-600">
              {t('account.privacy_consent_unavailable')}
            </p>
          )}
          {PURPOSES.map((purpose) => {
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
                  {' · '}
                  {t('account.privacy_scope_codes', { scopes: notice.scope_codes.join(', ') })}
                </p>
                <p className="mt-3 text-sm text-gray-700" aria-live="polite">
                  {historyError
                    ? t('account.privacy_history_error')
                    : !historyReady
                      ? t('account.privacy_history_loading')
                      : state.choice
                        ? t('account.privacy_consent_current', {
                            choice:
                              state.choice === 'mixed'
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
                    {t(
                      feedback.kind === 'success'
                        ? 'account.privacy_consent_saved'
                        : 'account.privacy_consent_save_error'
                    )}
                  </p>
                )}
              </article>
            )
          })}
        </div>
      )}
    </section>
  )
}
