'use client'

import { useLHSession } from '@components/Contexts/LHSessionContext'
import {
  getActivityLearningAvailability,
  LearningRequestError,
  recoverActivityLearningSurface,
  sendActivityLearningAction,
  startActivityLearningSurface,
} from '@services/ai/ai'
import type { ActivityLearningAvailability } from '@services/ai/ai'
import type {
  LearningActionRequest, LearningOperation, LearningSurface, LearningSurfaceRequest,
} from '@services/ai/learningSurface'
import { getAuthenticatedSessionIdentity } from '@/lib/auth/sessionScope'
import { useIsPresent } from 'motion/react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import LearningSurfaceView from './LearningSurfaceView'

const button = 'min-h-11 rounded-md border border-white/30 px-3 py-2 text-sm text-white hover:bg-white/15 disabled:opacity-60 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-300'

function lostAccess(error: unknown) {
  return error instanceof LearningRequestError && error.code !== 'LEARNING_SURFACE_ALLOWANCE' &&
    ([401, 403, 404].includes(error.status) || error.code === 'POLICY_NOT_APPROVED')
}

function feedbackKey(error: unknown) {
  if (!(error instanceof LearningRequestError)) return 'failed'
  if (error.code === 'LEARNING_SURFACE_ALLOWANCE' || error.status === 429) return 'allowance'
  if (error.code === 'PRACTICE_ANSWER_INVALID') return 'invalid_answer'
  if (error.code === 'LEARNING_SURFACE_UNSUPPORTED' || error.code === 'LEARNING_SURFACE_UNAVAILABLE') {
    return 'unavailable'
  }
  return 'failed'
}

export default function ActivityPracticePanel({ activityUuid }: { activityUuid: string }) {
  const session = useLHSession()
  const isPresent = useIsPresent()
  const accessToken = session?.data?.tokens?.access_token
  const user = session?.data?.user
  if (!isPresent || session?.status !== 'authenticated' || !accessToken || !activityUuid) return null
  const identity = getAuthenticatedSessionIdentity(user?.id ?? user?.user_uuid, session.data)
  return <PracticePanel key={`${identity}:${activityUuid}`} activityUuid={activityUuid} accessToken={accessToken} />
}

function PracticePanel({ activityUuid, accessToken }: { activityUuid: string; accessToken: string }) {
  const { t } = useTranslation()
  const [available, setAvailable] = useState(false)
  const [allowExplore, setAllowExplore] = useState(false)
  const [allowPractice, setAllowPractice] = useState(false)
  const [surface, setSurface] = useState<LearningSurface | null>(null)
  const [pending, setPending] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const active = useRef<AbortController | null>(null)
  const unfinishedStart = useRef<LearningSurfaceRequest | null>(null)
  const unfinishedAction = useRef<{ signature: string; request: LearningActionRequest } | null>(null)
  const applyAvailability = useCallback((result: ActivityLearningAvailability) => {
    setAllowExplore(result.allow_explore)
    setAllowPractice(result.allow_practice)
    setAvailable(result.allow_explore || result.allow_practice)
    setSurface(result.surface && (
      result.surface.mode === 'explore' ? result.allow_explore : result.allow_practice
    ) ? result.surface : null)
  }, [])

  useEffect(() => {
    function clearView() {
      active.current?.abort()
      active.current = null
      setPending(false)
      setNotice(null)
      setAvailable(false)
      setAllowExplore(false)
      setAllowPractice(false)
      setSurface(null)
    }

    function clear() {
      clearView()
      unfinishedStart.current = null
      unfinishedAction.current = null
    }

    function recover() {
      const controller = new AbortController()
      active.current = controller
      getActivityLearningAvailability(activityUuid, accessToken, controller.signal).then((result) => {
        if (active.current !== controller) return
        applyAvailability(result)
      }).catch(() => {
        if (active.current !== controller) return
        setAvailable(false)
        setAllowExplore(false)
        setAllowPractice(false)
        setSurface(null)
      }).finally(() => {
        if (active.current === controller) {
          active.current = null
          setPending(false)
        }
      })
    }

    function restore(event: PageTransitionEvent) {
      if (!event.persisted) return
      clear()
      recover()
    }

    function revalidateVisibility() {
      clearView()
      if (document.visibilityState === 'visible') recover()
    }

    recover()
    document.addEventListener('visibilitychange', revalidateVisibility)
    window.addEventListener('pagehide', clear)
    window.addEventListener('pageshow', restore)
    return () => {
      active.current?.abort()
      active.current = null
      document.removeEventListener('visibilitychange', revalidateVisibility)
      window.removeEventListener('pagehide', clear)
      window.removeEventListener('pageshow', restore)
    }
  }, [activityUuid, accessToken, applyAvailability])

  function revoke() {
    unfinishedStart.current = null
    unfinishedAction.current = null
    setAvailable(false)
    setAllowExplore(false)
    setAllowPractice(false)
    setSurface(null)
    setNotice('access_lost')
  }

  async function run(work: (_controller: AbortController) => Promise<void>) {
    if (active.current) return
    const controller = new AbortController()
    active.current = controller
    setPending(true)
    setNotice(null)
    try {
      await work(controller)
    } catch (error) {
      if (active.current !== controller) return
      if (error instanceof LearningRequestError && error.code === 'PRACTICE_ACTION_UNAVAILABLE') {
        unfinishedStart.current = null
        unfinishedAction.current = null
        setAvailable(false)
        setAllowExplore(false)
        setAllowPractice(false)
        setSurface(null)
        try {
          const result = await getActivityLearningAvailability(activityUuid, accessToken, controller.signal)
          if (active.current === controller) applyAvailability(result)
        } catch {
          // The server remains authoritative; a failed recheck leaves the controls hidden.
        }
        if (active.current === controller) setNotice('unavailable')
        return
      }
      if (lostAccess(error)) revoke()
      else setNotice(feedbackKey(error))
    } finally {
      if (active.current === controller) {
        active.current = null
        setPending(false)
      }
    }
  }

  function start(mode: LearningSurface['mode']) {
    if (mode === 'explore' ? !allowExplore : !allowPractice) return Promise.resolve()
    return run(async (controller) => {
      const request = unfinishedStart.current?.mode === mode
        ? unfinishedStart.current
        : { schema_version: 1 as const, activity_uuid: activityUuid, request_id: crypto.randomUUID(), mode }
      unfinishedStart.current = request
      const result = await startActivityLearningSurface(request, accessToken, controller.signal)
      if (active.current !== controller) return
      unfinishedStart.current = null
      unfinishedAction.current = null
      setSurface(result)
    })
  }

  function reload() {
    return run(async (controller) => {
      const result = await getActivityLearningAvailability(activityUuid, accessToken, controller.signal)
      if (active.current !== controller) return
      if (result.surface?.request_id === unfinishedStart.current?.request_id) unfinishedStart.current = null
      if (result.surface && (result.surface.surface_id !== unfinishedAction.current?.request.surface_id ||
        result.surface.revision !== unfinishedAction.current?.request.expected_revision)) unfinishedAction.current = null
      applyAvailability(result)
    })
  }

  function submit(operation: LearningOperation) {
    if (!surface) return Promise.resolve()
    return run(async (controller) => {
      const signature = JSON.stringify([surface.surface_id, surface.revision, operation])
      const request = unfinishedAction.current?.signature === signature
        ? unfinishedAction.current.request
        : { ...operation, surface_id: surface.surface_id, expected_revision: surface.revision, request_id: crypto.randomUUID() }
      unfinishedAction.current = { signature, request }
      try {
        const result = await sendActivityLearningAction(activityUuid, request, accessToken, controller.signal)
        if (active.current !== controller) return
        unfinishedAction.current = null
        setSurface(result)
      } catch (error) {
        if (active.current !== controller) return
        if (!(error instanceof LearningRequestError) || !['STALE_VERSION', 'IDEMPOTENCY_CONFLICT'].includes(error.code ?? '')) {
          throw error
        }
        const result = await recoverActivityLearningSurface(activityUuid, surface.surface_id, accessToken, controller.signal)
        if (active.current !== controller) return
        unfinishedAction.current = null
        setSurface(result)
        setNotice('practice_updated')
      }
    })
  }

  function cancel() {
    active.current?.abort()
    active.current = null
    setPending(false)
    setNotice('cancelled')
  }

  if (!available) {
    return notice === 'access_lost'
      ? <p role="status" className="text-sm text-white">{t('ai.learning_surface.access_lost')}</p>
      : null
  }

  return (
    <section aria-label={t('ai.learning_surface.lesson_practice')} className="space-y-3 pb-2">
      <div className="flex flex-wrap gap-2">
        {allowExplore && <button type="button" className={button} disabled={pending} onClick={() => { void start('explore') }}>
          {t('ai.learning_surface.show_visually')}
        </button>}
        {allowPractice && <button type="button" className={button} disabled={pending} onClick={() => { void start('practice') }}>
          {t('ai.learning_surface.start_practice')}
        </button>}
        {pending && <button type="button" className={button} onClick={cancel}>{t('ai.learning_surface.cancel')}</button>}
        {!pending && notice === 'cancelled' && (
          <button type="button" className={button} onClick={() => { void reload() }}>{t('ai.learning_surface.reload')}</button>
        )}
      </div>
      {pending && <p role="status" className="text-sm text-white">{t('ai.learning_surface.pending')}</p>}
      {notice && <p role="status" className="text-sm text-white">{t(`ai.learning_surface.${notice}`)}</p>}
      {surface && (
        <LearningSurfaceView key={surface.surface_id} surface={surface} onAction={submit} busy={pending} />
      )}
    </section>
  )
}
