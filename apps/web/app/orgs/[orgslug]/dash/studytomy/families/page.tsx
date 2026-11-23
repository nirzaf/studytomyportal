'use client'

import React, { use } from 'react'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { useOrg } from '@components/Contexts/OrgContext'
import { getAuthenticatedSessionIdentity } from '@/lib/auth/sessionScope'
import FamilyWorkspace from '@components/Studytomy/FamilyWorkspace'

interface FamilyWorkspaceParams {
  orgslug: string
}

export default function FamilyWorkspacePage(props: { params: Promise<FamilyWorkspaceParams> }) {
  const params = use(props.params)
  const org = useOrg()
  const session = useLHSession()
  const sessionData = session?.status === 'authenticated' ? session.data : undefined
  const accessToken = sessionData?.tokens?.access_token
  const actor = accessToken
    ? getAuthenticatedSessionIdentity(sessionData?.user?.id ?? sessionData?.user?.user_uuid, sessionData)
    : undefined

  return (
    <FamilyWorkspace
      key={JSON.stringify([params.orgslug, org?.org_uuid, actor])}
      orgUuid={org?.org_uuid}
      accessToken={accessToken}
    />
  )
}
