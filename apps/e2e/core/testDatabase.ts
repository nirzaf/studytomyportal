import { isIP } from 'node:net'
import { createHash } from 'node:crypto'

type DatabaseIdentity = { host: string; port: number; database: string }

function parseSafeStudytomyTestDatabase(raw: string | undefined): DatabaseIdentity | null {
  if (!raw) return null
  try {
    const url = new URL(raw)
    const hostname = url.hostname.toLowerCase().replace(/^\[|\]$/g, '')
    const loopback = hostname === 'localhost' || hostname === '::1'
      || (isIP(hostname) === 4 && hostname.startsWith('127.'))
    const database = decodeURIComponent(url.pathname.slice(1))
    if (!/^postgres(?:ql)?(?:\+[a-z0-9]+)?:$/.test(url.protocol)
      || !loopback
      || url.search !== ''
      || url.hash !== ''
      || !/^[a-z0-9_-]+_test$/i.test(database)) return null
    return { host: hostname, port: Number(url.port) || 5432, database }
  } catch {
    return null
  }
}

/** Hash the endpoint identity only; credentials and connection strings never leave the runner. */
export function studytomyTestDatabaseIdentity(raw: string | undefined): string | null {
  const identity = parseSafeStudytomyTestDatabase(raw)
  if (!identity) return null
  const stableIdentity = `${identity.host}\0${identity.port}\0${identity.database}`
  return createHash('sha256').update(stableIdentity).digest('hex')
}

/** Accept only PostgreSQL test databases on loopback with the repository's `_test` suffix. */
export function isSafeStudytomyTestDatabase(raw: string | undefined): boolean {
  return studytomyTestDatabaseIdentity(raw) !== null
}
