import {
  DEFAULT_LEARNER_LEVELS,
  MAX_ADAPTATION_LABEL_LENGTH,
  MAX_LEARNER_LEVELS,
  MAX_TARGET_SKILLS,
} from '@services/playgrounds/generator'

/**
 * Defense-in-depth containment for untrusted/AI-generated Playground HTML.
 *
 * The iframe uses an opaque origin and grants no script, form, popup, or
 * top-level-navigation privileges. Generated scripts, inline event handlers,
 * and non-fragment link destinations are removed; native HTML and CSS remain
 * available as a static preview. The document starts with a complete,
 * already-closed trusted `<head>` carrying restrictive resource policies
 * before byte 0 of untrusted widget markup. Because that trusted `<head>` closes before any
 * untrusted token is parsed, HTML comments (`<!-- <head> -->`), unclosed tags,
 * duplicate `<head>` elements, malformed/streaming fragments, or early
 * `<script>` tags cannot bypass or precede policy activation.
 *
 * Scope boundary: downloaded raw HTML and collaborative Board renderers are
 * separate surfaces and do not inherit this portal iframe wrapper.
 */

export const PLAYGROUND_IFRAME_SANDBOX = ''

export const PLAYGROUND_WIDGET_CSP = [
  "default-src 'none'",
  "script-src 'none'",
  "style-src 'unsafe-inline'",
  'img-src data: blob:',
  'font-src data:',
  'media-src data: blob:',
  "connect-src 'none'",
  "form-action 'none'",
  "frame-src 'none'",
  "child-src 'none'",
  "worker-src 'none'",
  "object-src 'none'",
  "base-uri 'none'",
  "manifest-src 'none'",
].join('; ')

const CSP_META_TAG = `<meta http-equiv="Content-Security-Policy" content="${PLAYGROUND_WIDGET_CSP}">`
const REFERRER_META_TAG = '<meta name="referrer" content="no-referrer">'
const TRUSTED_HEAD_PREFIX = `<!DOCTYPE html><html><head>${CSP_META_TAG}${REFERRER_META_TAG}</head>`

const RAWTEXT_TAGS = new Set(['script', 'style', 'textarea', 'title', 'xmp', 'iframe', 'noembed', 'noframes'])

function isHtmlWhitespace(ch: string): boolean {
  return ch === ' ' || ch === '\t' || ch === '\n' || ch === '\r' || ch === '\f'
}

function isAsciiAlpha(ch: string): boolean {
  const code = ch.charCodeAt(0)
  return (code >= 65 && code <= 90) || (code >= 97 && code <= 122)
}

interface ParsedAttr {
  name: string
  value: string
  start: number
  end: number
}

function isActiveAttribute(name: string): boolean {
  return (
    /^on[a-z]/i.test(name) ||
    name === 'nonce' ||
    name === 'srcdoc' ||
    name === 'ping' ||
    name === 'attributionsrc'
  )
}

/**
 * Scan from the `<` of an HTML open tag and return the tag name, attributes,
 * and the exclusive end index after the closing `>`. Never matches inside
 * comments or rawtext elements because the caller advances through those states
 * per HTML5 tokenization rules.
 */
function scanStartTag(
  html: string,
  ltIndex: number
): { tagName: string; attrs: ParsedAttr[]; end: number } | null {
  const len = html.length
  let pos = ltIndex + 1
  if (pos >= len || !isAsciiAlpha(html[pos])) return null

  const nameStart = pos
  while (pos < len && !isHtmlWhitespace(html[pos]) && html[pos] !== '>' && html[pos] !== '/') {
    pos++
  }
  const tagName = html.slice(nameStart, pos).toLowerCase()
  const attrs: ParsedAttr[] = []

  while (pos < len) {
    while (pos < len && (isHtmlWhitespace(html[pos]) || html[pos] === '/')) {
      pos++
    }
    if (pos >= len) {
      return { tagName, attrs, end: len }
    }
    if (html[pos] === '>') {
      return { tagName, attrs, end: pos + 1 }
    }

    const attrStart = pos
    while (
      pos < len &&
      !isHtmlWhitespace(html[pos]) &&
      html[pos] !== '=' &&
      html[pos] !== '>' &&
      html[pos] !== '/'
    ) {
      pos++
    }
    const attrName = html.slice(attrStart, pos).toLowerCase()

    while (pos < len && isHtmlWhitespace(html[pos])) {
      pos++
    }

    let attrValue = ''
    if (pos < len && html[pos] === '=') {
      pos++
      while (pos < len && isHtmlWhitespace(html[pos])) {
        pos++
      }
      if (pos < len && (html[pos] === '"' || html[pos] === "'")) {
        const quote = html[pos]
        pos++
        const valStart = pos
        while (pos < len && html[pos] !== quote) {
          pos++
        }
        attrValue = html.slice(valStart, pos)
        if (pos < len && html[pos] === quote) {
          pos++
        }
      } else {
        const valStart = pos
        while (pos < len && !isHtmlWhitespace(html[pos]) && html[pos] !== '>') {
          pos++
        }
        attrValue = html.slice(valStart, pos)
      }
    }

    if (attrName) {
      attrs.push({ name: attrName, value: attrValue, start: attrStart, end: pos })
    }
  }

  return { tagName, attrs, end: len }
}

/**
 * Find the closing `</tagName>` rawtext boundary per HTML5 rawtext end-tag rules
 * so `<base>` or `<meta>` literals inside `<script>` / `<style>` are left intact
 * while real tags in document markup are sanitized.
 */
function findRawTextClose(html: string, startIndex: number, tagName: string): number {
  const lower = html.toLowerCase()
  const needle = `</${tagName}`
  let pos = startIndex
  while (pos < html.length) {
    const idx = lower.indexOf(needle, pos)
    if (idx === -1) return html.length
    const nextCharPos = idx + needle.length
    if (nextCharPos >= html.length) return html.length
    const nextChar = html[nextCharPos]
    if (isHtmlWhitespace(nextChar) || nextChar === '>' || nextChar === '/') {
      const gt = html.indexOf('>', nextCharPos)
      return gt === -1 ? html.length : gt + 1
    }
    pos = idx + 2
  }
  return html.length
}

function sanitizeAnchorOrAreaTag(rawTag: string, tagName: string, attrs: ParsedAttr[]): string {
  const hrefAttrs = attrs.filter((a) => a.name === 'href' || a.name === 'xlink:href')
  const hrefAttr = hrefAttrs.length === 1 ? hrefAttrs[0] : undefined
  const targetAttr = attrs.find((a) => a.name === 'target')
  // Widgets only need in-document navigation. Keep literal fragment links and
  // strip every other destination, including relative and protocol URLs.
  const isAllowedFragment = hrefAttr !== undefined && hrefAttr.value.startsWith('#')
  const shouldRemoveHref = hrefAttrs.length > 0 && !isAllowedFragment
  const shouldDropTarget = targetAttr !== undefined
  const shouldDropActiveAttribute = attrs.some((attr) => isActiveAttribute(attr.name))

  if (!shouldRemoveHref && !shouldDropTarget && !shouldDropActiveAttribute) {
    return rawTag
  }

  const keptAttrs: string[] = []
  for (const attr of attrs) {
    if (attr.name === 'target') continue
    if ((attr.name === 'href' || attr.name === 'xlink:href') && shouldRemoveHref) continue
    if (isActiveAttribute(attr.name)) continue
    keptAttrs.push(rawTag.slice(attr.start, attr.end))
  }
  const attrStr = keptAttrs.length > 0 ? ` ${keptAttrs.join(' ')}` : ''
  return `<${tagName}${attrStr}>`
}

/**
 * Strip executable markup, `<base>`, and untrusted pragma `<meta>` elements;
 * keep only in-document fragment links on anchors and areas.
 */
export function stripUnsafeMarkup(rawHtml: string): string {
  const input = typeof rawHtml === 'string' ? rawHtml : ''
  const len = input.length
  const parts: string[] = []
  let cursor = 0
  let i = 0

  while (i < len) {
    const lt = input.indexOf('<', i)
    if (lt === -1) break

    // HTML comment <!-- ... -->
    if (input.startsWith('<!--', lt)) {
      let endComment = input.indexOf('-->', lt + 4)
      if (endComment === -1) {
        // Close an unclosed streaming comment so downstream structural parsing stays clean
        parts.push(input.slice(cursor), '-->')
        cursor = len
        i = len
        break
      }
      endComment += 3
      i = endComment
      continue
    }

    // <!DOCTYPE ...> or bogus <!...> comment
    if (lt + 1 < len && (input[lt + 1] === '!' || input[lt + 1] === '?')) {
      const gt = input.indexOf('>', lt + 2)
      i = gt === -1 ? len : gt + 1
      continue
    }

    // Closing tags </...>
    if (lt + 1 < len && input[lt + 1] === '/') {
      const gt = input.indexOf('>', lt + 2)
      i = gt === -1 ? len : gt + 1
      continue
    }

    if (lt + 1 < len && isAsciiAlpha(input[lt + 1])) {
      const parsed = scanStartTag(input, lt)
      if (!parsed) {
        i = lt + 1
        continue
      }

      const { tagName, attrs, end } = parsed

      if (tagName === 'script') {
        parts.push(input.slice(cursor, lt))
        const scriptEnd = findRawTextClose(input, end, 'script')
        cursor = scriptEnd
        i = scriptEnd
        continue
      }

      if (
        tagName === 'style' &&
        attrs.some(
          (attr) => attr.name === 'id' && attr.value.toLowerCase() === 'studytomy-adaptive-runtime-style'
        )
      ) {
        // This generated stylesheet hid every adaptation variant until its
        // generated switcher ran. Scripts are disabled in previews, so remove
        // only that reserved rule and leave all lesson variants readable.
        parts.push(input.slice(cursor, lt))
        const styleEnd = findRawTextClose(input, end, 'style')
        cursor = styleEnd
        i = styleEnd
        continue
      }

      if (tagName === 'base') {
        parts.push(input.slice(cursor, lt))
        cursor = end
        i = end
        continue
      }

      if (tagName === 'meta') {
        const httpEquivAttr = attrs.find((a) => a.name === 'http-equiv')
        if (httpEquivAttr) {
          parts.push(input.slice(cursor, lt))
          cursor = end
          i = end
          continue
        }
      }

      const rawTag = input.slice(lt, end)
      const adjustedAttrs: ParsedAttr[] = attrs.map((a) => ({
        ...a,
        start: a.start - lt,
        end: a.end - lt,
      }))
      const rewritten =
        tagName === 'a' || tagName === 'area'
          ? sanitizeAnchorOrAreaTag(rawTag, tagName, adjustedAttrs)
          : stripActiveAttributes(rawTag, tagName, adjustedAttrs)
      if (rewritten !== rawTag) {
        parts.push(input.slice(cursor, lt), rewritten)
        cursor = end
      }

      if (RAWTEXT_TAGS.has(tagName)) {
        i = findRawTextClose(input, end, tagName)
        continue
      }

      i = end
      continue
    }

    i = lt + 1
  }

  if (cursor < len) {
    parts.push(input.slice(cursor))
  }
  return parts.join('')
}

export interface AdaptivePreviewSettings {
  enabled: boolean
  levels: string[]
  target_skills: string[]
}

export interface AdaptivePreviewSelection {
  level: string
  skill: string
}

export interface AdaptiveWidgetOptions {
  levels: string[]
  skills: string[]
}

interface AdaptiveWidgetContract extends AdaptiveWidgetOptions {
  document: Document
  levelSelector: HTMLSelectElement
  skillSelector: HTMLSelectElement
}

function normalizedWidgetText(value: string): string {
  return value.replace(/\s+/g, ' ').trim().toLowerCase()
}

function boundedLabels(values: string[]): boolean {
  return values.every(
    (value) =>
      typeof value === 'string' &&
      value.length > 0 &&
      value.length <= MAX_ADAPTATION_LABEL_LENGTH &&
      !/[\r\n]/.test(value) &&
      value.trim() === value
  )
}

function selectOptions(select: HTMLSelectElement): string[] {
  return Array.from(select.options, (option) =>
    (option.value || option.textContent || '').trim()
  )
}

function readAdaptiveWidgetContract(
  rawHtml: string,
  settings: AdaptivePreviewSettings
): AdaptiveWidgetContract | null {
  if (
    settings.enabled !== true ||
    !Array.isArray(settings.levels) ||
    !Array.isArray(settings.target_skills) ||
    settings.levels.length > MAX_LEARNER_LEVELS ||
    settings.target_skills.length > MAX_TARGET_SKILLS ||
    !boundedLabels(settings.levels) ||
    !boundedLabels(settings.target_skills) ||
    typeof DOMParser === 'undefined'
  ) {
    return null
  }

  const parsed = new DOMParser().parseFromString(stripUnsafeMarkup(rawHtml), 'text/html')
  const levelMarkers = Array.from(parsed.querySelectorAll('[data-study-level-selector]'))
  const skillMarkers = Array.from(parsed.querySelectorAll('[data-study-skill-selector]'))
  if (
    levelMarkers.length !== 1 ||
    skillMarkers.length !== 1 ||
    levelMarkers[0].tagName !== 'SELECT' ||
    skillMarkers[0].tagName !== 'SELECT' ||
    levelMarkers[0] === skillMarkers[0]
  ) {
    return null
  }

  const levelSelector = levelMarkers[0] as HTMLSelectElement
  const skillSelector = skillMarkers[0] as HTMLSelectElement
  for (const selector of [levelSelector, skillSelector]) {
    if (
      selector.hasAttribute('disabled') ||
      selector.hasAttribute('hidden') ||
      selector.getAttribute('aria-hidden') === 'true' ||
      selector.closest('[hidden], [data-study-adaptation-variant]') ||
      selector.multiple ||
      Array.from(selector.options).some((option) => option.disabled || option.hidden)
    ) {
      return null
    }
  }

  const levels = selectOptions(levelSelector)
  const skills = selectOptions(skillSelector)
  const expectedLevels = settings.levels.length ? settings.levels : [...DEFAULT_LEARNER_LEVELS]
  if (
    levels.length < 2 ||
    levels.length > MAX_LEARNER_LEVELS ||
    skills.length < 2 ||
    skills.length > MAX_TARGET_SKILLS ||
    new Set(levels).size !== levels.length ||
    new Set(skills).size !== skills.length ||
    !boundedLabels(levels) ||
    !boundedLabels(skills) ||
    levels.length !== expectedLevels.length ||
    levels.some((label, index) => label !== expectedLevels[index]) ||
    !settings.target_skills.every((skill) => skills.includes(skill))
  ) {
    return null
  }

  const variants = Array.from(parsed.querySelectorAll('[data-study-adaptation-variant]'))
  const expectedPairs = new Set(levels.flatMap((level) => skills.map((skill) => `${level}\u0000${skill}`)))
  const foundPairs = new Set<string>()
  const variantContent = new Map<string, { scaffolding: string; challenge: string }>()
  for (const variant of variants) {
    const level = variant.getAttribute('data-level') || ''
    const skill = variant.getAttribute('data-skill') || ''
    const key = `${level}\u0000${skill}`
    const scaffolding = variant.querySelectorAll('[data-study-scaffolding]')
    const challenge = variant.querySelectorAll('[data-study-challenge]')
    if (
      variant.tagName !== 'ARTICLE' ||
      !expectedPairs.has(key) ||
      foundPairs.has(key) ||
      scaffolding.length !== 1 ||
      challenge.length !== 1
    ) {
      return null
    }
    const scaffoldText = scaffolding[0].textContent || ''
    const challengeText = challenge[0].textContent || ''
    if (scaffoldText.trim().length < 12 || challengeText.trim().length < 12) return null
    foundPairs.add(key)
    variantContent.set(key, {
      scaffolding: normalizedWidgetText(scaffoldText),
      challenge: normalizedWidgetText(challengeText),
    })
  }
  if (foundPairs.size !== expectedPairs.size) return null

  for (const skill of skills) {
    const rows = levels.map((level) => variantContent.get(`${level}\u0000${skill}`)!)
    if (
      new Set(rows.map((row) => row.scaffolding)).size < 2 ||
      new Set(rows.map((row) => row.challenge)).size < 2
    ) {
      return null
    }
  }
  for (const level of levels) {
    const columns = skills.map((skill) => variantContent.get(`${level}\u0000${skill}`)!)
    if (
      new Set(columns.map((column) => column.scaffolding)).size < 2 ||
      new Set(columns.map((column) => column.challenge)).size < 2
    ) {
      return null
    }
  }

  return { document: parsed, levelSelector, skillSelector, levels, skills }
}

/** Return only bounded, validated options; malformed legacy HTML stays static. */
export function getAdaptiveWidgetOptions(
  rawHtml: string,
  settings: AdaptivePreviewSettings | null | undefined
): AdaptiveWidgetOptions | null {
  if (!settings) return null
  const contract = readAdaptiveWidgetContract(rawHtml, settings)
  return contract ? { levels: contract.levels, skills: contract.skills } : null
}

function stripActiveAttributes(rawTag: string, tagName: string, attrs: ParsedAttr[]): string {
  if (!attrs.some((attr) => isActiveAttribute(attr.name))) {
    return rawTag
  }
  const keptAttrs = attrs
    .filter((attr) => !isActiveAttribute(attr.name))
    .map((attr) => rawTag.slice(attr.start, attr.end))
  return `<${tagName}${keptAttrs.length ? ` ${keptAttrs.join(' ')}` : ''}>`
}

function selectedAdaptiveMarkup(
  rawHtml: string,
  settings: AdaptivePreviewSettings | null | undefined,
  selection: AdaptivePreviewSelection | null | undefined
): string | null {
  if (!settings || !selection) return null
  const contract = readAdaptiveWidgetContract(rawHtml, settings)
  if (
    !contract ||
    !contract.levels.includes(selection.level) ||
    !contract.skills.includes(selection.skill)
  ) {
    return null
  }

  for (const selector of [contract.levelSelector, contract.skillSelector]) {
    const label = selector.closest('label')
    if (label) label.remove()
    else selector.remove()
  }
  for (const variant of Array.from(
    contract.document.querySelectorAll('[data-study-adaptation-variant]')
  )) {
    const selected =
      variant.getAttribute('data-level') === selection.level &&
      variant.getAttribute('data-skill') === selection.skill
    if (!selected) variant.remove()
    else {
      variant.removeAttribute('hidden')
      variant.removeAttribute('aria-hidden')
    }
  }

  return `<!DOCTYPE html>${contract.document.documentElement.outerHTML}`
}

function removeGeneratedAdaptiveSelectors(rawHtml: string): string {
  if (typeof DOMParser === 'undefined') return rawHtml
  const parsed = new DOMParser().parseFromString(rawHtml, 'text/html')
  for (const selector of Array.from(
    parsed.querySelectorAll('[data-study-level-selector], [data-study-skill-selector]')
  )) {
    const label = selector.closest('label')
    if (label) label.remove()
    else selector.remove()
  }
  return `<!DOCTYPE html>${parsed.documentElement.outerHTML}`
}

export function buildSandboxedPlaygroundHtml(
  rawHtml: string,
  settings?: AdaptivePreviewSettings | null,
  selection?: AdaptivePreviewSelection | null
): string {
  const sanitized = stripUnsafeMarkup(rawHtml)
  const adaptiveMarkup = selectedAdaptiveMarkup(sanitized, settings, selection)
  const staticMarkup = adaptiveMarkup ?? removeGeneratedAdaptiveSelectors(sanitized)
  return `${TRUSTED_HEAD_PREFIX}${staticMarkup}`
}

