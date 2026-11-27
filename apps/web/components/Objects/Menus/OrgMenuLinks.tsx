import { useOrg } from '@components/Contexts/OrgContext'
import { getUriWithOrg } from '@services/config/config'
import { Books, CaretDown, FolderSimple, ChatsCircle, Headphones, Cube, ShoppingBag, Steps, VideoCamera, Compass, DotsThree, TextAa, ChatCircleDots, ShieldWarning } from '@phosphor-icons/react'
import { menuIcon } from '@components/Objects/Menus/menuIcons'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useTranslation } from 'react-i18next'
import { getMenuColorClasses } from '@services/utils/ts/colorUtils'
import { sitePages } from '@/lib/site-search/registry'
import { activeOrgMenuItem, ORG_HELP_ROUTES } from '@/lib/org-menu-path'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@components/ui/dropdown-menu'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@components/ui/tooltip'
import { Keys } from '@components/Objects/Shortcuts/Kbd'

// Chord per builtin menu entry, read from the same registry the palette uses.
const CHORD_BY_TYPE: Record<string, string | undefined> = Object.fromEntries(
  sitePages.filter((p) => p.menuType && p.shortcut).map((p) => [p.menuType as string, p.shortcut]),
)

type Builtin = { feature: string; link: string; labelKey?: string; label?: string; Icon: any }

const BUILTIN: Record<string, Builtin> = {
  courses: { feature: 'courses', link: '/courses', labelKey: 'courses.courses', Icon: Books },
  explore: { feature: 'courses', link: '/explore', label: 'Explore Topics', Icon: Compass },
  paths: { feature: 'paths', link: '/paths', labelKey: 'paths.title', Icon: Steps },
  library: { feature: 'folders', link: '/library', labelKey: 'library.library', Icon: FolderSimple },
  podcasts: { feature: 'podcasts', link: '/podcasts', labelKey: 'podcasts.podcasts', Icon: Headphones },
  live: { feature: 'live_sessions', link: '/live', labelKey: 'live_sessions.nav', Icon: VideoCamera },
  communities: { feature: 'communities', link: '/communities', labelKey: 'communities.title', Icon: ChatsCircle },
  playgrounds: { feature: 'playgrounds', link: '/playgrounds', labelKey: 'common.playgrounds', Icon: Cube },
  store: { feature: 'payments', link: '/store', labelKey: 'common.store', Icon: ShoppingBag },
}

// Default order when an org has no custom menu config.
const DEFAULT_ORDER = ['courses', 'explore', 'paths', 'library', 'podcasts', 'live', 'communities', 'playgrounds', 'store']
const DESKTOP_VISIBLE_ITEMS = 3

export type OrgMenuItem = {
  key: string
  label: string
  Icon: any
  href: string
  external: boolean
  shortcut?: string
}

export function useOrgHelpItems(orgslug: string): OrgMenuItem[] {
  const { t } = useTranslation()
  return ORG_HELP_ROUTES.map(item => ({
    ...item,
    href: getUriWithOrg(orgslug, item.href),
    label: item.key === 'support'
      ? t('common.help_menu.contact_support', 'Contact support')
      : t('common.help_menu.report_safety', 'Report a safety concern'),
    Icon: item.key === 'support' ? ChatCircleDots : ShieldWarning,
  }))
}

/**
 * The org's navigation entries: its custom menu config when it has one, else
 * the enabled features in the default order. Shared by the desktop header and
 * the phone tab bar / More sheet so both always list the same pages.
 */
export function useOrgMenuItems(orgslug: string): OrgMenuItem[] {
  const { t } = useTranslation()
  const org = useOrg() as any

  const rf = org?.config?.config?.resolved_features
  const isEnabled = (feature: string) => rf?.[feature]?.enabled === true

  const configItems: any[] | undefined =
    org?.config?.config?.customization?.menu?.items ?? org?.config?.config?.general?.menu?.items

  // Build the items to render (config-driven, else feature-driven defaults)
  const source =
    configItems && configItems.length
      ? [...configItems].sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
      : DEFAULT_ORDER.map((type, i) => ({ type, enabled: true, order: i, label: '', url: '' }))

  return source
    .map((item: any) => {
      if (item.type === 'custom') {
        if (!item.enabled || !item.url) return null
        const external = /^https?:\/\//i.test(item.url)
        return {
          key: `custom-${item.url}`,
          label: item.label || item.url,
          Icon: menuIcon(item.icon),
          href: external ? item.url : getUriWithOrg(orgslug, item.url),
          external,
        }
      }
      const meta = BUILTIN[item.type]
      if (!meta) return null
      if (!item.enabled) return null
      if (!isEnabled(meta.feature)) return null // plan/feature gating
      return {
        key: item.type,
        label: item.label || meta.label || t(meta.labelKey || 'common.search'),
        Icon: meta.Icon,
        href: getUriWithOrg(orgslug, meta.link),
        external: false,
        shortcut: CHORD_BY_TYPE[item.type],
      }
    })
    .filter(Boolean) as OrgMenuItem[]
}

function MenuLinks({ orgslug, primaryColor = '', expanded, onToggle }: {
  orgslug: string
  primaryColor?: string
  expanded: boolean
  onToggle: () => void
}) {
  const { t } = useTranslation()
  const colors = getMenuColorClasses(primaryColor)
  const items = useOrgMenuItems(orgslug)
  const pathname = usePathname()
  const active = pathname ? activeOrgMenuItem(items, pathname) : undefined
  const visible = items.slice(0, DESKTOP_VISIBLE_ITEMS)
  const more = items.slice(DESKTOP_VISIBLE_ITEMS)
  const buttonClass = 'inline-flex h-10 items-center justify-center rounded-lg transition-colors focus-visible:outline-2 focus-visible:outline-offset-2'
  const linkClass = `${buttonClass} ${expanded ? 'gap-1.5 whitespace-nowrap px-2.5 text-sm font-semibold' : 'w-10'}`
  const idleClass = `${colors.text} ${colors.hoverBg}`
  const activeClass = primaryColor ? `${colors.text} bg-current/10` : 'bg-teal-50 text-teal-900'
  const toggleLabel = t('dashboard.nav.show_labels', { defaultValue: 'Show navigation labels' })

  return (
    <TooltipProvider delayDuration={250}>
      <ul className="flex items-center gap-0.5 xl:gap-1">
        {visible.map((item) => {
          const selected = item === active
          const className = `${linkClass} ${selected ? activeClass : idleClass}`
          const content = (
            <>
              <item.Icon size={expanded ? 18 : 23} weight={selected ? 'fill' : 'regular'} aria-hidden className="shrink-0" />
              {expanded && <span className="max-w-[4.5rem] truncate min-[1150px]:max-w-28 xl:max-w-32">{item.label}</span>}
            </>
          )
          const link = item.external ? (
            <a href={item.href} target="_blank" rel="noopener noreferrer" className={className} aria-label={item.label}>{content}</a>
          ) : (
            <Link href={item.href} className={className} aria-label={item.label} aria-current={selected ? 'page' : undefined}>{content}</Link>
          )
          return (
            <li key={item.key}>
              <Tooltip>
                <TooltipTrigger asChild>{link}</TooltipTrigger>
                <TooltipContent side="bottom" className="flex items-center gap-2 text-xs">
                  {item.label}
                  {item.shortcut && <Keys keys={item.shortcut} />}
                </TooltipContent>
              </Tooltip>
            </li>
          )
        })}
        {more.length > 0 && (
          <li>
            <DropdownMenu modal={false}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <DropdownMenuTrigger asChild>
                    <button
                      type="button"
                      aria-label={t('common.more')}
                      className={`${linkClass} ${active && more.includes(active) ? activeClass : idleClass} data-[state=open]:bg-current/10`}
                    >
                      {expanded ? (
                        <>{t('common.more')}<CaretDown size={13} weight="bold" aria-hidden /></>
                      ) : <DotsThree size={23} weight="bold" aria-hidden />}
                    </button>
                  </DropdownMenuTrigger>
                </TooltipTrigger>
                <TooltipContent side="bottom">{t('common.more')}</TooltipContent>
              </Tooltip>
              <DropdownMenuContent align="start" sideOffset={10} className="max-h-[var(--radix-dropdown-menu-content-available-height)] w-60 overflow-y-auto rounded-xl p-1.5">
                {more.map((item) => {
                  const selected = item === active
                  const content = (
                    <>
                      <item.Icon size={17} aria-hidden />
                      <span className="min-w-0 flex-1 wrap-break-word">{item.label}</span>
                      {item.shortcut && <span aria-hidden="true"><Keys keys={item.shortcut} /></span>}
                    </>
                  )
                  return (
                    <DropdownMenuItem key={item.key} asChild className={`min-h-9 rounded-md ${selected ? 'bg-teal-50 font-semibold text-teal-900' : ''}`}>
                      {item.external ? (
                        <a href={item.href} target="_blank" rel="noopener noreferrer">{content}</a>
                      ) : (
                        <Link href={item.href} aria-current={selected ? 'page' : undefined}>{content}</Link>
                      )}
                    </DropdownMenuItem>
                  )
                })}
              </DropdownMenuContent>
            </DropdownMenu>
          </li>
        )}
        {visible.length > 0 && (
          <li className="ms-1 border-s border-current/10 ps-1">
            <Tooltip>
              <TooltipTrigger asChild>
                <button type="button" aria-label={toggleLabel} aria-pressed={expanded} onClick={onToggle}
                  className={`${buttonClass} w-10 ${expanded ? activeClass : idleClass}`}>
                  <TextAa size={21} weight="bold" aria-hidden />
                </button>
              </TooltipTrigger>
              <TooltipContent side="bottom">
                {expanded ? t('dashboard.nav.hide_labels', { defaultValue: 'Hide navigation labels' }) : toggleLabel}
              </TooltipContent>
            </Tooltip>
          </li>
        )}
      </ul>
    </TooltipProvider>
  )
}

export default MenuLinks
