import { expect, test } from 'bun:test'
import { activeOrgMenuItem, ORG_HELP_ROUTES } from '../lib/org-menu-path'

const internal = (key, href) => ({ key, href, external: false })

test('help and safety reporting use distinct organization routes', () => {
  expect(activeOrgMenuItem(ORG_HELP_ROUTES, '/orgs/synthetic/help')?.key).toBe('support')
  expect(activeOrgMenuItem(ORG_HELP_ROUTES, '/orgs/synthetic/help/report')?.key).toBe('safety')
  expect(activeOrgMenuItem(ORG_HELP_ROUTES, '/helper')).toBeUndefined()
})

test('organization navigation marks the current catalog on direct and nested routes', () => {
  const courses = internal('courses', '/courses')
  const paths = internal('paths', '/paths')
  const items = [courses, paths]

  expect(activeOrgMenuItem(items, '/orgs/synthetic/courses/')).toBe(courses)
  expect(activeOrgMenuItem(items, '/course/example/activity/lesson')).toBe(courses)
  expect(activeOrgMenuItem(items, '/path/example')).toBe(paths)
  expect(activeOrgMenuItem(items, '/coursework')).toBeUndefined()
})

test('organization navigation prefers the most specific internal section and ignores external links', () => {
  const library = internal('library', '/library')
  const guides = internal('guides', '/library/guides')
  const external = { key: 'external', href: 'https://example.com/library/guides/topic', external: true }

  expect(activeOrgMenuItem([library, external, guides], '/library/guides/topic')).toBe(guides)
  expect(activeOrgMenuItem([external], '/library/guides/topic')).toBeUndefined()
})
