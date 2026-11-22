import type Redis from 'ioredis';
export declare function preservePendingBoardSnapshot(pending: Uint8Array, maxBytes: number, readPending: () => Promise<Uint8Array | null>, replacePending: (previous: Uint8Array | null, next: Uint8Array) => Promise<boolean>): Promise<Uint8Array>;
export declare function cachePendingBoardSnapshot(redis: Pick<Redis, 'getBuffer' | 'eval'>, pendingKey: string, cacheKey: string, state: Uint8Array, maxBytes: number, ttlSeconds: number): Promise<Uint8Array>;
export declare function writeBoardSnapshot(pending: Uint8Array, maxBytes: number, request: (init: RequestInit) => Promise<Response>): Promise<Uint8Array>;
