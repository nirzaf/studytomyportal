import { expect, test } from 'bun:test'
import sample from '../../api/src/tests/fixtures/learning_surface_v1.json'
import { getActivityLearningAvailability } from '../services/ai/ai'
import { isLearningSurface, readLearningSurface } from '../services/ai/learningSurface'

test('reads the API fixture and accepts the alternate visual catalogue entry', () => {
  expect(isLearningSurface(sample)).toBe(true)
  const alternate = structuredClone(sample)
  alternate.components[1] = {
    kind: 'number_line',
    id: 'visual',
    first_mark: 0,
    last_mark: 2,
    divisions_per_unit: 4,
  }
  expect(isLearningSurface(alternate)).toBe(true)
})

const expected = {
  schema_version: 1, request_id: sample.request_id, activity_uuid: sample.activity_id, mode: sample.mode,
}
const frame = (surface) => `data: ${JSON.stringify({ type: 'learning_surface', surface })}\n\n`
function streamOf(text, width = 1) {
  const bytes = new TextEncoder().encode(text)
  const body = new ReadableStream({
    start(controller) {
      for (let offset = 0; offset < bytes.length; offset += width) {
        controller.enqueue(bytes.subarray(offset, offset + width))
      }
      controller.close()
    },
  })
  return new Response(body, { headers: { 'content-type': 'text/event-stream; charset=utf-8' } })
}

test('validates fragmented UTF-8 and deduplicates a repeated delivery', async () => {
  const surface = structuredClone(sample)
  surface.components[0].text = 'Compare ½ with the other fraction.'
  const event = frame(surface)
  expect(await readLearningSurface(streamOf(event + event), expected)).toEqual(surface)
})

test('rejects wrong request, activity, revision and incomplete or contradictory frames', async () => {
  const wrongRequest = { ...expected, request_id: 'a82bf806-e58d-4399-b72e-1472ee48f248' }
  const wrongActivity = { ...expected, activity_uuid: 'activity_other' }
  const validEvent = frame(sample)
  const newerEvent = frame({ ...sample, revision: 2 })
  await expect(readLearningSurface(streamOf(validEvent, 35), wrongRequest)).rejects.toThrow('did not match')
  await expect(readLearningSurface(streamOf(validEvent, 35), wrongActivity)).rejects.toThrow('did not match')
  await expect(readLearningSurface(streamOf(newerEvent, 35), expected)).rejects.toThrow('did not match')
  await expect(readLearningSurface(streamOf(validEvent.slice(0, -1), 35), expected)).rejects.toThrow('interrupted')
  await expect(readLearningSurface(streamOf(validEvent + newerEvent, 35), expected)).rejects.toThrow('Invalid')
})

test('rejects unsupported versions, actions, markup, and hidden answer fields', () => {
  expect(isLearningSurface({ ...sample, schema_version: 2 })).toBe(false)
  expect(isLearningSurface({ ...sample, schema_version: true })).toBe(false)
  expect(isLearningSurface({ ...sample, schema_version: '1' })).toBe(false)
  expect(isLearningSurface({ ...sample, actions: ['grant_access'] })).toBe(false)
  expect(isLearningSurface({ ...sample, actions: ['next_question'] })).toBe(false)
  expect(isLearningSurface({ ...sample, mode: 'explore' })).toBe(false)
  const invalid = [
    [0, { kind: 'html' }],
    [0, { text: '<script>alert(1)</script>' }],
    [0, { text: 'x'.repeat(601) }],
    [1, { selected: 5 }],
    [1, { parts: 0 }],
    [2, { question: { ...sample.components[2].question, answer_key: '1/2' } }],
  ]
  for (const [index, changes] of invalid) {
    const value = structuredClone(sample)
    Object.assign(value.components[index], changes)
    expect(isLearningSurface(value)).toBe(false)
  }
})

test('validates per-mode availability and accepts the legacy response with modes disabled', async () => {
  const originalFetch = globalThis.fetch
  let body = { available: true, surface: null, allow_explore: true, allow_practice: false }
  globalThis.fetch = async () => new Response(JSON.stringify(body), { status: 200 })
  try {
    const exploreOnly = await getActivityLearningAvailability(sample.activity_id, 'test-token', new AbortController().signal)
    expect(exploreOnly.allow_explore).toBe(true)
    expect(exploreOnly.allow_practice).toBe(false)

    body = { available: true, surface: null, allow_explore: false, allow_practice: true }
    const practiceOnly = await getActivityLearningAvailability(sample.activity_id, 'test-token', new AbortController().signal)
    expect(practiceOnly.allow_explore).toBe(false)
    expect(practiceOnly.allow_practice).toBe(true)

    body = { available: false, surface: null, allow_explore: false, allow_practice: false }
    const disabled = await getActivityLearningAvailability(sample.activity_id, 'test-token', new AbortController().signal)
    expect(disabled.available).toBe(false)

    body = { available: true, surface: null }
    const legacy = await getActivityLearningAvailability(sample.activity_id, 'test-token', new AbortController().signal)
    expect(legacy.available).toBe(true)
    expect(legacy.allow_explore).toBe(false)
    expect(legacy.allow_practice).toBe(false)

    body = { available: true, surface: sample }
    const legacySurface = await getActivityLearningAvailability(sample.activity_id, 'test-token', new AbortController().signal)
    expect(legacySurface.surface).toEqual(sample)
    expect(legacySurface.allow_explore).toBe(false)
    expect(legacySurface.allow_practice).toBe(false)

    body = { available: true, surface: null, allow_explore: false, allow_practice: false }
    await expect(getActivityLearningAvailability(sample.activity_id, 'test-token', new AbortController().signal))
      .rejects.toThrow('Invalid lesson practice availability')

    body = {
      available: true, surface: sample, allow_explore: true, allow_practice: false,
    }
    await expect(getActivityLearningAvailability(sample.activity_id, 'test-token', new AbortController().signal))
      .rejects.toThrow('Invalid lesson practice availability')
  } finally {
    globalThis.fetch = originalFetch
  }
})
