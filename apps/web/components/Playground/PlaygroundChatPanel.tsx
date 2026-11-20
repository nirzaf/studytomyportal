'use client'

import React, { useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { PaperPlaneTilt, Sparkle, CircleNotch, BookOpen, Brain, Plus, X } from '@phosphor-icons/react'
import {
  DEFAULT_LEARNER_LEVELS,
  MAX_ADAPTATION_LABEL_LENGTH,
  MAX_LEARNER_LEVELS,
  MAX_TARGET_SKILLS,
  type PlaygroundLearnerAdaptation,
} from '@services/playgrounds/generator'

interface Message {
  role: 'user' | 'model'
  content: string
}

interface Course {
  course_uuid: string
  name: string
}

interface PlaygroundChatPanelProps {
  messages: Message[]
  isGenerating: boolean
  iterationCount: number
  maxIterations: number
  onSend: (_prompt: string, _learnerAdaptation: PlaygroundLearnerAdaptation) => void
  generationError?: string | null
  disabled?: boolean
  orgCourses?: Course[]
  selectedCourseUuid?: string
  onCourseChange?: (uuid: string) => void
  sessionStarted?: boolean
  initialLearnerAdaptation?: PlaygroundLearnerAdaptation | null
  onLearnerAdaptationChange?: (_learnerAdaptation: PlaygroundLearnerAdaptation) => void
}

function sanitizeLabels(values: readonly string[], maxItems: number): string[] {
  const result: string[] = []
  const seen = new Set<string>()
  for (const raw of values) {
    const trimmed = raw.replace(/[\r\n]+/g, ' ').trim().slice(0, MAX_ADAPTATION_LABEL_LENGTH)
    if (!trimmed) continue
    const key = trimmed.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    result.push(trimmed)
    if (result.length >= maxItems) break
  }
  return result
}

function parseSkillTokens(raw: string): string[] {
  return raw
    .split(',')
    .map((item) => item.replace(/[\r\n]+/g, ' ').trim())
    .filter(Boolean)
}

const SUGGESTION_CHIPS = [
  'playgrounds.chat.chip_quiz',
  'playgrounds.chat.chip_bar_chart',
  'playgrounds.chat.chip_physics',
  'playgrounds.chat.chip_flashcards',
  'playgrounds.chat.chip_math',
  'playgrounds.chat.chip_memory_game',
  'playgrounds.chat.chip_timeline',
  'playgrounds.chat.chip_sorting',
]

export default function PlaygroundChatPanel({
  messages,
  isGenerating,
  iterationCount,
  maxIterations,
  onSend,
  generationError = null,
  disabled,
  orgCourses = [],
  selectedCourseUuid = '',
  onCourseChange,
  sessionStarted = false,
  initialLearnerAdaptation = null,
  onLearnerAdaptationChange,
}: PlaygroundChatPanelProps) {
  const { t } = useTranslation()
  const [input, setInput] = React.useState('')
  const [sourceMode, setSourceMode] = React.useState<'ai' | 'course'>('ai')
  const [adaptationEnabled, setAdaptationEnabled] = React.useState<boolean>(
    initialLearnerAdaptation?.enabled ?? true
  )
  const [levelLabels, setLevelLabels] = React.useState<string[]>(() =>
    initialLearnerAdaptation?.levels && initialLearnerAdaptation.levels.length > 0
      ? [...initialLearnerAdaptation.levels]
      : [...DEFAULT_LEARNER_LEVELS]
  )
  const [focusSkills, setFocusSkills] = React.useState<string[]>(() =>
    initialLearnerAdaptation?.target_skills ? [...initialLearnerAdaptation.target_skills] : []
  )
  const [skillDraft, setSkillDraft] = React.useState('')
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const buildAdaptationPayload = React.useCallback(
    (
      enabled: boolean,
      levels: readonly string[],
      skills: readonly string[],
      pendingSkill = ''
    ): PlaygroundLearnerAdaptation => ({
      enabled,
      levels: sanitizeLabels(levels, MAX_LEARNER_LEVELS),
      target_skills: sanitizeLabels([...skills, ...parseSkillTokens(pendingSkill)], MAX_TARGET_SKILLS),
    }),
    []
  )

  useEffect(() => {
    onLearnerAdaptationChange?.(buildAdaptationPayload(adaptationEnabled, levelLabels, focusSkills))
  }, [adaptationEnabled, levelLabels, focusSkills, buildAdaptationPayload, onLearnerAdaptationChange])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const commitSkillDraft = () => {
    const tokens = parseSkillTokens(skillDraft)
    if (tokens.length === 0) return
    setFocusSkills((prev) => sanitizeLabels([...prev, ...tokens], MAX_TARGET_SKILLS))
    setSkillDraft('')
  }

  const handleSkillKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      commitSkillDraft()
    }
  }

  const handleLevelChange = (index: number, value: string) => {
    setLevelLabels((prev) => prev.map((label, i) => (i === index ? value : label)))
  }

  const handleAddLevel = () => {
    if (levelLabels.length >= MAX_LEARNER_LEVELS) return
    setLevelLabels((prev) => [...prev, ''])
  }

  const handleRemoveLevel = (index: number) => {
    setLevelLabels((prev) => prev.filter((_, i) => i !== index))
  }

  const handleRemoveSkill = (index: number) => {
    setFocusSkills((prev) => prev.filter((_, i) => i !== index))
  }

  const handleSend = () => {
    const trimmed = input.trim()
    if (!trimmed || isGenerating || disabled) return
    const nextAdaptation = buildAdaptationPayload(
      adaptationEnabled,
      levelLabels,
      focusSkills,
      skillDraft
    )
    if (skillDraft.trim()) {
      setFocusSkills(nextAdaptation.target_skills)
      setSkillDraft('')
    }
    onSend(trimmed, nextAdaptation)
    setInput('')
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleSourceMode = (mode: 'ai' | 'course') => {
    setSourceMode(mode)
    if (mode === 'ai') onCourseChange?.('')
    else if (mode === 'course' && orgCourses.length > 0 && !selectedCourseUuid) {
      onCourseChange?.(orgCourses[0].course_uuid)
    }
  }

  const handleChipClick = (chip: string) => {
    setInput(chip)
    textareaRef.current?.focus()
  }

  const handleTextareaChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value)
    e.target.style.height = 'auto'
    e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px'
  }

  const remaining = maxIterations - iterationCount
  const canSend = !!input.trim() && !isGenerating && !disabled && remaining > 0

  return (
    <div className="flex flex-col h-full bg-white">
      {/* Header */}
      <div className="px-4 py-3 border-b border-gray-100 flex-shrink-0">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Sparkle size={15} weight="fill" className="text-neutral-400" />
            <span className="text-sm font-bold text-neutral-700">{t('playgrounds.chat.title')}</span>
          </div>
          <span className="text-[11px] font-medium text-neutral-400 bg-neutral-100 px-2 py-0.5 rounded-full">
            {remaining}/{maxIterations}
          </span>
        </div>

        {/* Source toggle, only shown before session starts and when courses exist */}
        {orgCourses.length > 0 && !sessionStarted && (
          <div className="mt-3 space-y-2">
            <p className="text-[10px] font-bold text-neutral-400 uppercase tracking-wider">{t('playgrounds.chat.source')}</p>
            <div className="flex gap-1.5">
              <button
                onClick={() => handleSourceMode('ai')}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${
                  sourceMode === 'ai'
                    ? 'bg-neutral-800 text-white nice-shadow'
                    : 'bg-neutral-100 text-neutral-500 hover:bg-neutral-200'
                }`}
              >
                <Brain size={12} weight="bold" />
                {t('playgrounds.chat.ai_knowledge')}
              </button>
              <button
                onClick={() => handleSourceMode('course')}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${
                  sourceMode === 'course'
                    ? 'bg-sky-600 text-white nice-shadow'
                    : 'bg-neutral-100 text-neutral-500 hover:bg-neutral-200'
                }`}
              >
                <BookOpen size={12} weight="bold" />
                {t('playgrounds.chat.course')}
              </button>
            </div>

            {/* Course dropdown, only when Course mode is active */}
            {sourceMode === 'course' && (
              <select
                value={selectedCourseUuid}
                onChange={(e) => onCourseChange?.(e.target.value)}
                className="w-full mt-1 px-3 py-2 rounded-lg border border-gray-200 text-xs font-medium text-gray-700 bg-white focus:outline-none focus:ring-2 focus:ring-sky-500 focus:border-transparent"
              >
                {orgCourses.map((c) => (
                  <option key={c.course_uuid} value={c.course_uuid}>
                    {c.name}
                  </option>
                ))}
              </select>
            )}
          </div>
        )}

        <section className="mt-3 rounded-lg border border-sky-100 bg-sky-50/70 p-2.5">
          <label className="flex items-start gap-2 text-xs font-semibold text-slate-700 cursor-pointer">
            <input
              type="checkbox"
              checked={adaptationEnabled}
              onChange={(event) => setAdaptationEnabled(event.target.checked)}
              className="mt-0.5 accent-sky-600"
            />
            <span>{t('playgrounds.chat.adapt_for_learner')}</span>
          </label>
          {adaptationEnabled && (
            <div className="mt-2.5 space-y-2.5">
              {/* Editable level labels */}
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <span className="text-[10px] font-semibold text-slate-600">
                    {t('playgrounds.chat.level_options')}
                  </span>
                  {levelLabels.length < MAX_LEARNER_LEVELS && (
                    <button
                      type="button"
                      onClick={handleAddLevel}
                      className="inline-flex items-center gap-0.5 text-[10px] font-semibold text-sky-700 hover:text-sky-800"
                    >
                      <Plus size={10} weight="bold" />
                      {t('playgrounds.chat.add_level')}
                    </button>
                  )}
                </div>
                <div className="space-y-1">
                  {levelLabels.map((label, index) => (
                    <div key={index} className="flex items-center gap-1">
                      <input
                        type="text"
                        value={label}
                        onChange={(event) => handleLevelChange(index, event.target.value)}
                        maxLength={MAX_ADAPTATION_LABEL_LENGTH}
                        placeholder={t('playgrounds.chat.level_placeholder', { index: index + 1 })}
                        aria-label={t('playgrounds.chat.level_placeholder', { index: index + 1 })}
                        className="flex-1 min-w-0 rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700 focus:outline-none focus:ring-2 focus:ring-sky-500"
                      />
                      <button
                        type="button"
                        onClick={() => handleRemoveLevel(index)}
                        aria-label={t('playgrounds.chat.remove_level', { label: label || index + 1 })}
                        className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-md text-slate-400 hover:bg-slate-200/70 hover:text-slate-600"
                      >
                        <X size={11} weight="bold" />
                      </button>
                    </div>
                  ))}
                </div>
              </div>

              {/* Focus skills chip input */}
              <div className="space-y-1.5">
                <label className="block text-[10px] font-semibold text-slate-600" htmlFor="playground-skills">
                  {t('playgrounds.chat.target_skills')}
                </label>
                {focusSkills.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {focusSkills.map((skill, index) => (
                      <span
                        key={`${skill}-${index}`}
                        className="inline-flex items-center gap-1 rounded-full bg-white border border-sky-200 px-2 py-0.5 text-[11px] font-medium text-slate-700"
                      >
                        <span className="max-w-[180px] truncate">{skill}</span>
                        <button
                          type="button"
                          onClick={() => handleRemoveSkill(index)}
                          aria-label={t('playgrounds.chat.remove_skill', { skill })}
                          className="text-slate-400 hover:text-slate-600"
                        >
                          <X size={10} weight="bold" />
                        </button>
                      </span>
                    ))}
                  </div>
                )}
                {focusSkills.length < MAX_TARGET_SKILLS && (
                  <div className="flex items-center gap-1">
                    <input
                      id="playground-skills"
                      type="text"
                      value={skillDraft}
                      onChange={(event) => setSkillDraft(event.target.value)}
                      onKeyDown={handleSkillKeyDown}
                      maxLength={MAX_ADAPTATION_LABEL_LENGTH}
                      placeholder={t('playgrounds.chat.target_skills_placeholder')}
                      className="flex-1 min-w-0 rounded-md border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700 focus:outline-none focus:ring-2 focus:ring-sky-500"
                    />
                    <button
                      type="button"
                      onClick={commitSkillDraft}
                      disabled={!skillDraft.trim()}
                      className="rounded-md bg-sky-600 px-2 py-1 text-[11px] font-semibold text-white hover:bg-sky-700 disabled:bg-slate-200 disabled:text-slate-400"
                    >
                      {t('playgrounds.chat.add_skill')}
                    </button>
                  </div>
                )}
              </div>

              <p className="text-[10px] leading-relaxed text-slate-500">
                {t('playgrounds.chat.adaptation_privacy_note')}
              </p>
            </div>
          )}
        </section>

        {iterationCount > 0 && (
          <div className="flex items-center gap-0.5 mt-2.5">
            {Array.from({ length: maxIterations }).map((_, i) => (
              <div
                key={i}
                className={`h-1 flex-1 rounded-full transition-all duration-300 ${
                  i < iterationCount ? 'bg-sky-500' : 'bg-gray-100'
                }`}
              />
            ))}
          </div>
        )}
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3 min-h-0">
        {generationError && (
          <p role="alert" className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs leading-relaxed text-rose-800">
            {generationError}
          </p>
        )}
        {messages.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-center px-4 gap-2">
            <div className="w-10 h-10 rounded-xl bg-neutral-100 nice-shadow flex items-center justify-center">
              {sourceMode === 'course'
                ? <BookOpen size={20} weight="fill" className="text-sky-500" />
                : <Sparkle size={20} weight="fill" className="text-neutral-400" />
              }
            </div>
            <p className="text-sm font-semibold text-neutral-600">{t('playgrounds.chat.describe_prompt')}</p>
            <p className="text-xs text-neutral-400">
              {sourceMode === 'course'
                ? t('playgrounds.chat.grounded_hint')
                : t('playgrounds.chat.prompt_hint')
              }
            </p>
          </div>
        ) : (
          messages.map((msg, i) => (
            <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div
                className={`max-w-[88%] px-3 py-2 rounded-xl text-sm leading-relaxed nice-shadow ${
                  msg.role === 'user'
                    ? 'bg-neutral-800 text-white'
                    : 'bg-neutral-100 text-neutral-500'
                }`}
              >
                {msg.role === 'model' ? (
                  <span className="text-xs italic flex items-center gap-1.5">
                    <Sparkle size={11} weight="fill" />
                    {t('playgrounds.chat.generated_chars', { count: msg.content.length.toLocaleString() })}
                  </span>
                ) : (
                  msg.content
                )}
              </div>
            </div>
          ))
        )}

        {isGenerating && (
          <div className="flex justify-start">
            <div className="bg-neutral-100 nice-shadow px-3 py-2 rounded-xl flex items-center gap-2">
              <CircleNotch size={13} weight="bold" className="animate-spin text-sky-500" />
              <span className="text-xs text-neutral-500 font-medium">{t('playgrounds.chat.generating_ellipsis')}</span>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Suggestion chips, only in AI mode */}
      {messages.length === 0 && sourceMode === 'ai' && (
        <div className="px-4 pb-3 flex flex-wrap gap-1.5 flex-shrink-0">
          {SUGGESTION_CHIPS.map((chip) => (
            <button
              key={chip}
              onClick={() => handleChipClick(t(chip))}
              className="text-[11px] font-medium px-2.5 py-1 rounded-full bg-neutral-100 nice-shadow text-neutral-500 hover:bg-neutral-200 hover:text-neutral-700 transition-all"
            >
              {t(chip)}
            </button>
          ))}
        </div>
      )}

      {/* Input */}
      <div className="p-3 border-t border-gray-100 flex-shrink-0">
        <div
          className="nice-shadow rounded-xl bg-neutral-50 overflow-hidden flex flex-col"
          style={{ outline: input ? '1.5px solid #0ea5e9' : undefined }}
        >
          <textarea
            ref={textareaRef}
            value={input}
            onChange={handleTextareaChange}
            onKeyDown={handleKeyDown}
            placeholder={
              remaining === 0
                ? t('playgrounds.chat.max_iterations')
                : iterationCount === 0
                ? t('playgrounds.chat.placeholder_first')
                : t('playgrounds.chat.placeholder_refine')
            }
            disabled={isGenerating || disabled || remaining === 0}
            rows={2}
            className="w-full resize-none bg-transparent px-3 pt-3 pb-1 text-sm text-neutral-800 placeholder:text-neutral-400 focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed"
            style={{ minHeight: 56, maxHeight: 120 }}
          />
          <div className="flex items-center justify-between px-3 pb-2">
            <span className="text-[10px] text-neutral-400 font-medium">
              {remaining === 0 ? (
                <span className="text-red-400">{t('playgrounds.chat.no_generations_left')}</span>
              ) : (
                t('playgrounds.chat.send_hint')
              )}
            </span>
            <button
              onClick={handleSend}
              disabled={!canSend}
              className={`flex items-center gap-1.5 h-7 px-3 rounded-lg text-xs font-black nice-shadow transition-all ease-linear ${
                canSend
                  ? 'bg-sky-600 hover:bg-sky-700 text-white cursor-pointer'
                  : 'bg-neutral-200 text-neutral-400 cursor-not-allowed'
              }`}
            >
              {isGenerating ? (
                <CircleNotch size={12} weight="bold" className="animate-spin" />
              ) : (
                <PaperPlaneTilt size={12} weight="bold" />
              )}
              {isGenerating ? t('playgrounds.chat.generating') : t('playgrounds.chat.send')}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
