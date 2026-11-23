import { getAPIUrl } from '@services/config/config'

export const DEFAULT_LEARNER_LEVELS = ['Foundation', 'Core', 'Extension'] as const
export const MAX_LEARNER_LEVELS = 5
export const MAX_TARGET_SKILLS = 8
export const MAX_ADAPTATION_LABEL_LENGTH = 80

export interface PlaygroundLearnerAdaptation {
  enabled: boolean
  levels: string[]
  target_skills: string[]
}

export function learnerAdaptationForPlayground(playground: {
  html_content?: string | null
  learner_adaptation?: PlaygroundLearnerAdaptation | null
}): PlaygroundLearnerAdaptation {
  const saved = playground.learner_adaptation
  return {
    // New/empty Playgrounds opt in. Existing HTML without saved settings keeps
    // its legacy non-adaptive iteration behavior.
    enabled: saved?.enabled ?? !playground.html_content?.trim(),
    levels: saved?.levels?.length ? [...saved.levels] : [...DEFAULT_LEARNER_LEVELS],
    target_skills: saved?.target_skills ? [...saved.target_skills] : [],
  }
}

interface PlaygroundContext {
  playground_name: string
  playground_description: string
  course_uuid?: string
  course_name?: string
  learner_adaptation: PlaygroundLearnerAdaptation
}

interface StreamChunk {
  type: 'chunk' | 'done' | 'error'
  content?: string
  session_uuid?: string
  message?: string
}

export async function startPlaygroundSession(
  playgroundUuid: string,
  prompt: string,
  context: PlaygroundContext,
  accessToken: string,
  onChunk: (chunk: string) => void,
  onComplete: (sessionUuid: string) => void,
  onError: (error: string) => void
): Promise<void> {
  try {
    const response = await fetch(`${getAPIUrl()}playgrounds/generate/start`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${accessToken}`,
      },
      body: JSON.stringify({
        playground_uuid: playgroundUuid,
        prompt,
        context,
      }),
    })

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}))
      throw new Error(errorData.detail || `HTTP error ${response.status}`)
    }

    await processStream(response, onChunk, onComplete, onError)
  } catch (error) {
    onError(error instanceof Error ? error.message : 'Unknown error occurred')
  }
}

export async function iteratePlayground(
  sessionUuid: string,
  playgroundUuid: string,
  message: string,
  accessToken: string,
  onChunk: (chunk: string) => void,
  onComplete: (sessionUuid: string) => void,
  onError: (error: string) => void,
  currentHtml?: string | null,
  learnerAdaptation?: PlaygroundLearnerAdaptation
): Promise<void> {
  try {
    const response = await fetch(`${getAPIUrl()}playgrounds/generate/iterate`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${accessToken}`,
      },
      body: JSON.stringify({
        session_uuid: sessionUuid,
        playground_uuid: playgroundUuid,
        message,
        current_html: currentHtml || undefined,
        learner_adaptation: learnerAdaptation,
      }),
    })

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}))
      throw new Error(errorData.detail || `HTTP error ${response.status}`)
    }

    await processStream(response, onChunk, onComplete, onError)
  } catch (error) {
    onError(error instanceof Error ? error.message : 'Unknown error occurred')
  }
}

async function processStream(
  response: Response,
  onChunk: (chunk: string) => void,
  onComplete: (sessionUuid: string) => void,
  onError: (error: string) => void
): Promise<void> {
  const reader = response.body?.getReader()
  if (!reader) {
    onError('No response body')
    return
  }

  const decoder = new TextDecoder()
  let buffer = ''
  let terminated = false

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      const lines = buffer.split('\n')
      buffer = ''

      for (let i = 0; i < lines.length; i++) {
        const line = lines[i]

        if (line.startsWith('data: ')) {
          const jsonStr = line.slice(6)
          try {
            const event: StreamChunk = JSON.parse(jsonStr)

            if (event.type === 'chunk' && event.content) {
              if (!terminated) {
                onChunk(event.content)
              }
            } else if (event.type === 'done' && event.session_uuid) {
              if (!terminated) {
                terminated = true
                onComplete(event.session_uuid)
              }
            } else if (event.type === 'error') {
              terminated = true
              await reader.cancel().catch(() => undefined)
              onError(event.message || 'An internal error occurred.')
              return
            }
          } catch {
            if (i === lines.length - 1) {
              buffer = line
            }
          }
        } else if (line.trim() !== '') {
          if (i === lines.length - 1) {
            buffer = line
          }
        }
      }
    }

    if (!terminated) {
      terminated = true
      onError('Generation stream ended before completion.')
    }
  } finally {
    reader.releaseLock()
  }
}
