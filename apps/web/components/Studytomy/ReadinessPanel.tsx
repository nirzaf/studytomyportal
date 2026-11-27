'use client'

import React from 'react'
import {
  approveStudyPolicy,
  createServiceOwner,
  createStudyOfferDraft,
  getCourseOfferReadiness,
  getStudyOfferDraft,
  grantServiceOwnerScope,
  listServiceOwnerScopes,
  listServiceOwners,
  listStudyPolicies,
  proposeStudyPolicy,
  reviseStudyOfferDraft,
  revokeServiceOwnerScope,
  setFinancialDelegation,
} from '@services/studytomy/readiness'
import type {
  StudyCourseReadiness,
  StudyOfferDraftRead,
  StudyOfferKind,
  StudyOwnerFunction,
  StudyOwnerAssignmentRead,
  StudyPolicyBody,
  StudyPolicyKey,
  StudyPolicyVersionRead,
  StudyStaffScopeRead,
} from '@/types/studytomy'

interface ReadinessPanelProps {
  orgUuid: string
  courseUuid: string
  accessToken: string
  offerKind?: StudyOfferKind
  adminMode?: boolean
}

const policyKeys: StudyPolicyKey[] = [
  'D-01', 'D-02', 'D-03', 'D-04', 'D-05', 'D-06', 'D-07', 'D-08', 'D-09', 'D-10',
  'D-11', 'D-12', 'D-13', 'D-14', 'D-15', 'D-16', 'D-17', 'D-18', 'D-19', 'D-20',
]
const ownerFunctions: StudyOwnerFunction[] = [
  'business', 'education', 'safeguarding', 'family_services', 'support', 'commercial', 'service', 'privacy',
]
const capabilities = [
  'family_authority_review', 'learner_dossier_read', 'safeguarding_case_review',
  'financial_reconciliation', 'refund_approval', 'offer_publication', 'adaptive_curriculum_author',
  'content_release_review', 'assessment_moderation', 'progress_report_review', 'privacy_retention_review',
  'support_operating_report',
] as const
const scopeKinds = ['course', 'learner', 'case', 'offer', 'order', 'collection', 'function'] as const
const fieldClass = 'mt-1 block min-h-10 w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-700'
const smallButton = 'min-h-10 rounded-md bg-teal-800 px-3 py-2 text-sm font-medium text-white hover:bg-teal-900 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-700 focus-visible:ring-offset-2'

function localDateTimeNow() {
  const date = new Date()
  date.setMinutes(date.getMinutes() - date.getTimezoneOffset())
  return date.toISOString().slice(0, 16)
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block text-sm font-medium text-gray-800">{label}{children}</label>
}

export default function ReadinessPanel({
  orgUuid,
  courseUuid,
  accessToken,
  offerKind = 'course',
  adminMode = false,
}: ReadinessPanelProps) {
  const [readiness, setReadiness] = React.useState<StudyCourseReadiness | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [workspaceBusy, setWorkspaceBusy] = React.useState(false)
  const [workspaceMessage, setWorkspaceMessage] = React.useState('')
  const [workspaceError, setWorkspaceError] = React.useState('')
  const [policies, setPolicies] = React.useState<StudyPolicyVersionRead[]>([])
  const [owners, setOwners] = React.useState<StudyOwnerAssignmentRead[]>([])
  const [scopes, setScopes] = React.useState<StudyStaffScopeRead[]>([])
  const [scopeOwnerUuid, setScopeOwnerUuid] = React.useState('')
  const [loadedOffer, setLoadedOffer] = React.useState<StudyOfferDraftRead | null>(null)
  const [offerMessage, setOfferMessage] = React.useState('')
  const [newOfferUuid, setNewOfferUuid] = React.useState('')

  const refreshReadiness = React.useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      if (!adminMode) return
      setReadiness(await getCourseOfferReadiness(orgUuid, courseUuid, accessToken, offerKind))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to load course readiness.')
      setReadiness(null)
    } finally {
      setLoading(false)
    }
  }, [orgUuid, courseUuid, accessToken, offerKind, adminMode])

  React.useEffect(() => { void refreshReadiness() }, [refreshReadiness])

  async function refreshGovernance(showMessage = true) {
    setWorkspaceBusy(true)
    setWorkspaceError('')
    try {
      const [policyRows, ownerRows] = await Promise.all([
        listStudyPolicies(orgUuid, accessToken),
        adminMode ? listServiceOwners(orgUuid, accessToken) : Promise.resolve([]),
      ])
      setPolicies(policyRows)
      setOwners(ownerRows)
      if (showMessage) setWorkspaceMessage('Governance records refreshed from the organization API.')
    } catch (reason) {
      setWorkspaceError(reason instanceof Error ? reason.message : 'Unable to load governance records.')
    } finally {
      setWorkspaceBusy(false)
    }
  }

  async function runWorkspaceAction(action: () => Promise<unknown>, success: string) {
    setWorkspaceBusy(true)
    setWorkspaceError('')
    setWorkspaceMessage('')
    try {
      await action()
      setWorkspaceMessage(success)
      await refreshGovernance(false)
      await refreshReadiness()
      setWorkspaceMessage(success)
    } catch (reason) {
      setWorkspaceError(reason instanceof Error ? reason.message : 'The governance request was denied.')
    } finally {
      setWorkspaceBusy(false)
    }
  }

  async function createOwner(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const formElement = event.currentTarget
    await runWorkspaceAction(() => createServiceOwner(orgUuid, accessToken, {
      user_uuid: String(form.get('owner_user_uuid') ?? '').trim(),
      responsibility: String(form.get('owner_responsibility')) as StudyOwnerFunction,
      evidence_reference: String(form.get('owner_evidence') ?? '').trim(),
      effective_from: new Date(String(form.get('owner_effective_from'))).toISOString(),
    }), 'Service-owner assignment recorded.')
    formElement.reset()
  }

  async function proposePolicy(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const formElement = event.currentTarget
    try {
      const body = JSON.parse(String(form.get('policy_body') ?? '')) as StudyPolicyBody
      await runWorkspaceAction(() => proposeStudyPolicy(orgUuid, accessToken, {
        policy_key: String(form.get('policy_key')) as StudyPolicyKey,
        body,
        evidence_reference: String(form.get('policy_evidence') ?? '').trim(),
        effective_from: new Date(String(form.get('policy_effective_from'))).toISOString(),
        effective_to: form.get('policy_effective_to')
          ? new Date(String(form.get('policy_effective_to'))).toISOString()
          : null,
      }), 'Policy draft saved. It is not approved or active.')
      formElement.reset()
    } catch (reason) {
      setWorkspaceError(reason instanceof Error ? reason.message : 'Policy JSON must be valid.')
    }
  }

  async function loadScopes(ownerUuid: string) {
    setScopeOwnerUuid(ownerUuid)
    if (!ownerUuid) { setScopes([]); return }
    setWorkspaceBusy(true)
    setWorkspaceError('')
    try { setScopes(await listServiceOwnerScopes(orgUuid, accessToken, ownerUuid)) }
    catch (reason) { setWorkspaceError(reason instanceof Error ? reason.message : 'Unable to load staff scopes.') }
    finally { setWorkspaceBusy(false) }
  }

  async function grantScope(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const formElement = event.currentTarget
    if (!scopeOwnerUuid) return
    const form = new FormData(event.currentTarget)
    const scopeUuid = String(form.get('scope_uuid') ?? '').trim()
    await runWorkspaceAction(() => grantServiceOwnerScope(orgUuid, accessToken, scopeOwnerUuid, {
      capability: String(form.get('scope_capability')) as typeof capabilities[number],
      scope_kind: String(form.get('scope_kind')) as typeof scopeKinds[number],
      scope_uuid: scopeUuid,
      evidence_reference: String(form.get('scope_evidence') ?? '').trim(),
    }), 'Staff scope recorded. The API will enforce its exact target and capability.')
    await loadScopes(scopeOwnerUuid)
    formElement.reset()
  }

  async function createOffer(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    try {
      const terms = JSON.parse(String(form.get('offer_terms') ?? '')) as Record<string, unknown>
      const offerUuid = String(form.get('offer_uuid') ?? '').trim()
      if (!offerUuid) throw new Error('Generate an offer UUID before creating a draft. Grant its offer scope first if required.')
      const result = await createStudyOfferDraft(orgUuid, accessToken, {
        offer_uuid: offerUuid,
        offer_kind: String(form.get('offer_kind')) as StudyOfferKind,
        terms,
      })
      setLoadedOffer(result)
      setOfferMessage(`Private draft ${result.offer_uuid} saved as version ${result.version_no}. It cannot be purchased.`)
      event.currentTarget.reset()
      await refreshReadiness()
    } catch (reason) {
      setWorkspaceError(reason instanceof Error ? reason.message : 'Offer terms must be valid JSON and pass server validation.')
    }
  }

  async function loadOffer(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const offerUuid = String(new FormData(event.currentTarget).get('offer_uuid_lookup') ?? '').trim()
    if (!offerUuid) return
    setWorkspaceBusy(true)
    setWorkspaceError('')
    try {
      const result = await getStudyOfferDraft(orgUuid, accessToken, offerUuid)
      setLoadedOffer(result)
      setOfferMessage(`Loaded private draft version ${result.version_no}.`)
    } catch (reason) { setWorkspaceError(reason instanceof Error ? reason.message : 'Unable to load this offer draft.') }
    finally { setWorkspaceBusy(false) }
  }

  async function reviseOffer(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!loadedOffer) return
    const form = new FormData(event.currentTarget)
    try {
      const terms = JSON.parse(String(form.get('offer_revision_terms') ?? '')) as Record<string, unknown>
      const result = await reviseStudyOfferDraft(
        orgUuid, accessToken, loadedOffer.offer_uuid, loadedOffer.current_version, terms,
      )
      setLoadedOffer(result)
      setOfferMessage(`Private offer draft saved as version ${result.version_no}.`)
      await refreshReadiness()
    } catch (reason) { setWorkspaceError(reason instanceof Error ? reason.message : 'Unable to revise this offer draft.') }
  }

  return (
    <section aria-labelledby="studytomy-readiness-title" aria-live="polite" className="rounded-lg border border-gray-200 bg-white p-5">
      <h2 id="studytomy-readiness-title" className="text-lg font-semibold text-gray-900">Course offer readiness</h2>
      {adminMode && (loading ? <p className="mt-3 text-sm text-gray-600">Checking current approvals and evidence…</p>
        : error ? <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>
          : readiness ? <>
            <p className={`mt-3 text-sm font-medium ${readiness.ready_to_sell ? 'text-green-700' : 'text-amber-800'}`}>
              {readiness.ready_to_sell ? 'Ready for sale review' : 'Not ready for sale'}
            </p>
            {readiness.blockers.length > 0 && <ul className="mt-4 space-y-3">
              {readiness.blockers.map((blocker, index) => <li key={`${blocker.code}-${blocker.policy_key ?? 'general'}-${index}`} className="rounded-md bg-gray-50 p-3">
                <p className="text-sm font-medium text-gray-900">{blocker.message}</p>
                <p className="mt-1 text-sm text-gray-700">{blocker.next_step}</p>
                <p className="mt-1 text-xs text-gray-500">{blocker.code}{blocker.policy_key ? ` · ${blocker.policy_key}` : ''}</p>
              </li>)}
            </ul>}
          </> : null)}

      <details className="mt-5 border-t border-gray-200 pt-4">
        <summary className="cursor-pointer text-sm font-semibold text-gray-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-700">
          Governance and private offer drafts
        </summary>
        <p className="mt-2 max-w-3xl text-sm text-gray-600">
          The API checks organization roles, current owner assignments, scoped capabilities, policy completeness and approvals for every change. Drafts never activate sales. No policy review date is stored by the current policy contract.
        </p>
        <div className="mt-4 flex flex-wrap gap-3">
          <button className={smallButton} type="button" disabled={workspaceBusy} onClick={() => void refreshGovernance()}>
            {workspaceBusy ? 'Loading…' : 'Load governance records'}
          </button>
          {adminMode && <button className="min-h-10 rounded-md border border-gray-300 px-3 py-2 text-sm font-medium text-gray-800" type="button" onClick={() => void refreshReadiness()}>
            Refresh readiness
          </button>}
        </div>
        {workspaceMessage && <p role="status" className="mt-3 text-sm text-teal-800">{workspaceMessage}</p>}
        {workspaceError && <p role="alert" className="mt-3 text-sm text-red-700">{workspaceError}</p>}

        <div className="mt-5 grid gap-6 xl:grid-cols-2">
          <div className="space-y-5">
            <section aria-labelledby="policy-drafts-title" className="border-t border-gray-100 pt-4">
              <h3 id="policy-drafts-title" className="font-semibold text-gray-900">Policy decisions</h3>
              <p className="mt-1 text-sm text-gray-600">Submit real decision evidence. Required approver roles come from the API; the server keeps incomplete or unauthorized decisions inactive.</p>
              {adminMode && <form className="mt-3 grid gap-3" onSubmit={proposePolicy}>
                <Field label="Decision key"><select className={fieldClass} name="policy_key">{policyKeys.map(key => <option key={key}>{key}</option>)}</select></Field>
                <Field label="Policy decision JSON"><textarea className={`${fieldClass} min-h-32 font-mono`} name="policy_body" required defaultValue={'{"decision_items":{}}'} /></Field>
                <Field label="Evidence reference"><input className={fieldClass} name="policy_evidence" required maxLength={500} /></Field>
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Effective from"><input className={fieldClass} name="policy_effective_from" type="datetime-local" required defaultValue={localDateTimeNow()} /></Field>
                  <Field label="Effective to (optional)"><input className={fieldClass} name="policy_effective_to" type="datetime-local" /></Field>
                </div>
                <button className={smallButton} disabled={workspaceBusy} type="submit">Save policy draft</button>
              </form>}
              <ul className="mt-4 space-y-3">
                {policies.map(policy => {
                  const approvedRoles = new Set(policy.approvals.map(approval => approval.responsibility))
                  const missingRoles = policy.required_responsibilities.filter(role => !approvedRoles.has(role))
                  return <li key={policy.policy_uuid} className="border-s-2 border-teal-700 ps-3">
                    <p className="font-medium text-gray-900">{policy.policy_key} · version {policy.version} · {policy.status}</p>
                    <p className="text-xs text-gray-600">Effective {new Date(policy.effective_from).toLocaleString()}{policy.effective_to ? ` to ${new Date(policy.effective_to).toLocaleString()}` : ''}. Evidence: {policy.evidence_reference}</p>
                    <p className="mt-1 text-xs text-gray-600">Required approvers: {policy.required_responsibilities.join(', ') || 'none'}; recorded: {policy.approvals.map(item => item.responsibility).join(', ') || 'none'}.</p>
                    <p className="text-xs text-gray-500">Next review date: not recorded in the current policy contract.</p>
                    {policy.status === 'draft' && missingRoles.length > 0 && <ApprovalForm
                      roles={missingRoles}
                      disabled={workspaceBusy}
                      onSubmit={(value) => runWorkspaceAction(
                        () => approveStudyPolicy(orgUuid, accessToken, policy.policy_uuid, value),
                        `${value.responsibility} approval recorded for ${policy.policy_key} version ${policy.version}.`,
                      )}
                    />}
                  </li>
                })}
              </ul>
            </section>

            {adminMode && <section aria-labelledby="owners-title" className="border-t border-gray-100 pt-4">
              <h3 id="owners-title" className="font-semibold text-gray-900">Service owners</h3>
              <form className="mt-3 grid gap-3" onSubmit={createOwner}>
                <Field label="Organization user UUID"><input className={fieldClass} name="owner_user_uuid" required /></Field>
                <Field label="Responsibility"><select className={fieldClass} name="owner_responsibility">{ownerFunctions.map(role => <option key={role}>{role}</option>)}</select></Field>
                <Field label="Assignment evidence"><input className={fieldClass} name="owner_evidence" required maxLength={500} /></Field>
                <Field label="Effective from"><input className={fieldClass} name="owner_effective_from" type="datetime-local" required defaultValue={localDateTimeNow()} /></Field>
                <button className={smallButton} type="submit" disabled={workspaceBusy}>Assign service owner</button>
              </form>
              <ul className="mt-4 space-y-2 text-sm text-gray-700">
                {owners.map(owner => <li key={owner.owner_uuid} className="rounded bg-gray-50 p-2">
                  <span className="font-medium">{owner.responsibility}</span> · {owner.user_uuid} · {owner.status} · {owner.owner_uuid}
                </li>)}
              </ul>
            </section>}
          </div>

          <div className="space-y-5">
            {adminMode && <section aria-labelledby="scopes-title" className="border-t border-gray-100 pt-4">
              <h3 id="scopes-title" className="font-semibold text-gray-900">Staff scopes and financial delegation</h3>
              <Field label="Service owner"><select className={fieldClass} value={scopeOwnerUuid} onChange={event => void loadScopes(event.target.value)}>
                <option value="">Choose an owner</option>{owners.map(owner => <option key={owner.owner_uuid} value={owner.owner_uuid}>{owner.responsibility} · {owner.user_uuid}</option>)}
              </select></Field>
              {scopeOwnerUuid && <>
                <form className="mt-3 grid gap-3" onSubmit={grantScope}>
                  <Field label="Capability"><select className={fieldClass} name="scope_capability">{capabilities.map(capability => <option key={capability}>{capability}</option>)}</select></Field>
                  <Field label="Resource type"><select className={fieldClass} name="scope_kind">{scopeKinds.map(kind => <option key={kind}>{kind}</option>)}</select></Field>
                <Field label="Exact resource UUID"><input className={fieldClass} name="scope_uuid" required placeholder="Course, learner, case, offer, or function identifier" /></Field>
                  <Field label="Scope evidence"><input className={fieldClass} name="scope_evidence" required maxLength={500} /></Field>
                  <button className={smallButton} type="submit" disabled={workspaceBusy}>Grant staff scope</button>
                </form>
                <ul className="mt-3 space-y-2 text-sm">
                  {scopes.map(scope => <li key={scope.scope_record_uuid} className="rounded bg-gray-50 p-2">
                    <span>{scope.capability} · {scope.scope_kind}:{scope.scope_uuid ?? 'all'} · {scope.status}</span>
                    {scope.status === 'active' && <button className="ms-2 underline" type="button" disabled={workspaceBusy} onClick={() => {
                      const evidence = window.prompt('Enter the evidence reference for revoking this scope.')
                      if (evidence?.trim()) void runWorkspaceAction(
                        () => revokeServiceOwnerScope(orgUuid, accessToken, scope.scope_record_uuid, evidence.trim()),
                        'Staff scope revoked.',
                      ).then(() => loadScopes(scopeOwnerUuid))
                    }}>Revoke</button>}
                  </li>)}
                </ul>
                <form className="mt-4 grid gap-3 border-t border-gray-100 pt-4" onSubmit={async event => {
                  event.preventDefault()
                  const form = new FormData(event.currentTarget)
                  const result = await runWorkspaceAction(() => setFinancialDelegation(orgUuid, accessToken, scopeOwnerUuid, {
                    policy_uuid: String(form.get('delegation_policy_uuid') ?? '').trim(),
                    currency: String(form.get('delegation_currency') ?? '').trim(),
                    max_amount_minor: Number(form.get('delegation_limit')),
                    evidence_reference: String(form.get('delegation_evidence') ?? '').trim(),
                  }), 'Financial delegation recorded under the active D-19 policy.')
                  void result
                }}>
                  <p className="text-sm text-gray-600">Requires an active, complete, approved D-19 decision. Values are not activated if the server rejects the policy or owner.</p>
                  <Field label="Approved D-19 policy UUID"><input className={fieldClass} name="delegation_policy_uuid" required /></Field>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="Currency"><input className={fieldClass} name="delegation_currency" minLength={3} maxLength={3} required /></Field>
                    <Field label="Limit (minor units)"><input className={fieldClass} name="delegation_limit" type="number" min={0} step={1} required /></Field>
                  </div>
                  <Field label="Delegation evidence"><input className={fieldClass} name="delegation_evidence" required maxLength={500} /></Field>
                  <button className={smallButton} type="submit" disabled={workspaceBusy}>Set financial delegation</button>
                </form>
              </>}
            </section>}

            <section aria-labelledby="offer-drafts-title" className="border-t border-gray-100 pt-4">
              <h3 id="offer-drafts-title" className="font-semibold text-gray-900">Private offer drafts</h3>
              <p className="mt-1 text-sm text-gray-600">Draft terms stay private and non-purchasable until separate policy, evidence and release gates pass.</p>
              <form className="mt-3 grid gap-3" onSubmit={createOffer}>
                <Field label="Offer UUID"><input className={fieldClass} name="offer_uuid" required value={newOfferUuid} onChange={event => setNewOfferUuid(event.target.value)} /></Field>
                <button className="min-h-10 justify-self-start rounded-md border border-gray-300 px-3 py-2 text-sm font-medium text-gray-800" type="button" onClick={() => setNewOfferUuid(crypto.randomUUID())}>Generate offer UUID</button>
                <Field label="Offer kind"><select className={fieldClass} name="offer_kind"><option value="course">Course</option><option value="membership">Membership</option></select></Field>
                <Field label="Terms JSON"><textarea className={`${fieldClass} min-h-32 font-mono`} name="offer_terms" required defaultValue={'{"title":"","goals":[],"audience":"","formats":[],"resources":[]}'} /></Field>
                <button className={smallButton} type="submit" disabled={workspaceBusy}>Save private offer draft</button>
              </form>
              <form className="mt-4 flex flex-wrap items-end gap-3" onSubmit={loadOffer}>
                <Field label="Load existing offer UUID"><input className={fieldClass} name="offer_uuid_lookup" required /></Field>
                <button className={smallButton} type="submit" disabled={workspaceBusy}>Load draft</button>
              </form>
              {loadedOffer && <form className="mt-3 grid gap-3" onSubmit={reviseOffer}>
                <p role="status" className="text-sm text-gray-700">{offerMessage} Current blockers: {loadedOffer.validation_blockers.join(', ') || 'none reported'}.</p>
                <Field label={`New terms JSON · expected version ${loadedOffer.current_version}`}><textarea className={`${fieldClass} min-h-32 font-mono`} name="offer_revision_terms" required defaultValue={JSON.stringify(loadedOffer.terms, null, 2)} /></Field>
                <button className={smallButton} type="submit" disabled={workspaceBusy}>Save next draft version</button>
              </form>}
              {offerMessage && !loadedOffer && <p role="status" className="mt-2 text-sm text-teal-800">{offerMessage}</p>}
            </section>
          </div>
        </div>
      </details>
    </section>
  )
}

function ApprovalForm({
  roles,
  disabled,
  onSubmit,
}: {
  roles: StudyOwnerFunction[]
  disabled: boolean
  onSubmit: (value: { responsibility: StudyOwnerFunction; evidence_reference: string; rationale: string }) => Promise<void>
}) {
  return <form className="mt-3 grid gap-2 rounded-md border border-gray-200 p-3" onSubmit={event => {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    void onSubmit({
      responsibility: String(form.get('approval_responsibility')) as StudyOwnerFunction,
      evidence_reference: String(form.get('approval_evidence') ?? '').trim(),
      rationale: String(form.get('approval_rationale') ?? '').trim(),
    })
  }}>
    <Field label="Approver responsibility"><select className={fieldClass} name="approval_responsibility">{roles.map(role => <option key={role}>{role}</option>)}</select></Field>
    <Field label="Approval evidence"><input className={fieldClass} name="approval_evidence" required maxLength={500} /></Field>
    <Field label="Rationale"><textarea className={fieldClass} name="approval_rationale" required maxLength={2000} /></Field>
    <button className={smallButton} type="submit" disabled={disabled}>Record this named approval</button>
  </form>
}
