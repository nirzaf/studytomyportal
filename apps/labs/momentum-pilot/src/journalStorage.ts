/* Derived from MIT-licensed danielw-sudo/agentuto @ 697025a14aabbd391072fbedc522d1d68bebcbc4
 * Hardened for Studytomy Pilot 01 (F-02 & F-04):
 * - resolveBrowserStorage wraps globalThis.localStorage access inside try/catch so
 *   sandboxed iframes or restricted webviews throwing SecurityError on the getter
 *   never crash initialization.
 * - Curated student builds default to temporary in-memory notebook persistence so
 *   observations are never shared across learners on a shared device.
 */

export interface JournalStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export interface InquiryEntry {
  id: string;
  createdAt: string;
  runId: number;
  note: string;
}

export interface JournalPersistence {
  readonly persistent: boolean;
  load(): InquiryEntry[];
  save(entries: readonly InquiryEntry[]): void;
  clear(): void;
}

export const JOURNAL_PROBE_KEY = 'studytomy.lab.storage.probe';

export const TEMPORARY_NOTEBOOK_NOTICE =
  'Temporary session notebook: notes stay in memory only and are cleared when you close or reload this lab. Export your notes before leaving.';

export class MemoryJournalPersistence implements JournalPersistence {
  readonly persistent = false;
  private entries: InquiryEntry[] = [];

  load(): InquiryEntry[] {
    return this.entries.map((entry) => ({ ...entry }));
  }

  save(entries: readonly InquiryEntry[]): void {
    this.entries = entries.map((entry) => ({ ...entry }));
  }

  clear(): void {
    this.entries = [];
  }
}

/**
 * Safely acquires and probes browser storage inside a single defensive try/catch.
 * Returns null if globalThis.localStorage is absent, throws SecurityError on property
 * access (e.g. sandboxed opaque iframe), denies writes, or exceeds quota.
 */
export function resolveBrowserStorage(candidate?: JournalStorage): JournalStorage | null {
  try {
    const storage =
      candidate ??
      (typeof globalThis === 'object' && globalThis !== null && 'localStorage' in globalThis
        ? (globalThis as { localStorage?: JournalStorage }).localStorage
        : undefined);
    if (!storage) return null;

    storage.setItem(JOURNAL_PROBE_KEY, '1');
    storage.removeItem(JOURNAL_PROBE_KEY);
    return storage;
  } catch {
    return null;
  }
}

/**
 * Student-safe temporary lab notebook.
 * Defaults strictly to in-memory storage (F-02 shared-device protection) and never
 * writes student text to shared origin-wide localStorage.
 */
export class StudentLabNotebook {
  private readonly persistence: JournalPersistence;
  private entries: InquiryEntry[];

  constructor(persistence?: JournalPersistence) {
    this.persistence = persistence ?? new MemoryJournalPersistence();
    this.entries = this.persistence.load();
  }

  isTemporaryMode(): boolean {
    return !this.persistence.persistent;
  }

  getNotice(): string {
    return TEMPORARY_NOTEBOOK_NOTICE;
  }

  listEntries(): readonly InquiryEntry[] {
    return this.entries.map((e) => ({ ...e }));
  }

  addEntry(runId: number, note: string): InquiryEntry {
    const trimmed = note.trim().slice(0, 2000);
    if (!trimmed) {
      throw new Error('Observation note cannot be empty.');
    }
    const entry: InquiryEntry = {
      id: `note_${Date.now()}_${this.entries.length + 1}`,
      createdAt: new Date().toISOString(),
      runId,
      note: trimmed,
    };
    this.entries = [...this.entries, entry];
    this.persistence.save(this.entries);
    return { ...entry };
  }

  clear(): void {
    this.entries = [];
    this.persistence.clear();
  }

  exportMarkdown(experimentTitle: string): string {
    const lines = [
      `# ${experimentTitle} — Student Lab Notes`,
      '',
      `> ${TEMPORARY_NOTEBOOK_NOTICE}`,
      '',
    ];
    if (this.entries.length === 0) {
      lines.push('_No notes recorded._');
    } else {
      for (const entry of this.entries) {
        lines.push(`- **[Run #${entry.runId} · ${entry.createdAt}]** ${entry.note}`);
      }
    }
    return lines.join('\n') + '\n';
  }
}
