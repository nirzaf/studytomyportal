'use client'

import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { checkMathAnswer, isMathTopic, mathChallenge, type MathTopic } from './mathPractice'

export const GRADE_EIGHT_MATH_WIDGET = '<!-- studytomy-widget:grade8-math-v1 -->\n'

function Range({ label, value, max, onChange }: {
  label: string
  value: number
  max: number
  onChange: (_value: number) => void
}) {
  return <label className="grid gap-1 text-sm font-medium">
    <span>{label} <span className="tabular-nums">{value}</span></span>
    <input type="range" min={1} max={max} value={value}
      className="h-8 w-full accent-teal-700"
      onChange={(event) => onChange(event.currentTarget.valueAsNumber)} />
  </label>
}

function FractionBar({ shaded, parts, label }: { shaded: number; parts: number; label: string }) {
  return <div className="grid h-8 gap-0.5" style={{ gridTemplateColumns: `repeat(${parts}, minmax(0, 1fr))` }} role="img" aria-label={label}>
    {Array.from({ length: parts }, (_, index) => <span key={index} aria-hidden="true"
      className={index < shaded ? 'bg-teal-700' : 'bg-slate-200'} />)}
  </div>
}

function MathChallenge({ topic }: { topic: MathTopic }) {
  const { t } = useTranslation()
  const [completed, setCompleted] = useState(0)
  const [response, setResponse] = useState('')
  const [feedback, setFeedback] = useState<ReturnType<typeof checkMathAnswer> | null>(null)
  const challenge = mathChallenge(topic, completed)
  const correct = feedback === 'correct'

  return <form className="grid gap-3 border-t border-slate-200 pt-4" onSubmit={event => {
    event.preventDefault()
    if (!correct) setFeedback(checkMathAnswer(response, challenge.answer))
  }}>
    <div className="flex flex-wrap items-baseline justify-between gap-1">
      <h3 className="font-semibold">{t('playgrounds.practice_studio.challenge', 'Try a challenge')}</h3>
      <span className="text-sm text-slate-600">{t('playgrounds.practice_studio.level', 'Level {{level}} of 3', { level: challenge.level })}</span>
    </div>
    <label className="grid gap-2 text-sm">
      {challenge.prompt}
      <input type="text" inputMode="numeric" maxLength={6} value={response} readOnly={correct}
        className="min-h-11 rounded-md border border-slate-300 px-3"
        onChange={event => setResponse(event.currentTarget.value)} />
    </label>
    <div role="status" className="text-sm text-slate-700">
      {feedback === 'invalid' && t('playgrounds.practice_studio.invalid', 'Enter a whole number.')}
      {feedback === 'retry' && challenge.hint}
      {correct && t('playgrounds.practice_studio.correct', 'Correct. Ready for the next challenge?')}
    </div>
    {correct
      ? <button type="button" className="min-h-11 w-fit rounded-md bg-teal-800 px-4 text-sm font-semibold text-white" onClick={() => {
        setCompleted(value => value + 1)
        setResponse('')
        setFeedback(null)
      }}>{t('playgrounds.practice_studio.next', 'Next challenge')}</button>
      : <button type="submit" className="min-h-11 w-fit rounded-md bg-teal-800 px-4 text-sm font-semibold text-white">
        {t('playgrounds.practice_studio.check', 'Check answer')}
      </button>}
  </form>
}

export default function GradeEightMathWidget({ initialTopic = 'fractions', showTopicPicker = true }: { initialTopic?: MathTopic; showTopicPicker?: boolean } = {}) {
  const { t } = useTranslation()
  const [topic, setTopic] = useState<MathTopic>(initialTopic)
  const [factor, setFactor] = useState(2)
  const [width, setWidth] = useState(6)
  const [height, setHeight] = useState(3)
  const [term, setTerm] = useState(3)
  const controls = 'grid gap-4 border-t border-slate-200 pt-4'
  const topics = {
    fractions: t('playgrounds.math_demo.fractions', 'Equivalent fractions'),
    perimeter: t('playgrounds.math_demo.perimeter', 'Rectangle perimeter'),
    squares: t('playgrounds.math_demo.squares', 'Square numbers'),
  }
  let practice: ReactNode

  if (topic === 'perimeter') {
    practice = <div className={controls}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Range label={t('playgrounds.math_demo.width', 'Width (cm)')} value={width} max={10} onChange={setWidth} />
        <Range label={t('playgrounds.math_demo.height', 'Height (cm)')} value={height} max={6} onChange={setHeight} />
      </div>
      <div aria-hidden="true" className="h-32">
        <div className="border-2 border-teal-800 bg-teal-50" style={{ width: width * 20, height: height * 20 }} />
      </div>
      <output aria-live="polite" className="font-semibold">
        {t('playgrounds.math_demo.distance', 'Perimeter: {{value}} cm', { value: 2 * (width + height) })}
      </output>
    </div>
  } else if (topic === 'squares') {
    practice = <div className={controls}>
      <Range label={t('playgrounds.math_demo.term', 'Term position')} value={term} max={6} onChange={setTerm} />
      <div className="grid w-fit gap-1" style={{ gridTemplateColumns: `repeat(${term}, 1.4rem)` }} aria-hidden="true">
        {Array.from({ length: term * term }, (_, index) => <span key={index} className="aspect-square bg-teal-700" />)}
      </div>
      <output aria-live="polite" className="font-semibold">
        {t('playgrounds.math_demo.tiles', '{{term}} rows of {{term}} tiles = {{total}}', { term, total: term * term })}
      </output>
    </div>
  } else {
    practice = <div className={controls}>
      <Range label={t('playgrounds.math_demo.factor', 'Multiply both parts by')} value={factor} max={4} onChange={setFactor} />
      <FractionBar shaded={2} parts={3} label={t('playgrounds.math_demo.shaded', '{{shaded}} of {{parts}} parts shaded', { shaded: 2, parts: 3 })} />
      <FractionBar shaded={2 * factor} parts={3 * factor} label={t('playgrounds.math_demo.shaded', '{{shaded}} of {{parts}} parts shaded', { shaded: 2 * factor, parts: 3 * factor })} />
      <output aria-live="polite" className="font-semibold">2/3 = {2 * factor}/{3 * factor}</output>
    </div>
  }

  return <section className="min-h-0 flex-1 overflow-auto bg-white px-4 py-6 text-slate-900 sm:px-8">
    <div className="mx-auto grid w-full max-w-md gap-4">
      {showTopicPicker ? <label className="grid gap-1 text-sm font-medium">
        {t('playgrounds.math_demo.choose', 'Choose an exercise')}
        <select value={topic} onChange={(event) => {
          if (isMathTopic(event.currentTarget.value)) setTopic(event.currentTarget.value)
        }} className="min-h-11 rounded-md border border-slate-300 bg-white px-2">
          {Object.entries(topics).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label> : <h3 className="text-xl font-semibold">{topics[topic]}</h3>}
      {practice}
      <MathChallenge key={topic} topic={topic} />
    </div>
  </section>
}
