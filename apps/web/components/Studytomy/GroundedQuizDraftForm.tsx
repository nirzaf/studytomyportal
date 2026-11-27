'use client'

import { type FormEvent, useEffect, useRef, useState } from 'react'
import { Button } from '@components/ui/button'
import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'

type Citation = { source_uuid: string; sha256: string; label: string; page: number; quote: string }
type Option = { text: string; is_correct: boolean }
type Draft = { title: string; questions: { question: string; options: Option[]; citation: Citation }[] }
type Preview = {
  title: string
  contents: { questions: {
    questionUUID: string; questionText: string
    options: { text: string; assigned_right_answer: boolean }[]
  }[] }
  citations: Record<string, Citation>
  matching_pages_omitted: number
}
type CitationResult = { status: 'current' | 'stale' | 'none'; citations: Record<string, Citation> }

export type QuizDestination = {
  assignmentUuid: string
  tasks: { uuid: string; title: string }[]
  canAddTask: boolean
  onSaved: () => void
}

export default function GroundedQuizDraftForm({ route, token, selected, destination }: {
  route: string; token: string; selected: string[]; destination: QuizDestination
}) {
  const [draft, setDraft] = useState<Draft>()
  const [omitted, setOmitted] = useState(0)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ text: string; error?: boolean }>()
  const [citations, setCitations] = useState<CitationResult>()
  const [savedTask, setSavedTask] = useState<{ uuid: string; title: string }>()
  const pending = useRef<AbortController | null>(null)
  const saveAttempt = useRef<{ signature: string; id: string } | null>(null)

  useEffect(() => () => {
    pending.current?.abort()
    pending.current = null
  }, [token])

  async function request<T>(path: string, body: object | null, receive: (_result: T) => void) {
    if (pending.current) return
    const controller = new AbortController()
    pending.current = controller
    setBusy(true)
    setMessage(undefined)
    try {
      const response = await secureFetch(`${getAPIUrl()}${route}/${path}`, {
        ...RequestBodyWithAuthHeader(body ? 'POST' : 'GET', body, null, token),
        redirect: 'error',
        signal: controller.signal,
      })
      const result = await response.json().catch(() => null)
      if (!response.ok) {
        throw new Error(typeof result?.detail === 'string' ? result.detail : 'The quiz request failed. Try again.')
      }
      if (!controller.signal.aborted) receive(result as T)
    } catch (error) {
      if (!controller.signal.aborted) {
        setMessage({ text: error instanceof Error ? error.message : 'The quiz request failed.', error: true })
      }
    } finally {
      if (pending.current === controller) pending.current = null
      if (!pending.current) setBusy(false)
    }
  }

  function preview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const topic = String(new FormData(event.currentTarget).get('topic') ?? '').trim()
    if (topic.length < 3 || !selected.length || !destination.canAddTask) return
    setDraft(undefined)
    setOmitted(0)
    void request<Preview>('grounded-quiz-preview', { source_uuids: selected, topic }, (result) => {
      const questions = result.contents.questions.map((question) => {
        const citation = result.citations[question.questionUUID]
        if (!citation) throw new Error('The quiz preview did not include a citation for every question.')
        return {
          question: question.questionText,
          options: question.options.map((option) => ({ text: option.text, is_correct: option.assigned_right_answer })),
          citation,
        }
      })
      setDraft({ title: result.title, questions })
      setOmitted(result.matching_pages_omitted)
    })
  }

  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!draft || !destination.canAddTask) return
    const body = {
      assignment_uuid: destination.assignmentUuid,
      title: draft.title.trim(),
      questions: draft.questions.map(({ question, options, citation: { source_uuid, sha256, page, quote } }) => ({
        question: question.trim(), options: options.map((option) => ({ ...option, text: option.text.trim() })),
        citation: { source_uuid, sha256, page, quote },
      })),
    }
    const signature = JSON.stringify(body)
    if (saveAttempt.current?.signature !== signature) {
      saveAttempt.current = { signature, id: crypto.randomUUID() }
    }
    void request<{ assignment_task_uuid: string }>(
      'grounded-quiz-draft', { ...body, request_id: saveAttempt.current.id }, (receipt) => {
        saveAttempt.current = null
        setSavedTask({ uuid: receipt.assignment_task_uuid, title: body.title })
        setDraft(undefined)
        setMessage({ text: 'Quiz draft saved for teacher review.' })
        destination.onSaved()
      },
    )
  }

  function lookup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const task = String(new FormData(event.currentTarget).get('task') ?? '')
    if (!task) return
    setCitations(undefined)
    void request<CitationResult>(
      `assignments/${encodeURIComponent(destination.assignmentUuid)}/tasks/${encodeURIComponent(task)}/grounded-quiz-sources`,
      null, setCitations,
    )
  }

  const tasks = savedTask && !destination.tasks.some((task) => task.uuid === savedTask.uuid)
    ? [...destination.tasks, savedTask] : destination.tasks

  return <section className="space-y-3 border-t pt-4" aria-label="Grounded quiz drafting">
    {destination.canAddTask && <form onSubmit={preview} className="space-y-2">
      <label className="grid gap-1 text-sm">
        Quiz topic
        <input name="topic" minLength={3} maxLength={500} required className="rounded border bg-background p-2" />
      </label>
      <Button type="submit" size="sm" disabled={busy || !selected.length}>Generate quiz draft</Button>
    </form>}
    {busy && <p role="status" className="text-sm">Working on your request…</p>}
    {message && <p role={message.error ? 'alert' : 'status'} className="text-sm">{message.text}</p>}
    {draft && <form className="space-y-3" onSubmit={save}>
      <label className="grid gap-1 text-sm">
        Quiz title
        <input required maxLength={160} value={draft.title} disabled={busy} className="rounded border bg-background p-2"
          onChange={(event) => setDraft({ ...draft, title: event.target.value })} />
      </label>
      {omitted > 0 && <p role="status" className="text-sm">Some matching source pages were omitted from this preview.</p>}
      {draft.questions.map((question, index) => {
        function edit(update: Draft['questions'][number]) {
          setDraft((current) => current && ({
            ...current, questions: current.questions.map((entry, position) => position === index ? update : entry),
          }))
        }
        return <fieldset key={index} disabled={busy} className="space-y-2 rounded border p-3">
          <legend className="text-sm font-medium">Question {index + 1}</legend>
          <label className="grid gap-1 text-sm">Question text
            <textarea required maxLength={500} value={question.question} className="rounded border bg-background p-2"
              onChange={(event) => edit({ ...question, question: event.target.value })} />
          </label>
          {question.options.map((option, position) => <div key={position} className="flex flex-wrap items-end gap-2">
            <label className="grid min-w-0 flex-1 gap-1 text-sm">Option {position + 1}
              <input required maxLength={400} value={option.text} className="rounded border bg-background p-2"
                onChange={(event) => edit({ ...question, options: question.options.map((entry, i) =>
                  i === position ? { ...entry, text: event.target.value } : entry) })} />
            </label>
            <label className="flex items-center gap-1 text-sm">
              <input type="radio" name={`quiz-answer-${index}`} checked={option.is_correct}
                onChange={() => edit({ ...question, options: question.options.map((entry, i) => ({ ...entry, is_correct: i === position })) })} />
              Correct
            </label>
          </div>)}
          <p className="text-sm font-medium">{question.citation.label} · Page {question.citation.page}</p>
          <blockquote className="break-words text-sm">{question.citation.quote}</blockquote>
        </fieldset>
      })}
      <Button type="submit" size="sm" disabled={busy || !destination.canAddTask}>Save unpublished quiz</Button>
    </form>}
    {tasks.length > 0 && <form className="space-y-2" onSubmit={lookup}>
      <label className="grid gap-1 text-sm">Saved quiz
        <select name="task" required disabled={busy} className="rounded border bg-background p-2" onChange={() => setCitations(undefined)}>
          <option value="">Choose a quiz task</option>
          {tasks.map((task) => <option key={task.uuid} value={task.uuid}>{task.title}</option>)}
        </select>
      </label>
      <Button type="submit" size="sm" variant="outline" disabled={busy}>Check quiz citations</Button>
    </form>}
    {citations?.status === 'stale' && <p role="status" className="text-sm">Saved quiz citations are stale or their sources are unavailable.</p>}
    {citations?.status === 'none' && <p role="status" className="text-sm">No citations were saved for this quiz.</p>}
    {citations?.status === 'current' && <ol className="space-y-2">
      {Object.entries(citations.citations).map(([question, citation]) => <li key={question} className="border-b pb-2">
        <p className="text-sm font-medium">{citation.label} · Page {citation.page}</p>
        <blockquote className="break-words text-sm">{citation.quote}</blockquote>
      </li>)}
    </ol>}
  </section>
}
