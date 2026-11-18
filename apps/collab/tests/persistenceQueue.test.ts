import assert from 'node:assert/strict'
import { once } from 'node:events'
import { createServer } from 'node:http'
import { describe, it } from 'node:test'
import * as Y from 'yjs'
import { loadBoardState } from '../src/boardState.ts'
import {
  assertSuccessfulYdocWrite,
  BoardPersistenceQueue,
  PersistenceQueueCapacityError,
  type PersistenceQueueOptions,
} from '../src/persistenceQueue.ts'

function queueOptions(
  write: PersistenceQueueOptions['write'],
  overrides: Partial<PersistenceQueueOptions> = {},
): PersistenceQueueOptions {
  return {
    debounceMs: 60_000,
    retryDelaysMs: [],
    requestTimeoutMs: 100,
    maxPendingDocuments: 8,
    maxPendingBytes: 1024,
    write,
    ...overrides,
  }
}

describe('board persistence queue', () => {
  it('retries a failed HTTP write and reloads the acknowledged Yjs snapshot', async () => {
    const received: Buffer[] = []
    let saved: Buffer | undefined
    const server = createServer(async (request, response) => {
      if (request.method === 'PUT') {
        const chunks: Buffer[] = []
        for await (const chunk of request) chunks.push(chunk)
        const body = Buffer.concat(chunks)
        received.push(body)
        if (received.length === 1) {
          response.writeHead(503).end()
          return
        }
        saved = body
        response.writeHead(200).end()
        return
      }
      response.writeHead(200, { 'X-YDoc-State': saved ? 'present' : 'empty' })
      response.end(saved)
    })
    const source = new Y.Doc()
    const reloaded = new Y.Doc()
    try {
      server.listen(0, '127.0.0.1')
      await once(server, 'listening')
      const address = server.address()
      assert(address && typeof address !== 'string')
      const url = `http://127.0.0.1:${address.port}/board/ydoc`
      const queue = new BoardPersistenceQueue(
        queueOptions(
          async (_boardUuid, state, timeoutMs) => {
            const response = await fetch(url, {
              method: 'PUT',
              body: Buffer.from(state),
              signal: AbortSignal.timeout(timeoutMs),
            })
            assertSuccessfulYdocWrite(response)
          },
          { debounceMs: 0, retryDelaysMs: [5], requestTimeoutMs: 2000 },
        ),
      )
      source.getText('content').insert(0, 'final synthetic edit')
      const original = Y.encodeStateAsUpdate(source)
      queue.schedule('board_http_retry', original)
      // Wait for automatic persistence, without another edit or shutdown flush.
      const deadline = Date.now() + 4000
      while (queue.pendingCount && Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 10))
      }
      assert.equal(queue.pendingCount, 0)
      assert.deepEqual(received, [Buffer.from(original), Buffer.from(original)])
      const snapshot = await loadBoardState('board_http_retry', {
        readCache: async () => null,
        fetchDatabase: () => fetch(url, { signal: AbortSignal.timeout(2000) }),
        warmCache: async () => {},
        reportError: () => {},
      })
      assert(snapshot)
      Y.applyUpdate(reloaded, snapshot)
      assert.equal(reloaded.getText('content').toString(), 'final synthetic edit')
    } finally {
      server.close()
      server.closeAllConnections()
      source.destroy()
      reloaded.destroy()
    }
  })

  it('retries a non-success response and persists the last snapshot without another edit', async () => {
    const writes: number[][] = []
    const responses = [503, 200]
    let attempts = 0
    const queue = new BoardPersistenceQueue(
      queueOptions(
        async (_boardUuid, state) => {
          attempts += 1
          assertSuccessfulYdocWrite(
            new Response(null, { status: responses.shift() }),
          )
          writes.push([...state])
        },
        { retryDelaysMs: [5] },
      ),
    )

    queue.schedule('board_retry', Uint8Array.from([1, 2, 3]))
    const unresolved = await queue.flushPending(500)

    assert.deepEqual(unresolved, [])
    assert.equal(attempts, 2)
    assert.deepEqual(writes, [[1, 2, 3]])
    assert.equal(queue.pendingCount, 0)
  })

  it('retries when the write adapter throws before returning a promise', async () => {
    let attempts = 0
    const writes: number[][] = []
    const queue = new BoardPersistenceQueue(
      queueOptions(
        (_boardUuid, state) => {
          attempts += 1
          if (attempts === 1) throw new Error('transport setup failed')
          writes.push([...state])
          return Promise.resolve()
        },
        { retryDelaysMs: [5] },
      ),
    )

    queue.schedule('board_sync_retry', Uint8Array.from([4, 5]))
    assert.deepEqual(await queue.flushPending(500), [])
    assert.equal(attempts, 2)
    assert.deepEqual(writes, [[4, 5]])
    assert.equal(queue.retainedBytes, 0)
  })

  it('serializes writes and sends the latest state after an older acknowledgement', async () => {
    let resolveFirstWrite!: () => void
    let firstStarted!: () => void
    const firstWriteStarted = new Promise<void>((resolve) => {
      firstStarted = resolve
    })
    const firstWrite = new Promise<void>((resolve) => {
      resolveFirstWrite = resolve
    })
    const sent: number[][] = []
    const acknowledged: number[][] = []
    let attempts = 0
    const queue = new BoardPersistenceQueue(
      queueOptions(async (_boardUuid, state) => {
        attempts += 1
        const snapshot = [...state]
        sent.push(snapshot)
        if (attempts === 1) {
          firstStarted()
          await firstWrite
        }
        acknowledged.push(snapshot)
      }),
    )

    queue.schedule('board_ordered', Uint8Array.from([1]))
    const shutdown = queue.flushPending(1000)
    await firstWriteStarted
    queue.schedule('board_ordered', Uint8Array.from([2]))
    assert.equal(attempts, 1)

    resolveFirstWrite()
    const unresolved = await shutdown

    assert.deepEqual(unresolved, [])
    assert.deepEqual(sent, [[1], [2]])
    assert.deepEqual(acknowledged, [[1], [2]])
  })

  it('keeps pending data and returns it when shutdown reaches its deadline', async () => {
    let resolveWrite!: () => void
    const blockedWrite = new Promise<void>((resolve) => {
      resolveWrite = resolve
    })
    const queue = new BoardPersistenceQueue(
      queueOptions(async () => blockedWrite),
    )
    queue.schedule('board_shutdown', Uint8Array.from([7, 8]))

    const unresolved = await queue.flushPending(25)

    assert.deepEqual(unresolved, ['board_shutdown'])
    assert.equal(queue.pendingCount, 1)
    assert.equal(queue.retainedBytes, 2)

    resolveWrite()
    await new Promise((resolve) => setTimeout(resolve, 0))
    assert.equal(queue.pendingCount, 0)
  })

  it('enforces the aggregate queue memory cap without replacing an existing snapshot', async () => {
    const queue = new BoardPersistenceQueue(
      queueOptions(async () => {}, { maxPendingBytes: 4 }),
    )
    queue.schedule('board_kept', Uint8Array.from([1, 2, 3]))

    assert.throws(
      () => queue.schedule('board_rejected', Uint8Array.from([4, 5])),
      PersistenceQueueCapacityError,
    )
    assert.deepEqual(queue.pendingBoardUuids, ['board_kept'])
    assert.equal(queue.retainedBytes, 3)
    assert.deepEqual(await queue.flushPending(100), [])
  })

  it('counts an in-flight snapshot as well as the latest snapshot against the memory cap', async () => {
    let started!: () => void
    let resolveWrite!: () => void
    const writeStarted = new Promise<void>((resolve) => {
      started = resolve
    })
    const blockedWrite = new Promise<void>((resolve) => {
      resolveWrite = resolve
    })
    const queue = new BoardPersistenceQueue(
      queueOptions(
        async () => {
          started()
          await blockedWrite
        },
        { maxPendingBytes: 4 },
      ),
    )
    queue.schedule('board_in_flight', Uint8Array.from([1, 2, 3]))
    const flushing = queue.flushPending(1000)
    await writeStarted

    assert.throws(
      () => queue.schedule('board_in_flight', Uint8Array.from([4, 5])),
      PersistenceQueueCapacityError,
    )
    assert.equal(queue.retainedBytes, 3)

    resolveWrite()
    assert.deepEqual(await flushing, [])
  })

  it('retains a write rejected after bounded retries and reports exhaustion', async () => {
    const exhausted: string[] = []
    const queue = new BoardPersistenceQueue(
      queueOptions(
        async () => {
          assertSuccessfulYdocWrite(new Response(null, { status: 500 }))
        },
        { onExhausted: (boardUuid) => exhausted.push(boardUuid) },
      ),
    )
    queue.schedule('board_failed', Uint8Array.from([9]))

    assert.deepEqual(await queue.flushPending(100), ['board_failed'])
    assert.deepEqual(exhausted, ['board_failed'])
    assert.equal(queue.retainedBytes, 1)
  })
})
