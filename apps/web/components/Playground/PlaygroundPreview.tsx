'use client'

import React, { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowsOutSimple, ArrowsInSimple, CircleNotch } from '@phosphor-icons/react'
import type { PlaygroundLearnerAdaptation } from '@services/playgrounds/generator'
import SafePlaygroundFrame from './SafePlaygroundFrame'

interface PlaygroundPreviewProps {
  html: string | null
  adaptation?: PlaygroundLearnerAdaptation | null
  isStreaming?: boolean
  isFullscreen?: boolean
  onToggleFullscreen?: () => void
}

export default function PlaygroundPreview({
  html,
  adaptation,
  isStreaming,
  isFullscreen,
  onToggleFullscreen,
}: PlaygroundPreviewProps) {
  const { t } = useTranslation()
  const [renderedHtml, setRenderedHtml] = useState<string | null>(null)
  const [renderedAdaptation, setRenderedAdaptation] =
    useState<PlaygroundLearnerAdaptation | null>(null)

  useEffect(() => {
    // Keep the previous ~300ms live-preview throttle; final content updates on
    // the next task so iframe writes do not run during React's effect flush.
    const writeTimeout = setTimeout(() => {
      if (!html) {
        setRenderedHtml(null)
        setRenderedAdaptation(adaptation ?? null)
      } else {
        setRenderedHtml(html)
        setRenderedAdaptation(adaptation ?? null)
      }
    }, isStreaming ? 300 : 0)
    return () => clearTimeout(writeTimeout)
  }, [html, adaptation, isStreaming])

  return (
    <div className="flex-1 relative bg-gray-50/50">
      {/* Empty state */}
      {!html && !isStreaming && (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="text-center text-gray-400">
            <div className="w-16 h-16 mx-auto mb-4 rounded-2xl bg-white nice-shadow flex items-center justify-center">
              <svg className="w-8 h-8 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                  d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
              </svg>
            </div>
            <p className="text-sm font-semibold text-gray-500">{t('playgrounds.preview.live_preview')}</p>
            <p className="text-xs text-gray-400 mt-1">{t('playgrounds.preview.empty_hint')}</p>
          </div>
        </div>
      )}

      {/* Streaming indicator, top left */}
      {isStreaming && (
        <div className="absolute top-3 start-3 z-10 flex items-center gap-1.5 px-2.5 py-1.5 bg-black/75 backdrop-blur-sm rounded-full nice-shadow">
          <CircleNotch size={11} weight="bold" className="animate-spin text-sky-400" />
          <span className="text-[11px] text-white font-bold">{t('playgrounds.preview.generating')}</span>
        </div>
      )}

      {/* Fullscreen toggle, top right */}
      {onToggleFullscreen && (
        <button
          onClick={onToggleFullscreen}
          title={isFullscreen ? t('playgrounds.preview.exit_fullscreen') : t('playgrounds.preview.fullscreen')}
          className="absolute top-3 end-3 z-10 flex items-center justify-center w-8 h-8 bg-black/60 hover:bg-black/80 backdrop-blur-sm rounded-lg nice-shadow transition-all"
        >
          {isFullscreen
            ? <ArrowsInSimple size={14} weight="bold" className="text-white" />
            : <ArrowsOutSimple size={14} weight="bold" className="text-white" />
          }
        </button>
      )}

      <SafePlaygroundFrame
        html={renderedHtml}
        adaptation={renderedAdaptation}
        title={t('playgrounds.preview.iframe_title')}
        className="absolute inset-0"
        controlsClassName="pe-10"
      />
    </div>
  )
}
