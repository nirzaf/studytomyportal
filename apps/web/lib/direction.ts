/**
 * Text direction: the single source of truth.
 *
 * Deliberately dependency-free: `app/global-error.tsx` renders outside every
 * provider (no i18n, no router) and still needs to import from here.
 *
 * The RTL language set is duplicated in `public/dir-init.js`, which runs before
 * any bundle loads and therefore cannot import this module. `tests/rtl-guard`
 * asserts the two stay in sync.
 */

export type Direction = 'ltr' | 'rtl'

/**
 * Superset of the locales we translate to. A browser can report `he` or `ur`
 * even though we don't ship those bundles yet; direction should still be
 * right, with the UI falling back to English text.
 */
export const RTL_LANGUAGES = new Set([
  'ar', // Arabic
  'fa', // Persian
  'he', // Hebrew
  'iw', // Hebrew (legacy code, still emitted by some browsers)
  'ur', // Urdu
  'ps', // Pashto
  'sd', // Sindhi
  'ug', // Uyghur
  'yi', // Yiddish
  'dv', // Dhivehi
  'ckb', // Central Kurdish
])

/** `en-GB` -> `en`. Mirrors the `.split('-')[0]` used throughout lib/i18n.ts. */
export function baseCode(lng?: string | null): string {
  return String(lng || 'en').split('-')[0].toLowerCase()
}

export function directionForLanguage(lng?: string | null): Direction {
  return RTL_LANGUAGES.has(baseCode(lng)) ? 'rtl' : 'ltr'
}

/** +1 in LTR, -1 in RTL. Multiply any physical x-offset by this. */
export function dirMultiplier(dir: Direction): 1 | -1 {
  return dir === 'rtl' ? -1 : 1
}

/** The application interface language is fixed to English. */
export function detectClientLanguage(): string {
  return 'en'
}

/**
 * Write `lang`/`dir` onto <html>. Idempotent.
 *
 * `--dir` is exposed as a CSS custom property so stylesheets can consume a
 * direction multiplier without reaching for JS.
 */
export function applyDocumentDirection(lng: string): Direction {
  const code = baseCode(lng)
  const dir = directionForLanguage(code)

  if (typeof document === 'undefined') return dir

  const el = document.documentElement
  el.setAttribute('lang', code)
  el.setAttribute('dir', dir)
  el.style.setProperty('--dir', dir === 'rtl' ? '-1' : '1')

  return dir
}
