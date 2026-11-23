// @ts-nocheck -- bun test file (bun:test and .ts imports are not part of the app tsconfig)
import { expect, test } from 'bun:test'
import {
  getNextCourseActivity,
  isCourseStartAvailable,
  type CourseChapterSummary,
} from '../lib/courses/courseNavigation.ts'

const activity = (id: number, suffix: string) => ({
  id,
  activity_uuid: `activity_${suffix}`,
})

test('course start chooses the first activity across chapter boundaries', () => {
  const chapters: CourseChapterSummary[] = [
    { activities: [] },
    { activities: [activity(12, 'later-chapter')] },
  ]

  expect(getNextCourseActivity(chapters)).toEqual(activity(12, 'later-chapter'))
})

test('resume chooses the first incomplete activity and normalizes ID types', () => {
  const chapters: CourseChapterSummary[] = [
    { activities: [activity(12, 'one'), activity(13, 'two')] },
    { activities: [activity(14, 'three')] },
  ]

  expect(getNextCourseActivity(chapters, ['12', 14])).toEqual(activity(13, 'two'))
})

test('empty or draft courses cannot be started', () => {
  const firstActivity = activity(12, 'one')

  expect(getNextCourseActivity([{ activities: [] }])).toBeNull()
  expect(isCourseStartAvailable(true, null)).toBe(false)
  expect(isCourseStartAvailable(false, firstActivity)).toBe(false)
  expect(isCourseStartAvailable(true, firstActivity)).toBe(true)
})

test('a completed course resumes from its first activity', () => {
  const first = activity(12, 'one')
  const chapters: CourseChapterSummary[] = [{ activities: [first, activity(13, 'two')] }]

  expect(getNextCourseActivity(chapters, [12, 13])).toEqual(first)
})
