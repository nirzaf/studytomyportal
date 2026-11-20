/** The database image and persistent mount already selected by an install. */
export interface PostgresComposeSettings {
  image: string
  dataVolumePath: string
  /** Exact short-form service mount so bind paths, names, and options survive. */
  dataVolumeMount: string
  dataVolumeSource: string
  dataVolumeIsBind: boolean
  /** Original top-level named-volume declaration, including external/name settings. */
  dataVolumeDeclaration?: string
}

/**
 * Read the local `db` service settings emitted by our Compose templates.
 * Reconfiguration must keep the database image and the actual volume source,
 * target and options: PostgreSQL 18+ changed PGDATA and VOLUME, so substituting
 * a fresh-install template could otherwise start an empty cluster beside the
 * preserved data. Ambiguous mount syntax fails closed at the caller.
 */
export function readPostgresComposeSettings(compose: string): PostgresComposeSettings | null {
  const lines = compose.split(/\r?\n/)
  const start = lines.findIndex((line) => /^  db:\s*$/.test(line))
  if (start < 0) return null

  let end = lines.length
  for (let i = start + 1; i < lines.length; i += 1) {
    if (/^  [a-zA-Z0-9_-]+:\s*$/.test(lines[i]) || /^[a-zA-Z0-9_-]+:\s*$/.test(lines[i])) {
      end = i
      break
    }
  }

  const serviceLines = lines.slice(start + 1, end)
  const image = serviceLines.join('\n').match(/^    image:\s*(\S+)\s*$/m)?.[1]
  if (!image) return null

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
    const volumeSection = lines.findIndex((line) => /^volumes:\s*$/.test(line))
    const escapedSource = dataVolumeSource.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    const keyIndex = volumeSection < 0
      ? -1
      : lines.findIndex((line, index) => index > volumeSection && new RegExp(`^  ${escapedSource}:\\s*(?:#.*)?$`).test(line))
    if (keyIndex >= 0) {
      let declarationEnd = keyIndex + 1
      while (declarationEnd < lines.length && !/^  [A-Za-z0-9_.-]+:\s*(?:#.*)?$/.test(lines[declarationEnd]) && !/^[A-Za-z0-9_.-]+:\s*$/.test(lines[declarationEnd])) {
        declarationEnd += 1
      }
      dataVolumeDeclaration = lines.slice(keyIndex, declarationEnd).join('\n').trimEnd()
    } else {
      dataVolumeDeclaration = `  ${dataVolumeSource}:`
    }
  }

  if (dataVolumeOptions.some((option) => !/^[A-Za-z0-9_,=.-]+$/.test(option))) return null
  return {
    image,
    dataVolumePath,
    dataVolumeMount,
    dataVolumeSource,
    dataVolumeIsBind,
    dataVolumeDeclaration,
  }
}

