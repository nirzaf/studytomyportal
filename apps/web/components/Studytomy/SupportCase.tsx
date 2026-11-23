'use client'

import Link from 'next/link'
import { type FormEvent, useEffect, useRef, useState } from 'react'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { Button } from '@components/ui/button'
import { getAuthenticatedSessionIdentity } from '@/lib/auth/sessionScope'
import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'

type Receipt = { case_uuid: string; topic: string; status: string }
type SupportProps = { orgUuid: string; topics: string[]; intakeAvailable: boolean }
const statuses = new Map<string, { label: string; nextStep: string }>([
  ['acknowledged', { label: 'Received', nextStep: 'Save your reference and check back for an assignment.' }],
  ['assigned', { label: 'Assigned for review', nextStep: 'Save your reference and check back for an update.' }],
  ['awaiting_information', {
    label: 'Awaiting information',
    nextStep: 'Give your case reference when you contact support with more information.',
  }],
  ['under_review', { label: 'Under review', nextStep: 'Check this reference for the review outcome.' }],
  ['resolved', {
    label: 'Resolved',
    nextStep: 'If the issue continues, contact support with this reference.',
  }],
  ['closed', { label: 'Closed', nextStep: 'Keep this reference if you need to contact support again.' }],
])

export default function SupportCase(props: SupportProps) {
  const session = useLHSession()
  const token = session?.data?.tokens?.access_token
  if (session?.status === 'loading') return <p role="status">Checking your session…</p>
  if (session?.status !== 'authenticated' || !token) {
    return <Link href="/login?redirect=/help" className="font-semibold underline">Sign in to check a support case</Link>
  }
  const user = session.data?.user
  const identity = getAuthenticatedSessionIdentity(user?.id ?? user?.user_uuid, session.data)
  return <SupportForms key={JSON.stringify([props.orgUuid, identity])} {...props} token={token} />
}

function SupportForms({ orgUuid, topics, intakeAvailable, token }: SupportProps & { token: string }) {
  const [topic, setTopic] = useState('')
  const [subject, setSubject] = useState('')
  const [reference, setReference] = useState('')
  const [receipt, setReceipt] = useState<Receipt | null>(null)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const pending = useRef<AbortController | null>(null)
  const attempt = useRef<string | null>(null)
  useEffect(() => () => pending.current?.abort(), [])
  const url = `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/support/cases`

  async function request(event: FormEvent<HTMLFormElement>, mode: 'open' | 'lookup') {
    event.preventDefault()
    if (pending.current) return
    if (mode === 'open' && (!intakeAvailable || !topics.includes(topic))) return
    const subjectReference = subject.trim()
    if (mode === 'open' && subjectReference && !/^user_[a-zA-Z0-9_-]{1,95}$/.test(subjectReference)) {
      setMessage('Enter a valid staff reference or use the approved support contact.')
      return
    }
    const caseReference = reference.trim()
    if (mode === 'lookup' && !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(caseReference)) {
      setReceipt(null)
      setMessage('Enter the full case reference.')
      return
    }
    const controller = new AbortController()
    pending.current = controller
    setBusy(true)
    setReceipt(null)
    setMessage('')
    try {
      if (mode === 'open') attempt.current ??= crypto.randomUUID()
      const response = await secureFetch(
        mode === 'open' ? url : `${url}/${encodeURIComponent(caseReference)}`,
        {
          ...RequestBodyWithAuthHeader(
            mode === 'open' ? 'POST' : 'GET',
            mode === 'open' ? {
              request_id: attempt.current, topic,
              subject_user_uuids: subjectReference ? [subjectReference] : [],
            } : null,
            null, token,
          ),
          redirect: 'error',
          signal: controller.signal,
        },
      )
      if (!response.ok) {
        if (!controller.signal.aborted) {
          const explanation = mode === 'open' && response.status === 409
            ? 'Online intake could not assign this case. Use the approved support contact.'
            : 'Support is temporarily unavailable. Try again later.'
          setMessage(mode === 'lookup' && response.status === 404 ? 'Support case not found.' : explanation)
        }
        return
      }
      const result: Receipt = await response.json()
      if (!controller.signal.aborted) {
        setReceipt(result)
        setReference(result.case_uuid)
        if (mode === 'open') {
          attempt.current = null
          setTopic('')
          setSubject('')
        }
      }
    } catch {
      if (!controller.signal.aborted) setMessage('The request could not be confirmed. Try again.')
    } finally {
      pending.current = null
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  const receiptStatus = receipt ? statuses.get(receipt.status) : undefined
  return (
    <section className="space-y-5 border-t border-slate-200 pt-5" aria-labelledby="support-case-heading">
      <h2 id="support-case-heading" className="text-lg font-semibold">Support cases</h2>
      {intakeAvailable && (
        <form className="space-y-3" onSubmit={(event) => request(event, 'open')}>
          <label className="block space-y-2">
            <span className="block font-medium">Support topic</span>
            <select className="block w-full rounded-lg border border-slate-400 bg-white p-2" required
              value={topic} disabled={busy} onChange={(event) => {
                setTopic(event.target.value)
                attempt.current = null
              }}>
              <option value="">Choose a topic</option>
              {topics.map((value) => <option key={value}>{value}</option>)}
            </select>
          </label>
          <label className="block space-y-2">
            <span className="block font-medium">Staff reference for a complaint</span>
            <input className="block w-full rounded-lg border border-slate-400 bg-white p-2"
              autoComplete="off" maxLength={100} value={subject} disabled={busy}
              aria-describedby="support-complaint-guidance"
              onChange={(event) => {
                setSubject(event.target.value)
                attempt.current = null
              }} />
          </label>
          <p id="support-complaint-guidance" className="text-sm text-slate-600">
            For a complaint about a staff member, enter their reference if you have it. Use the contact instructions above if you do not have the reference.
          </p>
          <Button type="submit" disabled={busy || !topic}>Open support case</Button>
        </form>
      )}
      <form className="space-y-3" onSubmit={(event) => request(event, 'lookup')}>
        <label className="block space-y-2">
          <span className="block font-medium">Case reference</span>
          <input className="block w-full rounded-lg border border-slate-400 bg-white p-2" required
            autoComplete="off" maxLength={36} value={reference} disabled={busy}
            onChange={(event) => { setReference(event.target.value); setReceipt(null); setMessage('') }} />
        </label>
        <Button type="submit" variant="outline" disabled={busy || !reference.trim()}>Check case status</Button>
      </form>
      {message && <p role="alert">{message}</p>}
      {receipt && <div role="status" className="space-y-1 rounded-lg bg-blue-50 p-4">
        <p className="font-semibold">{receiptStatus?.label ?? 'Status unavailable'}</p>
        <p>{receipt.topic}</p>
        <p className="break-all">Case reference {receipt.case_uuid}</p>
        {receiptStatus && <p>{receiptStatus.nextStep}</p>}
      </div>}
    </section>
  )
}
