export type PlaygroundReviewConfirmationField =
  | 'rightsConfirmed'
  | 'accessibilityConfirmed'
  | 'ageSuitabilityConfirmed'

export interface PlaygroundReviewAssent {
  playgroundUuid: string | null
  revisionDigest: string | null
  rightsConfirmed: boolean
  accessibilityConfirmed: boolean
  ageSuitabilityConfirmed: boolean
}

export interface PlaygroundReviewDecisionSnapshot {
  content_digest: string
  latest_decision: 'approved' | 'rejected' | null
  latest_reviewed_digest: string | null
}

export function emptyPlaygroundReviewAssent(): PlaygroundReviewAssent {
  return {
    playgroundUuid: null,
    revisionDigest: null,
    rightsConfirmed: false,
    accessibilityConfirmed: false,
    ageSuitabilityConfirmed: false,
  }
}

export function currentPlaygroundReviewAssent(
  assent: PlaygroundReviewAssent,
  playgroundUuid: string | null | undefined,
  digest: string | null | undefined
): PlaygroundReviewAssent {
  if (
    !playgroundUuid ||
    !digest ||
    assent.playgroundUuid !== playgroundUuid ||
    assent.revisionDigest !== digest
  ) {
    return emptyPlaygroundReviewAssent()
  }
  return assent
}

export function updatePlaygroundReviewAssent(
  assent: PlaygroundReviewAssent,
  playgroundUuid: string | null | undefined,
  digest: string | null | undefined,
  field: PlaygroundReviewConfirmationField,
  checked: boolean
): PlaygroundReviewAssent {
  if (!playgroundUuid || !digest) return emptyPlaygroundReviewAssent()
  return {
    ...currentPlaygroundReviewAssent(assent, playgroundUuid, digest),
    playgroundUuid,
    revisionDigest: digest,
    [field]: checked,
  }
}

export function hasCurrentPlaygroundReviewAssent(
  assent: PlaygroundReviewAssent,
  playgroundUuid: string | null | undefined,
  digest: string | null | undefined
): boolean {
  const current = currentPlaygroundReviewAssent(assent, playgroundUuid, digest)
  return Boolean(
    current.playgroundUuid &&
    current.revisionDigest &&
    current.rightsConfirmed &&
    current.accessibilityConfirmed &&
    current.ageSuitabilityConfirmed
  )
}

export function isCurrentPlaygroundRejection(
  status: PlaygroundReviewDecisionSnapshot | null | undefined
): boolean {
  return Boolean(
    status &&
    status.latest_decision === 'rejected' &&
    status.latest_reviewed_digest &&
    status.latest_reviewed_digest === status.content_digest
  )
}
