import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createServer } from 'node:http'
import { chromium } from 'playwright'
import { blockNonLoopbackRequests } from './browser-egress-guard.js'

test('blocks and records non-loopback HTTP and WebSocket traffic in Chromium', async () => {
  const server = createServer((_request, response) => {
    response.writeHead(200, { 'content-type': 'text/html' })
    response.end(`<!doctype html><img src="http://outside.invalid/pixel.png"><script>
      window.externalSocket = new WebSocket('ws://outside.invalid/socket')
      window.externalSocket.onerror = () => {}
    </script>`)
  })
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  if (!address || typeof address === 'string') throw new Error('Expected an ephemeral loopback listener')

  const browser = await chromium.launch({
    headless: true,
    args: [
      '--disable-background-networking',
      '--host-resolver-rules=MAP * 0.0.0.0,EXCLUDE localhost,EXCLUDE 127.0.0.1,EXCLUDE [::1]',
    ],
  })
  try {
    const context = await browser.newContext({ serviceWorkers: 'block' })
    const blockedHosts: string[] = []
    await blockNonLoopbackRequests(context, blockedHosts)
    const page = await context.newPage()
    await page.goto(`http://127.0.0.1:${address.port}/`, { waitUntil: 'load' })

    const deadline = Date.now() + 5_000
    while (blockedHosts.length < 2 && Date.now() < deadline) await page.waitForTimeout(10)
    assert.deepEqual(blockedHosts.sort(), ['outside.invalid', 'outside.invalid'])
    await context.close()
  } finally {
    await browser.close()
    await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()))
  }
})
