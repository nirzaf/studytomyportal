import * as Y from 'yjs';
export function createDocSizeGuard(opts) {
    const unmeasured = new Map();
    const oversized = new Set();
    /** Measures the document now. Returns true when it is over the cap. */
    function measure(name, doc) {
        unmeasured.set(name, 0);
        if (Y.encodeStateAsUpdate(doc).byteLength > opts.maxDocBytes)
            oversized.add(name);
        return oversized.has(name);
    }
    /** Counts an applied update. Returns true when the document is over the cap. */
    function recordUpdate(name, doc, updateBytes) {
        if (oversized.has(name))
            return true;
        const pending = (unmeasured.get(name) ?? 0) + updateBytes;
        if (pending < opts.remeasureEveryBytes) {
            unmeasured.set(name, pending);
            return false;
        }
        return measure(name, doc);
    }
    function isOversized(name) {
        return oversized.has(name);
    }
    /** Drops the bookkeeping once the document is unloaded. */
    function forget(name) {
        unmeasured.delete(name);
        oversized.delete(name);
    }
    return { measure, recordUpdate, isOversized, forget };
}
