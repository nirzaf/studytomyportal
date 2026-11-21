import type { BrowserContext } from '@playwright/test'

const LOOPBACK_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]'])

export async function blockNonLoopbackRequests(context: BrowserContext, blockedHosts: string[]) {
  await context.route('**/*', async route => {
    const host = new URL(route.request().url()).hostname
    if (LOOPBACK_HOSTS.has(host)) return route.continue()
    blockedHosts.push(host || 'non-http-url')
    return route.abort('blockedbyclient')
  })

  await context.routeWebSocket(() => true, webSocket => {
    const host = new URL(webSocket.url()).hostname
    if (LOOPBACK_HOSTS.has(host)) return webSocket.connectToServer()
    blockedHosts.push(host || 'non-websocket-url')
    webSocket.close()
  })
}
