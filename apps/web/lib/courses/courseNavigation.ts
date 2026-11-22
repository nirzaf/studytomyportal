export type CourseActivitySummary = {
  id: number | string
  activity_uuid: string
}

export type CourseChapterSummary = {
  activities?: readonly CourseActivitySummary[] | null
}

/** Pick the first incomplete visible activity in course order. */
export function getNextCourseActivity(
  chapters: readonly CourseChapterSummary[] | null | undefined,
  completedActivityIds: readonly (number | string)[] = [],
): CourseActivitySummary | null {
  const activities = (chapters ?? [])
    .flatMap((chapter) => chapter.activities ?? [])
    .filter((activity) => Boolean(activity?.activity_uuid))

  if (activities.length === 0) return null

  const completed = new Set(completedActivityIds.map(String))
  return activities.find((activity) => !completed.has(String(activity.id))) ?? activities[0]
}

export function isCourseStartAvailable(
  published: boolean | undefined,
  firstActivity: CourseActivitySummary | null,
): boolean {
  return published !== false && firstActivity !== null
}
