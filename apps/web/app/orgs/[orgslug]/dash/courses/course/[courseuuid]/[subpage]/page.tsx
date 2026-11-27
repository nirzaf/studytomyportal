'use client'
import React, { use, useEffect, useMemo, useState } from 'react';
import { CourseProvider } from '../../../../../../../../components/Contexts/CourseContext'
import { CourseOverviewTop } from '@components/Dashboard/Misc/CourseOverviewTop'
import { motion } from 'motion/react'
import { GalleryVerticalEnd, Globe, Info, UserPen, Award, Lock, Search, CalendarClock, Shapes, ClipboardCheck } from 'lucide-react'
import { ChartBar } from '@phosphor-icons/react'
import { useCourseRights } from '@hooks/useCourseRights'
import useAdminStatus from '@components/Hooks/useAdminStatus'
import { getAuthenticatedSessionIdentity } from '@/lib/auth/sessionScope'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { useOrg } from '@components/Contexts/OrgContext'
import { useRouter } from 'next/navigation'
import { getUriWithOrg } from '@services/config/config';
import { useTranslation } from 'react-i18next';
import { PlanLevel } from '@services/plans/plans';
import FeatureGate from '@components/Dashboard/Shared/FeatureGate/FeatureGate';
import { DashTabBar, DashTabItem } from '@components/Dashboard/Shared/DashTabBar/DashTabBar';
import dynamic from 'next/dynamic'
import { isOrgAnalyticsAvailable } from '@ee/components/Analytics/availability'
import ReadinessPanel from '@components/Studytomy/ReadinessPanel'
import { adaptiveRequest, type AdaptiveConfiguration as AdaptiveCatalog } from '@services/studytomy/adaptive'
import { courseReviewClient, type CourseReviewCandidate } from '@services/studytomy/contentReview'

// One chunk per tab: only the open tab's code (charts, editors) is loaded.
const EditCourseStructure = dynamic(() => import('@components/Dashboard/Pages/Course/EditCourseStructure/EditCourseStructure'))
const EditCourseGeneral = dynamic(() => import('@components/Dashboard/Pages/Course/EditCourseGeneral/EditCourseGeneral'))
const EditCourseAccess = dynamic(() => import('@components/Dashboard/Pages/Course/EditCourseAccess/EditCourseAccess'))
const EditCourseContributors = dynamic(() => import('@components/Dashboard/Pages/Course/EditCourseContributors/EditCourseContributors'))
const EditCourseCertification = dynamic(() => import('@components/Dashboard/Pages/Course/EditCourseCertification/EditCourseCertification'))
const EditCourseSEO = dynamic(() => import('@components/Dashboard/Pages/Course/EditCourseSEO/EditCourseSEO'))
const EditCourseSchedule = dynamic(() => import('@components/Dashboard/Pages/Course/EditCourseSchedule/EditCourseSchedule'))
const CourseAnalyticsTab = dynamic(() => import('@ee/components/Analytics/Dashboard/Course/CourseAnalyticsTab'))
const AdaptiveConfiguration = dynamic(() => import('@components/Studytomy/AdaptiveConfiguration'))
const ContentReviewPanel = dynamic(() => import('@components/Studytomy/ContentReviewPanel'))
const StaffCertificateReview = dynamic(() => import('@components/Studytomy/StaffCertificateReview'))

export type CourseOverviewParams = {
  orgslug: string
  courseuuid: string
  subpage: string
}

function CourseOverviewPage(props: { params: Promise<CourseOverviewParams> }) {
  const { t } = useTranslation()
  const params = use(props.params);
  const router = useRouter();
  const session = useLHSession() as any
  const org = useOrg() as any
  const { canManageOrg } = useAdminStatus()

  function getEntireCourseUUID(courseuuid: string) {
    // add course_ to uuid
    return `course_${courseuuid}`
  }

  const courseuuid = getEntireCourseUUID(params.courseuuid)
  const { hasPermission, isLoading: rightsLoading } = useCourseRights(courseuuid)
  const orgUuid = org?.slug === params.orgslug ? org.org_uuid as string | undefined : undefined
  const signedIn = session?.status === 'authenticated'
  const token = signedIn ? session.data?.tokens?.access_token as string | undefined : undefined
  const user = session?.data?.user
  const actor = signedIn ? getAuthenticatedSessionIdentity(user?.id ?? user?.user_uuid, session.data) : undefined
  const workspaceIdentity = orgUuid && actor && token ? JSON.stringify([params.orgslug, orgUuid, courseuuid, actor]) : undefined
  const adaptiveClient = useMemo(() => workspaceIdentity && orgUuid && token
    ? { scope: workspaceIdentity, request: adaptiveRequest(orgUuid, token) } : undefined, [workspaceIdentity, orgUuid, token])
  const reviewClient = useMemo(() => workspaceIdentity && orgUuid && token
    ? courseReviewClient(orgUuid, courseuuid, token) : undefined, [workspaceIdentity, orgUuid, courseuuid, token])
  const [reviewResult, setReviewResult] = useState<{ client: typeof reviewClient; candidate: CourseReviewCandidate | null }>()
  const candidate = reviewClient && reviewResult?.client === reviewClient ? reviewResult.candidate : null
  const reviewLoading = session?.status === 'loading' || Boolean(
    token && actor && (!orgUuid || (reviewClient && reviewResult?.client !== reviewClient)),
  )
  useEffect(() => {
    if (!reviewClient) return
    const controller = new AbortController()
    reviewClient.candidate(controller.signal).then(result => {
      if (!controller.signal.aborted) setReviewResult({ client: reviewClient, candidate: result })
    }).catch(() => {
      if (!controller.signal.aborted) setReviewResult({ client: reviewClient, candidate: null })
    })
    return () => controller.abort()
  }, [reviewClient, params.subpage])
  const [adaptiveResult, setAdaptiveResult] = useState<{ client: typeof adaptiveClient; catalog: AdaptiveCatalog | null }>()
  const adaptive = adaptiveClient && adaptiveResult?.client === adaptiveClient ? adaptiveResult.catalog : null
  const adaptiveLoading = session?.status === 'loading' || Boolean(
    token && actor && (!orgUuid || (adaptiveClient && adaptiveResult?.client !== adaptiveClient)),
  )
  useEffect(() => {
    if (!adaptiveClient) return
    const controller = new AbortController()
    adaptiveClient.request<AdaptiveCatalog>(
      `courses/${encodeURIComponent(courseuuid)}/configuration`, 'GET', undefined, undefined, controller.signal,
    ).then(catalog => {
      if (!controller.signal.aborted) setAdaptiveResult({ client: adaptiveClient, catalog })
    }).catch(() => {
      if (!controller.signal.aborted) setAdaptiveResult({ client: adaptiveClient, catalog: null })
    })
    return () => controller.abort()
  }, [adaptiveClient, courseuuid, params.subpage])
  const [certificateResult, setCertificateResult] = useState<{ client: typeof adaptiveClient; available: boolean }>()
  const certificateAvailable = adaptiveClient && certificateResult?.client === adaptiveClient && certificateResult.available
  const certificateLoading = session?.status === 'loading' || Boolean(
    token && actor && (!orgUuid || (adaptiveClient && certificateResult?.client !== adaptiveClient)),
  )
  useEffect(() => {
    if (!adaptiveClient) return
    const controller = new AbortController()
    adaptiveClient.request<{ available: boolean }>(
      `courses/${encodeURIComponent(courseuuid)}/award-workspace`, 'GET', undefined, undefined, controller.signal,
    ).then(result => {
      if (!controller.signal.aborted) setCertificateResult({ client: adaptiveClient, available: result.available })
    }).catch(() => {
      if (!controller.signal.aborted) setCertificateResult({ client: adaptiveClient, available: false })
    })
    return () => controller.abort()
  }, [adaptiveClient, courseuuid])

  // Define tab configurations with their required permissions
  const allTabs = [
    {
      key: 'general',
      label: t('dashboard.courses.settings.tabs.general'),
      icon: Info,
      href: `/dash/courses/course/${params.courseuuid}/general`,
      requiredPermission: 'update' as const
    },
    {
      key: 'content',
      label: t('dashboard.courses.settings.tabs.content'),
      icon: GalleryVerticalEnd,
      href: `/dash/courses/course/${params.courseuuid}/content`,
      requiredPermission: 'update_content' as const
    },
    {
      key: 'schedule',
      label: t('dashboard.courses.settings.tabs.schedule', 'Schedule'),
      icon: CalendarClock,
      href: `/dash/courses/course/${params.courseuuid}/schedule`,
      requiredPermission: 'update' as const,
      requiresPlan: 'standard' as PlanLevel
    },
    {
      key: 'access',
      label: t('dashboard.courses.settings.tabs.access'),
      icon: Globe,
      href: `/dash/courses/course/${params.courseuuid}/access`,
      requiredPermission: 'manage_access' as const
    },
    {
      key: 'contributors',
      label: t('dashboard.courses.settings.tabs.contributors'),
      icon: UserPen,
      href: `/dash/courses/course/${params.courseuuid}/contributors`,
      requiredPermission: 'manage_contributors' as const
    },
    {
      key: 'seo',
      label: t('dashboard.courses.settings.tabs.seo'),
      icon: Search,
      href: `/dash/courses/course/${params.courseuuid}/seo`,
      requiredPermission: 'update' as const,
      requiresPlan: 'standard' as PlanLevel
    },
    {
      key: 'certification',
      label: t('dashboard.courses.settings.tabs.certification'),
      icon: Award,
      href: `/dash/courses/course/${params.courseuuid}/certification`,
      requiredPermission: 'create_certifications' as const,
      requiresPlan: 'pro' as PlanLevel
    },
    {
      key: 'analytics',
      label: t('dashboard.courses.settings.tabs.analytics'),
      icon: ChartBar,
      href: `/dash/courses/course/${params.courseuuid}/analytics`,
      requiredPermission: 'update' as const,
      requiresPlan: 'pro' as PlanLevel
    }
  ]
  // Course analytics ship with Enterprise Edition; the tab is hidden without it.
  const tabs = allTabs.filter((tab) => tab.key !== 'analytics' || isOrgAnalyticsAvailable())
  if (adaptive) {
    tabs.push({
      key: 'adaptive',
      label: t('adaptive.title', 'Adaptive learning'),
      icon: Shapes,
      href: `/dash/courses/course/${params.courseuuid}/adaptive`,
      requiredPermission: 'update_content',
    })
  }
  if (candidate) {
    tabs.push({
      key: 'content-review',
      label: t('contentReview.title', 'Content review'),
      icon: ClipboardCheck,
      href: `/dash/courses/course/${params.courseuuid}/content-review`,
      requiredPermission: 'update_content',
    })
  }
  if (certificateAvailable) {
    tabs.push({
      key: 'managed-certificates',
      label: t('dashboard.courses.settings.tabs.managedCertificates', 'Managed certificates'),
      icon: Award,
      href: `/dash/courses/course/${params.courseuuid}/managed-certificates`,
      requiredPermission: 'update_content',
    })
  }

  // Filter tabs based on permissions
  const visibleTabs = tabs.filter(tab => ['adaptive', 'content-review', 'managed-certificates'].includes(tab.key)
    || hasPermission(tab.requiredPermission))

  // Check if current subpage is accessible
  const currentTab = tabs.find(tab => tab.key === params.subpage)
  const hasAccessToCurrentPage = currentTab
    ? visibleTabs.includes(currentTab)
    : false
  const checkingAccess = rightsLoading ||
    (adaptiveLoading && (params.subpage === 'adaptive' || visibleTabs.length === 0)) ||
    (reviewLoading && (params.subpage === 'content-review' || visibleTabs.length === 0)) ||
    (certificateLoading && (params.subpage === 'managed-certificates' || visibleTabs.length === 0))

  // Redirect to first available tab if current page is not accessible
  useEffect(() => {
    if (!checkingAccess && !hasAccessToCurrentPage && visibleTabs.length > 0) {
      const firstAvailableTab = visibleTabs[0]
      router.replace(getUriWithOrg(params.orgslug, '') + firstAvailableTab.href)
    }
  }, [checkingAccess, hasAccessToCurrentPage, visibleTabs, router, params.orgslug])

  // Access denied (rights loaded but no tabs visible)
  if (!checkingAccess && visibleTabs.length === 0) {
    return (
      <div className="h-screen w-full bg-[#f8f8f8] flex items-center justify-center">
        <div className="text-center">
          <Lock className="mx-auto h-12 w-12 text-gray-400 mb-4" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">{t('dashboard.courses.settings.access_denied.title')}</h3>
          <p className="text-gray-500">{t('dashboard.courses.settings.access_denied.message')}</p>
        </div>
      </div>
    )
  }

  // CourseProvider is always rendered so course meta fetches IN PARALLEL with rights,
  // no sequential waterfall. The tab content is gated by hasPermission() which returns
  // false (safe default) until rights finish loading.
  return (
    <div className="h-screen w-full bg-[#f8f8f8] grid grid-rows-[auto_1fr] grid-cols-1">
      <CourseProvider courseuuid={courseuuid} withUnpublishedActivities={true}>
        <div className="ps-4 pe-4 sm:ps-10 sm:pe-10 text-sm tracking-tight bg-[#fcfbfc] z-10 nice-shadow relative min-w-0 overflow-hidden">
          <CourseOverviewTop params={params} />
          <DashTabBar tabs={tabs.map((tab) => {
            const hasAccess = visibleTabs.includes(tab)
            const IconComponent = tab.icon
            return {
              key: tab.key,
              label: tab.label,
              icon: <IconComponent size={16} />,
              href: getUriWithOrg(params.orgslug, '') + tab.href,
              active: params.subpage.toString() === tab.key,
              disabled: !hasAccess,
              disabledTooltip: !hasAccess ? (
                <div className="text-center">
                  <div className="font-medium text-gray-900">{t('dashboard.courses.settings.access_restricted.title')}</div>
                  <div className="text-sm text-gray-600">
                    {t('dashboard.courses.settings.access_restricted.message', { tab: tab.label })}
                  </div>
                </div>
              ) : undefined,
              requiresPlan: tab.requiresPlan,
            } as DashTabItem
          })} />
        </div>
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.1, type: 'spring', stiffness: 80 }}
          className="h-full overflow-y-auto overflow-x-hidden"
        >
          <div>
            {checkingAccess ? (
              <div className="p-10 space-y-4 animate-pulse">
                <div className="h-6 bg-gray-200 rounded w-48" />
                <div className="h-4 bg-gray-200 rounded w-full max-w-xl" />
                <div className="h-4 bg-gray-200 rounded w-full max-w-md" />
                <div className="h-32 bg-gray-200 rounded w-full max-w-2xl mt-6" />
              </div>
            ) : null}
            {!rightsLoading && params.subpage == 'content' && hasPermission('update_content') ? (
              <EditCourseStructure orgslug={params.orgslug} />
            ) : null}
            {!checkingAccess && params.subpage === 'adaptive' && adaptive && orgUuid && token ? (
              <AdaptiveConfiguration key={adaptiveClient?.scope} orgUuid={orgUuid} courseUuid={courseuuid} accessToken={token} initial={adaptive} />
            ) : null}
            {!checkingAccess && params.subpage === 'content-review' && candidate && reviewClient ? (
              <ContentReviewPanel key={workspaceIdentity} client={reviewClient} initial={candidate} />
            ) : null}
            {!checkingAccess && params.subpage === 'managed-certificates' && certificateAvailable && adaptiveClient ? (
              <div className="mx-auto w-full max-w-6xl p-4 sm:p-10">
                <StaffCertificateReview key={workspaceIdentity} client={adaptiveClient.request} courseUuid={courseuuid} />
              </div>
            ) : null}
            {!rightsLoading && params.subpage == 'general' && hasPermission('update') ? (
              <>
                <EditCourseGeneral orgslug={params.orgslug} />
                {hasPermission('update') && org?.org_uuid && session?.data?.tokens?.access_token ? (
                  <div className="mx-auto w-full max-w-6xl px-4 pb-8 sm:px-10">
                    <ReadinessPanel
                      orgUuid={org.org_uuid}
                      courseUuid={courseuuid}
                      accessToken={session.data.tokens.access_token}
                      adminMode={canManageOrg}
                    />
                  </div>
                ) : null}
              </>
            ) : null}
            {!rightsLoading && params.subpage == 'schedule' && hasPermission('update') ? (
              <FeatureGate feature="drip">
                <EditCourseSchedule orgslug={params.orgslug} />
              </FeatureGate>
            ) : null}
            {!rightsLoading && params.subpage == 'access' && hasPermission('manage_access') ? (
              <EditCourseAccess orgslug={params.orgslug} />
            ) : null}
            {!rightsLoading && params.subpage == 'contributors' && hasPermission('manage_contributors') ? (
              <EditCourseContributors orgslug={params.orgslug} />
            ) : null}
            {!rightsLoading && params.subpage == 'seo' && hasPermission('update') ? (
              <>
                <div className="h-6" />
                <FeatureGate feature="seo">
                  <EditCourseSEO orgslug={params.orgslug} />
                </FeatureGate>
              </>
            ) : null}
            {!rightsLoading && params.subpage == 'certification' && hasPermission('create_certifications') ? (
              <>
                <div className="h-6" />
                <FeatureGate feature="certifications">
                  <EditCourseCertification orgslug={params.orgslug} />
                </FeatureGate>
              </>
            ) : null}
            {!rightsLoading && params.subpage == 'analytics' && hasPermission('update') ? (
              <FeatureGate feature="course_analytics">
                <CourseAnalyticsTab courseUUID={courseuuid} />
              </FeatureGate>
            ) : null}
          </div>
        </motion.div>
      </CourseProvider>
    </div>
  )
}

export default CourseOverviewPage
