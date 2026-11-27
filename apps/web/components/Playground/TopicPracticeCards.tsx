'use client'

import { useState } from 'react'
import { BookOpen, Check } from 'lucide-react'
import { buildTopicPractice, matchesTopicAnswer } from './topicPractice'

export default function TopicPractice({ topic, cards }: {
  topic: string
  cards: ReturnType<typeof buildTopicPractice>
}) {
  const [index, setIndex] = useState(0)
  const [answer, setAnswer] = useState('')
  const [feedback, setFeedback] = useState<'retry' | 'correct' | null>(null)
  const [complete, setComplete] = useState(false)
  const card = cards[index]
  const correct = feedback === 'correct'

  return <section className="mx-auto max-w-2xl space-y-4 px-5 py-6 text-slate-900 sm:px-8" aria-label="Generated topic practice">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="break-words text-xl font-bold">{topic}</h3>
      <span className="text-sm font-semibold text-teal-800">{complete ? 'Complete' : `Card ${index + 1} of ${cards.length}`}</span>
    </div>
    <progress aria-label="Cards completed" max={cards.length} value={index + Number(correct)} className="h-2 w-full accent-teal-700" />
    {complete ? <p role="status" className="flex items-center gap-2 rounded-xl bg-teal-50 p-4 text-teal-950">
      <Check className="h-5 w-5" aria-hidden="true" /> Practice complete. Enter another topic to keep going.
    </p> : <form className="space-y-4" onSubmit={event => {
      event.preventDefault()
      if (!correct) setFeedback(matchesTopicAnswer(answer, card.answer) ? 'correct' : 'retry')
    }}>
      <div className="rounded-xl border border-teal-200 bg-teal-50 p-4 sm:p-5">
        <p className="text-sm font-semibold text-teal-900">Complete the passage</p>
        <p className="mt-2 leading-relaxed">{correct ? card.sentence : <>{card.before}<span className="rounded bg-teal-200 px-2" aria-label="missing word">_____</span>{card.after}</>}</p>
        <p className="mt-3 flex items-center gap-2 text-xs text-slate-600"><BookOpen className="h-4 w-4 shrink-0" aria-hidden="true" />{card.source.title}, page {card.source.page}</p>
      </div>
      <label className="grid gap-1 text-sm font-semibold">Missing word
        <input value={answer} onChange={event => setAnswer(event.currentTarget.value)} readOnly={correct} maxLength={100}
          autoComplete="off" className="min-h-11 rounded-lg border border-slate-300 px-3 font-normal" />
      </label>
      <p role="status" className="text-sm text-slate-700">
        {correct ? 'Correct. The full passage is shown above.' : feedback === 'retry' ? `Try again. The word begins with “${card.answer[0]}” and has ${[...card.answer].length} letters.` : ''}
      </p>
      {correct ? <button type="button" className="min-h-11 rounded-lg bg-teal-800 px-4 text-sm font-bold text-white" onClick={() => {
        if (index + 1 === cards.length) {
          setComplete(true)
          return
        }
        setIndex(index + 1)
        setAnswer('')
        setFeedback(null)
      }}>{index + 1 === cards.length ? 'Finish practice' : 'Next card'}</button> :
      <button type="submit" disabled={!answer.trim()} className="min-h-11 rounded-lg bg-teal-800 px-4 text-sm font-bold text-white disabled:opacity-50">Check answer</button>}
    </form>}
  </section>
}
