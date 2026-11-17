import SafeguardingReportForm from '@components/Studytomy/SafeguardingReportForm'
import { getOrganizationContextInfoWithoutCredentials } from '@services/organizations/orgs'
import { getPublicSafeguardingContact, isSafeguardingReportingAvailable } from '@services/studytomy/safeguarding'

export const metadata = {
  title: 'Report a safety concern',
  robots: { index: false, follow: false },
}

export default async function SafeguardingReportPage({ params }: { params: Promise<{ orgslug: string }> }) {
  const { orgslug } = await params
  const org = await getOrganizationContextInfoWithoutCredentials(orgslug)
  const [contact, available] = await Promise.all([
    getPublicSafeguardingContact(org.org_uuid).catch(() => null),
    isSafeguardingReportingAvailable(org.org_uuid).catch(() => false),
  ])
  const reportingAvailable = contact !== null && available

  return (
    <main className="mx-auto w-full max-w-2xl space-y-7 px-5 py-10 text-slate-950 sm:px-8">
      <h1 className="text-3xl font-semibold tracking-tight">Report a safety concern</h1>
      {contact ? (
        <>
          <section aria-label="Safety contact information" className="space-y-3 border-s-4 border-amber-400 bg-amber-50 p-4">
            <p className="whitespace-pre-line font-medium">{contact.emergency_guidance}</p>
            <p className="whitespace-pre-line">{contact.contact_instructions}</p>
            <p className="whitespace-pre-line"><strong>Monitored hours</strong> {contact.monitored_hours}</p>
          </section>
        </>
      ) : <p>Online safety reporting is currently unavailable for this organization.</p>}
      {contact && !reportingAvailable && <p>Use the contact instructions above to report a concern.</p>}
      <SafeguardingReportForm orgUuid={org.org_uuid} reportingAvailable={reportingAvailable} />
    </main>
  )
}
