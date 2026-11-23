import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import path, { dirname, join, resolve, win32, posix } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const docsRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const tutorialPath = join(docsRoot, 'content/guides/build-learning-platform/do-it-yourself.mdx')
const tutorial = readFileSync(tutorialPath, 'utf8')

function snippetsWithFilename(source, filename) {
  return [...source.matchAll(/```[^\n]*\n([\s\S]*?)```/g)]
    .filter((match) => match[0].split('\n', 1)[0].includes(`filename="${filename}`))
    .map((match) => match[1])
}

function isWithinRoot(root, candidate, pathApi = path) {
  const relative = pathApi.relative(root, candidate)
  return relative !== '' && !pathApi.isAbsolute(relative) && relative !== '..' && !relative.startsWith(`..${pathApi.sep}`)
}

test('documentation route containment is portable and rejects traversal', () => {
  const posixRoot = '/repo/docs/content'
  assert.equal(isWithinRoot(posixRoot, posix.resolve(posixRoot, 'guides/start'), posix), true)
  assert.equal(isWithinRoot(posixRoot, posix.resolve(posixRoot, '../app/page'), posix), false)
  const windowsRoot = 'C:\\repo\\docs\\content'
  assert.equal(isWithinRoot(windowsRoot, win32.resolve(windowsRoot, 'guides\\start'), win32), true)
  assert.equal(isWithinRoot(windowsRoot, win32.resolve(windowsRoot, '..\\app\\page'), win32), false)
})

test('tutorial imports the helper file that it asks readers to create', () => {
  assert.match(tutorial, /Create `src\/lib\/studytomy\.ts`/)
  assert.doesNotMatch(tutorial, /from\s+['"]@\/lib\/learnhouse['"]|from\s+['"]\.\/?(?:\.\.\/)*lib\/learnhouse['"]|from\s+['"]@\/lib\/studytomy['"][^\n]*learnhouse/)

  const helper = [
    ...snippetsWithFilename(tutorial, 'src/lib/studytomy.ts'),
    ...snippetsWithFilename(tutorial, 'src/lib/studytomy.ts (continued)'),
  ].join('\n')
  assert.ok(helper.length > 0, 'the helper implementation snippets must be present')

  const exports = new Set([...helper.matchAll(/export\s+(?:async\s+)?(?:const|function|type|interface)\s+(\w+)/g)].map((match) => match[1]))
  const fencedCode = [...tutorial.matchAll(/```[^\n]*\n([\s\S]*?)```/g)].map((match) => match[1]).join('\n')
  const imports = [...fencedCode.matchAll(/import\s*\{([^}]+)\}\s*from\s*['"]@\/lib\/studytomy['"]/g)]
  assert.ok(imports.length > 0, 'later examples must import the helper')
  for (const [, bindings] of imports) {
    for (const binding of bindings.split(',')) {
      const name = binding.trim().replace(/^type\s+/, '').split(/\s+as\s+/)[0].trim()
      if (name) assert.ok(exports.has(name), `helper must export imported name ${name}`)
    }
  }
})

test('tutorial internal links resolve to a documentation route and do not advertise removed demo/release pages', () => {
  const prose = tutorial.replace(/```[\s\S]*?```/g, '')
  const links = [...prose.matchAll(/\[[^\]]+\]\((\/[^)]+)\)/g)].map((match) => match[1])
  assert.ok(links.length > 0)
  const contentRoot = join(docsRoot, 'content')

  for (const href of links) {
    const pathname = decodeURIComponent(href.split(/[?#]/, 1)[0]).replace(/^\//, '')
    assert.ok(!/^(?:demos?|releases?)(?:\/|$)/i.test(pathname), `removed local route must not be linked: ${href}`)
    if (pathname === 'reference' || pathname.startsWith('reference/')) {
      assert.ok(existsSync(join(docsRoot, 'app/reference/page.jsx')) || existsSync(join(docsRoot, 'app/reference/[group]/page.jsx')), `reference route must exist: ${href}`)
      continue
    }
    const route = resolve(contentRoot, pathname)
    assert.ok(isWithinRoot(contentRoot, route), `link must remain within docs content: ${href}`)
    assert.ok(existsSync(`${route}.mdx`) || existsSync(join(route, 'index.mdx')), `documentation route must exist: ${href}`)
  }
})
