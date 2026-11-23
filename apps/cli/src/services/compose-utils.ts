// The CLI-generated Compose files use two-space service indentation and
// four-space direct service properties. Fail closed on structures we cannot
// identify without a YAML parser rather than choosing another service's image.
type ComposeLine = { text: string; ending: string }

function splitComposeLines(compose: string): ComposeLine[] {
  const lines: ComposeLine[] = []
  const pattern = /([^\r\n]*)(\r\n|\n|\r|$)/g
  let match: RegExpExecArray | null
  while ((match = pattern.exec(compose)) !== null) {
    if (match[0] === '') break
    lines.push({ text: match[1], ending: match[2] })
  }
  return lines
}

function parseImageScalar(raw: string): string | null {
  const value = raw.trim()
  if (!value) return null

  const quote = value[0]
  if (quote === '"' || quote === "'") {
    if (value.length < 2 || value.at(-1) !== quote) return null
    const image = value.slice(1, -1)
    return image && !/\s/.test(image) ? image : null
  }

  const withoutComment = value.replace(/\s+#.*$/, '').trim()
  return withoutComment && !/[\s'"#]/.test(withoutComment) ? withoutComment : null
}

function locateServiceImage(compose: string, service: string): { lines: ComposeLine[]; imageLine: number; image: string } | null {
  const lines = splitComposeLines(compose)
  const serviceHeaders = lines.flatMap((line, index) => /^services:\s*(?:#.*)?$/.test(line.text) ? [index] : [])
  if (serviceHeaders.length !== 1) return null
  const header = serviceHeaders[0]

  let sectionEnd = lines.length
  for (let index = header + 1; index < lines.length; index += 1) {
    const text = lines[index].text
    if (text.trim() && !/^\s*#/.test(text) && !/^\s/.test(text)) {
      sectionEnd = index
      break
    }
  }

  const serviceHeadersFound: number[] = []
  for (let index = header + 1; index < sectionEnd; index += 1) {
    const match = lines[index].text.match(/^  ([A-Za-z0-9_.-]+):\s*(?:#.*)?$/)
    if (match?.[1] === service) serviceHeadersFound.push(index)
  }
  if (serviceHeadersFound.length !== 1) return null

  const serviceStart = serviceHeadersFound[0]
  let serviceEnd = sectionEnd
  for (let index = serviceStart + 1; index < sectionEnd; index += 1) {
    if (/^  [A-Za-z0-9_.-]+:\s*(?:#.*)?$/.test(lines[index].text)) {
      serviceEnd = index
      break
    }
  }

  const imageLines: { index: number; image: string }[] = []
  for (let index = serviceStart + 1; index < serviceEnd; index += 1) {
    const match = lines[index].text.match(/^    image:\s*(.*?)\s*(?:#.*)?$/)
    if (match) {
      const image = parseImageScalar(match[1])
      if (!image) return null
      imageLines.push({ index, image })
    }
  }
  if (imageLines.length !== 1) return null
  return { lines, imageLine: imageLines[0].index, image: imageLines[0].image }
}

/** Read only the direct image field for an exact Compose service. */
export function readComposeServiceImage(compose: string, service: string): string | null {
  if (!/^[A-Za-z0-9_.-]+$/.test(service)) return null
  return locateServiceImage(compose, service)?.image ?? null
}

const UPSTREAM_TAGGED_IMAGE_RE = /^ghcr\.io\/learnhouse\/app:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/

/** Replace the Community app image only when both current and target images are supported upstream tags. */
export function replaceComposeImageTag(compose: string, newImage: string): string {
  const located = locateServiceImage(compose, 'learnhouse-app')
  if (!located) {
    throw new Error('Cannot update the runtime image: the learnhouse-app service or its direct image is missing or ambiguous.')
  }
  if (!UPSTREAM_TAGGED_IMAGE_RE.test(located.image)) {
    throw new Error(`Cannot update the runtime image "${located.image}": the upstream tag-based updater does not support custom or digest-pinned images.`)
  }
  if (!UPSTREAM_TAGGED_IMAGE_RE.test(newImage)) {
    throw new Error(`Cannot update the runtime image to unsupported target "${newImage}".`)
  }

  const line = located.lines[located.imageLine]
  const match = line.text.match(/^(    image:\s*)(.*?)(\s+#.*)?$/)
  if (!match) {
    throw new Error('Cannot update the runtime image: the learnhouse-app image property is ambiguous.')
  }
  const oldValue = match[2].trim()
  const quote = oldValue[0] === '"' || oldValue[0] === "'" ? oldValue[0] : ''
  located.lines[located.imageLine] = {
    ...line,
    text: `${match[1]}${quote}${newImage}${quote}${match[3] ?? ''}`,
  }
  return located.lines.map((entry) => entry.text + entry.ending).join('')
}
