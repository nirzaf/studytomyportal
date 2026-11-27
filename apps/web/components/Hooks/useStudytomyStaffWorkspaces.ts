'use client'

import useSWR from 'swr'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { useOrg } from '@components/Contexts/OrgContext'
import { getAuthenticatedSessionIdentity } from '@/lib/auth/sessionScope'
import { STUDYTOMY_STAFF_WORKSPACES } from '@/lib/dashboard-menu-items'
import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'

export function useStudytomyStaffWorkspaces(canOpenDashboard: boolean) {
  const orgUuid = useOrg()?.org_uuid
  const session = useLHSession()
  const token = session?.status === 'authenticated' ? session.data?.tokens?.access_token : undefined
  const user = session?.data?.user
  const identity = getAuthenticatedSessionIdentity(user?.id ?? user?.user_uuid, session?.data)
  const { data, error } = useSWR<string[]>(
    orgUuid && token ? ['studytomy-staff-workspaces', orgUuid, identity] : null,
    async () => {
      if (!orgUuid || !token) return []
      const url = `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/me/staff-workspaces`
      const response = await secureFetch(url, {
        ...RequestBodyWithAuthHeader('GET', null, null, token), redirect: 'error',
      })
      if (!response.ok) return []
      const result: unknown = await response.json()
      return Array.isArray(result) ? result.filter((entry): entry is string => typeof entry === 'string') : []
    },
    { shouldRetryOnError: false },
  )
  if (!token || error || !data) return []
  return STUDYTOMY_STAFF_WORKSPACES.filter(item =>
    data.includes(item.id) && (item.id !== 'families' || canOpenDashboard))
}
