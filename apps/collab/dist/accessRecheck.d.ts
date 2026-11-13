/**
 * "expired" means the token the socket authenticated with can no longer be
 * used to re-check; the client must reconnect with a fresh one. "unknown"
 * means the check itself failed (API unreachable or erroring).
 */
export type AccessVerdict = 'write' | 'read' | 'denied' | 'expired' | 'unknown';
export interface RecheckableConnection {
    readOnly: boolean;
    context: unknown;
    close(event?: {
        code: number;
        reason: string;
    }): void;
    webSocket: {
        close(code?: number, reason?: string): void;
    };
}
export interface ConnectionAuth {
    token: string;
    boardUuid: string;
    /** When access was last confirmed (ms epoch); updated by each re-check. */
    verifiedAt: number;
}
/** Terminal: the client must not reconnect. */
export declare const ACCESS_REVOKED: {
    code: number;
    reason: string;
};
/** Reconnectable: the client reconnects and authenticates with a fresh token. */
export declare const REAUTH_REQUIRED: {
    code: number;
    reason: string;
};
/**
 * "expired" covers tokens that lapsed (or are about to) since the socket
 * authenticated; "invalid" is anything that never verified.
 */
export type TokenState = 'valid' | 'expired' | 'invalid';
export interface MembershipResponse {
    ok: boolean;
    status: number;
    json(): Promise<unknown>;
}
/**
 * Builds the per-connection access check. The only credential we hold is the
 * user's token from connect time; once it expires the API can no longer answer
 * for that user, so the verdict is "expired" and the socket is closed with a
 * reconnectable code (the client comes back with a fresh token, which is
 * checked like any new connection). Explicit 401/403/404 answers for a token
 * that is still valid are a terminal denial.
 */
export declare function createAccessChecker(deps: {
    tokenState: (token: string) => TokenState;
    fetchMembership: (boardUuid: string, token: string) => Promise<MembershipResponse>;
}): (auth: ConnectionAuth) => Promise<AccessVerdict>;
/** Detaches the connection from its document, then closes its socket. */
export declare function dropConnection(connection: RecheckableConnection, event: {
    code: number;
    reason: string;
}): void;
export interface RecheckOptions {
    /**
     * Longest a connection may stay open without a successful check. Past it, a
     * failed check closes the socket with the reconnectable code, so an API
     * outage can't keep a revoked user connected indefinitely.
     */
    maxUnverifiedMs: number;
    now?: () => number;
}
/**
 * Re-checks each connection: closes those that lost access (terminal), closes
 * those whose access can't be confirmed any more (reconnectable), and moves
 * the rest between read-only and read-write to match their current role.
 * Returns the number of connections closed.
 */
export declare function recheckConnections(connections: RecheckableConnection[], check: (auth: ConnectionAuth) => Promise<AccessVerdict>, { maxUnverifiedMs, now }: RecheckOptions): Promise<number>;
