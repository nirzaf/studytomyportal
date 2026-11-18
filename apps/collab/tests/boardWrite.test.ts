import assert from 'node:assert/strict'
import { it } from 'node:test'
import * as Y from 'yjs'
import { writeBoardSnapshot } from '../src/boardWrite.ts'

it('rereads after a stale precondition and preserves both writers’ edits', async () => {
  const local = new Y.Doc()
  const concurrent = new Y.Doc()
  const restored = new Y.Doc()
  try {
    local.getText('local').insert(0, 'pending')
    concurrent.getText('remote').insert(0, 'acknowledged')
    const pending = Y.encodeStateAsUpdate(local)
    const remote = Y.encodeStateAsUpdate(concurrent)
    const firstRead = async (init: RequestInit) => {
      if (init.method === 'GET') {
        return new Response(null, { headers: { ETag: '"empty"', 'X-YDoc-State': 'empty' } })
      }
      assert.equal(new Headers(init.headers).get('If-Match'), '"empty"')
      return new Response(null, { status: 412 })
    }
    await assert.rejects(writeBoardSnapshot(pending, 1024, firstRead), /412/)

    await writeBoardSnapshot(pending, 1024, async (init) => {
      if (init.method === 'GET') {
        return new Response(Buffer.from(remote), {
          headers: { ETag: '"new"', 'X-YDoc-State': 'present' },
        })
      }
      assert.equal(new Headers(init.headers).get('If-Match'), '"new"')
      const body = await new Response(init.body).arrayBuffer()
      Y.applyUpdate(restored, new Uint8Array(body))
      return new Response(null)
    })
    assert.equal(restored.getText('local').toString(), 'pending')
    assert.equal(restored.getText('remote').toString(), 'acknowledged')
  } finally {
    local.destroy()
    concurrent.destroy()
    restored.destroy()
  }
})
