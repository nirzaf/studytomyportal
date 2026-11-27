'use client'
import React, { useState } from 'react'
import { WarningCircle, Globe, FloppyDisk, SpinnerGap, ArrowSquareOut } from '@phosphor-icons/react'
import { updateActivity } from '@services/courses/activities'
import { useAuth } from '@components/Contexts/AuthContext'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import toast from 'react-hot-toast'
import { toEmbedUrl } from '@/lib/media/embedUrl'
import { useUrlEmbedSandbox } from '@/lib/media/useUrlEmbedSandbox'
import { safeExternalUrl } from '@services/security/url'
import { useTranslation } from 'react-i18next'

// Video hosts letterbox well at 16:9; documents, forms and slides are made for
// a taller frame, which matters on a portrait phone.
const VIDEO_EMBED = /(youtube(-nocookie)?\.com|youtu\.be|vimeo\.com|loom\.com|wistia\.(com|net)|dailymotion\.com)/i

type GovernedLab = {
  build_version: string
  build_sha256: string
  entry_url: string
  linked_assignment_activity_uuid?: string | null
  submission_formats_note?: string | null
}

interface EmbedActivityProps {
  activity: any
  editable?: boolean
  style?: React.CSSProperties
  virtualLabOptions?: GovernedLab[] | null
}

function GovernedLabEditor({ activity, options }: { activity: any; options: GovernedLab[] }) {
  const { t } = useTranslation()
  const { accessToken } = useAuth()
  const [saved, setSaved] = useState<GovernedLab | undefined>(activity.content?.virtual_lab)
  const [version, setVersion] = useState(activity.current_version || 1)
  const [choice, setChoice] = useState('')
  const [saving, setSaving] = useState(false)
  const selected = choice ? options[Number(choice)] : undefined

  async function save() {
    if (!selected || !accessToken) return
    setSaving(true)
    try {
      const response = await updateActivity({
        content: { virtual_lab: {
          ...selected,
          linked_assignment_activity_uuid: saved?.linked_assignment_activity_uuid ?? null,
          submission_formats_note: saved?.submission_formats_note ?? null,
        } },
        expected_current_version: version,
      }, activity.activity_uuid, accessToken)
      if (!response.success || !response.data?.content?.virtual_lab) {
        throw new Error('Lab draft update failed')
      }
      setSaved(response.data.content.virtual_lab)
      setVersion(response.data.current_version)
      setChoice('')
      toast.success('Lab draft saved')
    } catch {
      toast.error('Could not save the lab draft. Reload the editor and try again.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="mx-auto max-w-xl space-y-4 rounded-xl border bg-white p-5">
      <h2 className="text-lg font-semibold">Virtual science lab</h2>
      <p className="text-sm text-gray-600">Save a reviewed simulator build to the draft. Learners receive it through the course review and release workflow.</p>
      {saved && <p className="text-sm">Saved build: {saved.build_version}</p>}
      {options.length ? (
        <div className="space-y-2">
          <label className="block text-sm font-medium" htmlFor="virtual-lab-build">Simulator build</label>
          <select id="virtual-lab-build" value={choice} onChange={event => setChoice(event.target.value)}
            disabled={saving} className="w-full rounded-lg border bg-white px-3 py-2 text-sm">
            <option value="">Select a build</option>
            {options.map((option, index) => (
              <option key={option.entry_url} value={index}>
                Conservation of momentum · {option.build_version} · {new URL(option.entry_url).host}
              </option>
            ))}
          </select>
          <button type="button" onClick={save} disabled={!selected || !accessToken || saving}
            className="rounded-lg bg-black px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
            {saving ? 'Saving…' : t('common.save')}
          </button>
        </div>
      ) : <p className="text-sm" role="status">No reviewed simulator builds are configured.</p>}
    </section>
  )
}

function UrlEmbedActivity({ activity, editable = false, style }: EmbedActivityProps) {
  const { t } = useTranslation()
  const session = useLHSession() as any
  const access_token = session?.data?.tokens?.access_token
  const embedUrl = activity.content?.embed_url || ''

  const [editUrl, setEditUrl] = useState(embedUrl)
  const [saving, setSaving] = useState(false)
  const [, setError] = useState(!embedUrl)

  const handleSaveUrl = async () => {
    const validatedUrl = safeExternalUrl(editUrl)
    if (!validatedUrl) {
      toast.error(t('activities.embed_activity.invalid_url'))
      return
    }
    setSaving(true)
    try {
      const response = await updateActivity(
        {
          content: { embed_url: validatedUrl },
          expected_current_version: activity.current_version || 1,
        },
        activity.activity_uuid,
        access_token
      )
      if (!response.success) throw new Error('Embed update failed')
      toast.success(t('activities.embed_activity.updated'))
      setError(false)
    } catch {
      toast.error(t('activities.embed_activity.update_failed'))
    } finally {
      setSaving(false)
    }
  }

  const displayUrl = safeExternalUrl(editable ? editUrl : embedUrl)
  const iframeSrc = displayUrl ? toEmbedUrl(displayUrl) : ''
  const iframeSandbox = useUrlEmbedSandbox(iframeSrc)

  if (!displayUrl && !editable) {
    return (
      <div className="flex flex-col items-center justify-center py-20 gap-4">
        <WarningCircle size={40} className="text-red-400" />
        <p className="text-sm text-gray-600">{t('activities.embed_activity.no_url')}</p>
      </div>
    )
  }

  return (
    <div className="w-full px-0 py-1 sm:px-6 sm:py-6" style={style}>
      {editable && (
        <div className="mb-6 flex items-center gap-3">
          <Globe size={20} weight="duotone" className="text-cyan-400 flex-shrink-0" />
          <input
            type="url"
            value={editUrl}
            onChange={(e) => setEditUrl(e.target.value)}
            placeholder="https://docs.google.com/document/d/..."
            className="flex-1 h-9 px-3 text-sm rounded-lg bg-gray-50 border border-gray-200 outline-none focus:border-gray-300 focus:ring-1 focus:ring-gray-200 transition-colors"
          />
          <button
            onClick={handleSaveUrl}
            disabled={saving || !safeExternalUrl(editUrl) || editUrl.trim() === embedUrl}
            className="inline-flex items-center gap-2 h-9 px-4 text-sm font-medium text-white bg-black rounded-lg hover:bg-gray-800 transition-colors disabled:opacity-50 flex-shrink-0"
          >
            {saving ? (
              <SpinnerGap size={16} className="animate-spin" />
            ) : (
              <FloppyDisk size={16} />
            )}
            {t('common.save')}
          </button>
        </div>
      )}

      {displayUrl && !editable && !VIDEO_EMBED.test(iframeSrc) && (
        // Embedded pages are cramped on a phone; offer the real thing too.
        <div className="md:hidden mb-3 flex justify-end">
          <a
            href={displayUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="lh-press inline-flex h-11 items-center gap-1.5 rounded-full bg-gray-100 px-4 text-sm font-medium text-gray-700"
          >
            <ArrowSquareOut size={16} />
            {t('activities.resource.open')}
          </a>
        </div>
      )}

      {displayUrl ? (
        <div
          className={`w-full rounded-xl overflow-hidden nice-shadow aspect-video ${VIDEO_EMBED.test(iframeSrc) ? '' : 'max-md:aspect-[4/5]'}`}
        >
          {iframeSandbox && <iframe
            src={iframeSrc}
            className="w-full h-full border-0"
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; fullscreen"
            allowFullScreen
            sandbox={iframeSandbox}
          />}
        </div>
      ) : (
        <div className="flex flex-col items-center justify-center py-20 gap-3 rounded-xl border-2 border-dashed border-gray-200">
          <Globe size={32} weight="duotone" className="text-gray-300" />
          <p className="text-sm text-gray-400">{t('activities.embed_activity.enter_url_hint')}</p>
        </div>
      )}
    </div>
  )
}

export default function EmbedActivity(props: EmbedActivityProps) {
  if (props.editable && Array.isArray(props.virtualLabOptions)) {
    return <GovernedLabEditor key={props.activity.activity_uuid} activity={props.activity} options={props.virtualLabOptions} />
  }
  return <UrlEmbedActivity {...props} />
}
