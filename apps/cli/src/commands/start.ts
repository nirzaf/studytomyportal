import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import * as p from '@clack/prompts'
import pc from 'picocolors'
import { findInstallDir, readConfig } from '../services/config-store.js'
import { dockerComposeUp } from '../services/docker.js'
import { migrateContentVolume } from '../services/content-volume-migration.js'
import { describeRuntimeImage } from '../services/version-check.js'

function configuredRuntimeImage(dir: string, service: string): string {
  try {
    const lines = readFileSync(join(dir, 'docker-compose.yml'), 'utf8').split(/\r?\n/)
    let inService = false
    for (const line of lines) {
      if (/^  [^ ]/.test(line)) {
        if (inService) break
        inService = line.trim() === `${service}:`
      } else if (inService) {
        const image = line.match(/^    image:\s*(\S+)/)?.[1]
        if (image) return image
      }
    }
  } catch {
    // Report unknown provenance and continue with the existing start flow.
  }
  return ''
}

export async function startCommand() {
  const dir = findInstallDir()
  const config = readConfig(dir)
  if (!config) {
    p.log.error('No runtime installation found. Run `npx learnhouse setup` first.')
    process.exit(1)
  }

  const appService = config.edition === 'enterprise' ? 'api' : 'learnhouse-app'
  const imageProvenance = describeRuntimeImage(configuredRuntimeImage(config.installDir, appService))
  p.intro(pc.cyan('Starting configured runtime services'))
  p.log.info(`Runtime image: ${imageProvenance.image || '(not identified)'}`)
  p.log.info(imageProvenance.disclosure)

  // Community only: Enterprise always mounted its content volume.
  if (config.edition !== 'enterprise') {
    try {
      const result = migrateContentVolume(config.installDir, config.deploymentId)
      if (result.status === 'migrated') {
        p.log.success('Preserved existing uploaded content into a persistent volume.')
      } else if (result.status === 'patched_no_data') {
        p.log.info('Added persistent content volume to docker-compose.yml.')
      }
    } catch (err) {
      // Safe to continue: the compose file is only patched after a successful
      // copy, so `up` will not recreate the container that holds the uploads.
      const msg = err instanceof Error ? err.message : String(err)
      p.log.warn(`Could not migrate content volume: ${msg}`)
    }
  }

  try {
    dockerComposeUp(config.installDir)
    p.log.success('Configured runtime services are running.')
  } catch {
    p.log.error('Failed to start services. Check Docker output above.')
    process.exit(1)
  }
}
