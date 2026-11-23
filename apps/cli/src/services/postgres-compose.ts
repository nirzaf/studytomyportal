/** Database identity already selected by a local Compose install. */
export interface PostgresComposeSettings {
  image: string
  dataVolumePath: string
  /** Exact short-form service mount so bind paths, names, and options survive. */
  dataVolumeMount: string
  dataVolumeSource: string
  dataVolumeIsBind: boolean
  /** Original top-level named-volume declaration, including external/name settings. */
  dataVolumeDeclaration?: string
  /** Explicit Compose project name; undefined means preserve Compose's directory/env default. */
  projectName?: string
  /** Literal PGDATA path, including a value loaded from the service's .env file. */
  pgData?: string
}

function parseProjectNameValue(raw: string): string | null {
  const match = raw.trim().match(/^(?:"([^"\r\n]*)"|'([^'\r\n]*)'|([^#\s]+))(?:\s+#.*)?$/)
  const value = match?.[1] ?? match?.[2] ?? match?.[3]
  return value && /^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(value) ? value : null
}

function parseSimpleProjectName(lines: string[]): { valid: boolean; value?: string } {
  const entries = lines.filter((line) => /^name\s*:/.test(line))
  if (entries.length === 0) return { valid: true }
  if (entries.length !== 1) return { valid: false }
  const match = entries[0].match(/^name:\s*(.*?)\s*$/)
  const value = match ? parseProjectNameValue(match[1]) : null
  return value ? { valid: true, value } : { valid: false }
}

function parseEnvProjectName(envFile: string): { valid: boolean; value?: string } {
  const entries = envFile.split(/\r?\n/)
    .filter((line) => !line.trimStart().startsWith('#') && /\bCOMPOSE_PROJECT_NAME\b/.test(line))
  if (entries.length === 0) return { valid: true }
  if (entries.length !== 1) return { valid: false }
  const match = entries[0].match(/^\s*COMPOSE_PROJECT_NAME\s*=\s*(.*?)\s*$/)
  const value = match ? parseProjectNameValue(match[1]) : null
  return value ? { valid: true, value } : { valid: false }
}

/** Only preserve literal absolute paths; interpolation and shell-like values fail closed. */
function parsePgDataValue(raw: string): string | null {
  const match = raw.trim().match(/^(?:"([^"\r\n]*)"|'([^'\r\n]*)'|([^#\s]+))(?:\s+#.*)?$/)
  const value = match?.[1] ?? match?.[2] ?? match?.[3]
  return value && /^\/[A-Za-z0-9._/-]+$/.test(value) ? value : null
}

function parseServicePgData(lines: string[]): { valid: boolean; found: boolean; value?: string } {
  const entries = lines.filter((line) => !line.trimStart().startsWith('#') && /\bPGDATA\b/.test(line))
  if (entries.length === 0) return { valid: true, found: false }
  if (entries.length !== 1) return { valid: false, found: true }

  const match = entries[0].match(/^\s*-\s+PGDATA\s*=\s*(.*?)\s*$/)
    ?? entries[0].match(/^\s+PGDATA\s*:\s*(.*?)\s*$/)
  if (!match) return { valid: false, found: true }
  const value = parsePgDataValue(match[1])
  return value ? { valid: true, found: true, value } : { valid: false, found: true }
}

function parseEnvFileSetting(lines: string[]): boolean | null {
  const entries = lines
    .map((line, index) => ({ line, index }))
    .filter(({ line }) => /^    env_file\s*:/.test(line))
  if (entries.length === 0) return false
  if (entries.length !== 1) return null

  const { line, index } = entries[0]
  const inlineValue = line.match(/^    env_file\s*:\s*(.*?)\s*$/)?.[1]
  const normalizedInline = inlineValue?.replace(/^(?:"([^"]+)"|'([^']+)')$/, (_all, double, single) => double ?? single)
  if (inlineValue) return normalizedInline === '.env'

  const paths: string[] = []
  for (let i = index + 1; i < lines.length; i += 1) {
    const current = lines[i]
    if (!current.trim() || current.trimStart().startsWith('#')) continue
    if (/^    \S/.test(current)) break
    const pathMatch = current.match(/^      -\s+(.+?)\s*$/)
    if (!pathMatch) return null
    const pathValue = pathMatch[1].replace(/^(?:"([^"]+)"|'([^']+)')$/, (_all, double, single) => double ?? single)
    paths.push(pathValue)
  }
  return paths.length === 1 && paths[0] === '.env' ? true : null
}

function parseEnvPgData(envFile: string): { valid: boolean; found: boolean; value?: string } {
  const entries = envFile.split(/\r?\n/)
    .filter((line) => !line.trimStart().startsWith('#') && /\bPGDATA\b/.test(line))
  if (entries.length === 0) return { valid: true, found: false }
  if (entries.length !== 1) return { valid: false, found: true }

  const match = entries[0].match(/^\s*PGDATA\s*=\s*(.*?)\s*$/)
  if (!match) return { valid: false, found: true }
  const value = parsePgDataValue(match[1])
  return value ? { valid: true, found: true, value } : { valid: false, found: true }
}

function parseVolumeDeclaration(lines: string[], source: string): { valid: boolean; declaration?: string } {
  const headers = lines.map((line, index) => ({ line, index })).filter(({ line }) => /^volumes\s*:/.test(line))
  if (headers.length > 1) return { valid: false }
  if (headers.length === 0) return { valid: true, declaration: `  ${source}:` }

  const { line: header, index: start } = headers[0]
  if (!/^volumes\s*:\s*(?:#.*)?$/.test(header)) return { valid: false }

  let end = lines.length
  for (let i = start + 1; i < lines.length; i += 1) {
    if (/^[A-Za-z0-9_-]+\s*:/.test(lines[i])) {
      end = i
      break
    }
  }

  const escapedSource = source.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const candidates = lines
    .map((value, index) => ({ value, index }))
    .filter(({ value, index }) => index > start && index < end
      && new RegExp(`^\\s+(?:${escapedSource}|"${escapedSource}"|'${escapedSource}'):`).test(value))
  if (candidates.length > 1) return { valid: false }
  if (candidates.length === 0) return { valid: true, declaration: `  ${source}:` }

  const { value, index } = candidates[0]
  if (!new RegExp(`^  ${escapedSource}:\\s*(?:#.*)?$`).test(value)) return { valid: false }
  let declarationEnd = index + 1
  while (declarationEnd < end && !/^  [A-Za-z0-9_.-]+:\s*(?:#.*)?$/.test(lines[declarationEnd])) {
    declarationEnd += 1
  }
  return {
    valid: true,
    declaration: lines.slice(index, declarationEnd).join('\n').trimEnd(),
  }
}

/** Detect a db service in the CLI's indentation-based Compose service mapping. */
export function hasPostgresServiceDeclaration(compose: string): boolean {
  const lines = compose.split(/\r?\n/)
  const anyServicesHeader = lines.findIndex((line) => /^(?:services|"services"|'services')\s*:/.test(line))
  if (anyServicesHeader < 0) {
    return lines.some((line) => /^  (?:db|"db"|'db')\s*:/.test(line))
  }
  const servicesHeader = lines.findIndex((line) => /^services\s*:\s*(?:#.*)?$/.test(line))
  if (servicesHeader < 0) {
    return /(?:^|[,\s{])(?:"db"|'db'|db)\s*:/m.test(lines.slice(anyServicesHeader).join('\n'))
  }

  let end = lines.length
  for (let i = servicesHeader + 1; i < lines.length; i += 1) {
    if (/^[A-Za-z0-9_-]+\s*:/.test(lines[i])) {
      end = i
      break
    }
  }

  const entries = lines.slice(servicesHeader + 1, end).flatMap((line) => {
    const match = line.match(/^(\s*)(?:"([^"]+)"|'([^']+)'|([A-Za-z0-9_.-]+))\s*:/)
    if (!match) return []
    return [{ indent: match[1].length, name: match[2] ?? match[3] ?? match[4] }]
  })
  if (entries.length === 0) return false
  const serviceIndent = Math.min(...entries.map((entry) => entry.indent))
  return entries.some((entry) => entry.indent === serviceIndent && entry.name === 'db')
}

/**
 * Read the CLI's supported local `db` service shape. Reconfiguration preserves
 * the database image, mount, explicit project identity, PGDATA, and named
 * volume metadata. Unsupported YAML constructs fail closed at the caller.
 */
export function readPostgresComposeSettings(compose: string, envFile = '', processProjectName?: string): PostgresComposeSettings | null {
  const lines = compose.split(/\r?\n/)
  const serviceHeaders = lines.filter((line) => /^services\s*:/.test(line))
  if (serviceHeaders.length !== 1 || !/^services:\s*(?:#.*)?$/.test(serviceHeaders[0])) return null
  const serviceStarts = lines
    .map((line, index) => ({ line, index }))
    .filter(({ line }) => /^  db:\s*$/.test(line))
  if (serviceStarts.length !== 1) return null
  const start = serviceStarts[0].index

  let end = lines.length
  for (let i = start + 1; i < lines.length; i += 1) {
    if (/^  [a-zA-Z0-9_-]+:\s*$/.test(lines[i]) || /^[a-zA-Z0-9_-]+:\s*$/.test(lines[i])) {
      end = i
      break
    }
  }

  const serviceLines = lines.slice(start + 1, end)
  const image = serviceLines.join('\n').match(/^    image:\s*(\S+)\s*$/m)?.[1]
  // The setup generator rewrites .env. Keep literal images only so an image
  // reference interpolated from a custom variable cannot silently change.
  if (!image || /[$`]/.test(image)) return null

  let dataVolumeMount: string | undefined
  let dataVolumePath: string | undefined
  let dataVolumeSource: string | undefined
  let dataVolumeOptions: string[] = []
  for (const line of serviceLines) {
    const mount = line.match(/^      -\s+(.+?)\s*$/)?.[1]
    if (!mount) continue
    const parts = mount.split(':')
    const targetIndex = parts.findIndex((part, index) => index > 0 && /^\/var\/lib\/postgresql(?:\/[^:\s]+)?$/.test(part))
    if (targetIndex < 1) continue
    dataVolumeMount = mount
    dataVolumeSource = parts.slice(0, targetIndex).join(':')
    dataVolumePath = parts[targetIndex]
    dataVolumeOptions = parts.slice(targetIndex + 1)
    break
  }
  if (!dataVolumeMount || !dataVolumePath || !dataVolumeSource || /[\s"'`$]/.test(dataVolumeSource)) return null

  const dataVolumeIsBind = /^(?:\.{1,2}[\\/]|\/|[A-Za-z]:[\\/])/.test(dataVolumeSource)
  let dataVolumeDeclaration: string | undefined
  if (!dataVolumeIsBind) {
    const declaration = parseVolumeDeclaration(lines, dataVolumeSource)
    if (!declaration.valid) return null
    dataVolumeDeclaration = declaration.declaration
  }

  const projectName = parseSimpleProjectName(lines)
  if (!projectName.valid) return null
  const envProjectName = parseEnvProjectName(envFile)
  if (!envProjectName.valid) return null
  const processName = processProjectName === undefined ? undefined : parseProjectNameValue(processProjectName)
  if (processProjectName !== undefined && !processName) return null
  if (!processName && projectName.value && envProjectName.value && projectName.value !== envProjectName.value) return null
  const effectiveProjectName = processName ?? envProjectName.value ?? projectName.value

  const servicePgData = parseServicePgData(serviceLines)
  if (!servicePgData.valid) return null
  const envFileSetting = parseEnvFileSetting(serviceLines)
  if (envFileSetting === null) return null
  const envPgData = envFileSetting ? parseEnvPgData(envFile) : { valid: true, found: false }
  if (!envPgData.valid) return null
  const pgData = servicePgData.found ? servicePgData.value : envPgData.value

  if (dataVolumeOptions.some((option) => !/^[A-Za-z0-9_,=.-]+$/.test(option))) return null
  return {
    image,
    dataVolumePath,
    dataVolumeMount,
    dataVolumeSource,
    dataVolumeIsBind,
    dataVolumeDeclaration,
    ...(effectiveProjectName !== undefined ? { projectName: effectiveProjectName } : {}),
    ...(pgData !== undefined ? { pgData } : {}),
  }
}
