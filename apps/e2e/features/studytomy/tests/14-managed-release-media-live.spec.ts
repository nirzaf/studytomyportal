import { test, expect } from '../../../core/fixtures'
import { API_URL, BASE_URL } from '../../../core/instance'

const learnerState = process.env.E2E_MANAGED_MEDIA_LEARNER_STATE
const releaseName = process.env.E2E_MANAGED_MEDIA_RELEASE_NAME
const captionCode = process.env.E2E_MANAGED_MEDIA_CAPTION_CODE

test.use({ storageState: learnerState, viewport: { width: 390, height: 844 } })
test.skip(
  !learnerState || !releaseName ||
    ![BASE_URL, API_URL].every(url => ['localhost', '127.0.0.1', '[::1]'].includes(new URL(url).hostname)),
  'Requires a loopback stack and a fresh, synthetic managed release with a pending first-use grant.',
)

test('activates and opens a reviewed PDF through the real managed release API', async ({ page }) => {
  const learningUrl = new URL(API_URL)
  const learningRoot = learningUrl.pathname.replace(/\/$/, '') + '/studytomy/orgs/'
  const isLearningResponse = (url: string) => {
    const received = new URL(url)
    return received.origin === learningUrl.origin && received.pathname.startsWith(learningRoot)
  }

  await page.goto('/account/learning', { waitUntil: 'domcontentloaded' })
  const course = page.getByRole('button', { name: releaseName, exact: true })
  await expect(course).toBeVisible()
  const indexResponse = page.waitForResponse(response =>
    isLearningResponse(response.url()) && response.request().method() === 'GET' &&
    new URL(response.url()).pathname.endsWith('/lessons'),
  )
  await course.click()
  const index = await indexResponse
  expect(index.status()).toBe(200)
  expect(await index.json()).toMatchObject({ requires_first_use: true })

  await expect(page.getByText('Opening a lesson starts your access period.')).toBeVisible()
  await expect(page.getByRole('button', { name: /Reviewed lesson Text/ })).toBeEnabled()
  const videoLesson = page.getByRole('button', { name: /Reviewed video Video/ })
  if (captionCode) await expect(videoLesson).toBeEnabled()
  else await expect(videoLesson).toBeDisabled()
  await expect(page.getByRole('button', { name: /Unsupported audio Unsupported/ })).toBeDisabled()
  const openPdf = page.getByRole('button', { name: /Open Reviewed PDF PDF/ })
  await expect(openPdf).toBeEnabled()

  const mediaResponse = page.waitForResponse(response =>
    isLearningResponse(response.url()) && response.request().method() === 'GET' &&
    new URL(response.url()).pathname.endsWith('/media'),
  )
  const activationResponse = page.waitForResponse(response =>
    isLearningResponse(response.url()) && response.request().method() === 'POST' &&
    new URL(response.url()).pathname.endsWith('/media/open'),
  )
  await openPdf.click()
  const activation = await activationResponse
  expect(activation.status()).toBe(206)
  expect(activation.headers()['content-range']).toMatch(/^bytes 0-0\/\d+$/)

  const pdf = await mediaResponse
  expect([200, 206]).toContain(pdf.status())
  expect(pdf.headers()['content-type']).toContain('application/pdf')
  expect(new URL(pdf.url()).pathname).toBe(new URL(activation.url()).pathname.replace(/\/open$/, ''))
  const frame = page.getByTitle('Reviewed PDF')
  await expect(frame).toBeVisible()
  await expect(frame).toHaveAttribute('src', pdf.url())

  const back = page.getByRole('button', { name: 'Back to lessons' })
  await expect(back).toBeEnabled()
  await back.click()
  await expect(page.getByRole('button', { name: 'Reviewed PDF PDF', exact: true })).toBeEnabled()
  await expect(page.getByText('Opening a lesson starts your access period.')).toHaveCount(0)

  if (captionCode) {
    const videoResponse = page.waitForResponse(response =>
      isLearningResponse(response.url()) && response.request().method() === 'GET' &&
      new URL(response.url()).pathname.endsWith('/media') &&
      Boolean(response.headers()['content-type']?.startsWith('video/')),
    )
    const trackResponse = page.waitForResponse(response =>
      isLearningResponse(response.url()) &&
      new URL(response.url()).pathname.endsWith(`/media/captions/${encodeURIComponent(captionCode)}`),
    )
    await videoLesson.click()
    const captions = await trackResponse
    expect(captions.status()).toBe(200)
    expect(captions.headers()['content-type']).toContain('text/vtt')
    expect([200, 206]).toContain((await videoResponse).status())

    const player = page.locator('video')
    await expect(player).toBeVisible()
    await expect.poll(() => player.evaluate((video: HTMLVideoElement) =>
      Array.from(video.textTracks).some(track => track.mode === 'showing' && Boolean(track.cues?.length)),
    )).toBe(true)
    await expect.poll(() => player.evaluate((video: HTMLVideoElement) => video.readyState)).toBeGreaterThan(0)
    await player.evaluate(async (video: HTMLVideoElement) => {
      video.muted = true
      await video.play()
    })
    await expect.poll(() => player.evaluate((video: HTMLVideoElement) => video.currentTime)).toBeGreaterThan(0.1)
    await player.evaluate((video: HTMLVideoElement) => video.pause())
    await player.evaluate((video: HTMLVideoElement) => { video.currentTime = video.duration / 2 })
    await expect.poll(() => player.evaluate((video: HTMLVideoElement) =>
      !video.seeking && video.currentTime > 0,
    )).toBe(true)
  }
})
