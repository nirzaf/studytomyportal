import { expect, test } from 'bun:test'
import { checkMathAnswer, mathChallenge } from '../components/Playground/mathPractice'

test('each visual has correctly scored and progressively harder challenges', () => {
  for (const [topic, first, second] of [
    ['fractions', 2, 8],
    ['perimeter', 18, 26],
    ['squares', 9, 25],
  ]) {
    expect(mathChallenge(topic, 0)).toMatchObject({ level: 1, answer: first })
    expect(mathChallenge(topic, 1)).toMatchObject({ level: 2, answer: second })
    expect(mathChallenge(topic, 3).level).toBe(3)
    expect(mathChallenge(topic, 0).hint).toBeTruthy()
  }
})

test('incorrect answers can be retried and malformed answers are rejected', () => {
  expect(checkMathAnswer(' 2 ', 2)).toBe('correct')
  expect(checkMathAnswer('1', 2)).toBe('retry')
  for (const input of ['', '2e0', '2.0', '2/1', '1234567']) {
    expect(checkMathAnswer(input, 2)).toBe('invalid')
  }
})

test('topic cards use exact words and page attribution from matching passages', async () => {
  const { buildTopicPractice, matchesTopicAnswer } = await import('../components/Playground/topicPractice')
  const excerpt = 'During photosynthesis, plants convert light energy into chemical energy.'
  const source = { title: 'Plant Science', page: 12, excerpt }
  const [card, duplicate] = buildTopicPractice('Photosynthesis', [source, source])
  expect(duplicate).toBeUndefined()
  expect(card.source).toEqual({ title: 'Plant Science', page: 12 })
  expect(card.before + card.answer + card.after).toBe(excerpt)
  expect(matchesTopicAnswer(' PHOTOSYNTHESIS ', card.answer)).toBe(true)
  expect(matchesTopicAnswer('respiration', card.answer)).toBe(false)
  expect(buildTopicPractice('respiration', [source])).toEqual([])
})
