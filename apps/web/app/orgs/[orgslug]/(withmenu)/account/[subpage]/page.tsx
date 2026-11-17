import { getOrganizationContextInfo } from '@services/organizations/orgs'
import { Metadata } from 'next'
import { getServerSession } from '@/lib/auth/server'
import { getOrgThumbnailMediaDirectory } from '@services/media/media'
import AccountClient from '@components/Objects/Account/AccountClient'
import { redirect } from 'next/navigation'

type MetadataProps = {
  params: Promise<{ orgslug: string; subpage: string }>
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>
}

const VALID_SUBPAGES = ['general', 'profile', 'security', 'purchases', 'ai-apps', 'privacy', 'reports', 'learning']

const getSubpageTitle = (subpage: string): string => {
  const titles: Record<string, string> = {
    'general': 'General Settings',
    'profile': 'Profile Builder',
    'security': 'Security',
    'purchases': 'Purchases',
    'ai-apps': 'AI apps',
    'privacy': 'Privacy',
    'reports': 'Progress reports',
    'learning': 'My learning',
  }
  return titles[subpage] || 'Account'
}

export async function generateMetadata(props: MetadataProps): Promise<Metadata> {
  const params = await props.params
  const org = await getOrganizationContextInfo(params.orgslug, {
    revalidate: 120,
    tags: ['organizations'],
  })

  const title = `${getSubpageTitle(params.subpage)} | ${org.name}`
  const description = `Manage your account settings at ${org.name}`

  return {
    title,
    description,
    robots: {
      index: false,
      follow: false,
    },
    openGraph: {
      title,
      description,
      type: 'website',
      images: [
        {
          url: getOrgThumbnailMediaDirectory(org?.org_uuid, org?.thumbnail_image),
          width: 800,
          height: 600,
          alt: org.name,
        },
      ],
    },
  }
}

const AccountSubPage = async (props: MetadataProps) => {
  const params = await props.params
  const session = await getServerSession()

  // Browser-relative redirects only: the org slug is NEVER a URL path segment
  // (the proxy adds the /orgs/{slug} prefix). A slug-prefixed path would be
  // double-prefixed by the proxy → 404.
  if (!session) {
    redirect(`/login?redirect=/account/${params.subpage}`)
  }

  // Redirect to general if invalid subpage
  if (!VALID_SUBPAGES.includes(params.subpage)) {
    redirect('/account/general')
  }

  const org = await getOrganizationContextInfo(params.orgslug, {
    revalidate: 120,
    tags: ['organizations'],
  })

  const query = await props.searchParams
  const selected = params.subpage === 'reports' ? query.report : undefined
  const order = params.subpage === 'purchases' ? query.order : undefined

  return (
    <AccountClient
      orgslug={params.orgslug}
      org_id={org.id}
      org_uuid={org.org_uuid}
      subpage={params.subpage}
      reportUuid={typeof selected === "string" ? selected : undefined}
      orderRef={typeof order === 'string' ? order : undefined}
    />
  )
}

export default AccountSubPage
