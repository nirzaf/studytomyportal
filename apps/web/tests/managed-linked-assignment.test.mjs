import { describe, expect, test } from 'bun:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

import {
  isCsvUploadRejected,
  ManagedLinkedAssignmentPanel,
  savedNumberDraftValue,
} from '../components/Objects/Account/subpages/ManagedLinkedAssignmentPanel.tsx'

const baseAssignment = {
  course_uuid: 'course_phys',
  release_uuid: 'rel_1',
  name: 'Momentum Lab Report',
  activity_uuid: 'act_asg',
  assignment_uuid: 'asg_1',
  submission_uuid: null,
  title: 'Conservation of Momentum Practical',
  description: 'Enter final velocities and upload your PDF/ZIP practical report.',
  due_date: null,
  grading_type: 'POINTS',
  submission_status: null,
  grade: null,
  max_grade: 10,
  overall_feedback: null,
  attempt_number: null,
  past_due: false,
  can_submit: true,
  can_retry: false,
  allow_retries: false,
  max_retries: 0,
  release_conflict_uuid: null,
  accepted_file_formats: ['PDF', 'ZIP'],
  manual_review_note: 'Practical lab reports are reviewed manually by your teacher and do not automatically award independent mastery.',
  tasks: [
    {
      assignment_task_uuid: 'task_num_1',
      title: 'Equal-mass final velocity',
      description: 'OpenStax Ex 9.8 prediction',
      assignment_type: 'NUMBER_ANSWER',
      max_grade_value: 2,
      student_grade: null,
      teacher_feedback: null,
      contents: { prompt: 'Find v2f in m/s', unit: 'm/s' },
      submitted_answer: { answer: '2.5' },
    },
    {
      assignment_task_uuid: 'task_file_1',
      title: 'Practical Report Upload',
      description: 'Upload PDF or ZIP containing CSV runs',
      assignment_type: 'FILE_SUBMISSION',
      max_grade_value: 8,
      student_grade: null,
      teacher_feedback: null,
      contents: {},
      submitted_answer: null,
    },
  ],
}

describe('ManagedLinkedAssignmentPanel helpers and rendering', () => {
  test('extracts saved numeric drafts and rejects raw CSV filenames', () => {
    expect(savedNumberDraftValue(baseAssignment.tasks[0])).toBe('2.5')
    expect(savedNumberDraftValue(baseAssignment.tasks[1])).toBe('')
    expect(isCsvUploadRejected('run-1.csv')).toBe(true)
    expect(isCsvUploadRejected('RUN-1.CSV')).toBe(true)
    expect(isCsvUploadRejected('report.zip')).toBe(false)
    expect(isCsvUploadRejected('report.pdf')).toBe(false)
  })

  test('renders editable NUMBER_ANSWER and FILE_SUBMISSION controls before submission', () => {
    const html = renderToStaticMarkup(
      createElement(ManagedLinkedAssignmentPanel, {
        orgUuid: 'org_1',
        token: 'tok_1',
        assignment: baseAssignment,
        onAssignmentUpdated: () => {},
      }),
    )
    expect(html).toContain('Not submitted yet')
    expect(html).toContain('Equal-mass final velocity')
    expect(html).toContain('value="2.5"')
    expect(html).toContain('Save draft')
    expect(html).toContain('type="file"')
    expect(html).toContain('Submit assignment')
  })

  test('renders graded feedback, locks edits, and displays retry when eligible', () => {
    const html = renderToStaticMarkup(
      createElement(ManagedLinkedAssignmentPanel, {
        orgUuid: 'org_1',
        token: 'tok_1',
        assignment: {
          ...baseAssignment,
          submission_status: 'GRADED',
          grade: 9,
          attempt_number: 1,
          overall_feedback: 'Strong SI unit conservation proof.',
          can_submit: false,
          can_retry: true,
          allow_retries: true,
          max_retries: 2,
          tasks: [
            { ...baseAssignment.tasks[0], student_grade: 2, teacher_feedback: 'Exact analytic match.' },
            { ...baseAssignment.tasks[1], student_grade: 7, teacher_feedback: 'Clear ZIP CSV + graph.', submitted_answer: { fileUUID: 'f1_submission_u2.zip' } },
          ],
        },
        onAssignmentUpdated: () => {},
      }),
    )
    expect(html).toContain('Graded')
    expect(html).toContain('9 / 10')
    expect(html).toContain('Strong SI unit conservation proof.')
    expect(html).toContain('Exact analytic match.')
    expect(html).toContain('f1_submission_u2.zip')
    expect(html).toContain('Download uploaded report')
    expect(html).toContain('Try again')
    expect(html).not.toContain('Submit assignment')
  })
})
