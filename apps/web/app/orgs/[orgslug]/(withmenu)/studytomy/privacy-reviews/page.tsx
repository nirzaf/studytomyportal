import { redirect } from 'next/navigation'
import { getServerSession } from '@/lib/auth/server'
import { getOrganizationContextInfo } from '@services/organizations/orgs'
import RecordRequestReviewQueue from '@components/Studytomy/RecordRequestReviewQueue'

export const metadata = {
  title: 'Learning record reviews',
  robots: { index: false, follow: false },
}

export default async function PrivacyReviewsPage({ params }: { params: Promise<{ orgslug: string }> }) {
  if (!await getServerSession()) redirect('/login?redirect=/studytomy/privacy-reviews')
  const { orgslug } = await params
  const org = await getOrganizationContextInfo(orgslug, { revalidate: 120, tags: ['organizations'] })
  return <RecordRequestReviewQueue orgUuid={org.org_uuid} />
}
