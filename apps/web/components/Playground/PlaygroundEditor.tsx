'use client'

import React, { useState, useCallback, useRef, useEffect } from 'react'
import Image from 'next/image'
import { useTranslation } from 'react-i18next'
import Link from 'next/link'
import { motion, type Transition, type TargetAndTransition } from 'motion/react'
import {
  ArrowLeft,
  Check,
  FloppyDisk,
  Eye,
  EyeSlash,
  SlidersHorizontal,
  ArrowSquareOut,
  PencilSimple,
  CircleNotch,
} from '@phosphor-icons/react'
import { SlashIcon, DividerVerticalIcon } from '@radix-ui/react-icons'
import PlaygroundPreview from './PlaygroundPreview'
import PlaygroundChatPanel from './PlaygroundChatPanel'
import PlaygroundOptionsModal from './PlaygroundOptionsModal'
import { useQueryClient } from '@tanstack/react-query'
import { queryKeys } from '@/lib/query/keys'
import {
  learnerAdaptationForPlayground,
  startPlaygroundSession,
  iteratePlayground,
  type PlaygroundLearnerAdaptation,
} from '@services/playgrounds/generator'
import {
  updatePlayground,
  getPlayground,
  getPlaygroundReviewStatus,
  submitPlaygroundReviewDecision,
  PlaygroundReviewError,
  type Playground,
  type PlaygroundReviewStatus,
} from '@services/playgrounds/playgrounds'
import { useLHAnalytics, AnalyticsEvent } from '@services/analytics'

interface Course {
  course_uuid: string
  name: string
}

interface PlaygroundEditorProps {
  playground: Playground
  orgslug: string
  accessToken: string
  orgCourses?: Course[]
}

type Message = { role: 'user' | 'model'; content: string }

const logoAnimations: { initial: TargetAndTransition; animate: TargetAndTransition; transition: Transition }[] = [
  {
    initial: { y: 10, opacity: 0 },
    animate: { y: 0, opacity: 1 },
    transition: { duration: 0.4, ease: 'easeOut' as const },
  },
  {
    initial: { x: -10, opacity: 0 },
    animate: { x: 0, opacity: 1 },
    transition: { duration: 0.4, ease: 'easeOut' as const },
  },
  {
    initial: { scale: 0.7, opacity: 0 },
    animate: { scale: 1, opacity: 1 },
    transition: { duration: 0.35, ease: 'easeOut' as const },
  },
]

const EditorLearnHouseLogo = () => {
  const [animation] = useState(
    () => logoAnimations[Math.floor(Math.random() * logoAnimations.length)]
  )
  return (
    <div className="bg-black rounded-md w-[25px] h-[25px] flex items-center justify-center overflow-hidden flex-shrink-0">
      <motion.div
        initial={animation.initial}
        animate={animation.animate}
        transition={animation.transition}
      >
        <Image src="/studytomy-mini.png" alt="Studytomy" width={14} height={14} className="invert" />
      </motion.div>
    </div>
  )
}

export default function PlaygroundEditor({
  playground: initialPlayground,
  orgslug,
  accessToken,
  orgCourses = [],
}: PlaygroundEditorProps) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { track } = useLHAnalytics('dashboard')
  const [playground, setPlayground] = useState(initialPlayground)
  const [title, setTitle] = useState(initialPlayground.name)
  const [html, setHtml] = useState<string>(initialPlayground.html_content || '')
  const [streamingHtml, setStreamingHtml] = useState<string>('')
  const [generationError, setGenerationError] = useState<string | null>(null)
  const [isStreaming, setIsStreaming] = useState(false)
  const [sessionUuid, setSessionUuid] = useState<string | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [iterationCount, setIterationCount] = useState(0)
  const [isSaving, setIsSaving] = useState(false)
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saved'>('idle')
  const [publishError, setPublishError] = useState<string | null>(null)
  const [reviewStatus, setReviewStatus] = useState<PlaygroundReviewStatus | null>(null)
  const [reviewStatusTick, setReviewStatusTick] = useState(0)
  const [reviewModalOpen, setReviewModalOpen] = useState(false)
  const [reviewBusy, setReviewBusy] = useState(false)
  const [reviewError, setReviewError] = useState<{ message: string; stale?: boolean } | null>(null)
  const [reviewNotice, setReviewNotice] = useState<string | null>(null)
  const [rightsConfirmed, setRightsConfirmed] = useState(false)
  const [accessibilityConfirmed, setAccessibilityConfirmed] = useState(false)
  const [ageSuitabilityConfirmed, setAgeSuitabilityConfirmed] = useState(false)
  const [evidenceReference, setEvidenceReference] = useState('')
  const [optionsOpen, setOptionsOpen] = useState(false)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [selectedCourseUuid, setSelectedCourseUuid] = useState<string>('')
  const [learnerAdaptation, setLearnerAdaptation] = useState<PlaygroundLearnerAdaptation>(
    () => learnerAdaptationForPlayground(initialPlayground)
  )
  const [titleFocused, setTitleFocused] = useState(false)
  const [isSavingTitle, setIsSavingTitle] = useState(false)
  const [titleSaveStatus, setTitleSaveStatus] = useState<'idle' | 'saved'>('idle')
  const titleInputRef = useRef<HTMLInputElement>(null)
  const toggleFullscreen = () => setIsFullscreen((f) => !f)
  const MAX_ITERATIONS = 10

  const titleDirty = title !== playground.name

  const selectedCourse = orgCourses.find((c) => c.course_uuid === selectedCourseUuid) || null
  const savedLearnerAdaptation = learnerAdaptationForPlayground(playground)

  // Auto-save title on blur
  const handleTitleBlur = useCallback(async () => {
    setTitleFocused(false)
    const trimmed = title.trim()
    if (!trimmed || trimmed === playground.name) return
    setIsSavingTitle(true)
    try {
      const updated = await updatePlayground(playground.playground_uuid, { name: trimmed }, accessToken)
      setPlayground(updated)
      setTitle(updated.name)
      setTitleSaveStatus('saved')
      setTimeout(() => setTitleSaveStatus('idle'), 2000)
      queryClient.invalidateQueries({ queryKey: queryKeys.playgrounds.detail(playground.playground_uuid) })
      queryClient.invalidateQueries({ queryKey: queryKeys.playgrounds.list(orgslug) })
    } catch {
      // revert on error
      setTitle(playground.name)
    } finally {
      setIsSavingTitle(false)
    }
  }, [title, playground, accessToken])

  // Keep playground state in sync if the editor mounts with a different playground UUID
  useEffect(() => {
    setPlayground(initialPlayground)
    setTitle(initialPlayground.name)
    setHtml(initialPlayground.html_content || '')
    setLearnerAdaptation(learnerAdaptationForPlayground(initialPlayground))
  }, [initialPlayground.playground_uuid])

  // Reset title dirty state when playground updates externally (e.g. via modal)
  useEffect(() => {
    setTitle(playground.name)
  }, [playground.name])

  // Load StudyTomy independent governance/review status for the saved revision
  useEffect(() => {
    const orgUuid = playground.org_uuid
    if (!orgUuid || !accessToken) {
      setReviewStatus(null)
      return
    }
    let active = true
    getPlaygroundReviewStatus(orgUuid, playground.playground_uuid, accessToken)
      .then((status) => {
        if (active) setReviewStatus(status)
      })
      .catch(() => {
        if (active) setReviewStatus(null)
      })
    return () => {
      active = false
    }
  }, [playground.org_uuid, playground.playground_uuid, playground.update_date, accessToken, reviewStatusTick])

  const reloadPlaygroundRevision = useCallback(async () => {
    try {
      const latest = await getPlayground(playground.playground_uuid, accessToken)
      setPlayground(latest)
      setTitle(latest.name)
      setHtml(latest.html_content || '')
      setLearnerAdaptation(learnerAdaptationForPlayground(latest))
      setReviewError(null)
      setReviewStatusTick((n) => n + 1)
    } catch (err) {
      setReviewError({
        message: err instanceof Error ? err.message : 'Unable to reload Playground revision.',
      })
    }
  }, [playground.playground_uuid, accessToken])

  const handleSubmitReview = async (decision: 'approved' | 'rejected') => {
    if (!playground.org_uuid || !reviewStatus || reviewBusy) return
    const trimmedEvidence = evidenceReference.trim()
    if (trimmedEvidence.length < 4) {
      setReviewError({ message: 'Enter an evidence reference (at least 4 characters).' })
      return
    }
    if (
      decision === 'approved' &&
      !(rightsConfirmed && accessibilityConfirmed && ageSuitabilityConfirmed)
    ) {
      setReviewError({
        message: 'Confirm rights, accessibility, and age suitability before approving.',
      })
      return
    }
    setReviewBusy(true)
    setReviewError(null)
    setReviewNotice(null)
    try {
      const receipt = await submitPlaygroundReviewDecision(
        playground.org_uuid,
        playground.playground_uuid,
        {
          expected_digest: reviewStatus.content_digest,
          decision,
          rights_confirmed: rightsConfirmed,
          accessibility_confirmed: accessibilityConfirmed,
          age_suitability_confirmed: ageSuitabilityConfirmed,
          evidence_reference: trimmedEvidence,
        },
        accessToken,
        crypto.randomUUID()
      )
      setReviewNotice(
        decision === 'approved'
          ? `Approved revision ${receipt.content_digest.slice(0, 12)}.`
          : `Rejected revision ${receipt.content_digest.slice(0, 12)}; learner delivery withdrawn.`
      )
      setReviewModalOpen(false)
      await reloadPlaygroundRevision()
      queryClient.invalidateQueries({ queryKey: queryKeys.playgrounds.detail(playground.playground_uuid) })
      queryClient.invalidateQueries({ queryKey: queryKeys.playgrounds.list(orgslug) })
    } catch (err) {
      const stale = err instanceof PlaygroundReviewError && err.code === 'PLAYGROUND_REVIEW_STALE'
      setReviewError({
        message: err instanceof Error ? err.message : 'Unable to record content review.',
        stale,
      })
    } finally {
      setReviewBusy(false)
    }
  }

  // Keep title in sync if playground name changes via modal
  const handleUpdated = (updated: Playground) => {
    setPlayground(updated)
    setTitle(updated.name)
  }

  const handleSend = useCallback(
    async (prompt: string, nextLearnerAdaptation: PlaygroundLearnerAdaptation) => {
      if (isStreaming) return

      const isFirstGeneration = !sessionUuid
      const sourceMode = isFirstGeneration ? 'start' : 'iterate'

      setIsStreaming(true)
      setStreamingHtml('')
      setGenerationError(null)
      setMessages((prev) => [...prev, { role: 'user' as const, content: prompt }])

      track(AnalyticsEvent.PlaygroundGenerationStarted, {
        is_first_generation: isFirstGeneration,
        iteration_count: iterationCount,
        source_mode: sourceMode,
      })

      let accumulated = ''

      const onChunk = (chunk: string) => {
        accumulated += chunk
        setStreamingHtml(accumulated)
      }

      const onComplete = (uuid: string) => {
        setSessionUuid(uuid)
        setIsStreaming(false)
        setIterationCount((c) => c + 1)
        setHtml(accumulated)
        setStreamingHtml('')
        setMessages((prev) => [...prev, { role: 'model' as const, content: accumulated }])
        track(AnalyticsEvent.PlaygroundGenerationCompleted, {
          output_char_length: accumulated.length,
          source_mode: sourceMode,
        })
      }

      const onError = (error: string) => {
        setIsStreaming(false)
        setStreamingHtml('')
        setGenerationError(error)
        setMessages((prev) => prev.slice(0, -1))
        track(AnalyticsEvent.PlaygroundGenerationFailed, {
          error_message: error,
        })
      }

      setLearnerAdaptation(nextLearnerAdaptation)
      if (!sessionUuid) {
        await startPlaygroundSession(
          playground.playground_uuid,
          prompt,
          {
            playground_name: title,
            playground_description: playground.description || '',
            course_uuid: selectedCourseUuid || undefined,
            course_name: selectedCourse?.name || undefined,
            learner_adaptation: nextLearnerAdaptation,
          },
          accessToken,
          onChunk,
          onComplete,
          onError
        )
      } else {
        await iteratePlayground(
          sessionUuid,
          playground.playground_uuid,
          prompt,
          accessToken,
          onChunk,
          onComplete,
          onError,
          html,
          nextLearnerAdaptation
        )
      }
    },
    [isStreaming, sessionUuid, playground, title, html, accessToken, selectedCourseUuid, selectedCourse, iterationCount, track]
  )

  const handleSave = async () => {
    setIsSaving(true)
    try {
      const updated = await updatePlayground(
        playground.playground_uuid,
        {
          name: title,
          html_content: html || undefined,
          learner_adaptation: learnerAdaptation,
        },
        accessToken
      )
      handleUpdated(updated)
      setSaveStatus('saved')
      setTimeout(() => setSaveStatus('idle'), 2000)
      track(AnalyticsEvent.PlaygroundSaved, {
        has_html_content: !!html,
        html_char_length: html.length,
      })
      queryClient.invalidateQueries({ queryKey: queryKeys.playgrounds.detail(playground.playground_uuid) })
      queryClient.invalidateQueries({ queryKey: queryKeys.playgrounds.list(orgslug) })
    } catch (err) {
      console.error('Save failed:', err)
    } finally {
      setIsSaving(false)
    }
  }

  const handlePublishToggle = async () => {
    const nowPublished = !playground.published
    setPublishError(null)
    try {
      const updated = await updatePlayground(
        playground.playground_uuid,
        { published: nowPublished },
        accessToken
      )
      handleUpdated(updated)
      track(AnalyticsEvent.PlaygroundPublishToggled, {
        now_published: nowPublished,
      })
      queryClient.invalidateQueries({ queryKey: queryKeys.playgrounds.detail(playground.playground_uuid) })
      queryClient.invalidateQueries({ queryKey: queryKeys.playgrounds.list(orgslug) })
    } catch (err) {
      const message =
        err instanceof Error && err.message
          ? err.message
          : 'Publishing requires an independent content review approval for the current revision.'
      setPublishError(message)
      console.error('Publish toggle failed:', err)
    }
  }

  const previewHtml = isStreaming ? streamingHtml : html

  return (
    <div className="activity-editor-page flex flex-col" style={{ paddingTop: 0 }}>
      {/* ── Toolbar ── */}
      <div
        style={{
          borderRadius: 15,
          margin: '20px 40px 16px',
          padding: '10px 16px',
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          background: 'rgba(255, 255, 255, 0.95)',
          backdropFilter: 'blur(12px)',
          WebkitBackdropFilter: 'blur(12px)',
          boxShadow: '0 4px 6px -1px rgba(209,213,219,0.25), 0 2px 4px -2px rgba(209,213,219,0.25)',
          outline: '1px solid rgba(229,231,235,0.5)',
          zIndex: 40,
        }}
      >
        {/* Logo */}
        <Link href="/">
          <EditorLearnHouseLogo />
        </Link>

        <SlashIcon style={{ color: '#d1d5db', flexShrink: 0 }} />

        {/* Back */}
        <Link
          href="/playgrounds"
          className="flex items-center gap-1.5 text-sm text-neutral-400 hover:text-neutral-700 transition-colors flex-shrink-0 font-medium"
        >
          <ArrowLeft size={14} weight="bold" data-dir-flip />
          <span>{t('playgrounds.view.breadcrumb')}</span>
        </Link>

        <SlashIcon style={{ color: '#d1d5db', flexShrink: 0 }} />

        {/* Editable title */}
        <div className="flex-1 flex items-center gap-1.5 min-w-0 group">
          <div className={`flex-1 flex items-center gap-1.5 px-2 py-1 rounded-lg transition-all min-w-0 ${
            titleFocused
              ? 'bg-gray-100 ring-1 ring-gray-300'
              : 'hover:bg-gray-50'
          }`}>
            <input
              ref={titleInputRef}
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onFocus={() => setTitleFocused(true)}
              onBlur={handleTitleBlur}
              onKeyDown={(e) => { if (e.key === 'Enter') titleInputRef.current?.blur() }}
              className="flex-1 text-sm font-semibold text-gray-800 bg-transparent border-0 outline-none focus:ring-0 min-w-0"
              placeholder={t('playgrounds.editor.title_placeholder')}
            />
            {!titleFocused && !isSavingTitle && (
              <PencilSimple
                size={11}
                weight="bold"
                className="text-gray-300 group-hover:text-gray-400 flex-shrink-0 transition-colors cursor-pointer"
                onClick={() => titleInputRef.current?.focus()}
              />
            )}
          </div>

          {/* Title status indicators */}
          {isSavingTitle && (
            <CircleNotch size={12} weight="bold" className="animate-spin text-gray-400 flex-shrink-0" />
          )}
          {titleSaveStatus === 'saved' && !isSavingTitle && (
            <span className="flex items-center gap-0.5 text-[10px] font-semibold text-green-500 flex-shrink-0">
              <Check size={10} weight="bold" />
              {t('playgrounds.editor.saved')}
            </span>
          )}
          {titleDirty && !titleFocused && !isSavingTitle && titleSaveStatus === 'idle' && (
            <span className="w-1.5 h-1.5 rounded-full bg-amber-400 flex-shrink-0" title={t('playgrounds.editor.unsaved_name')} />
          )}
        </div>

        {/* Right controls */}
        <div className="flex items-center gap-2 flex-shrink-0">

          {reviewStatus?.governed && (
            <div className="flex items-center gap-1.5">
              <span
                className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
                  reviewStatus.current_revision_approved
                    ? 'bg-emerald-100 text-emerald-900'
                    : reviewStatus.latest_decision === 'rejected'
                      ? 'bg-red-100 text-red-900'
                      : 'bg-amber-100 text-amber-900'
                }`}
                title={`Saved digest ${reviewStatus.content_digest}`}
              >
                {reviewStatus.current_revision_approved
                  ? 'Review approved'
                  : reviewStatus.latest_decision === 'rejected'
                    ? 'Review rejected'
                    : 'Review required'}
              </span>
              {reviewStatus.can_review && (
                <button
                  type="button"
                  onClick={() => {
                    setReviewError(null)
                    setReviewModalOpen(true)
                  }}
                  className="flex items-center gap-1.5 h-9 px-3 py-2 font-black text-sm nice-shadow rounded-lg transition-all ease-linear hover:cursor-pointer bg-amber-600 hover:bg-amber-700 text-white"
                >
                  Review
                </button>
              )}
            </div>
          )}

          {/* Options */}
          <button
            onClick={() => setOptionsOpen(true)}
            className="flex items-center gap-1.5 h-9 px-3 py-2 font-black text-sm nice-shadow rounded-lg transition-all ease-linear hover:cursor-pointer bg-neutral-100 hover:bg-neutral-200 text-neutral-600"
          >
            <SlidersHorizontal size={14} weight="bold" />
            {t('playgrounds.editor.options')}
          </button>

          <DividerVerticalIcon style={{ marginTop: 'auto', marginBottom: 'auto', color: 'grey', opacity: 0.5 }} />

          {/* Save */}
          <button
            onClick={handleSave}
            disabled={isSaving}
            className={`flex items-center gap-1.5 h-9 px-3 py-2 font-black text-sm nice-shadow rounded-lg transition-all ease-linear hover:cursor-pointer disabled:opacity-50 ${
              saveStatus === 'saved'
                ? 'bg-green-500 hover:bg-green-600 text-white'
                : 'bg-sky-600 hover:bg-sky-700 text-white'
            }`}
          >
            {saveStatus === 'saved' ? (
              <><Check size={14} weight="bold" />{t('playgrounds.editor.saved')}</>
            ) : (
              <><FloppyDisk size={14} weight="bold" />{t('common.save')}</>
            )}
          </button>

          {/* Publish */}
          <button
            onClick={handlePublishToggle}
            className={`flex items-center gap-1.5 h-9 px-3 py-2 font-black text-sm nice-shadow rounded-lg transition-all ease-linear hover:cursor-pointer ${
              playground.published
                ? 'bg-neutral-100 hover:bg-neutral-200 text-neutral-600'
                : 'bg-neutral-600 hover:bg-neutral-700 text-neutral-100'
            }`}
          >
            {playground.published ? (
              <><Eye size={14} weight="bold" />{t('playgrounds.view.published')}</>
            ) : (
              <><EyeSlash size={14} weight="bold" />{t('playgrounds.editor.publish')}</>
            )}
          </button>

          {/* Preview */}
          <Link
            href={`/playground/${playground.playground_uuid}`}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1.5 h-9 px-3 py-2 font-black text-sm nice-shadow rounded-lg transition-all ease-linear bg-neutral-100 hover:bg-neutral-200 text-neutral-600"
          >
            <ArrowSquareOut size={14} weight="bold" />
            {t('editor.preview')}
          </Link>
        </div>
      </div>

      {publishError && (
        <div
          role="alert"
          style={{ margin: '0 40px 12px' }}
          className="flex items-center justify-between gap-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-2.5 text-sm text-amber-900"
        >
          <span>{publishError}</span>
          <button
            type="button"
            onClick={() => setPublishError(null)}
            className="text-xs font-semibold text-amber-800 underline hover:text-amber-950"
          >
            Dismiss
          </button>
        </div>
      )}

      {reviewNotice && (
        <div
          role="status"
          style={{ margin: '0 40px 12px' }}
          className="flex items-center justify-between gap-3 rounded-lg border border-emerald-300 bg-emerald-50 px-4 py-2.5 text-sm text-emerald-950"
        >
          <span>{reviewNotice}</span>
          <button
            type="button"
            onClick={() => setReviewNotice(null)}
            className="text-xs font-semibold text-emerald-800 underline hover:text-emerald-950"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* ── Main split panel ── */}
      <div
        style={{
          margin: '0 40px 20px',
          flex: 1,
          display: 'flex',
          overflow: 'hidden',
          borderRadius: 15,
          background: 'rgba(255, 255, 255, 0.95)',
          backdropFilter: 'blur(12px)',
          WebkitBackdropFilter: 'blur(12px)',
          boxShadow: '0 4px 6px -1px rgba(209,213,219,0.25), 0 2px 4px -2px rgba(209,213,219,0.25)',
          outline: '1px solid rgba(229,231,235,0.5)',
          minHeight: 'calc(100vh - 120px)',
        }}
      >
        {/* Preview: takes all space in fullscreen */}
        <div
          className="flex-1 flex overflow-hidden"
          style={{ borderRight: isFullscreen ? 'none' : '1px solid rgba(229,231,235,0.8)' }}
        >
          <PlaygroundPreview
            html={previewHtml || null}
            adaptation={learnerAdaptation}
            isStreaming={isStreaming}
            isFullscreen={isFullscreen}
            onToggleFullscreen={toggleFullscreen}
          />
        </div>

        {/* Chat panel, hidden in fullscreen */}
        {!isFullscreen && (
          <div className="flex-shrink-0 flex flex-col overflow-hidden" style={{ width: 320 }}>
            <PlaygroundChatPanel
              key={playground.playground_uuid}
              messages={messages}
              isGenerating={isStreaming}
              generationError={generationError}
              iterationCount={iterationCount}
              maxIterations={MAX_ITERATIONS}
              onSend={handleSend}
              disabled={false}
              orgCourses={orgCourses}
              selectedCourseUuid={selectedCourseUuid}
              onCourseChange={setSelectedCourseUuid}
              sessionStarted={sessionUuid !== null}
              initialLearnerAdaptation={learnerAdaptation}
              onLearnerAdaptationChange={setLearnerAdaptation}
            />
          </div>
        )}
      </div>

      {/* Options modal */}
      <PlaygroundOptionsModal
        open={optionsOpen}
        onOpenChange={setOptionsOpen}
        playground={playground}
        orgslug={orgslug}
        onUpdated={handleUpdated}
      />

      {reviewModalOpen && reviewStatus?.governed && reviewStatus.can_review && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="playground-review-dialog-title"
        >
          <div className="w-full max-w-lg rounded-xl border border-gray-200 bg-white p-6 shadow-xl space-y-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 id="playground-review-dialog-title" className="text-lg font-semibold text-gray-900">
                  Independent Playground Review
                </h2>
                <p className="mt-1 text-xs text-gray-600">
                  Reviewing saved revision for <span className="font-medium text-gray-800">{playground.name}</span>.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setReviewModalOpen(false)}
                className="rounded px-2 py-1 text-xs font-semibold text-gray-600 hover:bg-gray-100"
              >
                Close
              </button>
            </div>

            <div className="rounded-md bg-gray-50 p-3 text-xs space-y-1.5">
              <div>
                <span className="font-semibold text-gray-700">Saved digest: </span>
                <span className="font-mono break-all text-gray-900">{reviewStatus.content_digest}</span>
              </div>
              <div>
                <span className="font-semibold text-gray-700">Saved adaptation: </span>
                <span className="text-gray-800">
                  {savedLearnerAdaptation.enabled
                    ? `Enabled (${savedLearnerAdaptation.levels.join(', ')})`
                    : 'Disabled'}
                </span>
              </div>
              {reviewStatus.latest_decision && (
                <div>
                  <span className="font-semibold text-gray-700">Latest decision: </span>
                  <span className="text-gray-800">
                    {reviewStatus.latest_decision}
                    {reviewStatus.latest_reviewed_digest
                      ? ` (${reviewStatus.latest_reviewed_digest.slice(0, 12)})`
                      : ''}
                  </span>
                </div>
              )}
            </div>

            {html !== (playground.html_content || '') && (
              <p className="rounded-md border border-amber-300 bg-amber-50 p-2.5 text-xs text-amber-900">
                Unsaved editor changes are not included in this review; save first if you intend to review them.
              </p>
            )}

            <div className="space-y-2 text-sm text-gray-800">
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={rightsConfirmed}
                  onChange={(e) => setRightsConfirmed(e.target.checked)}
                />
                <span>Rights & syllabus attribution confirmed</span>
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={accessibilityConfirmed}
                  onChange={(e) => setAccessibilityConfirmed(e.target.checked)}
                />
                <span>Accessibility requirements confirmed</span>
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={ageSuitabilityConfirmed}
                  onChange={(e) => setAgeSuitabilityConfirmed(e.target.checked)}
                />
                <span>Age suitability for target learner cohort confirmed</span>
              </label>
              <label className="block pt-1 text-xs font-medium text-gray-700">
                Review evidence / rationale reference
                <input
                  type="text"
                  value={evidenceReference}
                  onChange={(e) => setEvidenceReference(e.target.value)}
                  placeholder="REVIEW-LK-PG-01"
                  className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 text-sm text-gray-900"
                />
              </label>
            </div>

            {reviewError && (
              <div className="rounded-md border border-red-300 bg-red-50 p-3 text-xs text-red-900 space-y-2" role="alert">
                <p>{reviewError.message}</p>
                {reviewError.stale && (
                  <button
                    type="button"
                    onClick={() => void reloadPlaygroundRevision()}
                    className="rounded border border-red-400 bg-white px-2.5 py-1 font-semibold text-red-800 hover:bg-red-100"
                  >
                    Reload revision
                  </button>
                )}
              </div>
            )}

            <div className="flex flex-wrap justify-end gap-2 pt-2">
              <button
                type="button"
                disabled={reviewBusy}
                onClick={() => void handleSubmitReview('rejected')}
                className="rounded-md border border-red-300 bg-white px-3 py-2 text-sm font-semibold text-red-700 hover:bg-red-50 disabled:opacity-50"
              >
                Reject revision
              </button>
              <button
                type="button"
                disabled={reviewBusy}
                onClick={() => void handleSubmitReview('approved')}
                className="rounded-md bg-emerald-700 px-3 py-2 text-sm font-semibold text-white hover:bg-emerald-800 disabled:opacity-50"
              >
                Approve revision
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
