import { test, expect } from '@playwright/test'
import { readFileSync } from 'node:fs'
import ts from 'typescript'

// No app/API assertions: this deliberately runs the production storage module
// against Chromium IndexedDB, with synthetic authenticated checkpoint inputs.
const origin = 'http://localhost:6879/'
const script = ts.transpileModule(readFileSync(new URL('../../../../web/lib/studytomyTopicDraft.ts', import.meta.url), 'utf-8'),
  { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const modulePath = '/topic-draft.js'
type DraftModule = typeof import('../../../../web/lib/studytomyTopicDraft')

test('draft storage survives closure and fails closed on change, expiry and logout', async ({ context, page }) => {
  await context.route(`${origin}**`, route => route.fulfill(route.request().url().endsWith(modulePath)
    ? { contentType: 'text/javascript', body: script }
    : { contentType: 'text/html', body: '<!doctype html><title>Synthetic draft storage</title>' }))
  const scope = 'synthetic-learner-a'
  const binding = 'synthetic-release-and-binding'
  await page.goto(origin)
  const old = await page.evaluate(async ({ scope, binding, modulePath }) => {
    const mod = await import(modulePath) as DraftModule
    const auth = await mod.beginTopicDraft()
    const ready = await mod.claimTopicDraft(auth, scope, binding, 1, true)
    if (!ready) throw new Error('No authorized draft lease')
    await mod.writeTopicDraft(ready.lease, '1/8', Date.now() + mod.TOPIC_DRAFT_TTL)
    return { auth, lease: ready.lease }
  }, { scope, binding, modulePath })
  await page.close()
  const reopened = await context.newPage()
  await reopened.goto(origin)
  const recover = (selectedScope: string, selection: string, revision: number) => reopened.evaluate(
    async ({ selectedScope, selection, revision, modulePath }) => {
      const mod = await import(modulePath) as DraftModule
      return (await mod.claimTopicDraft(await mod.beginTopicDraft(), selectedScope, selection, revision, true))?.answer
    }, { selectedScope, selection, revision, modulePath })
  expect(await recover(scope, binding, 1)).toBe('1/8')
  // Claim in a second tab at the *same* revision; an old lease cannot write over it.
  expect(await reopened.evaluate(async ({ old, modulePath }) => {
    const mod = await import(modulePath) as DraftModule
    return Boolean(await mod.writeTopicDraft(old.lease, '2/7', Date.now() + mod.TOPIC_DRAFT_TTL))
  }, { old, modulePath })).toBe(false)
  expect(await recover(scope, binding, 1)).toBe('1/8')
  expect(await reopened.evaluate(async ({ scope, binding, modulePath }) => {
    const mod = await import(modulePath) as DraftModule
    const entry = await mod.claimTopicDraft(await mod.beginTopicDraft(), scope, binding, 1, true)
    if (!entry) throw new Error('No active lease')
    await mod.writeTopicDraft(entry.lease, 'contains personal information', Date.now() + mod.TOPIC_DRAFT_TTL)
    return (await mod.claimTopicDraft(await mod.beginTopicDraft(), scope, binding, 1, true))?.answer
  }, { scope, binding, modulePath })).toBe('')
  expect(await recover('synthetic-learner-b', binding, 1)).toBe('')
  expect(await recover(scope, 'changed-release-binding', 1)).toBe('')
  expect(await recover(scope, binding, 2)).toBe('')
  const stale = await reopened.evaluate(async ({ old, scope, binding, modulePath }) => {
    const mod = await import(modulePath) as DraftModule
    const lateStore = await mod.writeTopicDraft(old.lease, 'stale', Date.now() + mod.TOPIC_DRAFT_TTL)
    const lateClaim = await mod.claimTopicDraft(old.auth, scope, binding, 1, true)
    return [Boolean(lateStore), Boolean(lateClaim)]
  }, { old, scope, binding, modulePath })
  expect(stale).toEqual([false, false])

  const writer = await context.newPage()
  await writer.goto(origin)
  const logoutChecks = await writer.evaluate(async ({ scope, binding, modulePath }) => {
    const mod = await import(modulePath) as DraftModule
    const auth = await mod.beginTopicDraft()
    const ready = await mod.claimTopicDraft(auth, scope, binding, 2, true)
    if (!ready) throw new Error('No recheck lease')
    // A fake past expiry does not require sleeping for the test.
    if (!await mod.writeTopicDraft(ready.lease, '3/7', Date.now() - 1)) throw new Error('Expiry write failed')
    const afterExpiry = await mod.claimTopicDraft(await mod.beginTopicDraft(), scope, binding, 2, true)
    if (!afterExpiry) throw new Error('No replacement lease')
    if (!await mod.writeTopicDraft(afterExpiry.lease, '2/5', Date.now() + mod.TOPIC_DRAFT_TTL)) {
      throw new Error('Sign-out setup write failed')
    }
    await mod.clearTopicDrafts()
    return { afterExpiry: afterExpiry.answer, lease: afterExpiry.lease }
  }, { scope, binding, modulePath })
  expect(logoutChecks.afterExpiry).toBe('')
  expect(await reopened.evaluate(async ({ lease, modulePath }) => {
    const mod = await import(modulePath) as DraftModule
    return Boolean(await mod.writeTopicDraft(lease, 'post-logout', Date.now() + mod.TOPIC_DRAFT_TTL))
  }, { lease: logoutChecks.lease, modulePath })).toBe(false)
  expect(await recover(scope, binding, 2)).toBe('')
})

test('missing IndexedDB yields no draft and does not throw', async ({ page }) => {
  await page.route(`${origin}**`, route => route.fulfill(route.request().url().endsWith(modulePath)
    ? { contentType: 'text/javascript', body: script }
    : { contentType: 'text/html', body: '<!doctype html>' }))
  await page.goto(origin)
  expect(await page.evaluate(async (path) => {
    Object.defineProperty(window, 'indexedDB', { get() { throw new DOMException('Storage blocked', 'SecurityError') } })
    const mod = await import(path) as DraftModule
    const auth = await mod.beginTopicDraft()
    await mod.clearTopicDrafts()
    return auth ?? null
  }, modulePath)).toBeNull()
})

test('stalled IndexedDB settles promptly and late open cannot save', async ({ page }) => {
  await page.route(`${origin}**`, route => route.fulfill(route.request().url().endsWith(modulePath)
    ? { contentType: 'text/javascript', body: script }
    : { contentType: 'text/html', body: '<!doctype html>' }))
  await page.goto(origin)
  const result = await page.evaluate(async (path) => {
    let closed = false
    const request: { result: { close: () => void }; onsuccess?: () => void } = {
      result: { close: () => { closed = true } },
    }
    Object.defineProperty(window, 'indexedDB', { value: { open: () => request } })
    const mod = await import(path) as DraftModule
    const started = performance.now()
    const auth = await mod.beginTopicDraft()
    request.onsuccess?.()
    await Promise.resolve()

    return { auth: auth ?? null, closed, bounded: performance.now() - started < 2_000 }
  }, modulePath)
  expect(result).toEqual({ auth: null, closed: true, bounded: true })
})
