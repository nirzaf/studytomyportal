export type LearningAction = 'submit_answer' | 'request_hint' | 'retry_practice' | 'next_question'

export type LearningOperation =
  | { action: 'submit_answer'; answer: string }
  | { action: 'request_hint' | 'retry_practice' | 'next_question' }

export type LearningActionRequest = LearningOperation & {
  surface_id: string
  request_id: string
  expected_revision: number
}

type Choice = { id: string; label: string }
export type LearningQuestion =
  | { kind: 'rational'; id: string; prompt: string }
  | { kind: 'single_choice'; id: string; prompt: string; choices: Choice[] }

export type LearningComponent =
  | { kind: 'explanation' | 'hint'; id: string; text: string }
  | { kind: 'fraction_explorer'; id: string; parts: number; selected: number }
  | { kind: 'number_line'; id: string; first_mark: number; last_mark: number; divisions_per_unit: number }
  | { kind: 'question'; id: string; question: LearningQuestion }
  | { kind: 'feedback'; id: string; outcome: 'correct' | 'incorrect'; attempt_id: string; text: string }

export type LearningSurface = {
  schema_version: 1
  surface_id: string
  turn_id: string
  request_id: string
  revision: number
  activity_id: string
  source_identity: string
  mode: 'explore' | 'practice'
  components: LearningComponent[]
  actions: LearningAction[]
}

export type LearningSurfaceRequest = {
  schema_version: 1
  request_id: string
  activity_uuid: string
  mode: LearningSurface['mode']
}

type JsonObject = Record<string, unknown>

const identifier = /^[a-z][a-z0-9_-]{0,47}$/
const uuid = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/
const activityId = /^activity_[a-zA-Z0-9_-]{1,80}$/
const contentIdentity = /^[a-f0-9]{64}$/

function object(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function exactFields(value: JsonObject, fields: readonly string[]): boolean {
  const keys = Object.keys(value)
  return keys.length === fields.length && keys.every((key) => fields.includes(key))
}

function matches(value: unknown, pattern: RegExp): value is string {
  return typeof value === 'string' && pattern.test(value)
}

function plainText(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= 1200 &&
    Array.from(value).length <= 600 && !/[<>]/.test(value)
}

function integer(value: unknown, min: number, max: number): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= min && value <= max
}

function choice(value: unknown): value is Choice {
  return object(value) && exactFields(value, ['id', 'label']) &&
    matches(value.id, identifier) && plainText(value.label)
}

function question(value: unknown): value is LearningQuestion {
  if (!object(value) || !matches(value.id, uuid) || !plainText(value.prompt)) return false
  if (value.kind === 'rational') return exactFields(value, ['kind', 'id', 'prompt'])
  return value.kind === 'single_choice' &&
    exactFields(value, ['kind', 'id', 'prompt', 'choices']) &&
    Array.isArray(value.choices) && value.choices.length >= 2 && value.choices.length <= 4 &&
    value.choices.every(choice) && new Set(value.choices.map(({ id }) => id)).size === value.choices.length
}

function component(value: unknown): value is LearningComponent {
  if (!object(value) || !matches(value.id, identifier)) return false
  switch (value.kind) {
    case 'explanation':
    case 'hint':
      return exactFields(value, ['kind', 'id', 'text']) && plainText(value.text)
    case 'fraction_explorer':
      return exactFields(value, ['kind', 'id', 'parts', 'selected']) &&
        integer(value.parts, 1, 12) && integer(value.selected, 0, value.parts)
    case 'number_line':
      return exactFields(value, ['kind', 'id', 'first_mark', 'last_mark', 'divisions_per_unit']) &&
        integer(value.first_mark, -12, 12) && integer(value.last_mark, value.first_mark + 1, 12) &&
        integer(value.divisions_per_unit, 1, 12) &&
        (value.last_mark - value.first_mark) * value.divisions_per_unit <= 48
    case 'question':
      return exactFields(value, ['kind', 'id', 'question']) && question(value.question)
    case 'feedback':
      return exactFields(value, ['kind', 'id', 'outcome', 'attempt_id', 'text']) &&
        (value.outcome === 'correct' || value.outcome === 'incorrect') &&
        matches(value.attempt_id, uuid) && plainText(value.text)
    default:
      return false
  }
}

function action(value: unknown): value is LearningAction {
  return value === 'submit_answer' || value === 'request_hint' ||
    value === 'retry_practice' || value === 'next_question'
}

export function isLearningSurface(value: unknown): value is LearningSurface {
  if (!object(value) ||
    !exactFields(value, [
      'schema_version', 'surface_id', 'turn_id', 'request_id', 'revision', 'activity_id',
      'source_identity', 'mode', 'components', 'actions',
    ]) ||
    value.schema_version !== 1 || !matches(value.surface_id, uuid) || !matches(value.turn_id, uuid) ||
    !matches(value.request_id, uuid) ||
    !integer(value.revision, 1, 2_147_483_647) ||
    !matches(value.activity_id, activityId) || !matches(value.source_identity, contentIdentity) ||
    !Array.isArray(value.components) || value.components.length > 6 || !value.components.every(component) ||
    !Array.isArray(value.actions) || value.actions.length > 4 || !value.actions.every(action)) return false

  const kinds = new Set(value.components.map(({ kind }) => kind))
  if (kinds.size !== value.components.length ||
    new Set(value.components.map(({ id }) => id)).size !== value.components.length ||
    new Set(value.actions).size !== value.actions.length ||
    !kinds.has('explanation') || kinds.has('fraction_explorer') === kinds.has('number_line')) return false

  if (value.mode === 'explore') {
    return value.actions.length === 0 && !kinds.has('question') && !kinds.has('hint') && !kinds.has('feedback')
  }
  if (value.mode !== 'practice' || !kinds.has('question')) return false
  if (kinds.has('feedback')) {
    return !value.actions.includes('submit_answer') && !value.actions.includes('request_hint')
  }
  return !value.actions.includes('retry_practice') && !value.actions.includes('next_question')
}

export async function readLearningSurface(
  response: Response, expected: LearningSurfaceRequest,
): Promise<LearningSurface> {
  const contentType = response.headers.get('content-type')?.split(';', 1)[0].trim().toLowerCase()
  const reader = response.body?.getReader()
  if (!reader) throw new Error('Lesson visual response was empty')

  try {
    if (contentType !== 'text/event-stream') throw new Error('Invalid lesson visual response')
    const decoder = new TextDecoder('utf-8', { fatal: true })
    let bytes = 0
    let data = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      bytes += value.byteLength
      if (bytes > 32 * 1024) throw new Error('Lesson visual response was too large')
      data += decoder.decode(value, { stream: true })
    }
    data = (data + decoder.decode()).replace(/\r\n/g, '\n')
    if (!data.endsWith('\n\n')) throw new Error('Lesson visual response was interrupted')
    const frames = new Set(data.split('\n\n').filter(Boolean))
    const frame = frames.values().next().value
    if (frames.size !== 1 || !frame?.startsWith('data: ')) throw new Error('Invalid lesson visual event')

    const event: unknown = JSON.parse(frame.slice(6))
    if (!object(event) || !exactFields(event, ['type', 'surface']) || event.type !== 'learning_surface' ||
      !isLearningSurface(event.surface) || event.surface.schema_version !== expected.schema_version ||
      event.surface.activity_id !== expected.activity_uuid || event.surface.request_id !== expected.request_id ||
      event.surface.mode !== expected.mode || event.surface.revision !== 1) {
      throw new Error('Lesson visual did not match this request')
    }
    return event.surface
  } catch (error) {
    await reader.cancel().catch(() => {})
    throw error
  } finally {
    reader.releaseLock()
  }
}
