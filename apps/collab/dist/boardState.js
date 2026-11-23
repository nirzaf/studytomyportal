export const YDOC_STATE_HEADER = 'x-ydoc-state';
export const YDOC_STATE_EMPTY = 'empty';
export const YDOC_STATE_PRESENT = 'present';
export async function readBoundedBoardState(body, maxBytes) {
    if (!body)
        return new Uint8Array();
    const reader = body.getReader();
    const chunks = [];
    let size = 0;
    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done)
                break;
            size += value.byteLength;
            if (size > maxBytes) {
                void reader.cancel().catch(() => { });
                throw new BoardStateLoadError('Stored board state exceeds the size limit');
            }
            chunks.push(value);
        }
    }
    finally {
        reader.releaseLock();
    }
    const state = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {
        state.set(chunk, offset);
        offset += chunk.byteLength;
    }
    return state;
}
export class BoardStateLoadError extends Error {
    constructor(message, options) {
        super(message, options);
        this.name = 'BoardStateLoadError';
    }
}
/**
 * Loads a board state without treating storage failures or an ambiguous API
 * response as a new, empty document. A null result is reserved for the
 * authenticated internal API's explicit `X-YDoc-State: empty` contract.
 */
export async function loadBoardState(boardUuid, dependencies) {
    if (!boardUuid.trim()) {
        throw new BoardStateLoadError('Cannot load board state without a board identifier');
    }
    let cached = null;
    try {
        cached = await dependencies.readCache();
    }
    catch (error) {
        dependencies.reportError('redis-read', error);
    }
    if (cached?.byteLength) {
        if (cached.byteLength > dependencies.maxBytes) {
            const error = new BoardStateLoadError('Cached board state exceeds the size limit');
            dependencies.reportError('redis-read', error);
            throw error;
        }
        return new Uint8Array(cached);
    }
    let response;
    try {
        response = await dependencies.fetchDatabase();
    }
    catch (error) {
        dependencies.reportError('database-read', error);
        throw new BoardStateLoadError('Board state storage is temporarily unavailable', { cause: error });
    }
    if (!response.ok) {
        const error = new Error(`Board state API returned ${response.status} ${response.statusText}`);
        dependencies.reportError('database-read', error);
        throw new BoardStateLoadError('Board state storage is temporarily unavailable', { cause: error });
    }
    let state;
    try {
        state = await readBoundedBoardState(response.body, dependencies.maxBytes);
    }
    catch (error) {
        dependencies.reportError('database-read', error);
        if (error instanceof BoardStateLoadError)
            throw error;
        throw new BoardStateLoadError('Board state response could not be read', { cause: error });
    }
    const stateKind = response.headers.get(YDOC_STATE_HEADER);
    if (stateKind === YDOC_STATE_EMPTY && state.byteLength === 0)
        return null;
    if (stateKind !== YDOC_STATE_PRESENT || state.byteLength === 0) {
        const error = new Error('Board state API returned an invalid state marker or body');
        dependencies.reportError('database-read', error);
        throw new BoardStateLoadError('Board state response was invalid', { cause: error });
    }
    try {
        await dependencies.warmCache(state);
    }
    catch (error) {
        dependencies.reportError('redis-warm', error);
    }
    return state;
}
