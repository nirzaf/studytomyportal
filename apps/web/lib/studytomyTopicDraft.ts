// Browser-local recovery only. A server-authorized GET must precede claim().
export const TOPIC_DRAFT_DB = 'studytomy-topic-drafts'
export const TOPIC_DRAFT_TTL = 30 * 60 * 1000
export const TOPIC_ANSWER_LIMIT = 48

// The grading API permits a signed integer or a fraction with a positive
// denominator. Partial signs/slashes are allowed here, arbitrary text is not.
export function recoverableFractionDraft(text: string): string {
  const candidate = text.trim()
  return /^[+-]?(?:[0-9]{1,5}(?: *\/ *[0-9]{0,5})?)?$/.test(candidate)
    ? text.slice(0, TOPIC_ANSWER_LIMIT) : ''
}

type Row = {
  key: string
  checkpoint: string
  generation: string
  seen: number
  revision: number
  expires: number
  answer: string
}
type Lease = Pick<Row, 'key' | 'checkpoint' | 'generation'> & { epoch: string }
let clientGeneration = 0

const database = () => new Promise<IDBDatabase>((resolve, reject) => {
  const request = indexedDB.open(TOPIC_DRAFT_DB, 1)
  request.onupgradeneeded = () => {
    request.result.createObjectStore('drafts', { keyPath: 'key' })
    request.result.createObjectStore('session')
  }
  let blocked = false
  request.onerror = request.onblocked = () => {
    blocked = true
    reject(new Error('Draft storage unavailable'))
  }
  request.onsuccess = () => {
    if (blocked) request.result.close()
    else resolve(request.result)
  }
})

function transact<T>(run: (_tx: IDBTransaction, _done: (_value: T) => void) => void): Promise<T | undefined> {
  return new Promise(resolve => {
    let ended = false
    let tx: IDBTransaction | undefined
    const finish = (result?: T) => {
      if (ended) return
      ended = true
      clearTimeout(timeout)
      resolve(result)
    }
    const timeout = setTimeout(() => {
      // Cancel a stalled write, and do not start one if open completes late.
      try { tx?.abort() } catch { /* Already completed. */ }
      finish()
    }, 600)
    void database().then(db => {
      if (ended) { db.close(); return }
      try {
        tx = db.transaction(['drafts', 'session'], 'readwrite')
        let result: T | undefined
        tx.oncomplete = () => { db.close(); finish(result) }
        tx.onabort = tx.onerror = () => { db.close(); finish() }
        run(tx, value => { result = value })
      } catch {
        try { tx?.abort() } catch { /* No active transaction. */ }
        db.close()
        finish()
      }
    }, () => finish())
  })
}

export async function beginTopicDraft() {
  const generation = clientGeneration
  const snapshot = await transact<{ epoch: string; seen: number }>((tx, done) => {
    const store = tx.objectStore('session')
    const get = store.get('epoch')
    get.onsuccess = () => {
      const value = (get.result as string | undefined) ?? crypto.randomUUID()
      if (!get.result) store.put(value, 'epoch')
      const order = store.get('order')
      order.onsuccess = () => {
        const seen = ((order.result as number | undefined) ?? 0) + 1
        store.put(seen, 'order')
        done({ epoch: value, seen })
      }
    }
    // A closed browser cannot run timers. Purge expired rows before reading
    // drafts on its next use; never display an expired answer.
    const cursor = tx.objectStore('drafts').openCursor()
    cursor.onsuccess = () => {
      const item = cursor.result
      if (!item) return
      if ((item.value as Row).expires <= Date.now()) item.delete()
      item.continue()
    }
  })
  return snapshot && generation === clientGeneration ? { ...snapshot, generation } : undefined
}

export function clearTopicDrafts() {
  ++clientGeneration
  return transact<void>(tx => {
    tx.objectStore('drafts').clear()
    tx.objectStore('session').put(crypto.randomUUID(), 'epoch')
  })
}

export function claimTopicDraft(
  auth: Awaited<ReturnType<typeof beginTopicDraft>>,
  key: string,
  checkpoint: string,
  revision: number,
  active: boolean,
) {
  if (!auth || auth.generation !== clientGeneration) return Promise.resolve(undefined)
  return transact<{ lease: Lease; answer: string; expires: number }>((tx, done) => {
    const session = tx.objectStore('session').get('epoch')
    session.onsuccess = () => {
      if (session.result !== auth.epoch || auth.generation !== clientGeneration) return
      const drafts = tx.objectStore('drafts')
      const get = drafts.get(key)
      get.onsuccess = () => {
        const now = Date.now()
        const row = get.result as Row | undefined
        const seen = auth.seen
        if (row && row.expires > now && (row.checkpoint === checkpoint
          ? row.revision > revision || (row.revision === revision && row.seen > seen)
          : row.seen > seen)) return
        const exact = active && row && row.expires > now && row.checkpoint === checkpoint && row.revision === revision
        const next: Row = exact
          ? { ...row, seen, generation: crypto.randomUUID() }
          : { key, checkpoint, revision, seen, generation: crypto.randomUUID(), expires: now + TOPIC_DRAFT_TTL, answer: '' }
        drafts.put(next)
        if (active) done({ lease: { key, checkpoint, generation: next.generation, epoch: auth.epoch }, answer: next.answer, expires: next.expires })
      }
    }
  })
}

export function writeTopicDraft(lease: Lease, text: string, expires: number) {
  const generation = clientGeneration
  return transact<boolean>((tx, done) => {
    const session = tx.objectStore('session').get('epoch')
    session.onsuccess = () => {
      if (session.result !== lease.epoch || generation !== clientGeneration) return
      const drafts = tx.objectStore('drafts')
      const get = drafts.get(lease.key)
      get.onsuccess = () => {
        const row = get.result as Row | undefined
        if (!row || row.generation !== lease.generation || row.checkpoint !== lease.checkpoint || row.expires <= Date.now()) return
        drafts.put({ ...row, answer: recoverableFractionDraft(text), expires })
        done(true)
      }
    }
  })
}
