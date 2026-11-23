// Docs tabs as plain data (no React), shared by the navbar/sidebar and by
// the build scripts that generate llms.txt and the sitemap.
export const TAB_DATA = [
  { key: 'start', label: 'Get started', href: '/getting-started', folders: ['/getting-started', '/guides'] },
  { key: 'platform', label: 'Platform', href: '/platform', folders: ['/platform', '/enterprise'] },
  { key: 'self-hosting', label: 'Self-hosting', href: '/self-hosting', folders: ['/self-hosting', '/cli'] },
  { key: 'developers', label: 'Developers', href: '/developers', folders: ['/developers'] },
  { key: 'reference', label: 'API Reference', href: '/reference', folders: [] },
]
