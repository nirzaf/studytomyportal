// @ts-nocheck -- bun test file (bun:test and .ts import paths are not part of the app tsconfig)
import { expect, test } from 'bun:test'
import {
  CourseDraftSync,
  isBlankDraft,
  type CourseDraft,
  type DraftApi,
  type DraftFields,
  type SaveResult,
} from '../lib/courses/courseDraftSync.ts'

const fields = (over: Partial<DraftFields> = {}): DraftFields => ({
  name: '',
  description: '',
  learnings: '',
  tags: '',
  public: true,
  ...over,
})

const draftOf = (revision: number, f: DraftFields, has_thumbnail = false): CourseDraft => ({
  draft_uuid: 'coursedraft_1',
  ...f,
  has_thumbnail,
  thumbnail_content_type: has_thumbnail ? 'image/png' : null,
  revision,
  created_at: '2026-11-21T00:00:00Z',
  updated_at: '2026-11-21T00:00:00Z',
})

/** In-memory server with the same compare-and-swap contract as the API. */
function fakeServer() {
  const server = {
    draft: null as CourseDraft | null,
    calls: [] as { kind: string; base: number | null }[],
    failNext: 0,
    gate: null as null | Promise<void>,
    api: {
      async saveFields(f: DraftFields, base: number | null): Promise<SaveResult> {
        server.calls.push({ kind: 'fields', base })
        if (server.gate) await server.gate
        if (server.failNext > 0) {
          server.failNext--
          return { ok: false, conflict: false, error: 'boom' }
        }
        if ((server.draft?.revision ?? null) !== (base || null)) {
          return { ok: false, conflict: true, current: server.draft }
        }
        server.draft = draftOf((server.draft?.revision ?? 0) + 1, f, server.draft?.has_thumbnail)
        return { ok: true, draft: server.draft }
      },
      async uploadThumbnail(_file: File, base: number | null): Promise<SaveResult> {
        server.calls.push({ kind: 'thumbnail', base })
        if (server.failNext > 0) {
          server.failNext--
          return { ok: false, conflict: false, error: 'upload failed' }
        }
        if ((server.draft?.revision ?? null) !== (base || null)) {
          return { ok: false, conflict: true, current: server.draft }
        }
        const f = server.draft ?? fields()
        server.draft = draftOf((server.draft?.revision ?? 0) + 1, f, true)
        return { ok: true, draft: server.draft }
      },
    } as DraftApi,
  }
  return server
}

const make = (server: ReturnType<typeof fakeServer>) =>
  new CourseDraftSync(server.api, () => {}, 5, 60_000)

test('a blank form never creates a draft, but an incomplete one does', async () => {
  const server = fakeServer()
  const sync = make(server)
  sync.schedule(fields())
  expect(await sync.flush()).toBe(true)
  expect(server.calls.length).toBe(0)

  sync.schedule(fields({ name: 'Momentum' })) // description is still empty
  expect(await sync.flush()).toBe(true)
  expect(server.draft?.name).toBe('Momentum')
  expect(sync.state.status).toBe('saved')
})

test('rapid edits coalesce and revisions advance one write at a time', async () => {
  const server = fakeServer()
  const sync = make(server)
  sync.schedule(fields({ name: 'M' }))
  sync.schedule(fields({ name: 'Mo' }))
  sync.schedule(fields({ name: 'Mom', public: false }))
  await sync.flush()
  expect(server.calls).toEqual([{ kind: 'fields', base: null }])
  expect(server.draft).toMatchObject({ name: 'Mom', public: false, revision: 1 })

  sync.schedule(fields({ name: 'Mom', public: false, tags: 'physics' }))
  await sync.flush()
  expect(server.calls[1]).toEqual({ kind: 'fields', base: 1 })
  expect(server.draft?.revision).toBe(2)
})

test('a failed save keeps the edits queued and a retry persists them', async () => {
  const server = fakeServer()
  const sync = make(server)
  server.failNext = 1
  sync.schedule(fields({ name: 'Keep me' }))
  expect(await sync.flush()).toBe(false)
  expect(sync.state).toMatchObject({ status: 'error', error: 'boom' })
  expect(sync.hasUnsaved()).toBe(true)
  expect(server.draft).toBeNull()

  expect(await sync.flush()).toBe(true)
  expect(server.draft?.name).toBe('Keep me')
  expect(sync.hasUnsaved()).toBe(false)
})

test('a stale tab stops on conflict instead of overwriting, then can reload or overwrite', async () => {
  const server = fakeServer()
  const tabA = make(server)
  const tabB = make(server)
  tabA.schedule(fields({ name: 'A1' }))
  await tabA.flush()
  tabB.hydrate(server.draft)

  tabA.schedule(fields({ name: 'A2' })) // tab A advances the draft to revision 2
  await tabA.flush()

  tabB.schedule(fields({ name: 'B-edit' })) // tab B still holds revision 1
  expect(await tabB.flush()).toBe(false)
  expect(tabB.state.status).toBe('conflict')
  expect(server.draft?.name).toBe('A2') // not overwritten
  const callsAfterConflict = server.calls.length
  tabB.schedule(fields({ name: 'B-edit-2' }))
  await tabB.flush()
  expect(server.calls.length).toBe(callsAfterConflict) // no more writes while in conflict

  const reloaded = tabB.resolveConflict('reload')
  expect(reloaded?.name).toBe('A2')
  expect(tabB.state.status).toBe('idle')
  expect(tabB.hasUnsaved()).toBe(false)

  // Overwrite path: re-base on the current revision, then the queued edit lands.
  tabA.schedule(fields({ name: 'A3' }))
  await tabA.flush()
  tabB.schedule(fields({ name: 'B-final' }))
  await tabB.flush()
  expect(tabB.state.status).toBe('conflict')
  tabB.resolveConflict('overwrite')
  expect(await tabB.flush()).toBe(true)
  expect(server.draft?.name).toBe('B-final')
})

test('pause waits for the in-flight write and blocks later ones until resume', async () => {
  const server = fakeServer()
  const sync = make(server)
  let release: () => void = () => {}
  server.gate = new Promise<void>((resolve) => (release = resolve))
  sync.schedule(fields({ name: 'first' }))
  await new Promise((r) => setTimeout(r, 15)) // debounce fires, write is in flight
  const paused = sync.pause()
  release()
  await paused
  expect(server.draft?.name).toBe('first')

  server.gate = null
  sync.schedule(fields({ name: 'second' }))
  await new Promise((r) => setTimeout(r, 15))
  expect(server.draft?.name).toBe('first') // paused: nothing written

  sync.resume()
  expect(await sync.flush()).toBe(true)
  expect(server.draft?.name).toBe('second')
})

test('thumbnail upload failures are reported and retried without losing the file', async () => {
  const server = fakeServer()
  const sync = make(server)
  sync.schedule(fields({ name: 'With image' }))
  await sync.flush()

  server.failNext = 1
  sync.setThumbnail(new File([new Uint8Array([1, 2, 3])], 'a.png', { type: 'image/png' }))
  expect(await sync.flush()).toBe(false)
  expect(sync.state).toMatchObject({ status: 'error', error: 'upload failed' })
  expect(server.draft?.has_thumbnail).toBe(false)

  expect(await sync.flush()).toBe(true)
  expect(server.draft?.has_thumbnail).toBe(true)
  expect(isBlankDraft(server.draft as CourseDraft)).toBe(false)
})
