export declare class PersistenceQueueCapacityError extends Error {
    constructor(message: string);
}
export declare function assertSuccessfulYdocWrite(response: Pick<Response, 'ok' | 'status'>): void;
export interface PersistenceQueueOptions {
    debounceMs: number;
    retryDelaysMs: readonly number[];
    requestTimeoutMs: number;
    maxPendingDocuments: number;
    maxPendingBytes: number;
    write: (boardUuid: string, state: Uint8Array, timeoutMs: number) => Promise<void>;
    onRetry?: (boardUuid: string, retry: number, delayMs: number, error: unknown) => void;
    onExhausted?: (boardUuid: string, error: unknown) => void;
    onCapacityExceeded?: (boardUuid: string, error: PersistenceQueueCapacityError) => void;
}
/**
 * Keeps the latest state for each board until an API write is acknowledged.
 * Writes for one board are serialized so an older response cannot overtake a
 * newer snapshot. The queue retains failed states for later edits or shutdown.
 */
export declare class BoardPersistenceQueue {
    private readonly options;
    private readonly pending;
    constructor(options: PersistenceQueueOptions);
    get pendingCount(): number;
    get pendingBoardUuids(): string[];
    get retainedBytes(): number;
    schedule(boardUuid: string, state: Uint8Array): void;
    /** Force queued writes now and wait only up to the supplied shutdown bound. */
    flushPending(deadlineMs: number, requestTimeoutMs?: number): Promise<string[]>;
    private capacityError;
    private bytesFor;
    private scheduleFlush;
    private flush;
    private report;
    private reportCapacity;
}
