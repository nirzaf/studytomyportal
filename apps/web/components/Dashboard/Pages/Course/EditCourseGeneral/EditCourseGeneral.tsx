'use client'
import FormLayout, {
  FormField,
  FormLabelAndMessage,
  Input,
  Textarea,
} from '@components/Objects/StyledElements/Form/Form';
import { useFormik } from 'formik';
import { AlertTriangle, Globe, LockKeyhole } from 'lucide-react';
import * as Form from '@radix-ui/react-form';
import React, { useEffect, useState, useRef, useCallback, useMemo } from 'react';
import ThumbnailUpdate from './ThumbnailUpdate';
import { useCourseFieldSync } from '@components/Contexts/CourseContext';
import FormTagInput from '@components/Objects/StyledElements/Form/TagInput';
import LearningItemsList from './LearningItemsList';
import { useTranslation } from 'react-i18next';

type EditCourseStructureProps = {
  orgslug: string
  course_uuid?: string
}

const validate = (values: any, t: any) => {
  const errors = {} as any;

  if (!values.name) {
    errors.name = t('dashboard.courses.general.form.name_required');
  } else if (values.name.length > 100) {
    errors.name = t('dashboard.courses.general.form.name_max_length');
  }

  if (!values.description) {
    errors.description = t('dashboard.courses.general.form.description_required');
  } else if (values.description.length > 1000) {
    errors.description = t('dashboard.courses.general.form.description_max_length');
  }

  if (!values.learnings) {
    errors.learnings = t('dashboard.courses.general.form.learnings_required');
  } else {
    try {
      const learningItems = JSON.parse(values.learnings);
      if (!Array.isArray(learningItems)) {
        errors.learnings = t('dashboard.courses.general.form.learnings_invalid_format');
      } else if (learningItems.length === 0) {
        errors.learnings = t('dashboard.courses.general.form.learnings_min_items');
      } else {
        // Check if any item has empty text
        const hasEmptyText = learningItems.some(item => !item.text || item.text.trim() === '');
        if (hasEmptyText) {
          errors.learnings = t('dashboard.courses.general.form.learnings_empty_text');
        }
      }
    } catch (e) {
      errors.learnings = t('dashboard.courses.general.form.learnings_invalid_json');
    }
  }

  return errors;
};

const fieldClassName = 'rounded-lg border border-gray-200 bg-white text-gray-900 shadow-none placeholder:text-gray-400 hover:border-gray-300 focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2';

function EditCourseGeneral(props: EditCourseStructureProps) {
  const { t } = useTranslation()
  const [error, setError] = useState('');

  // Use the new field sync hook
  const {
    syncChanges,
    courseStructure,
    isLoading,
    isSaving,
  } = useCourseFieldSync('editCourseGeneral');

  const previousValuesRef = useRef<any>(null);

  // Initialize learnings as a JSON array if it's not already
  const initializeLearnings = useCallback((learnings: any) => {
    if (!learnings) {
      return JSON.stringify([{ id: 'default-1', text: '', emoji: '📝' }]);
    }

    try {
      const parsed = JSON.parse(learnings);
      if (Array.isArray(parsed)) {
        return learnings;
      }
      if (typeof learnings === 'string') {
        return JSON.stringify([{
          id: 'default-1',
          text: learnings,
          emoji: '📝'
        }]);
      }
      return JSON.stringify([{ id: 'default-1', text: '', emoji: '📝' }]);
    } catch (e) {
      if (typeof learnings === 'string') {
        return JSON.stringify([{
          id: 'default-1',
          text: learnings,
          emoji: '📝'
        }]);
      }
      return JSON.stringify([{ id: 'default-1', text: '', emoji: '📝' }]);
    }
  }, []);

  // Memoize initial values to prevent unnecessary recalculations
  const initialValues = useMemo(() => {
    const thumbnailType = courseStructure?.thumbnail_type || 'image';
    return {
      name: courseStructure?.name || '',
      description: courseStructure?.description || '',
      about: courseStructure?.about || '',
      learnings: initializeLearnings(courseStructure?.learnings || ''),
      tags: courseStructure?.tags || '',
      public: courseStructure?.public || false,
      thumbnail_type: thumbnailType,
    };
  }, [courseStructure?.name, courseStructure?.description, courseStructure?.about,
      courseStructure?.learnings, courseStructure?.tags, courseStructure?.public,
      courseStructure?.thumbnail_type, initializeLearnings]);

  const formik = useFormik({
    initialValues,
    validate: (values) => validate(values, t),
    onSubmit: async values => {
      // The actual save is handled by SaveState component
      // This is just for form validation purposes
    },
    enableReinitialize: true,
  }) as any;

  // Sync form changes to context; compare against formik.initialValues
  // so that reinitialization from server data is never treated as a user edit
  useEffect(() => {
    if (isLoading || isSaving) return;

    // Compare current values against formik's own initialValues.
    // When enableReinitialize triggers, both update together → no false diff.
    const changes: any = {};
    Object.keys(formik.values).forEach(key => {
      if (formik.values[key] !== formik.initialValues[key]) {
        changes[key] = formik.values[key];
      }
    });

    const hasChanges = Object.keys(changes).length > 0;

    // Only sync when there are actual user changes AND values changed since last sync
    if (hasChanges) {
      const changesStr = JSON.stringify(changes);
      const prevStr = JSON.stringify(previousValuesRef.current);
      if (changesStr !== prevStr) {
        previousValuesRef.current = changes;
        syncChanges(changes);
      }
    } else {
      previousValuesRef.current = null;
    }
  }, [formik.values, formik.initialValues, isLoading, isSaving, syncChanges]);

  // useCourseFieldSync handles the unmount cleanup (flushing pending edits
  // instead of discarding them), so no local cleanup is needed here.

  if (isLoading || !courseStructure) {
    return (
      <div className="h-full">
        <div className="mx-auto max-w-7xl px-4 pt-4 pb-10 sm:px-8 lg:px-10">
          <div className="mb-6 space-y-2">
            <div className="h-7 w-44 animate-pulse rounded bg-gray-200" />
            <div className="h-4 w-72 max-w-full animate-pulse rounded bg-gray-100" />
          </div>
          <div className="grid animate-pulse gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
            <div className="space-y-6 rounded-xl border border-gray-200 bg-white p-5 sm:p-6">
              <div className="h-5 w-40 rounded bg-gray-200" />
              {[1, 2, 3, 4, 5].map((i) => (
                <div key={i} className="space-y-2">
                  <div className="h-3.5 w-28 rounded bg-gray-200" />
                  <div className={`${i === 3 ? 'h-36' : 'h-10'} w-full rounded-lg bg-gray-100`} />
                </div>
              ))}
            </div>
            <div className="space-y-4">
              <div className="h-64 rounded-xl border border-gray-200 bg-white" />
              <div className="h-36 rounded-xl border border-gray-200 bg-white" />
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full">
      <div className="mx-auto max-w-7xl px-4 pt-4 pb-10 sm:px-8 lg:px-10">
        <header className="mb-6">
          <h1 className="text-2xl font-semibold tracking-tight text-gray-950">
            {t('dashboard.courses.general.form.editor_title')}
          </h1>
          <p className="mt-1.5 text-sm text-gray-600">
            {t('dashboard.courses.general.form.editor_subtitle')}
          </p>
          <p className="mt-2 text-xs font-medium text-gray-500">
            {t('dashboard.courses.general.form.autosave_note')}
          </p>
        </header>

        <FormLayout
          onSubmit={formik.handleSubmit}
          className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_360px]"
        >
          {error && (
            <div className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 p-4 text-sm font-medium text-red-900 lg:col-span-2">
              <AlertTriangle size={18} aria-hidden="true" />
              <div>{error}</div>
            </div>
          )}

          <section aria-labelledby="course-information-heading" className="min-w-0 rounded-xl border border-gray-200 bg-white p-5 shadow-xs sm:p-6">
            <div className="mb-6 border-b border-gray-100 pb-4">
              <h2 id="course-information-heading" className="text-base font-semibold text-gray-900">
                {t('dashboard.courses.general.form.course_information_heading')}
              </h2>
              <p className="mt-1 text-sm text-gray-500">
                {t('dashboard.courses.general.form.course_information_hint')}
              </p>
            </div>

            <div className="space-y-6">
              <FormField name="name">
                <FormLabelAndMessage label={t('dashboard.courses.general.form.name_label')} message={formik.errors.name} />
                <Form.Control asChild>
                  <Input
                    className={fieldClassName}
                    name="name"
                    onChange={formik.handleChange}
                    value={formik.values.name}
                    type="text"
                    maxLength={100}
                    autoComplete="off"
                    required
                    disabled={isSaving}
                  />
                </Form.Control>
                <p className="text-end text-xs text-gray-500">
                  {t('dashboard.courses.general.form.character_count', {
                    count: formik.values.name.length,
                    max: 100,
                  })}
                </p>
              </FormField>

              <FormField name="description">
                <FormLabelAndMessage label={t('dashboard.courses.general.form.description_label')} message={formik.errors.description} />
                <Form.Control asChild>
                  <Textarea
                    className={fieldClassName}
                    name="description"
                    onChange={formik.handleChange}
                    value={formik.values.description}
                    rows={3}
                    maxLength={1000}
                    required
                    disabled={isSaving}
                  />
                </Form.Control>
                <p className="text-end text-xs text-gray-500">
                  {t('dashboard.courses.general.form.character_count', {
                    count: formik.values.description.length,
                    max: 1000,
                  })}
                </p>
              </FormField>

              <FormField name="about">
                <FormLabelAndMessage label={t('dashboard.courses.general.form.about_label')} message={formik.errors.about} />
                <Form.Control asChild>
                  <Textarea
                    className={fieldClassName}
                    name="about"
                    style={{ backgroundColor: 'white', height: '180px', minHeight: '180px' }}
                    onChange={formik.handleChange}
                    value={formik.values.about}
                    required
                    disabled={isSaving}
                  />
                </Form.Control>
              </FormField>

              <FormField name="learnings">
                <FormLabelAndMessage label={t('dashboard.courses.general.form.learnings_label')} message={formik.touched.learnings ? formik.errors.learnings : undefined} />
                <p className="mb-2 text-sm text-gray-500">
                  {t('dashboard.courses.general.form.learnings_hint')}
                </p>
                <Form.Control asChild>
                  <LearningItemsList
                    value={formik.values.learnings}
                    onChange={(value) => {
                      formik.setFieldTouched('learnings', true, false)
                      formik.setFieldValue('learnings', value)
                    }}
                    error={formik.touched.learnings ? formik.errors.learnings : undefined}
                  />
                </Form.Control>
              </FormField>

              <FormField name="tags">
                <FormLabelAndMessage label={t('dashboard.courses.general.form.tags_label')} message={formik.errors.tags} />
                <p className="mb-2 text-sm text-gray-500">
                  {t('dashboard.courses.general.form.tags_hint')}
                </p>
                <Form.Control asChild>
                  <FormTagInput
                    placeholder={t('dashboard.courses.general.form.tags_placeholder')}
                    onChange={(value) => formik.setFieldValue('tags', value)}
                    value={formik.values.tags}
                  />
                </Form.Control>
              </FormField>
            </div>
          </section>

          <aside className="space-y-4">
            <section aria-labelledby="course-thumbnail-heading" className="min-w-0 rounded-xl border border-gray-200 bg-white p-4 shadow-xs sm:p-5">
              <div className="mb-4">
                <h2 id="course-thumbnail-heading" className="text-base font-semibold text-gray-900">
                  {t('dashboard.courses.general.form.thumbnail_label')}
                </h2>
                <p className="mt-1 text-sm text-gray-500">
                  {t('dashboard.courses.general.form.thumbnail_hint')}
                </p>
              </div>
              <FormField name="thumbnail_type" className="mb-4">
                <FormLabelAndMessage label={t('dashboard.courses.general.form.thumbnail_type_label')} />
                <Form.Control asChild>
                  <select
                    name="thumbnail_type"
                    value={formik.values.thumbnail_type}
                    onChange={(event) => formik.setFieldValue('thumbnail_type', event.target.value)}
                    disabled={isSaving}
                    className={`${fieldClassName} mt-1 h-10 cursor-pointer px-3`}
                  >
                    <option value="image">{t('dashboard.courses.general.form.thumbnail_type_image')}</option>
                    <option value="video">{t('dashboard.courses.general.form.thumbnail_type_video')}</option>
                    <option value="both">{t('dashboard.courses.general.form.thumbnail_type_both')}</option>
                  </select>
                </Form.Control>
              </FormField>
              <ThumbnailUpdate thumbnailType={formik.values.thumbnail_type} />
            </section>

            <section aria-labelledby="course-access-heading" className="rounded-xl border border-gray-200 bg-white p-4 shadow-xs sm:p-5">
              <div className="flex items-center gap-2">
                {formik.values.public ? (
                  <Globe className="size-4 text-gray-500" aria-hidden="true" />
                ) : (
                  <LockKeyhole className="size-4 text-gray-500" aria-hidden="true" />
                )}
                <h2 id="course-access-heading" className="text-base font-semibold text-gray-900">
                  {t('dashboard.courses.general.form.visibility_heading')}
                </h2>
              </div>
              <div className="mt-3 flex items-center gap-2">
                <span className="rounded-full bg-gray-100 px-2.5 py-1 text-xs font-semibold text-gray-700">
                  {formik.values.public
                    ? t('dashboard.courses.access.public.title')
                    : t('dashboard.courses.access.users_only.title')}
                </span>
              </div>
              <p className="mt-2 text-sm leading-5 text-gray-600">
                {formik.values.public
                  ? t('dashboard.courses.general.form.visibility_public_summary')
                  : t('dashboard.courses.general.form.visibility_private_summary')}
              </p>
              <p className="mt-3 border-t border-gray-100 pt-3 text-xs text-gray-500">
                {t('dashboard.courses.general.form.visibility_hint')}
              </p>
            </section>
          </aside>
        </FormLayout>
      </div>
    </div>
  );
}

export default EditCourseGeneral;
