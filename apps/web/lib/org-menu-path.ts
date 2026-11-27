type MenuDestination = { key: string; href: string; external: boolean }

// The org proxy may leave its internal prefix on the current path.
export function orgMenuPath(pathname: string) {
  return pathname.replace(/^\/orgs\/[^/]+/, '').replace(/\/+$/, '') || '/'
}

export function activeOrgMenuItem<T extends MenuDestination>(items: T[], pathname: string) {
  const path = orgMenuPath(pathname)
  let match: T | undefined
  let matchLength = 0
  for (const item of items) {
    if (item.external) continue
    const target = orgMenuPath(new URL(item.href, 'http://localhost').pathname)
    const inSection = target !== '/' && path.startsWith(target + '/')
    const inCatalog = (item.key === 'courses' && path.startsWith('/course/')) ||
      (item.key === 'paths' && path.startsWith('/path/'))
    if (path === target) return item
    if ((inSection || inCatalog) && target.length > matchLength) {
      match = item
      matchLength = target.length
    }
  }
  return match
}
