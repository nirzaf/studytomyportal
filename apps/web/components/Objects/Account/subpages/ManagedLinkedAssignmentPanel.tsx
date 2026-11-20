'use client'

import { useState } from 'react'
import { Button } from '@components/ui/button'
import { getAPIUrl } from '@services/config/config'
import { RequestBodyWithAuthHeader, secureFetch } from '@services/utils/ts/requests'

export type LinkedAssignmentTask = {
  assignment_task_uuid: string
  title: string
  description: string
  assignment_type: string
  max_grade_value: number | null
  student_grade: number | null
  teacher_feedback: string | null
  contents?: {
    prompt?: string
    question?: string
    instructions?: string
    unit?: string
    units?: string
    tolerance?: number
    hint?: string
    correct_value?: number | string
    [key: string]: unknown
  }
  submitted_answer?: {
    answer?: string | number
    fileUUID?: string
    [key: string]: unknown
  } | null
}

export type LinkedAssignment = {
  course_uuid: string
  release_uuid: string
  name: string
  activity_uuid: string
  assignment_uuid: string
  submission_uuid?: string | null
  title: string
  description: string
  due_date: string | null
  grading_type: string
  submission_status: string | null
  grade: number | null
  max_grade?: number
  overall_feedback: string | null
  attempt_number: number | null
  past_due?: boolean
  can_submit?: boolean
  can_retry?: boolean
  allow_retries?: boolean
  max_retries?: number
  release_conflict_uuid?: string | null
  tasks: LinkedAssignmentTask[]
  accepted_file_formats: string[]
  manual_review_note: string
}

function makeIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `st-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

async function readErrorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const data = await response.json() as { detail?: unknown; message?: unknown }
    if (typeof data?.detail === 'string' && data.detail.trim()) return data.detail
    if (data?.detail && typeof data.detail === 'object') {
      const msg = (data.detail as { message?: unknown }).message
      if (typeof msg === 'string' && msg.trim()) return msg
    }
    if (typeof data?.message === 'string' && data.message.trim()) return data.message
  } catch {
    // Ignore parse error and fall back to generic error message.
  }
  return fallback
}

export function savedNumberDraftValue(task: LinkedAssignmentTask): string {
  const val = task.submitted_answer?.answer
  return val === undefined || val === null ? '' : String(val)
}

export function isCsvUploadRejected(filename: string): boolean {
  return filename.trim().toLowerCase().endsWith('.csv')
}

function formatSubmissionStatus(status: string | null): string {
  switch (status) {
    case 'GRADED':
      return 'Graded'
    case 'SUBMITTED':
      return 'Submitted — awaiting teacher review'
    case 'PENDING':
    case 'NOT_SUBMITTED':
      return 'Draft in progress'
    default:
      return 'Not submitted yet'
  }
}

export function ManagedLinkedAssignmentPanel({
  orgUuid,
  token,
  assignment,
  onAssignmentUpdated,
}: {
  orgUuid: string
  token: string
  assignment: LinkedAssignment
  onAssignmentUpdated: (_next: LinkedAssignment) => void
}) {
  const [numberDrafts, setNumberDrafts] = useState<Record<string, string>>({})
  const [busyTaskUuid, setBusyTaskUuid] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [retrying, setRetrying] = useState(false)
  const [downloadingTaskUuid, setDownloadingTaskUuid] = useState<string | null>(null)
  const [statusMessage, setStatusMessage] = useState<string | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const basePath = `${getAPIUrl()}studytomy/orgs/${encodeURIComponent(orgUuid)}/learning/courses/${encodeURIComponent(assignment.course_uuid)}/releases/${encodeURIComponent(assignment.release_uuid)}/assignments/${encodeURIComponent(assignment.activity_uuid)}`

  const isEditable =
    !assignment.release_conflict_uuid &&
    !assignment.past_due &&
    (assignment.submission_status === null ||
      assignment.submission_status === 'PENDING' ||
      assignment.submission_status === 'NOT_SUBMITTED')

  async function saveDraftPayload(
    taskUuid: string,
    payload: Record<string, unknown>,
    announce = true,
  ): Promise<LinkedAssignment | null> {
    const reqInit = RequestBodyWithAuthHeader('PUT', { task_submission: payload }, null, token)
    const headers = new Headers(reqInit.headers)
    headers.set('Idempotency-Key', makeIdempotencyKey())
    const response = await secureFetch(
      `${basePath}/tasks/${encodeURIComponent(taskUuid)}/draft`,
      { ...reqInit, headers, cache: 'no-store' },
    )
    if (!response.ok) {
      throw new Error(await readErrorMessage(response, 'Could not save task draft.'))
    }
    const updated = await response.json() as LinkedAssignment
    onAssignmentUpdated(updated)
    if (announce) {
      setStatusMessage('Draft saved.')
    }
    return updated
  }

  async function handleSaveNumberTask(task: LinkedAssignmentTask) {
    setErrorMessage(null)
    setStatusMessage(null)
    const raw = (numberDrafts[task.assignment_task_uuid] ?? savedNumberDraftValue(task)).trim()
    if (!raw) {
      setErrorMessage('Enter a numeric answer before saving.')
      return
    }
    setBusyTaskUuid(task.assignment_task_uuid)
    try {
      await saveDraftPayload(task.assignment_task_uuid, { answer: raw }, true)
      setNumberDrafts(prev => {
        const next = { ...prev }
        delete next[task.assignment_task_uuid]
        return next
      })
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : 'Could not save numeric answer.')
    } finally {
      setBusyTaskUuid(null)
    }
  }

  async function handleFileSelected(taskUuid: string, file: File | null) {
    if (!file) return
    setErrorMessage(null)
    setStatusMessage(null)
    if (isCsvUploadRejected(file.name)) {
      setErrorMessage(
        'Direct CSV uploads are not accepted. Package exported CSV readings inside a ZIP archive or upload a PDF report.'
      )
      return
    }
    setBusyTaskUuid(taskUuid)
    try {
      const formData = new FormData()
      formData.append('sub_file', file)
      const uploadResponse = await secureFetch(
        `${basePath}/tasks/${encodeURIComponent(taskUuid)}/files`,
        {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${token}`,
            'Idempotency-Key': makeIdempotencyKey(),
          },
          body: formData,
          credentials: 'include',
          cache: 'no-store',
        },
      )
      if (!uploadResponse.ok) {
        throw new Error(await readErrorMessage(uploadResponse, 'File upload failed.'))
      }
      const receipt = await uploadResponse.json() as { file_uuid: string }
      await saveDraftPayload(taskUuid, { fileUUID: receipt.file_uuid }, false)
      setStatusMessage(`Uploaded ${file.name} and saved to draft.`)
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : 'Could not upload practical report.')
    } finally {
      setBusyTaskUuid(null)
    }
  }

  async function handleSubmitAssignment() {
    setErrorMessage(null)
    setStatusMessage(null)
    setSubmitting(true)
    try {
      // Save any pending numeric drafts before invoking final hand-in.
      for (const task of assignment.tasks) {
        if (task.assignment_type === 'NUMBER_ANSWER') {
          const val = (numberDrafts[task.assignment_task_uuid] ?? savedNumberDraftValue(task)).trim()
          const currentStr = savedNumberDraftValue(task).trim()
          if (val && val !== currentStr) {
            await saveDraftPayload(task.assignment_task_uuid, { answer: val }, false)
          }
        }
      }

      const reqInit = RequestBodyWithAuthHeader('POST', null, null, token)
      const headers = new Headers(reqInit.headers)
      headers.set('Idempotency-Key', makeIdempotencyKey())
      const response = await secureFetch(
        `${basePath}/submit`,
        { ...reqInit, headers, cache: 'no-store' },
      )
      if (!response.ok) {
        throw new Error(await readErrorMessage(response, 'Assignment submission failed.'))
      }
      const updated = await response.json() as LinkedAssignment
      setNumberDrafts({})
      onAssignmentUpdated(updated)
      setStatusMessage(
        updated.submission_status === 'GRADED'
          ? 'Assignment submitted and graded.'
          : 'Assignment submitted for teacher review.'
      )
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : 'Assignment submission failed.')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleRetryAssignment() {
    setErrorMessage(null)
    setStatusMessage(null)
    setRetrying(true)
    try {
      const reqInit = RequestBodyWithAuthHeader('POST', null, null, token)
      const response = await secureFetch(
        `${basePath}/retry`,
        { ...reqInit, cache: 'no-store' },
      )
      if (!response.ok) {
        throw new Error(await readErrorMessage(response, 'Could not start a new attempt.'))
      }
      const updated = await response.json() as LinkedAssignment
      setNumberDrafts({})
      onAssignmentUpdated(updated)
      setStatusMessage(`Attempt ${updated.attempt_number ?? ''} started.`.trim())
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : 'Could not start a new attempt.')
    } finally {
      setRetrying(false)
    }
  }

  async function handleDownloadFile(taskUuid: string, filename: string) {
    setErrorMessage(null)
    setDownloadingTaskUuid(taskUuid)
    try {
      const response = await secureFetch(
        `${basePath}/tasks/${encodeURIComponent(taskUuid)}/files/${encodeURIComponent(filename)}`,
        {
          method: 'GET',
          headers: { Authorization: `Bearer ${token}` },
          credentials: 'include',
          cache: 'no-store',
        },
      )
      if (!response.ok) {
        throw new Error(await readErrorMessage(response, 'Could not download uploaded file.'))
      }
      const blob = await response.blob()
      const objectUrl = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = objectUrl
      anchor.download = filename
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(objectUrl)
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : 'Could not download uploaded file.')
    } finally {
      setDownloadingTaskUuid(null)
    }
  }

  const maxScoreTotal = assignment.max_grade ?? assignment.tasks.reduce(
    (sum, t) => sum + (t.max_grade_value ?? 0), 0
  )

  return (
    <div className="space-y-4">
      {assignment.description && (
        <p className="max-w-prose text-sm leading-relaxed">{assignment.description}</p>
      )}
      <div className="rounded-lg border p-4 space-y-2 text-sm">
        <p>
          <strong>Status:</strong> {formatSubmissionStatus(assignment.submission_status)}
          {assignment.attempt_number ? ` · Attempt ${assignment.attempt_number}` : ''}
          {assignment.due_date ? ` · Due ${assignment.due_date}` : ''}
        </p>
        {assignment.past_due && isEditable === false && (assignment.submission_status === null || assignment.submission_status === 'PENDING' || assignment.submission_status === 'NOT_SUBMITTED') && (
          <p role="alert" className="text-destructive font-medium">
            The submission deadline has passed.
          </p>
        )}
        {assignment.release_conflict_uuid && (
          <p role="alert" className="rounded border border-destructive/50 bg-destructive/10 p-3 text-destructive">
            An existing attempt for this assignment is bound to another course release ({assignment.release_conflict_uuid}). Submissions are locked for this release to preserve grading history.
          </p>
        )}
        {assignment.grade !== null && (
          <p>
            <strong>Grade:</strong> {assignment.grade}
            {maxScoreTotal > 0 ? ` / ${maxScoreTotal}` : ''}
          </p>
        )}
        {assignment.overall_feedback && (
          <p className="rounded bg-muted p-3">
            <strong>Teacher feedback:</strong> {assignment.overall_feedback}
          </p>
        )}
        <p className="text-muted-foreground">
          <strong>Accepted practical upload formats:</strong> {assignment.accepted_file_formats.join(', ')} (max 50 MiB).
          Package exported CSV files inside a ZIP archive or include measurements in a PDF report.
        </p>
        <p className="text-xs text-muted-foreground">{assignment.manual_review_note}</p>
      </div>

      {statusMessage && (
        <p role="status" className="rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm text-emerald-700 dark:text-emerald-300">
          {statusMessage}
        </p>
      )}
      {errorMessage && (
        <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          {errorMessage}
        </p>
      )}

      <ol className="space-y-3">
        {assignment.tasks.map((task, idx) => {
          const taskBusy = busyTaskUuid === task.assignment_task_uuid
          const uploadedFileUuid = typeof task.submitted_answer?.fileUUID === 'string'
            ? task.submitted_answer.fileUUID
            : null
          const promptText = task.contents?.question || task.contents?.prompt || task.contents?.instructions
          const unitLabel = task.contents?.unit || task.contents?.units

          return (
            <li key={task.assignment_task_uuid} className="rounded-lg border p-4 space-y-3 text-sm">
              <div className="space-y-1">
                <h3 className="font-semibold">
                  {idx + 1}. {task.title}{' '}
                  <span className="font-normal text-xs text-muted-foreground">({task.assignment_type})</span>
                </h3>
                {task.description && <p className="text-muted-foreground">{task.description}</p>}
                {typeof promptText === 'string' && promptText.trim() && (
                  <p className="text-sm">{promptText}</p>
                )}
                {task.max_grade_value !== null && (
                  <p className="text-xs text-muted-foreground">
                    {task.student_grade !== null
                      ? `Score: ${task.student_grade} / ${task.max_grade_value}`
                      : `Max points: ${task.max_grade_value}`}
                  </p>
                )}
              </div>

              {task.assignment_type === 'NUMBER_ANSWER' && (
                <div className="space-y-2">
                  <label
                    htmlFor={`task-num-${task.assignment_task_uuid}`}
                    className="block text-xs font-medium text-muted-foreground"
                  >
                    Numeric answer{typeof unitLabel === 'string' && unitLabel ? ` (${unitLabel})` : ''}
                  </label>
                  <div className="flex flex-wrap items-center gap-2">
                    <input
                      id={`task-num-${task.assignment_task_uuid}`}
                      type="text"
                      inputMode="decimal"
                      disabled={!isEditable || submitting || taskBusy}
                      value={numberDrafts[task.assignment_task_uuid] ?? savedNumberDraftValue(task)}
                      onChange={e =>
                        setNumberDrafts(prev => ({
                          ...prev,
                          [task.assignment_task_uuid]: e.target.value,
                        }))
                      }
                      placeholder="Enter numeric value (SI units)"
                      className="min-h-11 rounded-md border bg-background px-3 py-2 text-sm"
                    />
                    {isEditable && (
                      <Button
                        type="button"
                        variant="outline"
                        className="min-h-11"
                        disabled={taskBusy || submitting}
                        onClick={() => void handleSaveNumberTask(task)}
                      >
                        {taskBusy ? 'Saving…' : 'Save draft'}
                      </Button>
                    )}
                  </div>
                </div>
              )}

              {task.assignment_type === 'FILE_SUBMISSION' && (
                <div className="space-y-2">
                  {uploadedFileUuid && (
                    <div className="flex flex-wrap items-center gap-2 rounded bg-muted/60 px-3 py-2 text-xs">
                      <span className="break-all">
                        <strong>Attached file:</strong> {uploadedFileUuid}
                      </span>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="min-h-9"
                        disabled={downloadingTaskUuid === task.assignment_task_uuid}
                        onClick={() => void handleDownloadFile(task.assignment_task_uuid, uploadedFileUuid)}
                      >
                        {downloadingTaskUuid === task.assignment_task_uuid ? 'Downloading…' : 'Download uploaded report'}
                      </Button>
                    </div>
                  )}
                  {isEditable && (
                    <div className="space-y-1">
                      <label
                        htmlFor={`task-file-${task.assignment_task_uuid}`}
                        className="block text-xs font-medium text-muted-foreground"
                      >
                        Upload PDF report or ZIP archive containing exported CSV files
                      </label>
                      <input
                        id={`task-file-${task.assignment_task_uuid}`}
                        type="file"
                        accept=".pdf,.zip,application/pdf,application/zip"
                        disabled={taskBusy || submitting}
                        onChange={e => {
                          const chosen = e.target.files?.[0] ?? null
                          void handleFileSelected(task.assignment_task_uuid, chosen)
                          e.currentTarget.value = ''
                        }}
                        className="block w-full text-xs file:me-3 file:min-h-10 file:rounded-md file:border file:bg-background file:px-3 file:py-2 file:text-xs file:font-medium"
                      />
                      {taskBusy && <p className="text-xs text-muted-foreground">Uploading file…</p>}
                    </div>
                  )}
                </div>
              )}

              {task.teacher_feedback && (
                <p className="rounded bg-muted p-2.5 text-sm">
                  <strong>Task feedback:</strong> {task.teacher_feedback}
                </p>
              )}
            </li>
          )
        })}
      </ol>

      <div className="flex flex-wrap items-center gap-3">
        {isEditable && (
          <Button
            type="button"
            className="min-h-11"
            disabled={submitting || busyTaskUuid !== null || assignment.tasks.length === 0}
            onClick={() => void handleSubmitAssignment()}
          >
            {submitting ? 'Submitting assignment…' : 'Submit assignment'}
          </Button>
        )}
        {assignment.can_retry && (
          <Button
            type="button"
            variant="outline"
            className="min-h-11"
            disabled={retrying || submitting}
            onClick={() => void handleRetryAssignment()}
          >
            {retrying ? 'Starting new attempt…' : 'Try again'}
          </Button>
        )}
      </div>
    </div>
  )
}
