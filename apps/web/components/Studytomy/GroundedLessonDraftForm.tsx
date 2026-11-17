'use client'

import { type FormEvent, useEffect, useRef, useState } from 'react'
import { Button } from '@components/ui/button'
import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'

type Citation = { source_uuid: string; sha256: string; label: string; page: number; quote: string }
type Draft = { title: string; blocks: { text: string; citation: Citation }[] }
type Props = { route: string; token: string; version: number; selected: string[] }

export default function GroundedLessonDraftForm({ route, token, version, selected }: Props) {
  const [topic, setTopic] = useState('')
  const [draftState, setDraftState] = useState<{ token: string; selectionKey: string; draft: Draft }>()
  const [messageState, setMessageState] = useState<{ token: string; message: string }>()
  const [pendingState, setPendingState] = useState<{ token: string; action: 'preview' | 'draft' }>()
  const request = useRef<AbortController | null>(null)
  const selectionKey = selected.join(':')
  const draft = draftState?.token === token && draftState.selectionKey === selectionKey ? draftState.draft : undefined
  const message = messageState?.token === token ? messageState.message : undefined
  const pending = pendingState?.token === token ? pendingState.action : undefined

  useEffect(() => () => {
    request.current?.abort()
    request.current = null
  }, [token])

  async function submit(event: FormEvent<HTMLFormElement>, action: 'preview' | 'draft') {
    event.preventDefault()
    if (request.current) return
    if (action === 'preview' && (!selected.length || topic.trim().length < 3)) return
    if (action === 'draft' && !draft) return
    const controller = new AbortController()
    request.current = controller
    setPendingState({ token, action })
    setMessageState(undefined)
    try {
      const payload = action === 'preview' ? { source_uuids: selected, topic: topic.trim() } : draft && {
        expected_current_version: version,
        title: draft.title,
        blocks: draft.blocks.map(({ text, citation: { source_uuid, sha256, page, quote } }) => ({
          text, citation: { source_uuid, sha256, page, quote },
        })),
      }
      const response = await secureFetch(`${getAPIUrl()}${route}/grounded-lesson-${action}`, {
        ...RequestBodyWithAuthHeader('POST', payload, null, token),
        redirect: 'error',
        signal: controller.signal,
      })
      if (!response.ok) {
        const error = await response.json().catch(() => null)
        const detail = action === 'draft' && response.status === 409
          ? 'This lesson changed. Copy your edits, then reload before saving again.'
          : typeof error?.detail === 'string' ? error.detail : 'The draft request failed. Try again.'
        throw new Error(detail)
      }
      if (controller.signal.aborted) return
      if (action === 'draft') {
        window.location.reload()
        return
      }
      const preview: Draft = await response.json()
      if (!controller.signal.aborted) setDraftState({ token, selectionKey, draft: preview })
    } catch (error) {
      if (!controller.signal.aborted) {
        setMessageState({ token, message: error instanceof Error ? error.message : 'The draft request failed.' })
      }
    } finally {
      if (request.current === controller) {
        request.current = null
        setPendingState(undefined)
      }
    }
  }

  return (
    <section className="space-y-3 border-t pt-4" aria-label="Grounded lesson draft">
      <form className="space-y-2" onSubmit={(event) => submit(event, 'preview')}>
        <label className="block space-y-1 text-sm font-medium">
          <span className="block">Lesson topic</span>
          <input className="block w-full rounded-md border p-2" value={topic} required minLength={3} maxLength={500}
            disabled={Boolean(pending)} onChange={(event) => setTopic(event.target.value)} />
        </label>
        <Button type="submit" size="sm" disabled={Boolean(pending) || !selected.length || topic.trim().length < 3}>
          {pending === 'preview' ? 'Generating…' : 'Generate lesson draft'}
        </Button>
      </form>
      {message && <p role="alert" className="text-sm">{message}</p>}
      {draft && (
        <form className="space-y-3" onSubmit={(event) => submit(event, 'draft')}>
          <p className="text-sm">Check that the quoted text supports each paragraph. Saving replaces the current lesson text and keeps it unpublished.</p>
          <label className="block space-y-1 text-sm font-medium">
            <span className="block">Draft title</span>
            <input className="block w-full rounded-md border p-2" value={draft.title} required maxLength={160}
              disabled={Boolean(pending)}
              onChange={(event) => setDraftState({ token, selectionKey, draft: { ...draft, title: event.target.value } })} />
          </label>
          {draft.blocks.map((block, index) => (
            <div className="space-y-1 border-b pb-3" key={index}>
              <label className="block space-y-1 text-sm font-medium">
                <span className="block">Draft paragraph {index + 1}</span>
                <textarea className="block min-h-20 w-full rounded-md border p-2" value={block.text} required
                  maxLength={1500} disabled={Boolean(pending)} onChange={(event) => setDraftState({
                    token, selectionKey, draft: { ...draft, blocks: draft.blocks.map((item, position) =>
                      position === index ? { ...item, text: event.target.value } : item) },
                  })} />
              </label>
              <p className="break-words text-sm font-medium">{block.citation.label} · Page {block.citation.page}</p>
              <blockquote className="whitespace-pre-wrap break-words text-sm">{block.citation.quote}</blockquote>
            </div>
          ))}
          <Button type="submit" size="sm" disabled={Boolean(pending)}>
            {pending === 'draft' ? 'Saving…' : 'Save unpublished lesson'}
          </Button>
        </form>
      )}
    </section>
  )
}
