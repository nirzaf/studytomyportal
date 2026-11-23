export const GET_STARTED_URL = '/getting-started/quickstart'

/** `placement` names the spot (navbar, toc_card, plan_callout…); `route` the docs page. */
export function ctaUrl(destination, { placement, route, term } = {}) {
  const relative = destination.startsWith('/')
  const url = new URL(destination, 'https://docs.invalid')
  url.searchParams.set('utm_source', 'docs')
  url.searchParams.set('utm_medium', 'referral')
  if (placement) url.searchParams.set('utm_campaign', placement)
  if (route) url.searchParams.set('utm_content', route === '/' ? 'home' : route.replace(/^\//, ''))
  if (term) url.searchParams.set('utm_term', term)
  return relative ? `${url.pathname}${url.search}${url.hash}` : url.toString()
}
