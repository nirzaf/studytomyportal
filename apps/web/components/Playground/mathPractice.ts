export type MathTopic = 'fractions' | 'perimeter' | 'squares'

export function isMathTopic(value: string): value is MathTopic {
  return value === 'fractions' || value === 'perimeter' || value === 'squares'
}

export function mathChallenge(topic: MathTopic, completed: number) {
  const level = Math.min(completed + 1, 3)
  const variant = completed % 3
  if (topic === 'fractions') {
    const numerator = level
    const denominator = numerator + 2
    const multiplier = level + 1 + variant
    return {
      level,
      prompt: `${numerator}/${denominator} = ?/${denominator * multiplier}. What is the missing numerator?`,
      answer: numerator * multiplier,
      hint: `Multiply ${numerator} by the same number that turns ${denominator} into ${denominator * multiplier}.`,
    }
  }
  if (topic === 'perimeter') {
    const width = 4 + 2 * level + variant
    const height = 2 + level
    return {
      level,
      prompt: `A rectangle is ${width} cm wide and ${height} cm high. What is its perimeter in cm?`,
      answer: 2 * (width + height),
      hint: 'Add both widths and both heights. Perimeter measures the distance around the shape.',
    }
  }
  const term = 2 + level + variant
  return {
    level,
    prompt: `How many tiles are in square-number term ${term}?`,
    answer: term * term,
    hint: `Draw ${term} rows with ${term} tiles in each row.`,
  }
}

export function checkMathAnswer(input: string, answer: number) {
  const value = input.trim()
  if (!/^\d{1,6}$/.test(value)) return 'invalid'
  return Number(value) === answer ? 'correct' : 'retry'
}
