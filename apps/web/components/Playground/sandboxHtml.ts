/**
 * Defense-in-depth containment for untrusted/AI-generated Playground HTML.
 *
 * The iframe runs on an opaque origin (`sandbox="allow-scripts"` without
 * `allow-same-origin`, `allow-forms`, `allow-popups`, or navigation flags),
 * and the document itself receives a strict zero-egress Content Security
 * Policy so learner choices and widget interactions cannot perform outbound
 * network requests (`fetch`, `XHR`, `sendBeacon`, `WebSocket`, external
 * scripts/styles/images/forms/frames).
 */

export const PLAYGROUND_IFRAME_SANDBOX = 'allow-scripts'

export const PLAYGROUND_WIDGET_CSP = [
  "default-src 'none'",
  "script-src 'unsafe-inline'",
  "style-src 'unsafe-inline'",
  'img-src data: blob:',
  'font-src data:',
  'media-src data: blob:',
  "connect-src 'none'",
  "form-action 'none'",
  "frame-src 'none'",
  "object-src 'none'",
  "base-uri 'none'",
].join('; ')

const CSP_META_TAG = `<meta http-equiv="Content-Security-Policy" content="${PLAYGROUND_WIDGET_CSP}">`

const EXISTING_CSP_META_RE =
  /<meta\b[^>]*http-equiv\s*=\s*(?:"[^"]*content-security-policy[^"]*"|'[^']*content-security-policy[^']*'|content-security-policy)[^>]*>/gi

const BASE_TAG_RE = /<base\b[^>]*>/gi

export function buildSandboxedPlaygroundHtml(rawHtml: string): string {
  const cleaned = rawHtml
    .replace(EXISTING_CSP_META_RE, '')
    .replace(BASE_TAG_RE, '')

  if (/<head\b[^>]*>/i.test(cleaned)) {
    return cleaned.replace(/<head\b[^>]*>/i, (match) => `${match}\n${CSP_META_TAG}`)
  }

  if (/<html\b[^>]*>/i.test(cleaned)) {
    return cleaned.replace(
      /<html\b[^>]*>/i,
      (match) => `${match}\n<head>${CSP_META_TAG}</head>`,
    )
  }

  return `<!DOCTYPE html><html><head>${CSP_META_TAG}</head><body>${cleaned}</body></html>`
}
