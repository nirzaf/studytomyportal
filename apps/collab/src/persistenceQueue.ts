interface PendingWrite {
  version: number
  state: Uint8Array
  retries: number
  timer?: ReturnType<typeof setTimeout>
  inFlight?: Promise<void>
  inFlightState?: Uint8Array
}

export class PersistenceQueueCapacityError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'PersistenceQueueCapacityError'
  }
}

export function assertSuccessfulYdocWrite(
  response: Pick<Response, 'ok' | 'status'>,
): void {
  if (!response.ok) {
    throw new Error(`Board state API returned ${response.status}`)
  }
}

export interface PersistenceQueueOptions {
  debounceMs: number
  retryDelaysMs: readonly number[]
  requestTimeoutMs: number
  maxPendingDocuments: number
  maxPendingBytes: number
  write: (boardUuid: string, state: Uint8Array, timeoutMs: number) => Promise<void>
  onRetry?: (boardUuid: string, retry: number, delayMs: number, error: unknown) => void
  onExhausted?: (boardUuid: string, error: unknown) => void
  onCapacityExceeded?: (boardUuid: string, error: PersistenceQueueCapacityError) => void
}

/**
 * Keeps the latest state for each board until an API write is acknowledged.
 * Writes for one board are serialized so an older response cannot overtake a
 * newer snapshot. The queue retains failed states for later edits or shutdown.
 */
export class BoardPersistenceQueue {
  private readonly pending = new Map<string, PendingWrite>()

  constructor(private readonly options: PersistenceQueueOptions) {}

  get pendingCount(): number {
    return this.pending.size
  }

  get pendingBoardUuids(): string[] {
    return [...this.pending.keys()]
  }

  get retainedBytes(): number {
    let bytes = 0
    for (const entry of this.pending.values()) bytes += this.bytesFor(entry)
    return bytes
  }

  schedule(boardUuid: string, state: Uint8Array): void {
    const stateCopy = new Uint8Array(state)
    const existing = this.pending.get(boardUuid)

    if (!existing) {
      const error = this.capacityError(boardUuid, stateCopy.byteLength)
      if (error) {
        this.reportCapacity(boardUuid, error)
        throw error
      }

      const entry: PendingWrite = { version: 1, state: stateCopy, retries: 0 }
      this.pending.set(boardUuid, entry)
      this.scheduleFlush(boardUuid, entry, this.options.debounceMs)
      return
    }

    const nextBytes = stateCopy.byteLength + (existing.inFlightState?.byteLength ?? 0)
    const projectedBytes = this.retainedBytes - this.bytesFor(existing) + nextBytes
    if (projectedBytes > this.options.maxPendingBytes) {
      const error = new PersistenceQueueCapacityError(
        `Board persistence queue byte limit reached for ${boardUuid}`,
      )
      this.reportCapacity(boardUuid, error)
      throw error
    }

    if (existing.timer) clearTimeout(existing.timer)
    existing.timer = undefined
    existing.version += 1
    existing.state = stateCopy
    existing.retries = 0

    if (!existing.inFlight) {
      this.scheduleFlush(boardUuid, existing, this.options.debounceMs)
    }
  }

  /** Force queued writes now and wait only up to the supplied shutdown bound. */
  async flushPending(
    deadlineMs: number,
    requestTimeoutMs = this.options.requestTimeoutMs,
  ): Promise<string[]> {
    const deadline = Date.now() + Math.max(0, deadlineMs)
    for (const [boardUuid, entry] of this.pending) {
      if (entry.timer) clearTimeout(entry.timer)
      entry.timer = undefined
      void this.flush(boardUuid, entry, requestTimeoutMs)
    }

    while (this.pending.size > 0) {
      const hasWork = [...this.pending.values()].some(
        (entry) => Boolean(entry.inFlight || entry.timer),
      )
      if (!hasWork) break

      const remaining = deadline - Date.now()
      if (remaining <= 0) break
      await new Promise((resolve) => setTimeout(resolve, Math.min(25, remaining)))
    }

    return this.pendingBoardUuids
  }

  private capacityError(boardUuid: string, newBytes: number): PersistenceQueueCapacityError | null {
    if (this.pending.size >= this.options.maxPendingDocuments) {
      return new PersistenceQueueCapacityError(
        `Board persistence queue document limit reached for ${boardUuid}`,
      )
    }
    if (this.retainedBytes + newBytes > this.options.maxPendingBytes) {
      return new PersistenceQueueCapacityError(
        `Board persistence queue byte limit reached for ${boardUuid}`,
      )
    }
    return null
  }

  private bytesFor(entry: PendingWrite): number {
    return (
      entry.state.byteLength +
      (entry.inFlightState && entry.inFlightState !== entry.state
        ? entry.inFlightState.byteLength
        : 0)
    )
  }

  private scheduleFlush(boardUuid: string, entry: PendingWrite, delayMs: number): void {
    if (entry.timer) clearTimeout(entry.timer)
    entry.timer = setTimeout(() => {
      entry.timer = undefined
      void this.flush(boardUuid, entry)
    }, delayMs)
  }

  private async flush(
    boardUuid: string,
    entry: PendingWrite,
    requestTimeoutMs = this.options.requestTimeoutMs,
  ): Promise<void> {
    if (entry.inFlight) return entry.inFlight
    if (entry.timer) clearTimeout(entry.timer)
    entry.timer = undefined

    const version = entry.version
    const state = entry.state
    entry.inFlightState = state
    let retryDelay: number | null = null

    const inFlight = Promise.resolve().then(async () => {
      try {
        await this.options.write(boardUuid, state, requestTimeoutMs)
        if (this.pending.get(boardUuid) !== entry) return

        if (entry.version === version) {
          this.pending.delete(boardUuid)
        } else {
          entry.retries = 0
          retryDelay = 0
        }
      } catch (error) {
        if (this.pending.get(boardUuid) !== entry) return

        if (entry.version !== version) {
          entry.retries = 0
          retryDelay = 0
        } else if (entry.retries < this.options.retryDelaysMs.length) {
          retryDelay = this.options.retryDelaysMs[entry.retries]
          entry.retries += 1
          this.report(() => this.options.onRetry?.(boardUuid, entry.retries, retryDelay!, error))
        } else {
          this.report(() => this.options.onExhausted?.(boardUuid, error))
        }
      } finally {
        entry.inFlight = undefined
        entry.inFlightState = undefined
        if (this.pending.get(boardUuid) === entry && retryDelay !== null) {
          this.scheduleFlush(boardUuid, entry, retryDelay)
        }
      }
    })

    entry.inFlight = inFlight
    await inFlight
  }

  private report(action: () => void): void {
    try {
      action()
    } catch {
      // Reporting must not lose the retained state or break the retry loop.
    }
  }

  private reportCapacity(boardUuid: string, error: PersistenceQueueCapacityError): void {
    this.report(() => this.options.onCapacityExceeded?.(boardUuid, error))
  }
}
