'use client'

import React, { useEffect, useState } from 'react'
import type { PlaygroundLearnerAdaptation } from '@services/playgrounds/generator'
import GradeEightMathWidget, { GRADE_EIGHT_MATH_WIDGET } from './GradeEightMathWidget'
import {
  buildSandboxedPlaygroundHtml,
  getAdaptiveWidgetOptions,
  PLAYGROUND_IFRAME_SANDBOX,
  type AdaptivePreviewSelection,
  type AdaptiveWidgetOptions,
} from './sandboxHtml'

interface SafePlaygroundFrameProps {
  html: string | null
  adaptation?: PlaygroundLearnerAdaptation | null
  title: string
  className?: string
  controlsClassName?: string
}

export default function SafePlaygroundFrame({
  html,
  adaptation,
  title,
  className = '',
  controlsClassName = '',
}: SafePlaygroundFrameProps) {
  const [validated, setValidated] = useState<{
    html: string
    options: AdaptiveWidgetOptions | null
  } | null>(null)
  const [selection, setSelection] = useState<AdaptivePreviewSelection | null>(null)
  const levelsKey = adaptation?.levels?.join('\u0000') ?? ''
  const skillsKey = adaptation?.target_skills?.join('\u0000') ?? ''
  // The marker selects fixed app code. Its HTML and scripts never reach the widget.
  const builtInPractice = !adaptation?.enabled && html?.startsWith(GRADE_EIGHT_MATH_WIDGET)

  useEffect(() => {
    const validateTimer = setTimeout(() => {
      if (!html || !adaptation?.enabled) {
        setValidated(null)
        return
      }
      setValidated({ html, options: getAdaptiveWidgetOptions(html, adaptation) })
    }, 0)
    return () => clearTimeout(validateTimer)
  }, [html, adaptation, levelsKey, skillsKey])

  const options = validated?.html === html ? validated.options : null
  const selected: AdaptivePreviewSelection | null = options
    ? {
        level: options.levels.includes(selection?.level ?? '')
          ? selection!.level
          : options.levels[0],
        skill: options.skills.includes(selection?.skill ?? '')
          ? selection!.skill
          : options.skills[0],
      }
    : null
  const safeHtml = html && !builtInPractice
    ? buildSandboxedPlaygroundHtml(
        html,
        adaptation,
        options ? selected : null
      )
    : ''

  return (
    <div className={`flex min-h-0 flex-col ${className}`}>
      {builtInPractice && <GradeEightMathWidget />}
      {options && selected && (
        <div
          className={`z-10 flex shrink-0 flex-wrap items-end gap-3 border-b border-gray-200 bg-white/95 px-3 py-2 ${controlsClassName}`}
          role="group"
          aria-label="Choose this activity's practice focus"
        >
          <label className="flex flex-col gap-1 text-xs font-semibold text-gray-700">
            Level
            <select
              aria-label="Level"
              className="min-w-36 rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm font-normal text-gray-900"
              value={selected.level}
              onChange={(event) =>
                setSelection((current) => ({
                  level: event.target.value,
                  skill: current?.skill ?? selected.skill,
                }))
              }
            >
              {options.levels.map((level) => (
                <option key={level} value={level}>{level}</option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs font-semibold text-gray-700">
            Focus skill
            <select
              aria-label="Focus skill"
              className="min-w-44 rounded-md border border-gray-300 bg-white px-2.5 py-1.5 text-sm font-normal text-gray-900"
              value={selected.skill}
              onChange={(event) =>
                setSelection((current) => ({
                  level: current?.level ?? selected.level,
                  skill: event.target.value,
                }))
              }
            >
              {options.skills.map((skill) => (
                <option key={skill} value={skill}>{skill}</option>
              ))}
            </select>
          </label>
        </div>
      )}

      {safeHtml && (
        <iframe
          srcDoc={safeHtml}
          sandbox={PLAYGROUND_IFRAME_SANDBOX}
          referrerPolicy="no-referrer"
          className="min-h-0 w-full flex-1 border-0"
          title={title}
        />
      )}

      {safeHtml && (
        <p className="shrink-0 border-t border-gray-200 bg-white/90 px-2 py-1 text-[11px] text-gray-600" role="note">
          {options && selected
            ? 'Level and skill choices update this activity locally. Generated scripts remain disabled.'
            : adaptation?.enabled
              ? 'Adaptive controls are unavailable for this widget; showing its static lesson content.'
              : 'Generated scripts are disabled; other JavaScript-dependent controls will not run.'}
        </p>
      )}
    </div>
  )
}
