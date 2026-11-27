export type Passage = { title: string; page: number; excerpt: string }

const queryWords = new Set(['about', 'and', 'are', 'can', 'could', 'does', 'explain', 'for', 'from', 'how', 'learn', 'meant', 'please', 'practice', 'practise', 'teach', 'the', 'what', 'when', 'where', 'which', 'why', 'with', 'would'])
const normalize = (value: string) => value.trim().normalize('NFC').toLowerCase()

export function buildTopicPractice(topic: string, sources: Passage[]) {
  const keywords = new Set((normalize(topic).match(/\p{L}{3,}/gu) ?? []).filter(word => !queryWords.has(word)))
  const seen = new Set<string>()
  return sources.flatMap(source => {
    const sentences = source.excerpt.trim().replace(/\s+/g, ' ').split(/(?<=[.!?])\s+/)
    return sentences.flatMap(sentence => {
      if (sentence.length < 30 || sentence.length > 320 || seen.has(sentence)) return []
      const words = [...sentence.matchAll(/\p{L}[\p{L}\p{M}]{2,}/gu)]
      const matches = words.filter(word => keywords.has(normalize(word[0]))).sort((a, b) => b[0].length - a[0].length)
      const answer = matches.find(word =>
        words.filter(other => normalize(other[0]) === normalize(word[0])).length === 1
      )
      if (!answer || answer.index === undefined) return []
      seen.add(sentence)
      return [{
        before: sentence.slice(0, answer.index),
        after: sentence.slice(answer.index + answer[0].length),
        answer: answer[0],
        sentence,
        source: { title: source.title, page: source.page },
      }]
    })
  }).slice(0, 5)
}

export function matchesTopicAnswer(response: string, answer: string) {
  return normalize(response) === normalize(answer)
}
