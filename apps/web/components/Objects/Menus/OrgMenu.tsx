'use client'
import React, { useEffect, useState } from 'react'
import dynamic from 'next/dynamic'
import { useMessaging, useMessagingEnabled } from '@components/Messaging/MessagingProvider'
import Image from 'next/image'
import Link from 'next/link'
import { getUriWithOrg } from '@services/config/config'
import { HeaderProfileBox } from '@components/Security/HeaderProfileBox'
import MenuLinks, { useOrgHelpItems } from './OrgMenuLinks'
import { activeOrgMenuItem } from '@/lib/org-menu-path'
import { MobileTabBar, OPEN_MOBILE_MORE_EVENT } from './MobileNav'
import UserAvatar from '@components/Objects/UserAvatar'
import { getOrgLogoMediaDirectory } from '@services/media/media'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { useOrg } from '@components/Contexts/OrgContext'
import { SearchTrigger } from '@components/Objects/Search/SearchTrigger'
import { usePathname } from 'next/navigation'
import { useLocalStorage, useMediaQuery } from 'usehooks-ts'
import { useTranslation } from 'react-i18next'
import useAdminStatus from '@components/Hooks/useAdminStatus'
import { useStudytomyStaffWorkspaces } from '@components/Hooks/useStudytomyStaffWorkspaces'
import {
  Question,
  Globe,
  ChatCircleDots,
  SquaresFour,
  ChalkboardSimple,
  Signpost,
  ChatsCircle,
  CalendarDots,
} from '@phosphor-icons/react'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@components/ui/dropdown-menu"
import { DASHBOARD_MENU_ITEMS, DashboardMenuItem } from '@/lib/dashboard-menu-items'
import { isFeatureAvailable } from '@services/plans/plans'
import { getMenuColorClasses } from '@services/utils/ts/colorUtils'
import AuthenticatedClientElement from '@components/Security/AuthenticatedClientElement'
import { useJoinBannerVisible, JOIN_BANNER_HEIGHT } from '@components/Objects/Banners/OrgJoinBanner'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@components/ui/tooltip'
import { useLHAnalytics, AnalyticsEvent } from '@services/analytics'
import { Keys } from '@components/Objects/Shortcuts/Kbd'
import { useShowLearnHouseBranding } from '@components/Hooks/useLearnHouseBranding'

// Only shows on demand (feedback button), so keep it out of every page's
// initial bundle.
const FeedbackModal = dynamic(
  () => import('@components/Objects/Modals/FeedbackModal').then((m) => m.FeedbackModal),
  { ssr: false }
)

export const OrgMenu = (props: any) => {
  const orgslug = props.orgslug
  const session = useLHSession() as any;
  const org = useOrg() as any;
  const [isFocusMode, setIsFocusMode] = useState(false)
  const [expanded, setExpanded] = useLocalStorage('studytomy-navigation-labels', false, { initializeWithValue: false })
  const isPhone = useMediaQuery('(max-width: 767px)')
  const pathname = usePathname()
  const { t } = useTranslation()
  const { rights } = useAdminStatus()
  const helpItems = useOrgHelpItems(orgslug)
  const activeHelp = activeOrgMenuItem(helpItems, pathname)
  const staffFeedback = session?.status === 'authenticated' && rights?.dashboard?.action_access
  const staffWorkspaces = useStudytomyStaffWorkspaces(Boolean(staffFeedback))
  const [feedbackModalOpen, setFeedbackModalOpen] = useState(false)
  // Mounted on first open and kept, so the close animation and draft survive.
  const [feedbackModalMounted, setFeedbackModalMounted] = useState(false)
  const { isVisible: isJoinBannerVisible } = useJoinBannerVisible()
  const { track } = useLHAnalytics()

  const messagingEnabled = useMessagingEnabled()
  const { unreadTotal } = useMessaging()


  const topOffset = isJoinBannerVisible ? JOIN_BANNER_HEIGHT : 0

  // Get primary color from org config (v2: customization.general.color, v1: general.color)
  const config = org?.config?.config
  const primaryColor = config?.customization?.general?.color || config?.general?.color || ''
  const colors = getMenuColorClasses(primaryColor)
  const utilityClass = `inline-flex ${expanded ? 'size-9' : 'size-10'} shrink-0 items-center justify-center rounded-lg transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 ${colors.iconBtn}`
  const utilityIconSize = expanded ? 19 : 22
  const dividerClass = primaryColor ? 'border-current/15' : 'border-slate-200'
  const showBranding = useShowLearnHouseBranding()

  // Filter dashboard menu items by resolved_features from API
  const rf = config?.resolved_features
  const visibleDashboardItems = DASHBOARD_MENU_ITEMS.filter((item: DashboardMenuItem) => {
    if (item.available && !item.available()) return false
    if (!item.featureKey) return true
    if (rf?.[item.featureKey]) return rf[item.featureKey].enabled
    return isFeatureAvailable(item.featureKey)
  })

  useEffect(() => {
    // Only check focus mode if we're in an activity page
    if (typeof window !== 'undefined' && pathname?.includes('/activity/')) {
      const saved = localStorage.getItem('globalFocusMode');
      setIsFocusMode(saved === 'true');
    } else {
      setIsFocusMode(false);
    }

    // Add storage event listener for cross-window changes
    const handleStorageChange = (e: StorageEvent) => {
      if (e.key === 'globalFocusMode' && pathname?.includes('/activity/')) {
        setIsFocusMode(e.newValue === 'true');
      }
    };

    // Add custom event listener for same-window changes
    const handleFocusModeChange = (e: CustomEvent) => {
      if (pathname?.includes('/activity/')) {
        setIsFocusMode(e.detail.isFocusMode);
      }
    };

    window.addEventListener('storage', handleStorageChange);
    window.addEventListener('focusModeChange', handleFocusModeChange as EventListener);

    // Cleanup
    return () => {
      window.removeEventListener('storage', handleStorageChange);
      window.removeEventListener('focusModeChange', handleFocusModeChange as EventListener);
    };
  }, [pathname]);

  // Only hide menu if we're in an activity page and focus mode is enabled.
  // Phones never enter focus mode (see activity.tsx).
  if (pathname?.includes('/activity/') && isFocusMode && !isPhone) {
    return null;
  }

  return (
    <>
      <div aria-hidden="true" className="h-[60px] shrink-0" style={{ marginTop: topOffset }} />
      <nav
        aria-label={t('dashboard.nav.top_navigation')}
        className={`fixed inset-x-0 h-[60px] border-b backdrop-blur-lg ${primaryColor ? 'border-current/10 ' + colors.text : 'border-slate-200 bg-white/95'}`}
        style={{
          zIndex: 'var(--z-nav)',
          backgroundColor: primaryColor || undefined,
          top: topOffset
        }}
      >
        <div className="mx-auto flex h-full w-full max-w-(--breakpoint-2xl) items-center gap-3 px-4 sm:px-6 xl:gap-5 xl:px-8">
          <div className="logo flex min-w-0 shrink-0">
            <Link href={getUriWithOrg(orgslug, '/')} className="flex h-11 min-w-0 items-center rounded-md focus-visible:outline-2 focus-visible:outline-offset-2">
              {org?.logo_image ? (
                <img
                  src={`${getOrgLogoMediaDirectory(org.org_uuid, org?.logo_image)}`}
                  alt={org?.name || 'Logo'}
                  className="h-10 w-auto max-w-28 object-contain rounded-md xl:max-w-36"
                />
              ) : showBranding ? (
                <StudytomyLogo logoFilter={colors.logoFilter} />
              ) : (
                <span className={`max-w-28 truncate text-base font-bold xl:max-w-36 ${colors.text}`} title={org?.name}>{org?.name}</span>
              )}
            </Link>
          </div>
          <div className="hidden lg:flex">
            <MenuLinks orgslug={orgslug} primaryColor={primaryColor} expanded={expanded} onToggle={() => setExpanded((value) => !value)} />
          </div>

          <div className={`lh-header-search ms-auto hidden min-w-10 justify-end lg:flex ${expanded ? 'max-w-72 flex-1' : 'w-10 shrink-0'}`}>
            <SearchTrigger primaryColor={primaryColor} />
          </div>

          <div className="ms-auto flex shrink-0 items-center gap-0.5 lg:ms-0">
            {/* Progress / Trail */}
            <AuthenticatedClientElement checkMethod="authentication">
              <div className="hidden lg:flex">
                <TooltipProvider delayDuration={0}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Link
                        href={getUriWithOrg(orgslug, '/trail')}
                        className={utilityClass}
                        aria-label={t('courses.progress')}
                      >
                        <Signpost size={utilityIconSize} aria-hidden />
                      </Link>
                    </TooltipTrigger>
                    <TooltipContent side="bottom" className="flex items-center gap-2 text-xs">
                      {t('courses.progress')}
                      <Keys keys="g t" />
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              </div>
            </AuthenticatedClientElement>
            {/* Calendar */}
            <AuthenticatedClientElement checkMethod="authentication">
              <div className="hidden lg:flex">
                <TooltipProvider delayDuration={0}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Link
                        href={getUriWithOrg(orgslug, '/calendar')}
                        className={utilityClass}
                        aria-label={t('calendar.title')}
                      >
                        <CalendarDots size={utilityIconSize} aria-hidden />
                      </Link>
                    </TooltipTrigger>
                    <TooltipContent side="bottom" className="flex items-center gap-2 text-xs">
                      {t('calendar.title')}
                      <Keys keys="g e" />
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              </div>
            </AuthenticatedClientElement>
            {/* Boards */}
            {rf?.boards?.enabled && (
              <AuthenticatedClientElement checkMethod="authentication">
                <div className="hidden lg:flex">
                  <TooltipProvider delayDuration={0}>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Link
                          href={getUriWithOrg(orgslug, '/boards')}
                          className={utilityClass}
                          aria-label={t('common.boards')}
                        >
                          <ChalkboardSimple size={utilityIconSize} aria-hidden />
                        </Link>
                      </TooltipTrigger>
                      <TooltipContent side="bottom" className="flex items-center gap-2 text-xs">
                        {t('common.boards')}
                        <Keys keys="g b" />
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                </div>
              </AuthenticatedClientElement>
            )}
            {/* Messages */}
            {messagingEnabled && (
              <AuthenticatedClientElement checkMethod="authentication">
                <div className="hidden lg:flex">
                  <TooltipProvider delayDuration={0}>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Link
                          href={getUriWithOrg(orgslug, '/messages')}
                          className={`relative ${utilityClass}`}
                          aria-label={unreadTotal ? `Messages, ${unreadTotal} unread` : 'Messages'}
                        >
                          <ChatsCircle size={utilityIconSize} aria-hidden />
                          {unreadTotal > 0 && (
                            <span className="absolute top-0.5 end-0.5 min-w-[16px] h-4 px-1 rounded-full bg-red-500 text-white text-[10px] font-bold leading-4 text-center ring-2 ring-white tabular-nums">
                              {unreadTotal > 99 ? '99+' : unreadTotal}
                            </span>
                          )}
                        </Link>
                      </TooltipTrigger>
                      <TooltipContent side="bottom" className="text-xs">
                        Messages
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                </div>
              </AuthenticatedClientElement>
            )}
            {/* Dashboard Dropdown - Only visible to admins */}
            {session?.status === 'authenticated' && rights?.dashboard?.action_access && (
              <div className="hidden lg:flex">
                <DropdownMenu>
                  <TooltipProvider delayDuration={0}>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <DropdownMenuTrigger asChild>
                          <button
                            className={utilityClass}
                            aria-label={t('common.dashboard')}
                          >
                            <SquaresFour size={utilityIconSize} aria-hidden />
                          </button>
                        </DropdownMenuTrigger>
                      </TooltipTrigger>
                      <TooltipContent side="bottom" className="text-xs">
                        {t('common.dashboard')}
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                  <DropdownMenuContent align="end" className="w-56">
                    <DropdownMenuLabel className="flex items-center gap-2">
                      <SquaresFour size={16} weight="fill" />
                      <span>{t('common.dashboard')}</span>
                    </DropdownMenuLabel>
                    <DropdownMenuSeparator />
                    {visibleDashboardItems.map((item) => {
                      const IconComponent = item.icon
                      return (
                        <DropdownMenuItem key={item.id} asChild>
                          <Link
                            href={item.href}
                            className="flex items-center gap-2"
                            onClick={() => track(AnalyticsEvent.DashboardEntered, { source: 'org_menu' })}
                          >
                            <IconComponent size={16} weight="fill" />
                            <span>{t(item.labelKey)}</span>
                          </Link>
                        </DropdownMenuItem>
                      )
                    })}
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            )}

            {orgslug && (
              <div className="hidden lg:flex">
                <DropdownMenu>
                  <TooltipProvider delayDuration={0}>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <DropdownMenuTrigger asChild>
                          <button
                            className={utilityClass}
                            aria-label={t('common.help')}
                          >
                            <Question size={utilityIconSize} aria-hidden />
                          </button>
                        </DropdownMenuTrigger>
                      </TooltipTrigger>
                      <TooltipContent side="bottom" className="text-xs">
                        {t('common.help')}
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                  <DropdownMenuContent align="end" className="w-56">
                    <DropdownMenuLabel className="flex items-center gap-2">
                      <Question size={16} weight="fill" />
                      <span>{t('common.help')}</span>
                    </DropdownMenuLabel>
                    <DropdownMenuSeparator />
                    {helpItems.map(item => (
                      <DropdownMenuItem key={item.key} asChild>
                        <Link href={item.href} aria-current={item === activeHelp ? 'page' : undefined}
                          className="flex items-center gap-2">
                          <item.Icon size={16} aria-hidden />
                          <span>{item.label}</span>
                        </Link>
                      </DropdownMenuItem>
                    ))}
                    {staffWorkspaces.length > 0 && <>
                      <DropdownMenuSeparator />
                      <DropdownMenuLabel>{t('dashboard.studytomy.workspaces', 'Staff workspaces')}</DropdownMenuLabel>
                      {staffWorkspaces.map(item => <DropdownMenuItem key={item.id} asChild>
                        <Link href={getUriWithOrg(orgslug, item.href)} className="flex items-center gap-2">
                          <item.icon size={16} aria-hidden />
                          {t(item.labelKey, item.label)}
                        </Link>
                      </DropdownMenuItem>)}
                    </>}
                    {staffFeedback && <>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem asChild>
                      <a
                        href="https://www.studytomy.com/"
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center gap-2"
                      >
                        <Globe size={16} weight="fill" />
                        <span>{t('common.help_menu.website')}</span>
                      </a>
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      onClick={() => {
                        setFeedbackModalMounted(true)
                        setFeedbackModalOpen(true)
                      }}
                      className="flex items-center gap-2"
                    >
                      <ChatCircleDots size={16} weight="fill" />
                      <span>{t('common.help_menu.report_feedback')}</span>
                    </DropdownMenuItem>
                    </>}
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            )}

            <div className={`hidden lg:flex ${session?.status === 'authenticated' ? `ms-1 border-s ps-2 ${dividerClass}` : ''}`}>
              <HeaderProfileBox primaryColor={primaryColor} />
            </div>
            {/* Phone and tablet actions. Navigation lives in the bottom tab bar
                (MobileTabBar); the header keeps only what a native app puts
                top-right: the inbox when signed in, sign-in otherwise. */}
            <div className="flex lg:hidden items-center gap-1">
              {session?.status === 'authenticated' ? (
                <>
                  {messagingEnabled && (
                    <Link
                      href={getUriWithOrg(orgslug, '/messages')}
                      className={`lh-press relative flex h-10 w-10 items-center justify-center rounded-full ${colors.iconBtn}`}
                      aria-label={unreadTotal ? `Messages, ${unreadTotal} unread` : 'Messages'}
                    >
                      <ChatsCircle size={24} weight="fill" />
                      {unreadTotal > 0 && (
                        <span className="absolute top-1 end-1 min-w-[16px] h-4 px-1 rounded-full bg-red-500 text-white text-[10px] font-bold leading-4 text-center ring-2 ring-white tabular-nums">
                          {unreadTotal > 99 ? '99+' : unreadTotal}
                        </span>
                      )}
                    </Link>
                  )}
                  <button
                    type="button"
                    onClick={() => window.dispatchEvent(new Event(OPEN_MOBILE_MORE_EVENT))}
                    className="lh-press flex h-10 w-10 items-center justify-center rounded-full"
                    aria-label={t('common.more')}
                  >
                    <UserAvatar border="border-2" rounded="rounded-full" width={32} />
                  </button>
                </>
              ) : session?.status === 'unauthenticated' ? (
                <Link
                  href={getUriWithOrg(orgslug, '/signup')}
                  className={`lh-press rounded-full px-4 py-2 text-sm font-semibold ${colors.signUpBtn}`}
                >
                  {t('auth.sign_up')}
                </Link>
              ) : null}
            </div>
          </div>
        </div>
      </nav>
      <MobileTabBar orgslug={orgslug} primaryColor={primaryColor} />

      {/* Feedback Modal */}
      {feedbackModalMounted && (
        <FeedbackModal
          open={feedbackModalOpen}
          onOpenChange={setFeedbackModalOpen}
          theme="light"
          userName={session?.data?.user?.username}
          userEmail={session?.data?.user?.email}
        />
      )}

    </>
  )
}

const StudytomyLogo = ({ logoFilter }: { logoFilter: string }) => {
  return (
    <Image
      src="/studytomy-logo.png"
      alt="Studytomy logo"
      width={639}
      height={428}
      loading="eager"
      className="h-10 w-auto object-contain"
      style={{ filter: logoFilter }}
    />
  )
}
