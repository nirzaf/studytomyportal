import { describe, expect, test } from 'bun:test'
import fs from 'node:fs'
import path from 'node:path'

/** Guards locale and direction invariants that ESLint can't see. */

const webRoot = path.resolve(import.meta.dirname, '..')
const read = (p) => fs.readFileSync(path.join(webRoot, p), 'utf8')

describe('English-only application locale', () => {
  test('dir-init.js sets English LTR before first paint', () => {
    const initSrc = read('public/dir-init.js')
    expect(initSrc).toContain("setAttribute('lang', 'en')")
    expect(initSrc).toContain("setAttribute('dir', 'ltr')")
    expect(initSrc).toContain("setProperty('--dir', '1')")
    expect(initSrc).not.toMatch(/navigator\.language|i18nextLng|localStorage/)
  })

  test('i18next cannot select a non-English application locale', () => {
    const src = read('lib/i18n.ts')
    expect(src).toMatch(/lng:\s*'en'/)
    expect(src).toMatch(/SUPPORTED_LANGUAGES\s*=\s*\['en'\]/)
    expect(src).toMatch(/i18n\.changeLanguage\('en'\)/)
    expect(src).not.toContain('LanguageDetector')
  })

  test('global navigation and settings expose no locale selector', () => {
    const surfaces = [
      'components/Security/HeaderProfileBox.tsx',
      'components/Auth/AuthLayout.tsx',
      'components/Auth/AuthMobileHeader.tsx',
      'components/Dashboard/Menus/DashMobileMenu.tsx',
      'components/Dashboard/Menus/DashLeftMenu.tsx',
      'components/Objects/Menus/MobileNav.tsx',
      'app/home/home.tsx',
      'components/Dashboard/Pages/Org/OrgEditGeneral/OrgEditGeneral.tsx',
    ]
    for (const file of surfaces) {
      const src = read(file)
      expect(src, file).not.toContain('LanguageSwitcher')
      expect(src, file).not.toContain('AVAILABLE_LANGUAGES')
      expect(src, file).not.toContain('common.language')
      expect(src, file).not.toContain('defaultLanguage')
    }
  })

  test('Arabic and Persian are RTL', () => {
    const src = read('lib/direction.ts')
    const setBlock = src.match(/RTL_LANGUAGES = new Set\(\[([\s\S]*?)\]\)/)[1]
    expect(setBlock).toContain("'ar'")
    expect(setBlock).toContain("'fa'")
  })
})

describe('language registry', () => {
  const src = read('lib/languages.ts')
  const entries = [...src.matchAll(/\{\s*code:\s*'([a-z]+)'[^}]*\}/g)]

  test('every language declares a direction', () => {
    expect(entries.length).toBeGreaterThan(20)
    for (const entry of entries) {
      expect(entry[0]).toMatch(/dir:\s*'(ltr|rtl)'/)
    }
  })

  test('ar and fa are the RTL entries', () => {
    const rtl = entries.filter((e) => /dir:\s*'rtl'/.test(e[0])).map((e) => e[1]).sort()
    expect(rtl).toEqual(['ar', 'fa'])
  })

  // A locale that loads but isn't in the registry can never be picked, and one
  // in the registry without a loader falls back to English with no warning.
  test('registry and lazy loaders agree', () => {
    const i18nSrc = read('lib/i18n.ts')
    // The type annotation contains `=>`, so match lazily up to the assignment.
    const loaderBlock = i18nSrc.match(/LOCALE_LOADERS[\s\S]*?=\s*\{([\s\S]*?)\n\}/)[1]
    const loaderCodes = [...loaderBlock.matchAll(/^\s*(\w+):/gm)].map((m) => m[1]).sort()

    const registryCodes = entries.map((e) => e[1]).filter((c) => c !== 'en').sort()
    expect(loaderCodes).toEqual(registryCodes)
  })
})

describe('globals.css', () => {
  const css = read('styles/globals.css')
  const lines = css.split('\n')

  const DIRECTIONAL = new RegExp(
    [
      String.raw`(^|[^-\w])(left|right)\s*:`,
      String.raw`margin-(left|right)`,
      String.raw`padding-(left|right)`,
      String.raw`border-(left|right)`,
      String.raw`text-align\s*:\s*(left|right)`,
      String.raw`float\s*:\s*(left|right)`,
    ].join('|')
  )

  // Physical declarations are allowed, but only with a stated reason, so the
  // next person doesn't have to guess whether it was deliberate.
  test('every physical declaration is justified', () => {
    const unjustified = []
    lines.forEach((line, i) => {
      if (!DIRECTIONAL.test(line)) return
      const context = lines.slice(Math.max(0, i - 8), i).join('\n')
      if (/rtl-ok|mirrored|no logical form/.test(context)) return
      unjustified.push(`${i + 1}: ${line.trim()}`)
    })
    expect(unjustified).toEqual([])
  })

  test('RTL affordances are present', () => {
    expect(css).toContain('--font-arabic')
    expect(css).toContain('unicode-bidi: plaintext')
    expect(css).toMatch(/\[dir='rtl'\]/)
  })

  // Being present in the source is not enough: a rule can be valid, correct,
  // and still never reach the browser. The icon-mirroring block was silently
  // swallowed once because a comment above it contained `*/`, which closed the
  // comment early and took the following rule with it. Nothing failed: no
  // build error, no lint error, the app just quietly stopped mirroring icons.
  // So assert against the COMPILED output, not the source.
  test('RTL rules survive compilation', async () => {
    const { default: tailwind } = await import('@tailwindcss/postcss')
    const { default: postcss } = await import('postcss')

    const result = await postcss([tailwind({ base: webRoot, optimize: { minify: true } })])
      .process(css, { from: path.join(webRoot, 'styles/globals.css') })

    for (const needle of [
      'lucide-chevron-right', // directional icon mirroring
      'data-dir-flip', // opt-in for icon libraries without per-icon classes
      'recharts-wrapper', // charts locked to LTR
      'unicode-bidi', // per-block direction for authored content
      '--leading-tight', // Arabic line-height
      'lh-toast', // toast direction
      'lh-org-font-root', // Tajawal overriding the inline org font
    ]) {
      expect(result.css).toContain(needle)
    }

    // Tajawal must be forced, not offered as a fallback. next/font injects a
    // local "<Family> Fallback" face for CLS, and on macOS that adjusted system
    // font covers Arabic, so a per-glyph fallback stack resolves there and the
    // real Arabic face never loads. Two !important rules (one per layer,
    // because the base font-family is declared in both) are what prevent that.
    const forced = result.css.match(/font-family:var\(--font-arabic\)[^}]*!important/g) ?? []
    expect(forced.length).toBe(2)
  }, 60_000)
})

describe('Arabic translations', () => {
  const en = JSON.parse(read('locales/en.json'))
  const ar = JSON.parse(read('locales/ar.json'))

  const flatten = (obj, prefix = '') =>
    Object.entries(obj).flatMap(([k, v]) =>
      v && typeof v === 'object' && !Array.isArray(v)
        ? flatten(v, `${prefix}${k}.`)
        : [`${prefix}${k}`]
    )

  test('ar.json covers every en.json key', () => {
    // Build the Arabic key set once. Inside the filter it was rebuilt (and
    // the whole file re-flattened) for every English key, which put the test
    // on the edge of its own timeout.
    const arabicKeys = new Set(flatten(ar))
    const missing = flatten(en).filter((k) => !arabicKeys.has(k))
    expect(missing).toEqual([])
  })
})
