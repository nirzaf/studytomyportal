'use client'
import React from 'react'
import { Search } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { getMenuColorClasses } from '@services/utils/ts/colorUtils'
import { useIsMac } from '@/hooks/useIsMac'
import { useSiteCommandPalette } from './SiteCommandPaletteContext'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@components/ui/tooltip'

interface Props {
  className?: string
  primaryColor?: string
  isMobile?: boolean
}

/**
 * Both sizes of the header search open the same command palette.
 */
export function SearchTrigger({ className = '', primaryColor = '', isMobile = false }: Props) {
  const { t } = useTranslation()
  const { setOpen } = useSiteCommandPalette()
  const isMac = useIsMac()
  const colors = getMenuColorClasses(primaryColor)
  const label = t('search.palette.trigger')
  const hint = isMac ? t('search.palette.hint_mac') : t('search.palette.hint_other')

  return (
    <TooltipProvider delayDuration={250}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            onClick={() => setOpen(true)}
            aria-label={label}
            aria-haspopup="dialog"
            className={`flex h-10 w-full items-center gap-2 rounded-lg px-2.5 text-start text-sm
                        transition-colors focus-visible:outline-2 focus-visible:outline-offset-2
                        ${primaryColor ? colors.searchBg : 'border border-slate-200 bg-slate-50 text-slate-700 hover:bg-slate-100'} ${className}`}
          >
            <Search size={20} aria-hidden className="shrink-0" />
            <span className="lh-search-label min-w-0 flex-1 truncate">{label}</span>
            {!isMobile && (
              <kbd aria-hidden="true" className="lh-search-kbd shrink-0 rounded border border-current/15 px-1.5 py-0.5 font-sans text-[10.5px] font-medium leading-none">
                {hint}
              </kbd>
            )}
          </button>
        </TooltipTrigger>
        <TooltipContent side="bottom">{label} <kbd className="ms-1">{hint}</kbd></TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
