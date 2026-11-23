'use client'
import React, { useState } from 'react'
import { motion, AnimatePresence, useReducedMotion } from 'motion/react'
import { useRouter } from 'next/navigation'
import { ArrowRight, Check } from '@phosphor-icons/react'
import { useTranslation } from 'react-i18next'
import * as DialogPrimitive from '@radix-ui/react-dialog'
import {
  useOnboarding,
  OnboardingIntent,
} from '@components/Hooks/useOnboarding'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { useLHAnalytics, AnalyticsEvent } from '@services/analytics'
import WelcomeGlobe from './WelcomeGlobe'
import { INTENTS } from './onboardingCopy'

const ease = [0.16, 1, 0.3, 1] as const

/**
 * First visit to the dashboard: welcome the user, then ask what they are
 * building so the launch plan can be shaped around their intent.
 */
export default function WelcomeModal() {
  const { welcomeSeen, markWelcomeSeen, dismissed, setIntent, intent } =
    useOnboarding()
  const { t } = useTranslation()
  const { track } = useLHAnalytics('dashboard')
  const router = useRouter()
  const session = useLHSession() as any
  const reduceMotion = useReducedMotion()
  const firstName = session?.data?.user?.first_name || ''
  const [screen, setScreen] = useState<'welcome' | 'intent'>('welcome')
  const [choice, setChoice] = useState<OnboardingIntent | null>(intent)

  if (welcomeSeen || dismissed) return null

  const finish = (goToPlan: boolean) => {
    if (choice) {
      setIntent(choice)
      track(AnalyticsEvent.OnboardingIntentSelected, { intent: choice })
    }
    track(AnalyticsEvent.OnboardingWelcomeCompleted, {
      intent: choice ?? 'none',
    })
    markWelcomeSeen()
    // Relative on purpose: the dashboard is already on the org's host.
    if (goToPlan) router.push('/dash/onboarding')
  }

  const primary =
    'flex min-h-11 w-full items-center justify-center gap-2 rounded-lg bg-black py-3 text-sm font-semibold text-white transition-colors hover:bg-gray-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 disabled:bg-gray-200 disabled:text-gray-500'

  return (
    <DialogPrimitive.Root
      open={!welcomeSeen && !dismissed}
      onOpenChange={(open) => {
        if (!open) finish(false)
      }}
    >
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay
          className="fixed inset-0 bg-black/15 backdrop-blur-[2px]"
          style={{ zIndex: 'var(--z-modal-backdrop)' }}
        />
        <DialogPrimitive.Content
          asChild
          onInteractOutside={(event) => event.preventDefault()}
        >
          <motion.div
            initial={reduceMotion ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: reduceMotion ? 0 : 0.25 }}
            className="fixed inset-0 flex items-end justify-center outline-none sm:items-center"
            style={{ zIndex: 'var(--z-modal)' as any }}
          >
            <motion.div
              initial={
                reduceMotion ? false : { y: 30, opacity: 0, scale: 0.97 }
              }
              animate={{ y: 0, opacity: 1, scale: 1 }}
              transition={{ duration: reduceMotion ? 0 : 0.35, ease }}
              className="relative max-h-[100dvh] w-full overflow-y-auto sm:mx-4 sm:max-w-2xl"
            >
              <div className="bg-white rounded-t-2xl sm:rounded-2xl nice-shadow relative overflow-hidden">
                {/* Secondary action, kept clear of the globe */}
                <button
                  type="button"
                  onClick={() =>
                    screen === 'welcome' ? finish(false) : setScreen('welcome')
                  }
                  className={`absolute top-3 z-10 inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg px-2 text-sm font-medium text-gray-700 transition-colors hover:text-gray-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 ${screen === 'welcome' ? 'end-3' : 'start-3'}`}
                >
                  {screen === 'welcome'
                    ? t('onboarding.welcome.skip')
                    : t('onboarding.welcome.back')}
                </button>
                <AnimatePresence mode="wait" initial={false}>
                  {screen === 'welcome' ? (
                    <motion.div
                      key="welcome"
                      exit={
                        reduceMotion ? { opacity: 1 } : { opacity: 0, y: -12 }
                      }
                      transition={{ duration: reduceMotion ? 0 : 0.25, ease }}
                      className="px-6 sm:px-10 pt-8 sm:pt-10"
                    >
                      <div className="text-center">
                        <img
                          src="/studytomy-mini.png"
                          alt="Studytomy"
                          className="h-10 w-10 mx-auto mb-4"
                        />
                        <DialogPrimitive.Title asChild>
                          <h1 className="text-2xl font-bold tracking-tight text-gray-900">
                            {firstName
                              ? t('onboarding.welcome.hello_name', {
                                  name: firstName,
                                })
                              : t('onboarding.welcome.title')}
                          </h1>
                        </DialogPrimitive.Title>
                        <DialogPrimitive.Description asChild>
                          <p className="mt-2 text-sm text-gray-600">
                            {t('onboarding.welcome.promise')}
                          </p>
                        </DialogPrimitive.Description>
                      </div>
                    </motion.div>
                  ) : (
                    <motion.div
                      key="intent"
                      initial={reduceMotion ? false : { opacity: 0, y: 12 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ duration: reduceMotion ? 0 : 0.3, ease }}
                      className="px-6 sm:px-10 pt-8 sm:pt-10"
                    >
                      <div className="text-center">
                        <DialogPrimitive.Title asChild>
                          <h2 className="text-2xl font-bold tracking-tight text-gray-900">
                            {t('onboarding.welcome.intent_title')}
                          </h2>
                        </DialogPrimitive.Title>
                        <DialogPrimitive.Description asChild>
                          <p className="mt-2 text-sm text-gray-600">
                            {t('onboarding.welcome.intent_subtitle')}
                          </p>
                        </DialogPrimitive.Description>
                      </div>
                      <div
                        className="mt-6 grid gap-2 sm:grid-cols-2"
                        role="group"
                        aria-label={t('onboarding.welcome.intent_title')}
                      >
                        {INTENTS.map((it) => {
                          const on = choice === it.id
                          return (
                            <button
                              key={it.id}
                              type="button"
                              aria-pressed={on}
                              onClick={() => setChoice(it.id)}
                              className={[
                                'relative flex min-h-16 items-start gap-3 rounded-xl p-4 text-start transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2',
                                on
                                  ? 'bg-white ring-2 ring-black'
                                  : 'bg-white nice-shadow hover:bg-gray-50',
                              ].join(' ')}
                            >
                              <it.icon
                                size={22}
                                weight="duotone"
                                className="shrink-0 text-gray-700"
                                aria-hidden="true"
                              />
                              <span className="min-w-0 pe-5">
                                <span className="block text-sm font-semibold text-gray-900">
                                  {t(`onboarding.intent.${it.id}.label`)}
                                </span>
                                <span className="mt-0.5 block text-xs leading-snug text-gray-500">
                                  {t(`onboarding.intent.${it.id}.hint`)}
                                </span>
                              </span>
                              {on && (
                                <span className="absolute top-3 end-3 flex h-4 w-4 items-center justify-center rounded-full bg-black text-white">
                                  <Check
                                    size={10}
                                    weight="bold"
                                    aria-hidden="true"
                                  />
                                </span>
                              )}
                            </button>
                          )
                        })}
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>

                {/* Keep the full globe above the action, including on short screens. */}
                <div
                  className="pointer-events-none flex justify-center mt-2"
                  aria-hidden
                >
                  <div
                    className="shrink-0"
                    style={{
                      width: 'clamp(180px, calc(100dvh - 212px), 280px)',
                    }}
                  >
                    <WelcomeGlobe />
                  </div>
                </div>

                <div className="relative z-10 px-6 pb-6">
                  {screen === 'welcome' ? (
                    <button
                      type="button"
                      onClick={() => setScreen('intent')}
                      className={primary}
                    >
                      {t('onboarding.welcome.get_started')}
                      <ArrowRight
                        size={16}
                        weight="bold"
                        data-dir-flip
                        aria-hidden="true"
                      />
                    </button>
                  ) : (
                    <button
                      type="button"
                      disabled={!choice}
                      onClick={() => finish(true)}
                      className={primary}
                    >
                      {t('onboarding.welcome.build_plan')}
                      <ArrowRight
                        size={16}
                        weight="bold"
                        data-dir-flip
                        aria-hidden="true"
                      />
                    </button>
                  )}
                </div>
              </div>
            </motion.div>
          </motion.div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}
