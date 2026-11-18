import type Redis from 'ioredis'
import { mergeUpdates } from 'yjs'
import { readBoundedBoardState, YDOC_STATE_EMPTY, YDOC_STATE_HEADER, YDOC_STATE_PRESENT } from './boardState.js'
import { assertSuccessfulYdocWrite } from './persistenceQueue.js'

export async function preservePendingBoardSnapshot(
  pending: Uint8Array,
  maxBytes: number,
  readPending: () => Promise<Uint8Array | null>,
  replacePending: (previous: Uint8Array | null, next: Uint8Array) => Promise<boolean>,
): Promise<Uint8Array> {
  for (let attempt = 0; attempt < 4; attempt++) {
    const previous = await readPending()
    if (previous && previous.length > maxBytes) throw new Error('Pending board state exceeds the size limit')
    const next = previous?.length ? mergeUpdates([previous, pending]) : pending
    if (next.length > maxBytes) throw new Error('Merged pending board state exceeds the size limit')
    if (await replacePending(previous, next)) return next
  }
  throw new Error('Pending board state changed during Redis storage')
}

export function cachePendingBoardSnapshot(
  redis: Pick<Redis, 'getBuffer' | 'eval'>,
  pendingKey: string,
  cacheKey: string,
  state: Uint8Array,
  maxBytes: number,
  ttlSeconds: number,
): Promise<Uint8Array> {
  return preservePendingBoardSnapshot(
    state,
    maxBytes,
    () => redis.getBuffer(pendingKey),
    async (previous, next) => (await redis.eval(
      `local current = redis.call('GET', KEYS[1])
       if (current or '') ~= ARGV[1] then return 0 end
       redis.call('SETEX', KEYS[1], ARGV[3], ARGV[2])
       redis.call('SETEX', KEYS[2], ARGV[3], ARGV[2])
       return 1`,
      2,
      pendingKey,
      cacheKey,
      previous ? Buffer.from(previous) : '',
      Buffer.from(next),
      ttlSeconds,
    )) === 1,
  )
}

export async function writeBoardSnapshot(
  pending: Uint8Array,
  maxBytes: number,
  request: (init: RequestInit) => Promise<Response>,
): Promise<Uint8Array> {
  const current = await request({ method: 'GET' })
  assertSuccessfulYdocWrite(current)
  const etag = current.headers.get('etag')
  const kind = current.headers.get(YDOC_STATE_HEADER)
  const stored = await readBoundedBoardState(current.body, maxBytes)
  if (!etag || !(
    (kind === YDOC_STATE_EMPTY && stored.length === 0) ||
    (kind === YDOC_STATE_PRESENT && stored.length > 0)
  )) {
    throw new Error('Board state API returned an invalid snapshot or ETag')
  }
  const merged = stored.length ? mergeUpdates([stored, pending]) : pending
  if (merged.length > maxBytes) throw new Error('Merged board state exceeds the size limit')
  if (Buffer.from(stored).equals(merged)) return stored

  const written = await request({
    method: 'PUT',
    headers: { 'If-Match': etag, 'Content-Type': 'application/octet-stream' },
    body: Buffer.from(merged),
  })
  assertSuccessfulYdocWrite(written)
  return merged
}
