'use client'

import { useState } from 'react'

const prompts = [
  { label: 'Key idea', question: 'How would you explain this topic in your own words?' },
  { label: 'Example', question: 'What is one example, and how does it illustrate the key idea?' },
  { label: 'Connection', question: 'How does this topic connect to something you already know?' },
]

export default function RecallPractice({ topic }: { topic: string }) {
  const [index, setIndex] = useState(0)
  const [note, setNote] = useState('')
  const [stage, setStage] = useState<'prepare' | 'recall' | 'compare' | 'complete'>('prepare')
  const [answer, setAnswer] = useState('')
  const prompt = prompts[index]
  const button = 'w-fit rounded-lg bg-teal-800 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50'

  return (
    <section aria-label="Guided recall" className="mx-auto max-w-2xl space-y-4 px-5 py-6 text-slate-900">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="break-all text-xl font-bold">{topic}</h3>
        <span className="text-sm font-semibold text-teal-800">Guided recall</span>
      </div>
      <progress aria-label="Recall steps completed" max={prompts.length} value={stage === 'complete' ? prompts.length : index}
        className="h-2 w-full accent-teal-700" />
      {stage === 'complete' ? <p role="status">All three recall steps complete.</p> : (
        <>
          <div className="rounded-xl bg-teal-50 p-4">
            <p className="text-sm font-semibold text-teal-900">{prompt.label} · {index + 1} of {prompts.length}</p>
            <p className="mt-2">{prompt.question}</p>
          </div>
          {stage === 'prepare' && <form className="grid gap-3" onSubmit={event => {
            event.preventDefault()
            if (note.trim()) setStage('recall')
          }}>
            <label className="grid gap-2 text-sm font-semibold">
              Your study note
              <textarea value={note} onChange={event => setNote(event.target.value)} maxLength={2000}
                rows={3} placeholder="Write a short answer after checking your lesson or textbook."
                className="rounded-lg border border-slate-300 p-3 font-normal" />
            </label>
            <button type="submit" className={button} disabled={!note.trim()}>Hide note and practise</button>
          </form>}
          {stage === 'recall' && <form className="grid gap-3" onSubmit={event => {
            event.preventDefault()
            if (answer.trim()) setStage('compare')
          }}>
            <label className="grid gap-2 text-sm font-semibold">
              What do you remember?
              <textarea value={answer} onChange={event => setAnswer(event.target.value)} maxLength={2000}
                rows={3} className="rounded-lg border border-slate-300 p-3 font-normal" />
            </label>
            <button type="submit" className={button} disabled={!answer.trim()}>Compare with my note</button>
          </form>}
          {stage === 'compare' && <>
            <div className="grid gap-3 break-words text-sm sm:grid-cols-2">
              <div className="rounded-lg border border-slate-200 p-3"><h4 className="font-semibold">Your answer</h4><p className="mt-1 whitespace-pre-wrap">{answer}</p></div>
              <div className="rounded-lg border border-slate-200 p-3"><h4 className="font-semibold">Your study note</h4><p className="mt-1 whitespace-pre-wrap">{note}</p></div>
            </div>
            <div className="flex flex-wrap gap-2">
              <button type="button" className={button} onClick={() => {
                setAnswer('')
                setStage('recall')
              }}>Practise again</button>
              <button type="button" className={button} onClick={() => {
                if (index + 1 === prompts.length) {
                  setStage('complete')
                  return
                }
                setIndex(index + 1)
                setAnswer('')
                setNote('')
                setStage('prepare')
              }}>I remembered it</button>
            </div>
          </>}
        </>
      )}
    </section>
  )
}
