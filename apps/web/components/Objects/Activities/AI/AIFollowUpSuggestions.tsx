'use client'
import React from 'react'
import { useTranslation } from 'react-i18next'
import { motion, AnimatePresence } from 'motion/react'
import { Sparkles } from 'lucide-react'

type AIFollowUpSuggestionsProps = {
  suggestions: string[]
  onSuggestionClick: (suggestion: string) => void
  disabled?: boolean
}

function AIFollowUpSuggestions({
  suggestions,
  onSuggestionClick,
  disabled = false,
}: AIFollowUpSuggestionsProps) {
  const { t } = useTranslation()
  if (!suggestions || suggestions.length === 0) {
    return null
  }

  return (
    <div className="flex flex-col items-center gap-2 mt-3 mb-2">
      <div className="flex items-center gap-1.5 text-slate-600 text-xs">
        <Sparkles size={12} />
        <span>{t('ai.follow_up_suggestions')}</span>
      </div>
      <AnimatePresence>
        <div className="flex flex-wrap justify-center gap-2">
          {suggestions.map((suggestion, index) => (
            <motion.button
              key={`${suggestion}-${index}`}
              initial={{ opacity: 0, y: 8, scale: 0.96 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -5, scale: 0.98 }}
              transition={{
                type: 'spring',
                stiffness: 400,
                damping: 25,
                mass: 0.8,
                delay: index * 0.08,
              }}
              onClick={() => !disabled && onSuggestionClick(suggestion)}
              disabled={disabled}
              className={`
                flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium
                bg-slate-50 border border-slate-200 text-slate-700
                transition-all duration-200 ease-out
                ${
                  disabled
                    ? 'opacity-50 cursor-not-allowed'
                    : 'hover:bg-slate-100 hover:text-slate-900 hover:border-slate-300 cursor-pointer hover:scale-[1.02]'
                }
              `}
            >
              <span className="truncate max-w-[250px]">{suggestion}</span>
            </motion.button>
          ))}
        </div>
      </AnimatePresence>
    </div>
  )
}

export default AIFollowUpSuggestions
