import { randomUUID } from 'node:crypto'
import { expect, test } from '../../../core/fixtures'
import { API_URL, BASE_URL, ORG_SLUG } from '../../../core/instance'
import { login, getOrg } from '../../../core/client'
import { sharedStudent, STUDENT_STATE } from '../../../core/sharedAuth'
import { isSafeStudytomyTestDatabase } from '../../../core/testDatabase'

test.use({ storageState: STUDENT_STATE, viewport: { width: 1280, height: 900 } })

type Curriculum = {
  uuid: string
  subject: string
  level: string
  language: string
  jurisdiction: string | null
}

function syntheticPdf(text: string): Buffer {
  const escaped = text.replaceAll('\\', '\\\\').replaceAll('(', '\\(').replaceAll(')', '\\)')
  const stream = `BT /F1 12 Tf 48 740 Td (${escaped}) Tj ET\n`
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    `<< /Length ${Buffer.byteLength(stream, 'ascii')} >>\nstream\n${stream}endstream`,
  ]
  const parts: Buffer[] = [Buffer.from('%PDF-1.4\n% synthetic fixture\n', 'ascii')]
  const offsets = [0]
  let cursor = parts[0].length
  objects.forEach((object, index) => {
    offsets.push(cursor)
    const part = Buffer.from(`${index + 1} 0 obj\n${object}\nendobj\n`, 'ascii')
    parts.push(part)
    cursor += part.length
  })
  const xrefOffset = cursor
  const xref = [
    'xref',
    `0 ${objects.length + 1}`,
    '0000000000 65535 f ',
    ...offsets.slice(1).map((offset) => `${String(offset).padStart(10, '0')} 00000 n `),
    'trailer',
    `<< /Size ${objects.length + 1} /Root 1 0 R >>`,
    'startxref',
    String(xrefOffset),
    '%%EOF',
    '',
  ].join('\n')
  parts.push(Buffer.from(xref, 'ascii'))
  return Buffer.concat(parts)
}

async function apiRequest(
  method: string,
  path: string,
  token: string,
  body?: unknown,
): Promise<Response> {
  const headers: Record<string, string> = { Authorization: `Bearer ${token}` }
  if (body && !(body instanceof FormData)) headers['Content-Type'] = 'application/json'
  return fetch(`${API_URL}${path}`, {
    method,
    headers,
    body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body),
  })
}

test('live learner search is backed by approved source records and revocation takes effect', async ({ page }) => {
  test.skip(!isSafeStudytomyTestDatabase(process.env.STUDYTOMY_TEST_DATABASE_URL),
    'Set STUDYTOMY_TEST_DATABASE_URL to the same loopback PostgreSQL database used by the API, with a database name ending in _test.')
  test.skip(!process.env.E2E_STUDYTOMY_SOURCE_AUTHOR_EMAIL || !process.env.E2E_STUDYTOMY_SOURCE_AUTHOR_PASSWORD,
    'Pre-provision a synthetic source author with organization-scoped syllabus author permission.')
  test.skip(!process.env.E2E_STUDYTOMY_SOURCE_REVIEWER_EMAIL || !process.env.E2E_STUDYTOMY_SOURCE_REVIEWER_PASSWORD,
    'Pre-provision a separate educational reviewer with active approval policy assignments.')

  const suffix = randomUUID()
  const org = await getOrg()
  const root = `/studytomy/orgs/${org.org_uuid}`
  const learner = sharedStudent()
  const learnerToken = await login(learner.email, learner.password)
  const authorToken = await login(
    process.env.E2E_STUDYTOMY_SOURCE_AUTHOR_EMAIL!,
    process.env.E2E_STUDYTOMY_SOURCE_AUTHOR_PASSWORD!,
  )
  const reviewerToken = await login(
    process.env.E2E_STUDYTOMY_SOURCE_REVIEWER_EMAIL!,
    process.env.E2E_STUDYTOMY_SOURCE_REVIEWER_PASSWORD!,
  )

  const [authorAccessResponse, reviewerAccessResponse, learnerAccessResponse, curriculumResponse] = await Promise.all([
    apiRequest('GET', `${root}/syllabus-library/access`, authorToken),
    apiRequest('GET', `${root}/syllabus-library/access`, reviewerToken),
    apiRequest('GET', `${root}/syllabus-library/access`, learnerToken),
    apiRequest('GET', `${root}/explore/curricula`, learnerToken),
  ])
  expect(authorAccessResponse.ok, 'the author account must have pre-approved source-author access').toBe(true)
  expect(reviewerAccessResponse.ok, 'the reviewer account must have pre-approved source-review access').toBe(true)
  const authorAccess = await authorAccessResponse.json()
  const reviewerAccess = await reviewerAccessResponse.json()
  const learnerAccess = await learnerAccessResponse.json()
  expect(authorAccess).toMatchObject({ can_author: true, can_review: false })
  expect(reviewerAccess).toMatchObject({ can_author: false, can_review: true })
  expect(learnerAccess).toMatchObject({ can_author: false, can_review: false })
  expect(curriculumResponse.ok).toBe(true)
  const curricula = await curriculumResponse.json() as Curriculum[]
  test.skip(curricula.length === 0, 'The isolated organization needs one published synthetic curriculum for context/scope coverage.')
  const curriculum = curricula[0]
  const title = `E2E synthetic photosynthesis ${suffix}`
  const grade = /grade\s*6/i.test(curriculum.level) ? 'Grade 7' : 'Grade 6'

  const metadata = {
    title,
    jurisdiction: curriculum.jurisdiction || 'Sri Lanka',
    grade,
    subject: curriculum.subject,
    language: curriculum.language,
    syllabus_edition: `Synthetic ${suffix}`,
    effective_cohort: `E2E ${suffix}`,
    publisher: 'Studytomy original synthetic E2E fixture',
    source_type: 'resource_book',
    source_revision: suffix,
    rights_status: 'unverified',
    rights_reference: '',
  }
  const form = new FormData()
  form.set('metadata', JSON.stringify(metadata))
  const pdfBuffer = syntheticPdf(
    `Synthetic fixture ${suffix}. Photosynthesis converts sunlight into glucose. Plants need sunlight to make food.`,
  )
  const pdfBytes = new Uint8Array(pdfBuffer.length)
  pdfBytes.set(pdfBuffer)
  form.set('file', new Blob([pdfBytes], { type: 'application/pdf' }), `photosynthesis-${suffix}.pdf`)

  let versionUuid: string | null = null
  let approved = false
  let revoked = false
  try {
    const upload = await apiRequest('POST', `${root}/syllabus-sources`, authorToken, form)
    expect(upload.status, await upload.clone().text()).toBe(201)
    const version = await upload.json()
    versionUuid = version.version_uuid as string
    expect(version).toMatchObject({ ingestion_status: 'ready', review_status: 'pending_review', page_count: 1 })

    const preApprovalSearch = await apiRequest('POST', `${root}/explore/topics`, learnerToken, {
      query: 'How do plants make food?', search_scope: 'all_approved',
    })
    expect(preApprovalSearch.ok).toBe(true)
    expect((await preApprovalSearch.json()).sources).not.toContainEqual(expect.objectContaining({ version_uuid: versionUuid }))
    const pendingPdf = await apiRequest('GET', `${root}/syllabus-sources/${versionUuid}/pdf`, learnerToken)
    expect([403, 404]).toContain(pendingPdf.status)

    const selfApproval = await apiRequest('POST', `${root}/syllabus-sources/${versionUuid}/review`, authorToken, {
      decision: 'approve', evidence: 'The uploader cannot independently review this source.',
      rights_reference: `Original synthetic fixture ${suffix}`,
    })
    if (selfApproval.status === 200) approved = true
    expect(selfApproval.status).toBe(403)
    const learnerApproval = await apiRequest('POST', `${root}/syllabus-sources/${versionUuid}/review`, learnerToken, {
      decision: 'approve', evidence: 'A learner cannot approve an educational source.',
      rights_reference: `Original synthetic fixture ${suffix}`,
    })
    if (learnerApproval.status === 200) approved = true
    expect(learnerApproval.status).toBe(403)
    const missingRights = await apiRequest('POST', `${root}/syllabus-sources/${versionUuid}/review`, reviewerToken, {
      decision: 'approve', evidence: 'Independent review of the synthetic fixture.',
    })
    if (missingRights.status === 200) approved = true
    expect(missingRights.status).toBe(422)

    const approval = await apiRequest('POST', `${root}/syllabus-sources/${versionUuid}/review`, reviewerToken, {
      decision: 'approve',
      evidence: `Independent role-separated review of original synthetic content ${suffix}.`,
      rights_reference: `Original test fixture authored in this repository; ${suffix}.`,
    })
    expect(approval.status, `Reviewer approval requires active organization policies and assignment; received ${approval.status}.`).toBe(200)
    approved = true
    expect((await approval.json()).review_status).toBe('approved')

    const directSearch = await apiRequest('POST', `${root}/explore/topics`, learnerToken, {
      query: 'How do plants make food?', search_scope: 'all_approved',
      selected_curriculum_uuid: curriculum.uuid,
    })
    expect(directSearch.ok).toBe(true)
    const directResult = await directSearch.json()
    const citation = directResult.sources.find((source: { version_uuid: string }) => source.version_uuid === versionUuid)
    expect(citation, `actual API response should cite the approved synthetic version: ${JSON.stringify(directResult)}`).toBeTruthy()
    expect(citation).toMatchObject({ title, page: 1, grade, subject: curriculum.subject })
    expect(citation.excerpt).toContain('Photosynthesis')

    const unsupportedApi = await apiRequest('POST', `${root}/explore/topics`, learnerToken, {
      query: 'How does zorbium transfer energy?', search_scope: 'all_approved',
    })
    expect(unsupportedApi.ok).toBe(true)
    const unsupportedResult = await unsupportedApi.json()
    expect(unsupportedResult.status).toBe('insufficient_evidence')
    expect(unsupportedResult.sources).toEqual([])

    const pdf = await apiRequest('GET', `${root}/syllabus-sources/${versionUuid}/pdf`, learnerToken)
    expect(pdf.status).toBe(200)
    expect(pdf.headers.get('content-type')).toContain('application/pdf')
    const pdfContent = Buffer.from(await pdf.arrayBuffer())
    expect(pdfContent.byteLength).toBeGreaterThan(100)
    expect(pdfContent.toString('ascii')).toContain(suffix)

    const browserTopicRequests: Record<string, unknown>[] = []
    page.on('request', (request) => {
      if (request.method() === 'POST' && request.url().endsWith('/explore/topics')) {
        try { browserTopicRequests.push(request.postDataJSON() as Record<string, unknown>) } catch { /* non-JSON request */ }
      }
    })
    await page.goto(`${BASE_URL}/orgs/${ORG_SLUG}/explore`)
    await expect(page.getByRole('heading', { name: 'Learn from approved sources' })).toBeVisible()
    const context = page.getByRole('combobox', { name: 'Syllabus context' })
    await context.click()
    await page.getByRole('option', { name: new RegExp(`${curriculum.subject}.*${curriculum.level}`) }).click()
    await expect(page.getByRole('combobox', { name: 'Search scope' })).toHaveText('All approved resources')
    await page.getByLabel('Topic or question').fill('How do plants make food?')
    await page.getByRole('button', { name: 'Explore topic' }).click()
    await expect(page.getByRole('heading', { name: title })).toBeVisible()
    await expect(page.getByText(`page 1`, { exact: false })).toBeVisible()
    expect(browserTopicRequests.at(-1)).toMatchObject({
      selected_curriculum_uuid: curriculum.uuid,
      search_scope: 'all_approved',
    })

    await page.getByRole('button', { name: /Ask a follow-up using these/ }).click()
    await page.getByLabel('Topic or question').fill('Why does it happen?')
    await page.getByRole('button', { name: 'Explore topic' }).click()
    await expect(page.getByRole('heading', { name: title })).toBeVisible()
    expect(browserTopicRequests.at(-1)).toMatchObject({
      context_question: 'How do plants make food?',
      source_version_uuids: [versionUuid],
    })

    const searchScope = page.getByRole('combobox', { name: 'Search scope' })
    await searchScope.click()
    await page.getByRole('option', { name: 'This subject across grades' }).click()
    await expect(page.getByRole('region', { name: 'Topic results' })).toHaveCount(0)
    await expect(page.getByText(/Follow-up searches only/)).toHaveCount(0)
    await page.getByLabel('Topic or question').fill('How do plants make food?')
    await page.getByRole('button', { name: 'Explore topic' }).click()
    await expect(page.getByRole('heading', { name: title })).toBeVisible()
    expect(browserTopicRequests.at(-1)).toMatchObject({
      selected_curriculum_uuid: curriculum.uuid,
      search_scope: 'selected_subject',
      context_question: null,
      source_version_uuids: [],
    })

    const popupPromise = page.waitForEvent('popup')
    await page.getByRole('button', { name: 'Open PDF · p. 1' }).click()
    const pdfPage = await popupPromise
    await expect.poll(() => pdfPage.url()).toContain('#page=1')
    await pdfPage.close()

    await page.getByLabel('Topic or question').fill('How does zorbium transfer energy?')
    await page.getByRole('button', { name: 'Explore topic' }).click()
    await expect(page.getByText('No sufficient approved source found')).toBeVisible()
    await expect(page.getByRole('heading', { name: title })).toHaveCount(0)

    await page.getByLabel('Topic or question').fill('How do plants make food?')
    await page.getByRole('button', { name: 'Explore topic' }).click()
    await expect(page.getByRole('heading', { name: title })).toBeVisible()
    const revoke = await apiRequest('POST', `${root}/syllabus-sources/${versionUuid}/revoke`, reviewerToken, {
      evidence: `Synthetic live E2E source access revocation ${suffix}.`,
    })
    expect(revoke.status).toBe(200)
    revoked = true
    const afterRevokeSearch = await apiRequest('POST', `${root}/explore/topics`, learnerToken, {
      query: 'How do plants make food?', search_scope: 'all_approved',
    })
    expect(afterRevokeSearch.ok).toBe(true)
    const afterRevoke = await afterRevokeSearch.json()
    expect(afterRevoke.sources).not.toContainEqual(expect.objectContaining({ version_uuid: versionUuid }))
    const revokedPdf = await apiRequest('GET', `${root}/syllabus-sources/${versionUuid}/pdf`, learnerToken)
    expect([403, 404]).toContain(revokedPdf.status)

    await page.getByRole('button', { name: 'Open PDF · p. 1' }).click()
    await expect(page.getByRole('status')).toContainText('This approved PDF is no longer available')
    await expect(page.getByRole('region', { name: 'Topic results' })).toHaveCount(0)
    await page.goto(`${BASE_URL}/orgs/${ORG_SLUG}/dash/studytomy/syllabus-library`)
    await expect(page.getByText('You are not assigned to view this organization’s syllabus library.')).toBeVisible()
    await expect(page.getByRole('button', { name: /approve/i })).toHaveCount(0)
  } finally {
    if (versionUuid && !revoked) {
      const cleanup = approved
        ? await apiRequest('POST', `${root}/syllabus-sources/${versionUuid}/revoke`, reviewerToken, {
            evidence: `Automatic cleanup of synthetic E2E fixture ${suffix}.`,
          })
        : await apiRequest('POST', `${root}/syllabus-sources/${versionUuid}/review`, reviewerToken, {
            decision: 'reject', evidence: `Automatic cleanup of unapproved synthetic E2E fixture ${suffix}.`,
          })
      if (!cleanup.ok) console.error(`Synthetic syllabus fixture cleanup failed with HTTP ${cleanup.status}.`)
    }
  }
})
