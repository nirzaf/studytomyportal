'use client'

import { useEffect, useRef, useState, type FormEvent } from 'react'
import Link from 'next/link'
import { ArrowRight, Grid3X3, RectangleHorizontal, Rows3 } from 'lucide-react'
import { getAPIUrl, getUriWithOrg } from '@services/config/config'
import { useTranslation } from 'react-i18next'
import { useOrg } from '@components/Contexts/OrgContext'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'
import RecallPractice from './RecallPractice'
import TopicPractice from './TopicPracticeCards'
import { buildTopicPractice, type Passage } from './topicPractice'
import GradeEightMathWidget from './GradeEightMathWidget'
import type { MathTopic } from './mathPractice'

export default function PracticeStudio({ orgslug }: { orgslug: string }) {
  const { t } = useTranslation()
  const [topic, setTopic] = useState<MathTopic | null>(null)
  const org = useOrg()
  const session = useLHSession()
  const token = session?.data?.tokens?.access_token
  const orgUuid = org?.org_uuid
  const [query, setQuery] = useState('')
  const [practice, setPractice] = useState<{ topic: string; cards: ReturnType<typeof buildTopicPractice> } | null>(null)
  const [busy, setBusy] = useState(false)
  const [recallTopic, setRecallTopic] = useState('')
  const request = useRef<AbortController | null>(null)

  useEffect(() => () => request.current?.abort(), [])

  async function generate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const input = query.trim()
    if (!orgUuid || !token || input.length < 2 || request.current) return
    const builtInTopic = topics.find(({ id, label }) =>
      [id, label, id === 'squares' ? 'square numbers' : ''].some(
        candidate => candidate.toLowerCase() === input.toLowerCase(),
      ),
    )
    if (builtInTopic) {
      setPractice(null)
      setRecallTopic('')
      setTopic(builtInTopic.id)
      return
    }
    const controller = new AbortController()
    request.current = controller
    setTopic(null)
    setPractice(null)
    setBusy(true)
    setRecallTopic('')
    try {
      const response = await secureFetch(
        `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/explore/topics`,
        {
          ...RequestBodyWithAuthHeader('POST', { query: input, sources_only: true }, null, token),
          cache: 'no-store',
          redirect: 'error',
          signal: controller.signal,
        },
      )
      if (!response.ok) throw new Error('Topic search failed')
      const { sources } = await response.json() as { sources: Passage[] }
      if (controller.signal.aborted) return
      const cards = buildTopicPractice(input, sources)
      if (cards.length) setPractice({ topic: input, cards })
      else setRecallTopic(input)
    } catch {
      if (!controller.signal.aborted) setRecallTopic(input)
    } finally {
      request.current = null
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  const topics = [
    { id: 'fractions', label: t('playgrounds.math_demo.fractions', 'Equivalent fractions'), Icon: Rows3 },
    { id: 'perimeter', label: t('playgrounds.math_demo.perimeter', 'Rectangle perimeter'), Icon: RectangleHorizontal },
    { id: 'squares', label: t('playgrounds.math_demo.squares', 'Square numbers'), Icon: Grid3X3 },
  ] as const

  return (
    <section aria-labelledby="practice-studio-title" className="!my-5 overflow-hidden rounded-2xl border border-teal-200 bg-white">
      <div className="bg-teal-950 p-5 text-white sm:p-7">
        <h2 id="practice-studio-title" className="text-2xl font-bold tracking-tight">
          {t('playgrounds.practice_studio.title', 'Practice studio')}
        </h2>
        <p className="mt-2 max-w-lg text-sm leading-relaxed text-teal-100">
          {t('playgrounds.practice_studio.intro', 'Enter any topic to start practising. Use cards from approved readings when available, or review what you learn with your own notes.')}
        </p>
        <form onSubmit={generate} className="mt-5 flex flex-wrap items-end gap-2">
          <label className="grid min-w-0 flex-1 basis-56 gap-1 text-sm font-semibold" htmlFor="practice-studio-topic">
            What do you want to practise?
            <input id="practice-studio-topic" value={query} onChange={event => setQuery(event.currentTarget.value)}
              maxLength={500} placeholder="e.g. photosynthesis"
              className="min-h-11 rounded-lg border border-teal-600 bg-white px-3 font-normal text-slate-900" />
          </label>
          <button type="submit" disabled={busy || !token || !orgUuid || query.trim().length < 2}
            className="min-h-11 rounded-lg bg-teal-100 px-4 text-sm font-bold text-teal-950 disabled:opacity-50">
            {busy ? 'Generating…' : 'Generate practice'}
          </button>
        </form>
        <div className="mt-5 grid gap-2 sm:grid-cols-3">
          {topics.map(({ id, label, Icon }) => (
            <button key={id} type="button" aria-pressed={topic === id}
              disabled={busy}
              onClick={() => { setPractice(null); setRecallTopic(''); setTopic(id) }}
              className="flex min-h-14 items-center gap-3 rounded-lg border border-teal-600 bg-teal-900 px-3 text-start text-sm font-semibold hover:bg-teal-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-200 aria-pressed:bg-teal-100 aria-pressed:text-teal-950">
              <Icon className="h-6 w-6 shrink-0" aria-hidden="true" />
              {label}
            </button>
          ))}
        </div>
      </div>
      {recallTopic && <RecallPractice topic={recallTopic} />}
      {practice && <TopicPractice topic={practice.topic} cards={practice.cards} />}
      {topic && <GradeEightMathWidget key={topic} initialTopic={topic} showTopicPicker={false} />}
      <div className="border-t border-teal-100 px-5 py-3 sm:px-7">
        <Link href={getUriWithOrg(orgslug, '/explore')} className="inline-flex items-center gap-2 text-sm font-semibold text-teal-800 hover:underline">
          {t('playgrounds.practice_studio.explore', 'Find other topics in Explore Topics')}
          <ArrowRight className="h-4 w-4" aria-hidden="true" />
        </Link>
      </div>
    </section>
  )
}
