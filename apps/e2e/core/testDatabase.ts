import { isIP } from 'node:net'

/** Accept only PostgreSQL test databases on loopback with the repository's `_test` suffix. */
export function isSafeStudytomyTestDatabase(raw: string | undefined): boolean {
  if (!raw) return false
  try {
    const url = new URL(raw)
    const hostname = url.hostname.toLowerCase().replace(/^\[|\]$/g, '')
    const loopback = hostname === 'localhost' || hostname === '::1'
      || (isIP(hostname) === 4 && hostname.startsWith('127.'))
    return /^postgres(?:ql)?(?:\+[a-z0-9]+)?:$/.test(url.protocol)
      && loopback
      && decodeURIComponent(url.pathname.slice(1)).toLowerCase().endsWith('_test')
  } catch {
    return false
  }
}
