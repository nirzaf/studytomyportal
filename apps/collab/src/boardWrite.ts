import { mergeUpdates } from 'yjs'
import { YDOC_STATE_EMPTY, YDOC_STATE_HEADER, YDOC_STATE_PRESENT } from './boardState.js'
import { assertSuccessfulYdocWrite } from './persistenceQueue.js'

export async function writeBoardSnapshot(
  pending: Uint8Array,
  maxBytes: number,
  request: (init: RequestInit) => Promise<Response>,
): Promise<void> {
  const current = await request({ method: 'GET' })
  assertSuccessfulYdocWrite(current)
  const etag = current.headers.get('etag')
  const kind = current.headers.get(YDOC_STATE_HEADER)
  const stored = new Uint8Array(await current.arrayBuffer())
  if (!etag || !(
    (kind === YDOC_STATE_EMPTY && stored.length === 0) ||
    (kind === YDOC_STATE_PRESENT && stored.length > 0)
  )) {
    throw new Error('Board state API returned an invalid snapshot or ETag')
  }
  if (stored.length > maxBytes) throw new Error('Stored board state exceeds the size limit')
  const merged = stored.length ? mergeUpdates([stored, pending]) : pending
  if (merged.length > maxBytes) throw new Error('Merged board state exceeds the size limit')
  if (Buffer.from(stored).equals(merged)) return

  const written = await request({
    method: 'PUT',
    headers: { 'If-Match': etag, 'Content-Type': 'application/octet-stream' },
    body: Buffer.from(merged),
  })
  assertSuccessfulYdocWrite(written)
}
