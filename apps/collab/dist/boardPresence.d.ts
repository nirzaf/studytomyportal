export declare const MAX_BOARD_EDITORS = 10;
export declare const MAX_BOARD_VIEWERS = 10;
export declare const MAX_SESSIONS_PER_SOCKET = 2;
export interface SeatHolder {
    readOnly: boolean;
    socketId: string;
}
/** Why a new connection can't join the board, or null when it can. */
export declare function seatRefusal(existing: Iterable<SeatHolder>, joining: {
    readOnly: boolean;
    socketId: string;
}): string | null;
export interface PresenceConnection {
    readOnly: boolean;
    context?: {
        user?: {
            name?: string;
        };
    } | undefined;
}
/**
 * Rewrites an inbound awareness update in place.
 *
 * `claims` maps awareness client ids to the connection that owns them, per
 * document. `live` lists the document's open connections, so an id left by a
 * closed connection (a client reconnecting keeps its id) can be claimed again.
 */
export declare function sanitizeAwareness<C extends PresenceConnection>(connection: C, states: Map<number, Record<string, any>>, removedClients: Set<number>, claims: Map<number, C>, live: readonly C[]): void;
