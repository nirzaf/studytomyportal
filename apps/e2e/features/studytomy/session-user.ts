import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { ADMIN_EMAIL, ADMIN_PASSWORD, makeStudent } from '../../core/instance'
import { AUTH_DIR } from '../../core/sharedAuth'
import { createStudent, getOrg, login } from '../../core/client'

export type SessionSwitchStudent = ReturnType<typeof makeStudent>

const SESSION_SWITCH_STUDENT_PATH = join(AUTH_DIR, 'studytomy-session-switch.json')
let sessionSwitchStudentPromise: Promise<SessionSwitchStudent> | null = null

/**
 * Reuse one synthetic learner across the session-isolation specs and local
 * reruns. Creating a new account for every spec needlessly hits the API's
 * signup protection and makes focused runs order-dependent.
 */
export function getSessionSwitchStudent(): Promise<SessionSwitchStudent> {
  if (!sessionSwitchStudentPromise) {
    sessionSwitchStudentPromise = loadOrCreateStudent().catch((error) => {
      sessionSwitchStudentPromise = null
      throw error
    })
  }
  return sessionSwitchStudentPromise
}

async function loadOrCreateStudent(): Promise<SessionSwitchStudent> {
  const existing = readExistingStudent()
  if (existing) {
    try {
      await login(existing.email, existing.password)
      return existing
    } catch (error) {
      // A fresh local database can outlive ignored Playwright auth artifacts.
      // In that case, replace the stale fixture with one account for this run.
      if (!/-> 401\b|-> 404\b/.test(String(error))) throw error
    }
  }

  const student = makeStudent('session-switch')
  const adminToken = await login(ADMIN_EMAIL, ADMIN_PASSWORD)
  const org = await getOrg()
  await createStudent(adminToken, org.id, {
    email: student.email,
    username: student.username,
    password: student.password,
    first_name: student.firstName,
    last_name: student.lastName,
  })

  mkdirSync(AUTH_DIR, { recursive: true })
  writeFileSync(SESSION_SWITCH_STUDENT_PATH, JSON.stringify(student, null, 2))
  return student
}

function readExistingStudent(): SessionSwitchStudent | null {
  if (!existsSync(SESSION_SWITCH_STUDENT_PATH)) return null
  try {
    const value = JSON.parse(readFileSync(SESSION_SWITCH_STUDENT_PATH, 'utf8'))
    if (
      typeof value?.email === 'string' &&
      typeof value?.username === 'string' &&
      typeof value?.password === 'string' &&
      typeof value?.firstName === 'string' &&
      typeof value?.lastName === 'string'
    ) {
      return value as SessionSwitchStudent
    }
  } catch {
    // Replace malformed local-only test state below.
  }
  return null
}
