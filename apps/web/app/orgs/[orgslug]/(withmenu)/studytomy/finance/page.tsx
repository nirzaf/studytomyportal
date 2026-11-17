import { redirect } from 'next/navigation'
import { getServerSession } from '@/lib/auth/server'
import { getOrganizationContextInfo } from '@services/organizations/orgs'
import CommerceReviewQueue from '@components/Studytomy/CommerceReviewQueue'
import RefundReview from '@components/Studytomy/RefundReview'

export const metadata = {
  title: 'Collection reviews',
  robots: { index: false, follow: false },
}

export default async function FinancePage({ params }: { params: Promise<{ orgslug: string }> }) {
  if (!await getServerSession()) redirect('/login?redirect=/studytomy/finance')
  const { orgslug } = await params
  const org = await getOrganizationContextInfo(orgslug, { revalidate: 120, tags: ['organizations'] })
  return <>
    <CommerceReviewQueue orgUuid={org.org_uuid} />
    <RefundReview orgUuid={org.org_uuid} />
  </>
}
