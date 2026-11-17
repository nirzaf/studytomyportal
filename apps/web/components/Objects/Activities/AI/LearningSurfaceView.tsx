'use client'

import { useId, useRef, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import type { LearningComponent, LearningOperation, LearningQuestion, LearningSurface } from '@services/ai/learningSurface'

const focus = 'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-300'
const button = `min-h-11 rounded-md border border-white/30 px-3 py-2 font-medium text-white hover:bg-white/15 disabled:opacity-60 ${focus}`
const field = `min-h-11 min-w-0 w-full rounded-md border border-white/30 bg-gray-950 px-3 text-white ${focus}`

function reducedFraction(numerator: number, denominator: number) {
  let a = Math.abs(numerator)
  let b = denominator
  while (b) [a, b] = [b, a % b]
  return [numerator / a, denominator / a] as const
}

type FractionVisual = Extract<LearningComponent, { kind: 'fraction_explorer' }>
type LineVisual = Extract<LearningComponent, { kind: 'number_line' }>

function FractionExplorer({ visual }: { visual: FractionVisual }) {
  const { t } = useTranslation()
  const [parts, setParts] = useState(visual.parts)
  const [selected, setSelected] = useState(visual.selected)
  const [num, denom] = reducedFraction(selected, parts)
  const alreadySimplest = denom === parts
  const equivalent = alreadySimplest ? `${num * 2}/${denom * 2}` : `${num}/${denom}`
  const fraction = `${selected}/${parts}`
  function reset() {
    setParts(visual.parts)
    setSelected(visual.selected)
  }

  return (
    <fieldset className="min-w-0 space-y-3">
      <legend className="font-semibold text-white">{t('ai.learning_surface.fraction_explorer')}</legend>
      <div aria-hidden="true" className="flex h-9 overflow-hidden rounded-md border border-white/40 bg-gray-950">
        {Array.from({ length: parts }, (_, index) => (
          <div
            key={index}
            className={`min-w-0 flex-1 border-e border-white/50 last:border-e-0 ${index < selected ? 'bg-violet-300' : ''}`}
          />
        ))}
      </div>
      <p className="text-white" aria-live="polite">
        {t('ai.learning_surface.fraction_equivalent', {
          fraction, equivalent, selected, parts, interpolation: { escapeValue: false },
        })}
      </p>
      <div className="grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="grid gap-1 text-white">
          {t('ai.learning_surface.parts')}
          <select
            className={field}
            value={parts}
            onChange={(event) => {
              const next = Number(event.currentTarget.value)
              setParts(next)
              setSelected(Math.min(selected, next))
            }}
          >
            {Array.from({ length: 12 }, (_, index) => <option key={index + 1}>{index + 1}</option>)}
          </select>
        </label>
        <label className="grid gap-1 text-white">
          {t('ai.learning_surface.selected_parts')}
          <select className={field} value={selected} onChange={(event) => setSelected(Number(event.currentTarget.value))}>
            {Array.from({ length: parts + 1 }, (_, index) => <option key={index}>{index}</option>)}
          </select>
        </label>
      </div>
      <button type="button" className={button} onClick={reset}>
        {t('ai.learning_surface.reset_visual')}
      </button>
    </fieldset>
  )
}

function NumberLine({ visual }: { visual: LineVisual }) {
  const { t } = useTranslation()
  const id = useId()
  const steps = (visual.last_mark - visual.first_mark) * visual.divisions_per_unit
  const [position, setPosition] = useState(0)
  const [num, denom] = reducedFraction(visual.first_mark * visual.divisions_per_unit + position, visual.divisions_per_unit)
  const value = denom === 1 ? String(num) : `${num}/${denom}`

  return (
    <fieldset className="min-w-0 space-y-3">
      <legend className="font-semibold text-white">{t('ai.learning_surface.number_line')}</legend>
      <div dir="ltr" aria-hidden="true" className="px-2 pt-3">
        <div className="relative border-t-2 border-white/55">
          {Array.from({ length: steps + 1 }, (_, index) => (
            <span
              key={index}
              style={{ left: `${index * 100 / steps}%` }}
              className={`absolute top-0 border-s border-white/55 ${index % visual.divisions_per_unit === 0 ? 'h-3' : 'h-1.5'}`}
            />
          ))}
          <span
            style={{ left: `${position * 100 / steps}%` }}
            className="absolute top-0 size-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-gray-950 bg-violet-300"
          />
        </div>
        <div className="flex justify-between pt-3 text-white">
          <span>{visual.first_mark}</span>
          <span>{visual.last_mark}</span>
        </div>
      </div>
      <label htmlFor={id} className="block text-white">{t('ai.learning_surface.move_marker')}</label>
      <input
        dir="ltr"
        id={id}
        type="range"
        min={0}
        max={steps}
        step={1}
        aria-valuetext={value}
        value={position}
        onChange={(event) => setPosition(event.currentTarget.valueAsNumber)}
        className={`block min-h-11 w-full accent-violet-300 ${focus}`}
      />
      <output htmlFor={id} className="block text-white">
        {t('ai.learning_surface.position', { value, interpolation: { escapeValue: false } })}
      </output>
      <button type="button" className={button} onClick={() => setPosition(0)}>
        {t('ai.learning_surface.reset_visual')}
      </button>
    </fieldset>
  )
}

function QuestionForm({
  question, disabled, submit,
}: {
  question: LearningQuestion
  disabled: boolean
  submit: (_answer: string) => void
}) {
  const { t } = useTranslation()
  const id = useId()
  const [answer, setAnswer] = useState('')
  const submitForm = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!disabled && answer.trim()) submit(answer.trim())
  }

  return (
    <form onSubmit={submitForm} className="min-w-0 space-y-3">
      <fieldset disabled={disabled} className="min-w-0 space-y-2">
        <legend className="font-semibold wrap-break-word text-white">{question.prompt}</legend>
        {question.kind === 'single_choice' ? question.choices.map((choice) => (
          <label key={choice.id} className="flex min-h-11 items-center gap-2 wrap-break-word text-white">
            <input
              className={`shrink-0 accent-violet-300 ${focus}`}
              type="radio"
              name={id}
              value={choice.id}
              checked={answer === choice.id}
              onChange={() => setAnswer(choice.id)}
            />
            <span className="min-w-0">{choice.label}</span>
          </label>
        )) : (
          <label className="grid gap-1 text-white">
            {t('ai.learning_surface.fraction_answer')}
            <input className={field} value={answer} maxLength={48} onChange={(event) => setAnswer(event.currentTarget.value)} />
          </label>
        )}
      </fieldset>
      <button type="submit" className={button} disabled={disabled || !answer.trim()}>
        {t('ai.learning_surface.submit_answer')}
      </button>
    </form>
  )
}

export default function LearningSurfaceView({
  surface, onAction, busy = false,
}: {
  surface: LearningSurface
  onAction?: (_operation: LearningOperation) => Promise<void>
  busy?: boolean
}) {
  const { t } = useTranslation()
  const titleId = useId()
  const inFlight = useRef(false)
  const [pending, setPending] = useState(false)
  const [failed, setFailed] = useState(false)
  const feedback = surface.components.find((item) => item.kind === 'feedback')
  const question = surface.components.find((item) => item.kind === 'question')
  const actions = onAction ? surface.actions : []
  async function act(operation: LearningOperation) {
    if (!onAction || busy || inFlight.current || !actions.includes(operation.action)) return
    inFlight.current = true
    setPending(true)
    setFailed(false)
    try {
      await onAction(operation)
    } catch {
      setFailed(true)
    } finally {
      inFlight.current = false
      setPending(false)
    }
  }

  return (
    <section aria-labelledby={titleId} className="min-w-0 space-y-4 rounded-xl border border-white/25 bg-gray-950/60 p-4 text-sm leading-relaxed">
      <h3 id={titleId} className="text-base font-semibold text-white">
        {t(surface.mode === 'explore' ? 'ai.learning_surface.explore' : 'ai.learning_surface.practice')}
      </h3>
      {surface.components.map((item) => {
        switch (item.kind) {
          case 'explanation':
            return <p key={item.id} className="wrap-break-word whitespace-pre-wrap text-white">{item.text}</p>
          case 'fraction_explorer':
            return <FractionExplorer key={question?.question.id ?? item.id} visual={item} />
          case 'number_line':
            return <NumberLine key={question?.question.id ?? item.id} visual={item} />
          case 'question':
            return feedback || !actions.includes('submit_answer')
              ? <p key={item.id} className="wrap-break-word font-semibold text-white">{item.question.prompt}</p>
              : <QuestionForm
                  key={`question-${item.question.id}`}
                  question={item.question}
                  disabled={pending || busy}
                  submit={(answer) => { void act({ action: 'submit_answer', answer }) }}
                />
          case 'hint':
            return (
              <div key={item.id} role="status" className="wrap-break-word text-white">
                <p className="font-semibold">{t('ai.learning_surface.hint')}</p>
                <p className="whitespace-pre-wrap">{item.text}</p>
              </div>
            )
          case 'feedback':
            return (
              <div key={item.id} role="status" className="wrap-break-word text-white">
                <p className="font-semibold">
                  {t(item.outcome === 'correct' ? 'ai.learning_surface.correct' : 'ai.learning_surface.incorrect')}
                </p>
                <p className="whitespace-pre-wrap">{item.text}</p>
                <p>{t('ai.learning_surface.saved')}</p>
              </div>
            )
        }
      })}
      {actions.some((action) => action !== 'submit_answer') && (
        <div className="flex flex-wrap gap-2">
          {actions.filter((action) => action !== 'submit_answer').map((action) => (
            <button key={action} type="button" className={button} disabled={pending || busy} onClick={() => { void act({ action }) }}>
              {t(`ai.learning_surface.${action}`)}
            </button>
          ))}
        </div>
      )}
      {pending && !busy && <p role="status" className="text-white">{t('ai.learning_surface.pending')}</p>}
      {failed && <p role="alert" className="text-white">{t('ai.learning_surface.failed')}</p>}
    </section>
  )
}
