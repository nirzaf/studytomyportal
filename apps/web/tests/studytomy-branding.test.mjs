import { expect, test } from 'bun:test'
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const webRootFromUrl = (url) => fileURLToPath(new URL('..', url))
const webRoot = webRootFromUrl(import.meta.url)
function sourceFiles(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name)
    if (entry.isDirectory()) {
      if (['.next', 'node_modules', 'public', 'tests'].includes(entry.name)) return []
      return sourceFiles(path)
    }
    return /\.(?:ts|tsx|js|jsx|css|scss)$/.test(entry.name) ? [path] : []
  })
}

test('legacy LearnHouse AI icon has no active frontend callers or public asset', () => {
  const references = sourceFiles(webRoot)
    .filter((path) => readFileSync(path, 'utf8').includes('lrnai_icon.png'))
  expect(references).toEqual([])

  for (const asset of ['lrnai_icon.png', 'lrn-text.svg', 'lrn.svg', 'lrn-dash.svg']) {
    expect(existsSync(join(webRoot, 'public', asset))).toBe(false)
  }
})

test('frontend test root resolves encoded spaces in file URLs', () => {
  const root = webRootFromUrl('file:///tmp/studytomy%20project/apps/web/tests/studytomy-branding.test.mjs')
  expect(root).toContain('studytomy project')
  expect(root.replaceAll('\\', '/').replace(/\/$/, '')).toMatch(/studytomy project\/apps\/web$/)
})

test('shared AI mark uses the existing Studytomy asset and descriptive defaults', () => {
  expect(existsSync(join(webRoot, 'public', 'studytomy-mini.png'))).toBe(true)
  expect(existsSync(join(webRoot, 'public', 'studytomy-logo.png'))).toBe(true)
  const mark = readFileSync(join(webRoot, 'components/Studytomy/StudytomyAiMark.tsx'), 'utf8')
  expect(mark).toContain('src="/studytomy-mini.png"')
  expect(mark).toContain('size = 20')
  expect(mark).toContain("alt={decorative ? '' : 'Studytomy AI assistant'}")
})

test('all active AI icon callers use the shared Studytomy mark', () => {
  const callers = [
    'Objects/Modals/Course/Create/CourseCreationTypeSelector.tsx',
    'Objects/Editor/Toolbar/ToolbarButtons.tsx',
    'Objects/Editor/Extensions/MagicBlocks/MagicBlockChat.tsx',
    'Objects/Modals/Course/Create/AICourse/AICourseChat.tsx',
    'Objects/Editor/Extensions/MagicBlocks/MagicBlockModal.tsx',
    'Objects/Modals/Course/Create/AICourse/AICoursePreview.tsx',
    'Objects/Modals/Course/Create/AICourse/AICourseCreationModal.tsx',
    'Objects/Editor/Extensions/MagicBlocks/MagicBlockComponent.tsx',
    'Objects/Editor/Extensions/SlashCommands/slashCommandsConfig.tsx',
  ]
  for (const caller of callers) {
    const source = readFileSync(join(webRoot, 'components', caller), 'utf8')
    expect(source).toContain('StudytomyAiMark')
    expect(source).not.toContain('lrnaiIcon')
  }
})
