import { expect, mock, test } from 'bun:test'
import { NextRequest } from 'next/server'

const fetchInstanceMode = mock(async () => null)
mock.module('../lib/instanceMode', () => ({ fetchInstanceMode }))
const { default: proxy } = await import('../proxy')

for (const mode of ['oss', null]) {
  test(`edition routes return real 404s when mode resolves to ${mode}`, async () => {
    fetchInstanceMode.mockResolvedValue(mode)
    for (const path of [
      '/live',
      '/orgs/default/live/session/room',
      '/dash/live-sessions',
      '/messages',
      '/copilot',
      '/auth/sso/callback',
    ]) {
      const response = await proxy(new NextRequest(`http://localhost${path}`))
      expect(response.status).toBe(404)
      expect(response.headers.get('x-robots-tag')).toBe('noindex')
    }
  })
}

test('paid editions pass the route gate and continue to tenant routing', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = mock(async () => Response.json({
    mode: 'ee',
    tenancy: 'single',
    default_org_slug: 'default',
  }))
  try {
    for (const mode of ['ee', 'saas']) {
      fetchInstanceMode.mockResolvedValue(mode)
      const response = await proxy(new NextRequest('http://localhost/live'))
      expect(response.headers.get('x-middleware-rewrite')).toBe('http://localhost/orgs/default/live')
    }
  } finally {
    globalThis.fetch = originalFetch
  }
})
