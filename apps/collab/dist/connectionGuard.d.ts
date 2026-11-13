import type { IncomingMessage } from 'node:http';
import type { Socket } from 'node:net';
/** Set on the upgrade request so onAuthenticate can find its connection. */
export declare const CONNECTION_ID_HEADER = "x-learnhouse-collab-connection";
export declare function createRateLimiter(windowMs: number, max: number): {
    isLimited: (key: string) => boolean;
    sweep: () => void;
};
export declare function clientIp(request: IncomingMessage, trustProxy: boolean): string;
/** Loopback and private peers are almost always a reverse proxy. */
export declare function isPrivateAddress(ip: string): boolean;
export interface ConnectionGuardOptions {
    trustProxy: boolean;
    rateLimitWindowMs: number;
    rateLimitMax: number;
    maxConnectionsPerIp: number;
    maxPendingConnections: number;
    authDeadlineMs: number;
    preAuthMaxBytes: number;
    /** Bytes an authenticated socket may send that never reach a document. */
    maxUnhandledBytes: number;
    /** Bytes of document messages a socket may send per rate limit window. */
    maxHandledBytesPerWindow: number;
}
export declare function createConnectionGuard(opts: ConnectionGuardOptions): {
    admit: (request: IncomingMessage, socket: Socket) => boolean;
    markAuthenticated: (id: string | null) => void;
    markHandled: (id: string | null, bytes: number) => boolean;
    sweep: () => void;
    readonly pendingCount: number;
};
