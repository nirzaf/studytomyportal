export declare const YDOC_STATE_HEADER = "x-ydoc-state";
export declare const YDOC_STATE_EMPTY = "empty";
export declare const YDOC_STATE_PRESENT = "present";
type YdocApiResponse = Pick<Response, 'ok' | 'status' | 'statusText' | 'headers' | 'body'>;
export declare function readBoundedBoardState(body: Response['body'], maxBytes: number): Promise<Uint8Array>;
export declare class BoardStateLoadError extends Error {
    constructor(message: string, options?: ErrorOptions);
}
export interface BoardStateLoaderDependencies {
    maxBytes: number;
    readCache: () => Promise<Uint8Array | null>;
    fetchDatabase: () => Promise<YdocApiResponse>;
    warmCache: (state: Uint8Array) => Promise<void>;
    reportError: (stage: 'redis-read' | 'database-read' | 'redis-warm', error: unknown) => void;
}
/**
 * Loads a board state without treating storage failures or an ambiguous API
 * response as a new, empty document. A null result is reserved for the
 * authenticated internal API's explicit `X-YDoc-State: empty` contract.
 */
export declare function loadBoardState(boardUuid: string, dependencies: BoardStateLoaderDependencies): Promise<Uint8Array | null>;
export {};
