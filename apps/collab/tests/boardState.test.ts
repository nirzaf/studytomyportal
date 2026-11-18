import assert from 'node:assert/strict'
import { Database } from '@hocuspocus/extension-database'
import { Hocuspocus } from '@hocuspocus/server'
import { describe, it } from 'node:test'
import * as Y from 'yjs'
import {
  BoardStateLoadError,
  loadBoardState,
  YDOC_STATE_EMPTY,
  YDOC_STATE_HEADER,
  YDOC_STATE_PRESENT,
  type BoardStateLoaderDependencies,
} from '../src/boardState.ts'

function dependencies(
  fetchDatabase: BoardStateLoaderDependencies['fetchDatabase'],
  readCache: BoardStateLoaderDependencies['readCache'] = async () => null,
  warmCache: BoardStateLoaderDependencies['warmCache'] = async () => {},
): BoardStateLoaderDependencies {
  return { maxBytes: 1024, fetchDatabase, readCache, warmCache, reportError: () => {} }
}

function apiResponse(
  body: Uint8Array,
  kind: string,
  status = 200,
): Response {
  const responseBody = body.buffer.slice(
    body.byteOffset,
    body.byteOffset + body.byteLength,
  ) as ArrayBuffer
  return new Response(responseBody, {
    status,
    headers: { [YDOC_STATE_HEADER]: kind },
  })
}

function runtime(loader: BoardStateLoaderDependencies) {
  let storeCalls = 0
  const server = new Hocuspocus({
    extensions: [
      new Database({
        fetch: ({ documentName }) => loadBoardState(documentName, loader),
        store: async () => {
          storeCalls += 1
        },
      }),
    ],
  })
  return { server, storeCalls: () => storeCalls }
}

async function loadDocument(server: Hocuspocus, documentName = 'board_test') {
  return server.createDocument(
    documentName,
    new Request('http://localhost/collab'),
    'test-socket',
    { readOnly: false, isAuthenticated: true },
  )
}

describe('authoritative board state loading', () => {
  it('rejects oversized snapshots from either storage path without creating a document', async () => {
    const oversized = new Uint8Array(1025)
    const cached = runtime(dependencies(
      async () => assert.fail('An oversized pending snapshot must not fall through to PostgreSQL'),
      async () => oversized,
    ))
    await assert.rejects(loadDocument(cached.server), BoardStateLoadError)
    assert.equal(cached.server.documents.has('board_test'), false)
    assert.equal(cached.storeCalls(), 0)

    const stored = runtime(dependencies(
      async () => apiResponse(oversized, YDOC_STATE_PRESENT),
      async () => null,
      async () => assert.fail('An oversized stored snapshot must not warm Redis'),
    ))
    await assert.rejects(loadDocument(stored.server), BoardStateLoadError)
    assert.equal(stored.server.documents.has('board_test'), false)
    assert.equal(stored.storeCalls(), 0)
  })

  it('does not initialize or persist an empty replacement after Redis and API failures, then recovers the original state', async () => {
    const original = new Y.Doc()
    original.getText('content').insert(0, 'saved board content')
    const originalState = Y.encodeStateAsUpdate(original)
    let apiAttempts = 0
    const { server, storeCalls } = runtime(
      dependencies(
        async () => {
          apiAttempts += 1
          if (apiAttempts === 1) throw new Error('simulated API timeout')
          if (apiAttempts === 2) return apiResponse(new Uint8Array(), YDOC_STATE_EMPTY, 503)
          return apiResponse(originalState, YDOC_STATE_PRESENT)
        },
        async () => {
          throw new Error('simulated Redis outage')
        },
      ),
    )

    try {
      await assert.rejects(loadDocument(server), BoardStateLoadError)
      assert.equal(server.documents.has('board_test'), false)
      assert.equal(storeCalls(), 0)

      await assert.rejects(loadDocument(server), BoardStateLoadError)
      assert.equal(server.documents.has('board_test'), false)
      assert.equal(storeCalls(), 0)

      const recovered = await loadDocument(server)
      assert.equal(recovered.getText('content').toString(), 'saved board content')
      assert.equal(storeCalls(), 0)
      recovered.destroy()
    } finally {
      original.destroy()
    }
  })

  it('allows a newly created board only when the API explicitly marks it empty', async () => {
    const { server, storeCalls } = runtime(
      dependencies(async () => apiResponse(new Uint8Array(), YDOC_STATE_EMPTY)),
    )

    const document = await loadDocument(server, 'board_new')
    assert.equal(document.getText('content').toString(), '')
    assert.equal(server.documents.has('board_new'), true)
    assert.equal(storeCalls(), 0)
    document.destroy()
  })

  it('rejects empty bodies without the explicit empty marker and rejects an empty present state', async () => {
    const missingMarker = dependencies(
      async () => new Response(new Uint8Array(), { status: 200 }),
    )
    const emptyPresent = dependencies(
      async () => apiResponse(new Uint8Array(), YDOC_STATE_PRESENT),
    )

    await assert.rejects(loadBoardState('board_test', missingMarker), BoardStateLoadError)
    await assert.rejects(loadBoardState('board_test', emptyPresent), BoardStateLoadError)
  })
})
