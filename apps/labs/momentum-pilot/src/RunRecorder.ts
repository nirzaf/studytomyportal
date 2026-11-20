/* Derived from MIT-licensed danielw-sudo/agentuto @ 697025a14aabbd391072fbedc522d1d68bebcbc4
 * Hardened for Studytomy Pilot 01 (F-03):
 * - Freezes one immutable initial-condition snapshot per run.
 * - Changing parameters never mixes different physical systems under one CSV header;
 *   it archives the previous run and opens a fresh numbered run after resetting the engine.
 */

import type { TelemetryPoint } from './types.ts';
import type { MomentumParams } from './MomentumConfig.ts';
import { MOMENTUM_METADATA, MOMENTUM_TELEMETRY_UNITS } from './MomentumConfig.ts';
import { MomentumEngine } from './MomentumEngine.ts';

export interface ExperimentRun {
  readonly runId: number;
  readonly experimentId: string;
  readonly experimentTitle: string;
  readonly buildVersion: string;
  readonly buildSha256: string;
  readonly startedAt: string;
  readonly initialParameters: Readonly<MomentumParams>;
  readonly units: Readonly<Record<string, string>>;
  readonly samples: readonly TelemetryPoint[];
  readonly sealedReason?: string;
}

export interface RunSessionOptions {
  sampleRateHz?: number;
  maxSamples?: number;
  buildVersion: string;
  buildSha256: string;
  lockParametersWhileRecording?: boolean;
}

export class ParameterLockedError extends Error {
  constructor() {
    super('Initial conditions are locked while recording. Pause or start a new run to change parameters.');
    this.name = 'ParameterLockedError';
  }
}

export class ExperimentRunSession {
  private readonly engine: MomentumEngine;
  private readonly sampleInterval: number;
  private readonly maxSamples: number;
  private readonly buildVersion: string;
  private readonly buildSha256: string;
  private readonly lockParametersWhileRecording: boolean;

  private runCounter = 0;
  private currentRun: {
    runId: number;
    startedAt: string;
    initialParameters: MomentumParams;
    samples: TelemetryPoint[];
    lastSampleTime: number;
  };
  private archivedRuns: ExperimentRun[] = [];
  private isRecording = false;

  constructor(engine: MomentumEngine, options: RunSessionOptions) {
    this.engine = engine;
    this.sampleInterval = 1 / Math.max(1, options.sampleRateHz ?? 50);
    this.maxSamples = Math.max(1, options.maxSamples ?? 10000);
    this.buildVersion = options.buildVersion;
    this.buildSha256 = options.buildSha256;
    this.lockParametersWhileRecording = options.lockParametersWhileRecording ?? false;
    this.currentRun = this.createFreshRun(this.engine.getParameters());
  }

  private createFreshRun(params: MomentumParams) {
    this.runCounter += 1;
    return {
      runId: this.runCounter,
      startedAt: new Date().toISOString(),
      initialParameters: Object.freeze({ ...params }),
      samples: [] as TelemetryPoint[],
      lastSampleTime: -Infinity,
    };
  }

  private freezeCurrentRun(sealedReason?: string): ExperimentRun {
    return Object.freeze({
      runId: this.currentRun.runId,
      experimentId: MOMENTUM_METADATA.id,
      experimentTitle: MOMENTUM_METADATA.title,
      buildVersion: this.buildVersion,
      buildSha256: this.buildSha256,
      startedAt: this.currentRun.startedAt,
      initialParameters: Object.freeze({ ...this.currentRun.initialParameters }),
      units: Object.freeze({ ...MOMENTUM_TELEMETRY_UNITS }),
      samples: Object.freeze(this.currentRun.samples.map((p) => Object.freeze({ ...p }))),
      ...(sealedReason ? { sealedReason } : {}),
    });
  }

  startRecording(): void {
    this.isRecording = true;
  }

  pauseRecording(): void {
    this.isRecording = false;
  }

  isRecordingActive(): boolean {
    return this.isRecording;
  }

  /**
   * Captures a telemetry point for the active run.
   */
  recordCurrentState(force = false): boolean {
    if (!this.isRecording && !force) return false;
    const point = this.engine.getTelemetrySnapshot();
    if (!force && point.t - this.currentRun.lastSampleTime < this.sampleInterval) {
      return false;
    }
    this.currentRun.lastSampleTime = point.t;
    if (this.currentRun.samples.length >= this.maxSamples) {
      this.currentRun.samples.shift();
    }
    this.currentRun.samples.push({ ...point });
    return true;
  }

  /**
   * Updates parameters cleanly.
   * - If lockParametersWhileRecording is true and recording is active, throws ParameterLockedError.
   * - Otherwise archives any non-empty previous run under its original immutable conditions,
   *   resets the engine to the new parameters, and opens a new numbered run so samples
   *   from different initial conditions are never mixed under one header.
   */
  updateInitialConditions(partial: Partial<MomentumParams>): ExperimentRun | null {
    if (this.lockParametersWhileRecording && this.isRecording) {
      throw new ParameterLockedError();
    }
    let archived: ExperimentRun | null = null;
    if (this.currentRun.samples.length > 0) {
      archived = this.freezeCurrentRun('initial_conditions_changed');
      this.archivedRuns.push(archived);
    }
    this.isRecording = false;
    const nextParams = { ...this.engine.getParameters(), ...partial };
    this.engine.reset(nextParams);
    this.currentRun = this.createFreshRun(this.engine.getParameters());
    return archived;
  }

  /**
   * Explicit user reset: archives any non-empty run (if keepArchived) or clears current run.
   */
  resetRun(options?: { keepArchived?: boolean }): void {
    if (options?.keepArchived && this.currentRun.samples.length > 0) {
      this.archivedRuns.push(this.freezeCurrentRun('manual_reset'));
    }
    this.isRecording = false;
    this.engine.reset();
    this.currentRun = this.createFreshRun(this.engine.getParameters());
  }

  getActiveRun(): ExperimentRun {
    return this.freezeCurrentRun();
  }

  getArchivedRuns(): readonly ExperimentRun[] {
    return Object.freeze([...this.archivedRuns]);
  }
}

function escapeCsvMetadata(value: string): string {
  return value.replace(/[\r\n]+/g, ' ').trim();
}

/**
 * Exports ONE coherent experiment run with its immutable initial-condition snapshot,
 * SI units, and pinned build provenance.
 */
export function formatRunToCSV(run: ExperimentRun, notes?: string): string {
  const lines: string[] = [];
  lines.push('# Studytomy Virtual Lab Telemetry Export');
  lines.push(`# Experiment ID: ${escapeCsvMetadata(run.experimentId)}`);
  lines.push(`# Experiment: ${escapeCsvMetadata(run.experimentTitle)}`);
  lines.push(`# Run ID: ${run.runId}`);
  lines.push(`# Build Version: ${escapeCsvMetadata(run.buildVersion)}`);
  lines.push(`# Build SHA-256: ${escapeCsvMetadata(run.buildSha256)}`);
  lines.push(`# Run Started At: ${escapeCsvMetadata(run.startedAt)}`);

  const paramEntries = Object.entries(run.initialParameters)
    .map(([k, v]) => `${k}=${v}`)
    .join(', ');
  lines.push(`# Initial Parameters (Immutable): ${paramEntries}`);

  if (run.samples.length === 0) {
    lines.push('# Data: No samples recorded in this run');
    return lines.join('\r\n') + '\r\n';
  }

  const columns = Object.keys(run.samples[0]);
  const declaredUnits = columns
    .filter((col) => run.units[col] !== undefined)
    .map((col) => `${col}(${run.units[col]})`)
    .join(', ');
  lines.push(`# Units: ${declaredUnits}`);
  if (notes && notes.trim()) {
    lines.push(`# Notes: ${escapeCsvMetadata(notes)}`);
  }

  lines.push(columns.join(','));
  for (const point of run.samples) {
    const row = columns.map((col) => {
      const val = point[col];
      if (typeof val === 'number') {
        return Number.isInteger(val) ? val.toString() : val.toPrecision(6);
      }
      return String(val ?? '');
    });
    lines.push(row.join(','));
  }

  return lines.join('\r\n') + '\r\n';
}
