'use client'
import { Input } from "@components/ui/input"
import { Textarea } from "@components/ui/textarea"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@components/ui/select"
import FormLayout, {
  FormField,
  FormLabelAndMessage,
} from '@components/Objects/StyledElements/Form/Form'
import * as Form from '@radix-ui/react-form'
import { createNewCourse } from '@services/courses/courses'
import { createChapter } from '@services/courses/chapters'
import { getOrganizationContextInfoWithoutCredentials } from '@services/organizations/orgs'
import React, { useEffect, useMemo, useRef } from 'react'
import { BarLoader } from 'react-spinners'
import { revalidateTags } from '@services/utils/ts/requests'
import { useRouter } from 'next/navigation'
import { useQueryClient } from '@tanstack/react-query'
import { queryKeys } from '@/lib/query/keys'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import toast from 'react-hot-toast'
import { useFormik } from 'formik'
import * as Yup from 'yup'
import { UploadCloud, Clipboard } from 'lucide-react'
import AIImageButton from '@components/Objects/AI/AIImageButton'
import FormTagInput from "@components/Objects/StyledElements/Form/TagInput"
import { useTranslation } from "react-i18next"
import { useLHAnalytics, AnalyticsEvent } from '@services/analytics'
import { useUpgradeModal } from '@components/Dashboard/Shared/PlanRestricted/UpgradeModalContext'
import {
  CourseDraftSync,
  draftFieldsOf,
  isBlankDraft,
  sameDraftFields,
  type CourseDraft,
  type DraftFields,
  type SyncState,
} from '@/lib/courses/courseDraftSync'
import {
  createCourseDraftApi,
  deleteCourseDraft,
  fetchCourseDraftThumbnail,
  getCourseDraft,
} from '@services/courses/courseDrafts'

const BLANK_FIELDS: DraftFields = { name: '', description: '', learnings: '', tags: '', public: true }

const _validationSchema = Yup.object().shape({
  name: Yup.string()
    .required('Course name is required')
    .max(100, 'Must be 100 characters or less'),
  description: Yup.string()
    .max(1000, 'Must be 1000 characters or less'),
  learnings: Yup.string(),
  tags: Yup.string(),
  visibility: Yup.boolean(),
  thumbnail: Yup.mixed().nullable()
})

function CreateCourseModal({ closeModal, orgslug }: any) {
  const { t } = useTranslation()
  const { track } = useLHAnalytics('dashboard')
  const router = useRouter()
  const session = useLHSession() as any
  const queryClient = useQueryClient()
  const [orgId, setOrgId] = React.useState(null) as any
  const [isUploading, setIsUploading] = React.useState(false)
  // A free org that hits its course limit gets the shared upgrade paywall.
  const { handlePlanLimit } = useUpgradeModal()

  // --- Server-persisted draft (see lib/courses/courseDraftSync.ts) ---
  const tokenRef = useRef<string | undefined>(undefined)
  tokenRef.current = session.data?.tokens?.access_token
  const syncRef = useRef<CourseDraftSync | null>(null)
  const resumeGuard = useRef<DraftFields | null>(null)
  const submitLock = useRef(false)
  const finishedRef = useRef(false)
  const [syncState, setSyncState] = React.useState<SyncState>({ status: 'idle', error: null, conflictDraft: null })
  const [phase, setPhase] = React.useState<'loading' | 'choose' | 'editing' | 'load_error'>('loading')
  const [savedDraft, setSavedDraft] = React.useState<CourseDraft | null>(null)
  const [loadError, setLoadError] = React.useState<string | null>(null)
  const [confirmDiscard, setConfirmDiscard] = React.useState(false)
  const [closing, setClosing] = React.useState(false)
  const [closeBlocked, setCloseBlocked] = React.useState(false)
  const [thumbnailRestoreFailed, setThumbnailRestoreFailed] = React.useState(false)

  const validationSchema = Yup.object().shape({
    name: Yup.string()
      .required(t('courses.course_name_required'))
      .max(100, 'Must be 100 characters or less'),
    description: Yup.string()
      .required(t('courses.course_description_required'))
      .max(1000, 'Must be 1000 characters or less'),
    learnings: Yup.string(),
    tags: Yup.string(),
    visibility: Yup.boolean(),
    thumbnail: Yup.mixed().nullable()
  })

  const formik = useFormik({
    initialValues: {
      name: '',
      description: '',
      learnings: '',
      visibility: true,
      tags: '',
      thumbnail: null
    },
    validationSchema,
    onSubmit: async (values, { setSubmitting }) => {
      // Exactly one create request: ignore re-submits while one is running.
      if (submitLock.current) return
      if (thumbnailRestoreFailed) {
        toast.error(
          t('courses.draft_thumbnail_restore_blocked', {
            defaultValue: 'Resolve the saved thumbnail error before creating the course.',
          })
        )
        setSubmitting(false)
        return
      }
      submitLock.current = true
      let created = false
      // Persist the draft first so a stable server draft_uuid is always
      // established as the idempotency key before POST /courses/ is sent.
      syncRef.current?.schedule({
        name: values.name,
        description: values.description,
        learnings: values.learnings,
        tags: values.tags,
        public: values.visibility,
      })
      const flushed = (await syncRef.current?.flush()) ?? false
      const draftUuid = syncRef.current?.currentDraftUuid() ?? null
      if (!flushed || !draftUuid) {
        submitLock.current = false
        setSubmitting(false)
        toast.error(
          t('courses.draft_save_before_create_failed', {
            defaultValue: 'Could not save your draft before creating the course. Resolve the error and try again.',
          })
        )
        return
      }
      // Stop autosave so no draft write can land after the draft is discarded.
      await syncRef.current?.pause()
      const toast_loading = toast.loading(t('courses.creating_course'))

      try {
        const res = await createNewCourse(
          orgId,
          {
            name: values.name,
            description: values.description,
            learnings: values.learnings,
            tags: values.tags,
            visibility: values.visibility
          },
          values.thumbnail,
          session.data?.tokens?.access_token,
          draftUuid
        )

        if (res.success) {
          created = true
          finishedRef.current = true
          // The course exists now: discard the draft (retry once; a leftover
          // draft is harmless and can be discarded from the resume prompt).
          const discard = await deleteCourseDraft(orgId, null, tokenRef.current)
          if (!discard.ok) await deleteCourseDraft(orgId, null, tokenRef.current)
          const thumbnailFile = values.thumbnail as File | null
          track(AnalyticsEvent.CourseCreated, {
            thumbnail_source: thumbnailFile
              ? thumbnailFile.name === 'ai_image.jpg'
                ? 'ai'
                : 'upload'
              : 'none',
            visibility: values.visibility ? 'public' : 'private',
            has_learnings: !!values.learnings?.trim(),
          })
          // Create a default first chapter so the new course isn't empty.
          // Never let a failure here block course creation.
          try {
            await createChapter(
              {
                name: t('courses.first_chapter_default_name', { defaultValue: 'First Chapter' }),
                description: '',
                thumbnail_image: '',
                course_id: res.data.id,
                org_id: res.data.org_id ?? orgId,
              },
              session.data?.tokens?.access_token
            )
          } catch (_chapterError) {
            // Ignore: the default chapter is a convenience, not a requirement.
          }

          await revalidateTags(['courses'], orgslug)
          // Refresh sidebar courses cache
          queryClient.invalidateQueries({ queryKey: queryKeys.courses.list(orgslug) })
          toast.dismiss(toast_loading)
          toast.success(t('courses.course_created_success'))

          closeModal()
          // Redirect straight to the Content tab and auto-open the "add activity"
          // popup so a brand-new course lands the teacher on the core creation
          // action (their first activity) instead of an empty settings page.
          const courseId = res.data.course_uuid?.replace('course_', '') || res.data.course_uuid
          router.push(`/dash/courses/course/${courseId}/content?new_activity=1`)
        } else {
          toast.dismiss(toast_loading)
          const detail = typeof res.data?.detail === 'string' ? res.data.detail : ''
          // A free org that has hit its course limit gets a contextual upgrade
          // paywall at the moment of value, instead of a dead-end error toast.
          if (handlePlanLimit(res, { source: 'course_create', feature: 'courses', requiredPlan: 'standard' })) {
            closeModal()
          } else {
            const errorMessage = detail
              ? detail
              : Array.isArray(res.data?.detail)
                ? res.data.detail.map((e: any) => e.msg).join(', ')
                : t('courses.failed_to_create_course')
            toast.error(errorMessage)
          }
        }
      } catch (_error) {
        toast.error(t('courses.failed_to_create_course'))
      } finally {
        setSubmitting(false)
        if (!created) {
          // Validation/network/server failure: keep the draft and keep autosaving.
          submitLock.current = false
          syncRef.current?.resume()
        }
      }
    }
  })

  const thumbnail = formik.values.thumbnail as File | null
  const thumbnailUrl = useMemo(() => (thumbnail ? URL.createObjectURL(thumbnail) : null), [thumbnail])
  useEffect(() => {
    return () => {
      if (thumbnailUrl) URL.revokeObjectURL(thumbnailUrl)
    }
  }, [thumbnailUrl])

  const applyFields = (fields: DraftFields) => {
    resumeGuard.current = fields
    formik.setValues(
      {
        name: fields.name,
        description: fields.description,
        learnings: fields.learnings,
        tags: fields.tags,
        visibility: fields.public,
        thumbnail: null,
      },
      false
    )
  }

  const restoreDraftThumbnail = async () => {
    setThumbnailRestoreFailed(false)
    try {
      const file = await fetchCourseDraftThumbnail(orgId, tokenRef.current)
      if (file) formik.setFieldValue('thumbnail', file, false)
    } catch (_e) {
      setThumbnailRestoreFailed(true)
      toast.error(
        t('courses.draft_thumbnail_restore_failed', {
          defaultValue: 'The saved thumbnail could not be loaded. Retry loading it, upload a new image, or explicitly continue without it.',
        })
      )
    }
  }

  const applyDraft = async (draft: CourseDraft) => {
    setThumbnailRestoreFailed(false)
    applyFields(draftFieldsOf(draft))
    if (!draft.has_thumbnail) return
    await restoreDraftThumbnail()
  }

  const loadDraft = React.useCallback(async () => {
    setPhase('loading')
    setLoadError(null)
    const result = await getCourseDraft(orgId, tokenRef.current)
    if (!result.ok) {
      setLoadError(result.error)
      setPhase('load_error')
      return
    }
    syncRef.current?.hydrate(result.draft)
    setSavedDraft(result.draft)
    if (!result.draft) {
      setPhase('editing')
    } else if (isBlankDraft(result.draft)) {
      await applyDraft(result.draft)
      setPhase('editing')
    } else {
      setPhase('choose')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgId])

  useEffect(() => {
    if (!orgId) return
    const sync = new CourseDraftSync(createCourseDraftApi(orgId, () => tokenRef.current), setSyncState)
    syncRef.current = sync
    void loadDraft()
    return () => {
      sync.detach()
      syncRef.current = null
    }
  }, [orgId, loadDraft])

  const { name: vName, description: vDescription, learnings: vLearnings, tags: vTags, visibility: vVisibility } = formik.values
  useEffect(() => {
    if (phase !== 'editing') return
    const fields: DraftFields = {
      name: vName,
      description: vDescription,
      learnings: vLearnings,
      tags: vTags,
      public: vVisibility,
    }
    if (resumeGuard.current) {
      if (!sameDraftFields(fields, resumeGuard.current)) return
      resumeGuard.current = null
    }
    syncRef.current?.schedule(fields)
  }, [phase, vName, vDescription, vLearnings, vTags, vVisibility])

  useEffect(() => {
    const handler = (event: BeforeUnloadEvent) => {
      if (!finishedRef.current && syncRef.current?.hasUnsaved()) {
        event.preventDefault()
        event.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [])

  const setThumbnail = (file: File) => {
    setThumbnailRestoreFailed(false)
    formik.setFieldValue('thumbnail', file)
    syncRef.current?.setThumbnail(file)
  }

  const handleResume = async () => {
    if (!savedDraft) return
    await applyDraft(savedDraft)
    setPhase('editing')
  }

  const handleStartNew = async () => {
    if (!savedDraft) return
    if (!confirmDiscard) {
      setConfirmDiscard(true)
      return
    }
    const result = await deleteCourseDraft(orgId, savedDraft.revision, tokenRef.current)
    setConfirmDiscard(false)
    if (result.ok) {
      syncRef.current?.hydrate(null)
      setSavedDraft(null)
      applyFields(BLANK_FIELDS)
      setPhase('editing')
    } else if (result.conflict) {
      toast.error(t('courses.draft_changed_elsewhere', { defaultValue: 'This draft was changed in another tab. Review it before deciding.' }))
      syncRef.current?.hydrate(result.current ?? null)
      setSavedDraft(result.current ?? null)
      if (!result.current) setPhase('editing')
    } else {
      toast.error(result.error || t('courses.draft_discard_failed', { defaultValue: 'Could not discard the draft. Try again.' }))
    }
  }

  const handleSaveAndClose = async () => {
    setClosing(true)
    setCloseBlocked(false)
    const ok = (await syncRef.current?.flush()) ?? true
    setClosing(false)
    if (ok) closeModal()
    else setCloseBlocked(true)
  }

  const handleReloadLatest = async () => {
    const latest = syncRef.current?.resolveConflict('reload') ?? null
    if (latest) {
      await applyDraft(latest)
      setSavedDraft(latest)
    } else {
      syncRef.current?.schedule({
        name: formik.values.name,
        description: formik.values.description,
        learnings: formik.values.learnings,
        tags: formik.values.tags,
        public: formik.values.visibility,
      })
    }
  }

  const getOrgMetadata = async () => {
    const org = await getOrganizationContextInfoWithoutCredentials(orgslug, {
      revalidate: 360,
      tags: ['organizations'],
    })
    setOrgId(org.id)
  }

  useEffect(() => {
    if (orgslug) {
      getOrgMetadata()
    }
  }, [orgslug])

  const handleFileChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (file) {
      setThumbnail(file)
    }
  }

  const handleRemoteImageSelect = async (imageUrl: string) => {
    setIsUploading(true)
    try {
      const response = await fetch(imageUrl)
      const blob = await response.blob()
      const file = new File([blob], 'ai_image.jpg', { type: 'image/jpeg' })
      setThumbnail(file)
    } catch (_error) {
      toast.error(t('courses.create.generated_image_failed'))
    }
    setIsUploading(false)
  }

  const handleAIImageFile = async (file: File) => {
    setThumbnail(file)
  }

  const handlePasteFromClipboard = async () => {
    try {
      const clipboardItems = await navigator.clipboard.read()
      for (const clipboardItem of clipboardItems) {
        const imageTypes = clipboardItem.types.filter(type => type.startsWith('image/'))
        if (imageTypes.length > 0) {
          const imageType = imageTypes[0]
          const blob = await clipboardItem.getType(imageType)
          const extensionByType: Record<string, string> = {
            'image/jpeg': 'jpg',
            'image/png': 'png',
            'image/gif': 'gif',
            'image/webp': 'webp',
          }
          const extension = extensionByType[imageType] ?? 'png'
          const file = new File([blob], `clipboard_image.${extension}`, { type: imageType })
          setThumbnail(file)
          return
        }
      }
      toast.error(t('courses.no_image_in_clipboard', { defaultValue: 'No image found in clipboard' }))
    } catch (_) {
      toast.error(t('courses.clipboard_read_error', { defaultValue: 'Failed to read from clipboard' }))
    }
  }

  if (phase !== 'editing') {
    return (
      <div className="flex flex-col gap-4" data-testid="course-draft-gate">
        {phase === 'loading' && (
          <p role="status" className="text-sm text-gray-500">
            {t('courses.draft_loading', { defaultValue: 'Checking for a saved draft...' })}
          </p>
        )}
        {phase === 'load_error' && (
          <>
            <p role="alert" className="text-sm text-red-600">
              {t('courses.draft_load_failed', { defaultValue: 'Could not load your saved draft.' })}
              {loadError ? ` (${loadError})` : ''}
            </p>
            <div className="flex flex-wrap justify-end gap-2">
              <button type="button" onClick={() => void loadDraft()} className="px-4 py-2 bg-black text-white text-sm font-bold rounded-md">
                {t('courses.draft_retry', { defaultValue: 'Retry' })}
              </button>
              <button
                type="button"
                onClick={() => setPhase('editing')}
                className="px-4 py-2 border border-gray-300 text-sm font-bold rounded-md"
              >
                {t('courses.draft_continue_without', { defaultValue: 'Continue without loading' })}
              </button>
              <button type="button" onClick={closeModal} className="px-4 py-2 border border-gray-300 text-sm font-bold rounded-md">
                {t('common.close', { defaultValue: 'Close' })}
              </button>
            </div>
          </>
        )}
        {phase === 'choose' && savedDraft && (
          <>
            <div className="rounded-md bg-gray-50 outline outline-1 outline-gray-200 p-4">
              <p className="text-sm font-bold">
                {t('courses.draft_found', { defaultValue: 'You have an unfinished course draft.' })}
              </p>
              <p className="text-sm text-gray-600 mt-1">
                {savedDraft.name || t('courses.draft_untitled', { defaultValue: 'Untitled course' })}
                {' · '}
                {t('courses.draft_last_saved', { defaultValue: 'last saved' })}{' '}
                {new Date(savedDraft.updated_at).toLocaleString()}
              </p>
              {confirmDiscard && (
                <p role="alert" className="text-sm text-red-600 mt-2">
                  {t('courses.draft_discard_warning', {
                    defaultValue: 'Starting new permanently discards this saved draft. Click again to confirm.',
                  })}
                </p>
              )}
            </div>
            <div className="flex flex-wrap justify-end gap-2">
              <button type="button" onClick={closeModal} className="px-4 py-2 border border-gray-300 text-sm font-bold rounded-md">
                {t('common.close', { defaultValue: 'Close' })}
              </button>
              <button type="button" onClick={() => void handleStartNew()} className="px-4 py-2 border border-gray-300 text-sm font-bold rounded-md">
                {confirmDiscard
                  ? t('courses.draft_confirm_start_new', { defaultValue: 'Discard & Start New' })
                  : t('courses.draft_start_new', { defaultValue: 'Start New' })}
              </button>
              <button type="button" onClick={() => void handleResume()} className="px-4 py-2 bg-black text-white text-sm font-bold rounded-md">
                {t('courses.draft_resume', { defaultValue: 'Resume Draft' })}
              </button>
            </div>
          </>
        )}
      </div>
    )
  }

  const statusLabel =
    syncState.status === 'saving'
      ? t('courses.draft_saving', { defaultValue: 'Saving...' })
      : syncState.status === 'saved'
        ? t('courses.draft_saved', { defaultValue: 'Saved' })
        : syncState.status === 'error'
          ? t('courses.draft_save_failed', { defaultValue: 'Save failed' })
          : syncState.status === 'conflict'
            ? t('courses.draft_conflict_label', { defaultValue: 'Not saved: draft changed elsewhere' })
            : ''

  return (
    <FormLayout onSubmit={formik.handleSubmit}>
      <FormField name="name">
        <FormLabelAndMessage
          label={t('courses.course_name')}
          message={formik.errors.name}
        />
        <Form.Control asChild>
          <Input
            onChange={formik.handleChange}
            value={formik.values.name}
            type="text"
            required
          />
        </Form.Control>
      </FormField>

      <FormField name="description">
        <FormLabelAndMessage
          label={t('library.description')}
          message={formik.errors.description}
        />
        <Form.Control asChild>
          <Textarea
            onChange={formik.handleChange}
            value={formik.values.description}
            required
          />
        </Form.Control>
      </FormField>

      <FormField name="thumbnail">
        <FormLabelAndMessage
          label={t('courses.course_thumbnail')}
          message={formik.errors.thumbnail}
        />
        <div className="w-auto bg-gray-50 rounded-xl outline outline-1 outline-gray-200 h-[200px] shadow-sm">
          <div className="flex flex-col justify-center items-center h-full">
            <div className="flex flex-col justify-center items-center">
              {formik.values.thumbnail ? (
                <img
                  src={thumbnailUrl ?? '/empty_thumbnail.png'}
                  alt=""
                  className={`${isUploading ? 'animate-pulse' : ''} shadow-sm w-[200px] h-[100px] rounded-md object-cover`}
                />
              ) : (
                <img
                  src="/empty_thumbnail.png"
                  alt=""
                  className="shadow-sm w-[200px] h-[100px] rounded-md bg-gray-200 object-cover"
                />
              )}
              <div className="flex justify-center items-center space-x-2">
                <input
                  type="file"
                  id="fileInput"
                  style={{ display: 'none' }}
                  onChange={handleFileChange}
                  accept="image/jpeg,image/png,image/webp,image/gif"
                />
                <button
                  type="button"
                  className="font-bold antialiased items-center text-gray text-sm rounded-md px-4 mt-6 flex"
                  onClick={() => document.getElementById('fileInput')?.click()}
                >
                  <UploadCloud size={16} className="me-2" />
                  <span>{t('courses.upload_image')}</span>
                </button>
                <button
                  type="button"
                  className="font-bold antialiased items-center text-gray text-sm rounded-md px-4 mt-6 flex"
                  onClick={handlePasteFromClipboard}
                >
                  <Clipboard size={16} className="me-2" />
                  <span>{t('courses.paste_from_clipboard')}</span>
                </button>
                <AIImageButton
                  onSelect={handleRemoteImageSelect}
                  onSelectFile={handleAIImageFile}
                  className="font-bold antialiased items-center text-gray text-sm rounded-md px-4 mt-6 flex gap-2"
                />
              </div>
            </div>
          </div>
        </div>
        {thumbnailRestoreFailed && (
          <div role="alert" className="mt-2 rounded-md bg-amber-50 outline outline-1 outline-amber-300 p-3 text-sm text-amber-900">
            <p>
              {t('courses.draft_thumbnail_restore_warning', {
                defaultValue: 'Your saved thumbnail failed to load. Retry loading it, upload a replacement, or continue without it before creating the course.',
              })}
            </p>
            <div className="flex flex-wrap gap-2 mt-2">
              <button
                type="button"
                onClick={() => void restoreDraftThumbnail()}
                className="px-3 py-1 border border-amber-400 font-bold rounded-md"
              >
                {t('courses.draft_retry_thumbnail', { defaultValue: 'Retry loading thumbnail' })}
              </button>
              <button
                type="button"
                onClick={() => setThumbnailRestoreFailed(false)}
                className="px-3 py-1 border border-amber-400 font-bold rounded-md"
              >
                {t('courses.draft_continue_without_thumbnail', { defaultValue: 'Continue without thumbnail' })}
              </button>
            </div>
          </div>
        )}
      </FormField>

      <FormField name="learnings">
        <FormLabelAndMessage
          label={t('courses.course_learnings')}
          message={formik.errors.learnings}
        />
        <FormTagInput
          placeholder={t('courses.enter_to_add')}
          value={formik.values.learnings}
          onChange={(value) => formik.setFieldValue('learnings', value)}
          error={formik.errors.learnings}
        />
      </FormField>

      <FormField name="tags">
        <FormLabelAndMessage
          label={t('courses.course_tags')}
          message={formik.errors.tags}
        />
        <FormTagInput
          placeholder={t('courses.enter_to_add')}
          value={formik.values.tags}
          onChange={(value) => formik.setFieldValue('tags', value)}
          error={formik.errors.tags}
        />
      </FormField>

      <FormField name="visibility">
        <FormLabelAndMessage
          label={t('courses.course_visibility')}
          message={formik.errors.visibility}
        />
        <Select
          value={formik.values.visibility.toString()}
          onValueChange={(value) => formik.setFieldValue('visibility', value === 'true')}
        >
          <SelectTrigger>
            <SelectValue placeholder={t('courses.select_visibility')} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="true">{t('courses.public')} ({t('courses.public_desc')})</SelectItem>
            <SelectItem value="false">{t('courses.private')} ({t('courses.private_desc')})</SelectItem>
          </SelectContent>
        </Select>
      </FormField>

      {syncState.status === 'conflict' && (
        <div role="alert" className="mt-4 rounded-md bg-amber-50 outline outline-1 outline-amber-300 p-3 text-sm">
          <p>
            {t('courses.draft_conflict', {
              defaultValue: 'This draft was changed in another tab or device, so your latest edits were not saved.',
            })}
          </p>
          <div className="flex flex-wrap gap-2 mt-2">
            <button type="button" onClick={() => void handleReloadLatest()} className="px-3 py-1 border border-gray-300 font-bold rounded-md">
              {t('courses.draft_load_latest', { defaultValue: 'Load latest draft' })}
            </button>
            <button
              type="button"
              onClick={() => syncRef.current?.resolveConflict('overwrite')}
              className="px-3 py-1 border border-gray-300 font-bold rounded-md"
            >
              {t('courses.draft_keep_mine', { defaultValue: 'Keep my version' })}
            </button>
          </div>
        </div>
      )}

      {(closeBlocked || syncState.status === 'error') && (
        <div role="alert" className="mt-4 rounded-md bg-red-50 outline outline-1 outline-red-200 p-3 text-sm text-red-700">
          <p>
            {closeBlocked
              ? t('courses.draft_close_blocked', {
                  defaultValue: 'Your latest changes are not saved yet, so the form was kept open.',
                })
              : t('courses.draft_save_failed_detail', { defaultValue: 'Your draft could not be saved.' })}
            {syncState.error ? ` (${syncState.error})` : ''}
          </p>
          <div className="flex flex-wrap gap-2 mt-2">
            <button type="button" onClick={() => void syncRef.current?.flush()} className="px-3 py-1 border border-red-300 font-bold rounded-md">
              {t('courses.draft_retry', { defaultValue: 'Retry' })}
            </button>
            {closeBlocked && (
              <button type="button" onClick={closeModal} className="px-3 py-1 border border-red-300 font-bold rounded-md">
                {t('courses.draft_close_anyway', { defaultValue: 'Close without saving' })}
              </button>
            )}
          </div>
        </div>
      )}

      <div className="flex items-center justify-between gap-3 mt-6">
        <span role="status" aria-live="polite" data-testid="draft-status" className="text-xs text-gray-500">
          {statusLabel}
        </span>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => void handleSaveAndClose()}
            disabled={closing || formik.isSubmitting}
            className="px-4 py-2 border border-gray-300 text-sm font-bold rounded-md"
          >
            {closing
              ? t('courses.draft_saving', { defaultValue: 'Saving...' })
              : t('courses.draft_save_and_close', { defaultValue: 'Save & Close' })}
          </button>
          <button
            type="submit"
            disabled={formik.isSubmitting}
            className="px-4 py-2 bg-black text-white text-sm font-bold rounded-md"
          >
            {formik.isSubmitting ? (
              <BarLoader
                cssOverride={{ borderRadius: 60 }}
                width={60}
                color="#ffffff"
              />
            ) : (
              t('courses.create_course_btn')
            )}
          </button>
        </div>
      </div>
    </FormLayout>
  )
}

export default CreateCourseModal
