'use client'

import React, { useEffect, useState } from 'react'
import i18n from '../../lib/i18n'
import { applyDocumentDirection } from '../../lib/direction'

export default function I18nProvider({ children }: { children: React.ReactNode }) {
  const [, setLang] = useState(i18n.language)

  // Keep <html lang/dir> aligned with the English-only interface. The early
  // public/dir-init.js script sets the same values before first paint.
  useEffect(() => {
    applyDocumentDirection(i18n.language)

    const handleLanguageChanged = (lng: string) => {
      applyDocumentDirection(lng)
      setLang(lng)
    }
    i18n.on('languageChanged', handleLanguageChanged)
    return () => {
      i18n.off('languageChanged', handleLanguageChanged)
    }
  }, [])

  return <>{children}</>
}
