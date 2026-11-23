export class PersistenceQueueCapacityError extends Error {
    constructor(message) {
        super(message);
        this.name = 'PersistenceQueueCapacityError';
    }
}
export function assertSuccessfulYdocWrite(response) {
    if (!response.ok) {
        throw new Error(`Board state API returned ${response.status}`);
    }
}
/**
 * Keeps the latest state for each board until an API write is acknowledged.
 * Writes for one board are serialized so an older response cannot overtake a
 * newer snapshot. The queue retains failed states for later edits or shutdown.
 */
export class BoardPersistenceQueue {
    options;
    pending = new Map();
    constructor(options) {
        this.options = options;
    }
    get pendingCount() {
        return this.pending.size;
    }
    get pendingBoardUuids() {
        return [...this.pending.keys()];
    }
    get retainedBytes() {
        let bytes = 0;
        for (const entry of this.pending.values())
            bytes += this.bytesFor(entry);
        return bytes;
    }
    schedule(boardUuid, state) {
        const stateCopy = new Uint8Array(state);
        const existing = this.pending.get(boardUuid);
        if (!existing) {
            const error = this.capacityError(boardUuid, stateCopy.byteLength);
            if (error) {
                this.reportCapacity(boardUuid, error);
                throw error;
            }
            const entry = { version: 1, state: stateCopy, retries: 0 };
            this.pending.set(boardUuid, entry);
            this.scheduleFlush(boardUuid, entry, this.options.debounceMs);
            return;
        }
        const nextBytes = stateCopy.byteLength + (existing.inFlightState?.byteLength ?? 0);
        const projectedBytes = this.retainedBytes - this.bytesFor(existing) + nextBytes;
        if (projectedBytes > this.options.maxPendingBytes) {
            const error = new PersistenceQueueCapacityError(`Board persistence queue byte limit reached for ${boardUuid}`);
            this.reportCapacity(boardUuid, error);
            throw error;
        }
        if (existing.timer)
            clearTimeout(existing.timer);
        existing.timer = undefined;
        existing.version += 1;
        existing.state = stateCopy;
        existing.retries = 0;
        if (!existing.inFlight) {
            this.scheduleFlush(boardUuid, existing, this.options.debounceMs);
        }
    }
    /** Force queued writes now and wait only up to the supplied shutdown bound. */
    async flushPending(deadlineMs, requestTimeoutMs = this.options.requestTimeoutMs) {
        const deadline = Date.now() + Math.max(0, deadlineMs);
        for (const [boardUuid, entry] of this.pending) {
            if (entry.timer)
                clearTimeout(entry.timer);
            entry.timer = undefined;
            void this.flush(boardUuid, entry, requestTimeoutMs);
        }
        while (this.pending.size > 0) {
            const hasWork = [...this.pending.values()].some((entry) => Boolean(entry.inFlight || entry.timer));
            if (!hasWork)
                break;
            const remaining = deadline - Date.now();
            if (remaining <= 0)
                break;
            await new Promise((resolve) => setTimeout(resolve, Math.min(25, remaining)));
        }
        return this.pendingBoardUuids;
    }
    capacityError(boardUuid, newBytes) {
        if (this.pending.size >= this.options.maxPendingDocuments) {
            return new PersistenceQueueCapacityError(`Board persistence queue document limit reached for ${boardUuid}`);
        }
        if (this.retainedBytes + newBytes > this.options.maxPendingBytes) {
            return new PersistenceQueueCapacityError(`Board persistence queue byte limit reached for ${boardUuid}`);
        }
        return null;
    }
    bytesFor(entry) {
        return (entry.state.byteLength +
            (entry.inFlightState && entry.inFlightState !== entry.state
                ? entry.inFlightState.byteLength
                : 0));
    }
    scheduleFlush(boardUuid, entry, delayMs) {
        if (entry.timer)
            clearTimeout(entry.timer);
        entry.timer = setTimeout(() => {
            entry.timer = undefined;
            void this.flush(boardUuid, entry);
        }, delayMs);
    }
    async flush(boardUuid, entry, requestTimeoutMs = this.options.requestTimeoutMs) {
        if (entry.inFlight)
            return entry.inFlight;
        if (entry.timer)
            clearTimeout(entry.timer);
        entry.timer = undefined;
        const version = entry.version;
        const state = entry.state;
        entry.inFlightState = state;
        let retryDelay = null;
        const inFlight = Promise.resolve().then(async () => {
            try {
                await this.options.write(boardUuid, state, requestTimeoutMs);
                if (this.pending.get(boardUuid) !== entry)
                    return;
                if (entry.version === version) {
                    this.pending.delete(boardUuid);
                }
                else {
                    entry.retries = 0;
                    retryDelay = 0;
                }
            }
            catch (error) {
                if (this.pending.get(boardUuid) !== entry)
                    return;
                if (entry.version !== version) {
                    entry.retries = 0;
                    retryDelay = 0;
                }
                else if (entry.retries < this.options.retryDelaysMs.length) {
                    retryDelay = this.options.retryDelaysMs[entry.retries];
                    entry.retries += 1;
                    this.report(() => this.options.onRetry?.(boardUuid, entry.retries, retryDelay, error));
                }
                else {
                    this.report(() => this.options.onExhausted?.(boardUuid, error));
                }
            }
            finally {
                entry.inFlight = undefined;
                entry.inFlightState = undefined;
                if (this.pending.get(boardUuid) === entry && retryDelay !== null) {
                    this.scheduleFlush(boardUuid, entry, retryDelay);
                }
            }
        });
        entry.inFlight = inFlight;
        await inFlight;
    }
    report(action) {
        try {
            action();
        }
        catch {
            // Reporting must not lose the retained state or break the retry loop.
        }
    }
    reportCapacity(boardUuid, error) {
        this.report(() => this.options.onCapacityExceeded?.(boardUuid, error));
    }
}
