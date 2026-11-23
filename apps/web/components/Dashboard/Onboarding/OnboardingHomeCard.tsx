'use client'
import React from 'react'
import Link from 'next/link'
import { ArrowRight, RocketLaunch, X } from '@phosphor-icons/react'
import { useTranslation } from 'react-i18next'
import { useOnboarding } from '@components/Hooks/useOnboarding'
import { useOrg } from '@components/Contexts/OrgContext'
import { getUriWithOrg } from '@services/config/config'

/**
 * The launch plan on the dashboard home, until setup is done: progress, the
 * next step with its walkthrough, and the way back in.
 */
export default function OnboardingHomeCard() {
  const { t } = useTranslation()
  const { steps, currentStep, progress, minutesLeft, dismissed, welcomeSeen, allCompleted, dismiss } = useOnboarding()
  const org = useOrg() as any
  if (dismissed || !welcomeSeen || allCompleted || !currentStep) return null

  const href = getUriWithOrg(org?.slug || '', '/dash/onboarding')
  const done = steps.filter((s) => s.completed).length

  return (
    <div className="bg-white rounded-xl nice-shadow overflow-hidden">
      <div className="flex items-center justify-between px-5 pt-4 pb-3">
        <div className="flex items-center gap-3">
          <h3 className="text-sm font-semibold text-gray-700">{t('onboarding.launch_plan')}</h3>
          <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-gray-100 text-gray-700 tabular-nums">
            {done}/{steps.length}
          </span>
          <span className="hidden sm:inline text-[11px] text-gray-600">{t('onboarding.minutes_left', { count: minutesLeft })}</span>
        </div>
        <button
          type="button"
          onClick={dismiss}
          className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg text-gray-600 transition-colors hover:text-gray-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
          aria-label={t('onboarding.dismiss')}
          title={t('onboarding.dismiss')}
        >
          <X size={14} weight="bold" aria-hidden="true" />
        </button>
      </div>

      <div className="flex flex-col sm:flex-row sm:items-center gap-4 px-5 pb-5">
        <Link
          href={href}
          aria-label={t('onboarding.continue_setup')}
          className="group relative flex aspect-video w-full shrink-0 items-center justify-center overflow-hidden rounded-lg border border-sky-100 bg-gradient-to-br from-sky-50 via-indigo-50 to-white text-sky-800 transition-colors hover:border-sky-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 sm:w-40"
        >
          <span className="absolute start-3 top-3 rounded-full bg-white/80 px-2 py-1 text-[10px] font-semibold text-gray-500">
            {t('onboarding.step_number', { n: done + 1 })}
          </span>
          <span className="flex size-12 items-center justify-center rounded-2xl bg-white text-sky-800 shadow-sm transition-transform group-hover:scale-105">
            <RocketLaunch size={23} weight="duotone" aria-hidden="true" />
          </span>
          <ArrowRight size={15} weight="bold" data-dir-flip aria-hidden="true" className="absolute bottom-3 end-3 text-sky-700 opacity-70 transition-transform group-hover:translate-x-0.5" />
        </Link>

        <div className="min-w-0 flex-1">
          <p className="text-xs text-gray-600">{t('onboarding.up_next')}</p>
          <p className="mt-0.5 truncate text-sm font-semibold text-gray-900">{t(`onboarding.steps.${currentStep.id}.title`)}</p>
          <div
            className="mt-3 h-1.5 w-full max-w-xs overflow-hidden rounded-full bg-gray-100"
            role="progressbar"
            aria-label={t('onboarding.launch_plan')}
            aria-valuemin={0}
            aria-valuemax={steps.length}
            aria-valuenow={done}
            aria-valuetext={t('onboarding.progress_count', { done, total: steps.length })}
          >
            <div aria-hidden="true" className="h-full rounded-full bg-black transition-[width] duration-500" style={{ width: `${Math.max(progress * 100, 3)}%` }} />
          </div>
        </div>

        <Link
          href={href}
          className="inline-flex min-h-11 shrink-0 items-center justify-center gap-2 rounded-lg bg-black px-4 py-2.5 text-sm font-semibold text-white nice-shadow transition-colors hover:bg-gray-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
        >
          {t('onboarding.continue_setup')}
          <ArrowRight size={13} weight="bold" data-dir-flip aria-hidden="true" />
        </Link>
      </div>
    </div>
  )
}
