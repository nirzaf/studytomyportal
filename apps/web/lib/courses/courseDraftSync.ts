// Framework-free autosave controller for the Create Course draft.
//
// It owns the only copy of the draft's optimistic-concurrency `revision`,
// serialises writes (never two in flight), coalesces rapid edits, keeps
// unsaved edits queued across failures, and stops on a conflict instead of
// overwriting another tab's work. The React component just feeds it values.

export type CourseDraft = {
  draft_uuid: string
  name: string
  description: string
  learnings: string
  tags: string
  public: boolean
  has_thumbnail: boolean
  thumbnail_content_type: string | null
  revision: number
  created_at: string
  updated_at: string
}

export type DraftFields = {
  name: string
  description: string
  learnings: string
  tags: string
  public: boolean
}

export type SaveResult =
  | { ok: true; draft: CourseDraft }
  | { ok: false; conflict: true; current: CourseDraft | null }
  | { ok: false; conflict: false; error: string }

export interface DraftApi {
  saveFields(fields: DraftFields, baseRevision: number | null): Promise<SaveResult>
  uploadThumbnail(file: File, baseRevision: number | null): Promise<SaveResult>
}

export type SyncStatus = 'idle' | 'saving' | 'saved' | 'error' | 'conflict'

export type SyncState = {
  status: SyncStatus
  error: string | null
  conflictDraft: CourseDraft | null
}

export const draftFieldsOf = (draft: CourseDraft): DraftFields => ({
  name: draft.name,
  description: draft.description,
  learnings: draft.learnings,
  tags: draft.tags,
  public: draft.public,
})

export const sameDraftFields = (a: DraftFields, b: DraftFields) =>
  a.name === b.name &&
  a.description === b.description &&
  a.learnings === b.learnings &&
  a.tags === b.tags &&
  a.public === b.public

export const isBlankDraftFields = (f: DraftFields) =>
  !f.name.trim() && !f.description.trim() && !f.learnings.trim() && !f.tags.trim()

export const isBlankDraft = (draft: CourseDraft) =>
  isBlankDraftFields(draftFieldsOf(draft)) && !draft.has_thumbnail

export class CourseDraftSync {
  private draftUuid: string | null = null
  private revision: number | null = null
  private lastSaved: DraftFields | null = null
  private inFlightFields: DraftFields | null = null
  private pendingFields: DraftFields | null = null
  private pendingThumb: File | null = null
  private debounceTimer: ReturnType<typeof setTimeout> | null = null
  private retryTimer: ReturnType<typeof setTimeout> | null = null
  private running: Promise<void> | null = null
  private paused = false
  state: SyncState = { status: 'idle', error: null, conflictDraft: null }

  constructor(
    private api: DraftApi,
    private onState: (state: SyncState) => void = () => {},
    private debounceMs = 800,
    private retryMs = 5000
  ) {}

  currentDraftUuid(): string | null {
    return this.draftUuid
  }

  private set(status: SyncStatus, error: string | null = null, conflictDraft: CourseDraft | null = null) {
    this.state = { status, error, conflictDraft }
    this.onState(this.state)
  }

  private clearTimers() {
    if (this.debounceTimer) clearTimeout(this.debounceTimer)
    if (this.retryTimer) clearTimeout(this.retryTimer)
    this.debounceTimer = null
    this.retryTimer = null
  }

  private get hasPending() {
    return this.pendingFields !== null || this.pendingThumb !== null
  }

  /** Adopt the server's copy (or null = no draft yet); drops queued edits. */
  hydrate(draft: CourseDraft | null) {
    this.clearTimers()
    this.draftUuid = draft ? draft.draft_uuid : null
    this.revision = draft ? draft.revision : null
    this.lastSaved = draft ? draftFieldsOf(draft) : null
    this.pendingFields = null
    this.pendingThumb = null
    this.set('idle')
  }

  hasUnsaved() {
    return this.hasPending || this.state.status === 'error' || this.state.status === 'conflict'
  }

  getRevision() {
    return this.revision
  }

  /** Queue the current form values; a blank form never creates a draft. */
  schedule(fields: DraftFields) {
    // Only skip when no field write is in flight: if an older write (e.g. B) is
    // still in flight and the user reverts back to lastSaved (A), we MUST keep
    // A queued so it is written after B acknowledges.
    if (!this.inFlightFields && this.lastSaved && sameDraftFields(fields, this.lastSaved)) {
      this.pendingFields = null
      if (!this.pendingThumb && this.state.status !== 'conflict') this.set('saved')
      return
    }
    if (!this.inFlightFields && this.revision === null && isBlankDraftFields(fields)) {
      this.pendingFields = null
      return
    }
    this.pendingFields = fields
    if (this.state.status === 'conflict') return
    this.set('saving')
    if (this.debounceTimer) clearTimeout(this.debounceTimer)
    this.debounceTimer = setTimeout(() => void this.kick(), this.debounceMs)
  }

  /** Queue a thumbnail upload (any source: upload, paste, AI). Starts at once. */
  setThumbnail(file: File) {
    this.pendingThumb = file
    if (this.state.status === 'conflict') return
    this.set('saving')
    void this.kick()
  }

  private kick(): Promise<void> {
    if (this.paused || this.state.status === 'conflict') return this.running ?? Promise.resolve()
    if (!this.running) {
      this.running = this.loop().finally(() => {
        this.running = null
      })
    }
    return this.running
  }

  private async loop() {
    if (this.retryTimer) clearTimeout(this.retryTimer)
    this.retryTimer = null
    while (this.hasPending && !this.paused) {
      this.set('saving')
      let result: SaveResult
      let sentFields: DraftFields | null = null
      let sentThumb: File | null = null
      try {
        if (this.pendingFields) {
          sentFields = this.pendingFields
          this.inFlightFields = sentFields
          result = await this.api.saveFields(sentFields, this.revision)
        } else {
          sentThumb = this.pendingThumb as File
          result = await this.api.uploadThumbnail(sentThumb, this.revision)
        }
      } catch (_e) {
        result = { ok: false, conflict: false, error: 'Network error' }
      } finally {
        this.inFlightFields = null
      }
      if (result.ok) {
        this.draftUuid = result.draft.draft_uuid
        this.revision = result.draft.revision
        if (sentFields) {
          this.lastSaved = sentFields
          if (
            this.pendingFields === sentFields ||
            (this.pendingFields !== null && sameDraftFields(this.pendingFields, sentFields))
          ) {
            this.pendingFields = null
          }
        } else if (this.pendingThumb === sentThumb) {
          this.pendingThumb = null
        }
        continue
      }
      if (result.conflict) {
        this.set('conflict', null, result.current)
      } else {
        this.set('error', result.error)
        this.retryTimer = setTimeout(() => void this.kick(), this.retryMs)
      }
      return
    }
    if (!this.hasPending && !this.paused) this.set('saved')
  }

  /** Persist everything queued now. True only when nothing is left unsaved. */
  async flush(fields?: DraftFields): Promise<boolean> {
    // Callers that close or navigate away can pass the current form snapshot
    // so correctness does not depend on a React effect having queued it first.
    if (fields) this.schedule(fields)
    if (this.debounceTimer) clearTimeout(this.debounceTimer)
    this.debounceTimer = null
    for (let attempt = 0; attempt < 2 && this.hasPending && this.state.status !== 'conflict'; attempt++) {
      await this.kick()
      if (this.state.status === 'error') break // one attempt per flush; the caller decides to retry
    }
    return !this.hasPending && this.state.status !== 'conflict' && this.state.status !== 'error'
  }

  /** Stop writing (e.g. while the course is being created) and wait for any in-flight write. */
  async pause() {
    this.paused = true
    this.clearTimers()
    if (this.running) await this.running
  }

  resume() {
    this.paused = false
    if (this.hasPending) void this.kick()
  }

  /** "reload" adopts the other tab's draft; "overwrite" re-bases this tab's edits on it. */
  resolveConflict(choice: 'reload' | 'overwrite'): CourseDraft | null {
    const current = this.state.conflictDraft
    if (choice === 'reload') {
      this.hydrate(current)
      return current
    }
    this.draftUuid = current ? current.draft_uuid : null
    this.revision = current ? current.revision : null
    this.set('saving')
    void this.kick()
    return null
  }

  /** Called on unmount: stop reporting state, but still try to persist queued edits. */
  detach() {
    this.onState = () => {}
    this.clearTimers()
    if (this.hasPending && this.state.status !== 'conflict' && !this.paused) void this.kick()
  }
}
