import * as Y from 'yjs';
export interface DocSizeGuardOptions {
    maxDocBytes: number;
    /** Update bytes to accumulate before measuring the document again. */
    remeasureEveryBytes: number;
}
export declare function createDocSizeGuard(opts: DocSizeGuardOptions): {
    measure: (name: string, doc: Y.Doc) => boolean;
    recordUpdate: (name: string, doc: Y.Doc, updateBytes: number) => boolean;
    isOversized: (name: string) => boolean;
    forget: (name: string) => void;
};
