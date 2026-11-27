import Link from 'next/link'
import SupportCase from '@components/Studytomy/SupportCase'
import { getAPIUrl } from '@services/config/config'
import { getOrganizationContextInfoWithoutCredentials } from '@services/organizations/orgs'
import { secureFetch } from '@services/utils/ts/requests'

export const metadata = { title: 'Get help', robots: { index: false, follow: false } }

type SupportGuidance = {
  categories: string[]
  contact_instructions: string
  monitored_hours: string
  service_limits: string
  response_expectations: string | null
  intake_available: boolean
  guest_verification_available?: boolean
}

async function getGuidance(orgUuid: string): Promise<SupportGuidance | null> {
  const url = `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/support/public-contact`
  const response = await secureFetch(url, { cache: 'no-store', redirect: 'error' })
  return response.ok ? response.json() : null
}

export default async function HelpPage({ params }: { params: Promise<{ orgslug: string }> }) {
  const { orgslug } = await params
  const organization = await getOrganizationContextInfoWithoutCredentials(orgslug)
  const guidance = await getGuidance(organization.org_uuid).catch(() => null)

  return (
    <main className="mx-auto w-full max-w-2xl space-y-6 px-5 py-10 text-slate-950 sm:px-8">
      <h1 className="text-3xl font-semibold tracking-tight">Get help</h1>
      {guidance ? (
        <section aria-labelledby="support-contact" className="space-y-4 border-s-4 border-blue-300 bg-blue-50 p-4">
          <h2 id="support-contact" className="text-lg font-semibold">Contact support</h2>
          <ul className="list-inside list-disc">
            {[...new Set(guidance.categories)].map((category) => <li key={category}>{category}</li>)}
          </ul>
          <p className="whitespace-pre-line">{guidance.contact_instructions}</p>
          <p className="whitespace-pre-line"><strong>Monitored hours</strong> {guidance.monitored_hours}</p>
          {guidance.response_expectations && (
            <p className="whitespace-pre-line"><strong>Response expectations</strong> {guidance.response_expectations}</p>
          )}
          <p className="whitespace-pre-line">{guidance.service_limits}</p>
        </section>
      ) : <p>Online support instructions are currently unavailable for this organization.</p>}
      <SupportCase orgUuid={organization.org_uuid} topics={[...new Set(guidance?.categories ?? [])]}
        intakeAvailable={guidance?.intake_available === true}
        guestAvailable={guidance?.guest_verification_available === true} />
      <Link className="font-semibold underline" href="/help/report">Report a safety concern</Link>
    </main>
  )
}
