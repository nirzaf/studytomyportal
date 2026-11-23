import { expect, test } from 'bun:test'
import {
  currentPlaygroundReviewAssent,
  emptyPlaygroundReviewAssent,
  hasCurrentPlaygroundReviewAssent,
  isCurrentPlaygroundRejection,
  updatePlaygroundReviewAssent,
} from '../components/Playground/playground-review-state'

function confirmAll(assent, digest) {
  return updatePlaygroundReviewAssent(
    updatePlaygroundReviewAssent(
      updatePlaygroundReviewAssent(assent, 'playground-1', digest, 'rightsConfirmed', true),
      'playground-1',
      digest,
      'accessibilityConfirmed',
      true
    ),
    'playground-1',
    digest,
    'ageSuitabilityConfirmed',
    true
  )
}

test('Playground review assent is tied to the saved digest and must be fresh after a revision change', () => {
  const originalAssent = confirmAll(emptyPlaygroundReviewAssent(), 'digest-v1')
  expect(hasCurrentPlaygroundReviewAssent(originalAssent, 'playground-1', 'digest-v1')).toBe(true)

  const changedRevisionAssent = currentPlaygroundReviewAssent(
    originalAssent,
    'playground-1',
    'digest-v2'
  )
  expect(changedRevisionAssent).toEqual(emptyPlaygroundReviewAssent())
  expect(hasCurrentPlaygroundReviewAssent(changedRevisionAssent, 'playground-1', 'digest-v2')).toBe(false)
  expect(
    currentPlaygroundReviewAssent(changedRevisionAssent, 'playground-1', 'digest-v1')
  ).toEqual(emptyPlaygroundReviewAssent())
  expect(
    hasCurrentPlaygroundReviewAssent(originalAssent, 'playground-2', 'digest-v1')
  ).toBe(false)

  const newlyCheckedRights = updatePlaygroundReviewAssent(
    originalAssent,
    'playground-1',
    'digest-v2',
    'rightsConfirmed',
    true
  )
  expect(newlyCheckedRights.revisionDigest).toBe('digest-v2')
  expect(newlyCheckedRights.accessibilityConfirmed).toBe(false)
  expect(newlyCheckedRights.ageSuitabilityConfirmed).toBe(false)
  expect(hasCurrentPlaygroundReviewAssent(newlyCheckedRights, 'playground-1', 'digest-v2')).toBe(false)
  expect(
    hasCurrentPlaygroundReviewAssent(
      confirmAll(newlyCheckedRights, 'digest-v2'),
      'playground-1',
      'digest-v2'
    )
  ).toBe(true)
})

test('a stale-review conflict reset requires new confirmations even for the same saved digest', () => {
  const priorAssent = confirmAll(emptyPlaygroundReviewAssent(), 'digest-current')
  expect(hasCurrentPlaygroundReviewAssent(priorAssent, 'playground-1', 'digest-current')).toBe(true)

  // PlaygroundEditor clears this state when the server returns PLAYGROUND_REVIEW_STALE.
  const afterConflict = emptyPlaygroundReviewAssent()
  expect(hasCurrentPlaygroundReviewAssent(afterConflict, 'playground-1', 'digest-current')).toBe(false)
  expect(
    hasCurrentPlaygroundReviewAssent(
      confirmAll(afterConflict, 'digest-current'),
      'playground-1',
      'digest-current'
    )
  ).toBe(true)
})

test('a rejection is current only for its reviewed digest while its historical decision remains available', () => {
  const currentRejection = {
    content_digest: 'digest-v2',
    latest_decision: 'rejected',
    latest_reviewed_digest: 'digest-v2',
  }
  const historicalRejection = {
    ...currentRejection,
    latest_reviewed_digest: 'digest-v1',
  }

  expect(isCurrentPlaygroundRejection(currentRejection)).toBe(true)
  expect(isCurrentPlaygroundRejection(historicalRejection)).toBe(false)
  expect(historicalRejection.latest_decision).toBe('rejected')
  expect(isCurrentPlaygroundRejection({ ...currentRejection, latest_decision: 'approved' })).toBe(false)
})
