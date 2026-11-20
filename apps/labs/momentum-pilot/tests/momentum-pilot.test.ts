import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';

import { MOMENTUM_DEFAULT_PARAMS } from '../src/MomentumConfig.ts';
import { MomentumEngine } from '../src/MomentumEngine.ts';
import {
  ExperimentRunSession,
  ParameterLockedError,
  formatRunToCSV,
} from '../src/RunRecorder.ts';
import {
  MemoryJournalPersistence,
  StudentLabNotebook,
  TEMPORARY_NOTEBOOK_NOTICE,
  resolveBrowserStorage,
} from '../src/journalStorage.ts';

const rootDir = path.resolve(import.meta.dirname, '..');

test('OpenStax §9.3 Example 9.8: equal-mass head-on elastic collision conserves momentum and exchanges velocity', () => {
  const engine = new MomentumEngine();
  engine.init({
    mass1: 2.0,
    mass2: 2.0,
    velocity1: 2.5,
    velocity2: 0.0,
    impactParameter: 0.0,
    restitution: 1.0,
  });

  // Advance through first collision before wall contact (initial gap = 7.0 - 0.7 = 6.3m, t_col = 2.52s)
  for (let i = 0; i < 280; i++) {
    engine.step(0.01);
  }
  const s = engine.getState();
  assert.equal(s.collisionCount, 1);
  assert.equal(s.wallContactCount, 0);
  assert.equal(s.isSystemIsolated, true);
  assert.ok(Math.abs(s.body1.vx - 0.0) < 1e-9, `expected v1f=0, got ${s.body1.vx}`);
  assert.ok(Math.abs(s.body2.vx - 2.5) < 1e-9, `expected v2f=2.5, got ${s.body2.vx}`);
  assert.ok(Math.abs(s.totalMomentumX - 5.0) < 1e-9);
  assert.ok(s.energyLostJoules < 1e-9);
});

test('OpenStax §9.4 Example 9.11: 12:15 mass ratio elastic collision matches analytic velocities', () => {
  const engine = new MomentumEngine();
  engine.init({
    mass1: 1.2,
    mass2: 1.5,
    velocity1: 2.5,
    velocity2: 0.0,
    impactParameter: 0.0,
    restitution: 1.0,
  });

  for (let i = 0; i < 270; i++) {
    engine.step(0.01);
  }
  const s = engine.getState();
  assert.equal(s.collisionCount, 1);
  assert.equal(s.wallContactCount, 0);
  assert.equal(s.isSystemIsolated, true);
  assert.ok(Math.abs(s.body1.vx - (-2.5 / 9)) < 1e-6, `got ${s.body1.vx}`);
  assert.ok(Math.abs(s.body2.vx - (20 / 9)) < 1e-6, `got ${s.body2.vx}`);
  assert.ok(Math.abs(s.totalMomentumX - 3.0) < 1e-9);
});

test('Perfectly inelastic collision (e=0) conserves momentum and dissipates internal kinetic energy', () => {
  const engine = new MomentumEngine();
  engine.init({
    mass1: 2.0,
    mass2: 4.0,
    velocity1: 6.0,
    velocity2: 0.0,
    impactParameter: 0.0,
    restitution: 0.0,
  });

  for (let i = 0; i < 120; i++) {
    engine.step(0.01);
  }
  const s = engine.getState();
  assert.equal(s.collisionCount, 1);
  assert.equal(s.wallContactCount, 0);
  assert.equal(s.isSystemIsolated, true);
  assert.ok(Math.abs(s.body1.vx - 2.0) < 1e-9);
  assert.ok(Math.abs(s.body2.vx - 2.0) < 1e-9);
  assert.ok(Math.abs(s.totalMomentumX - 12.0) < 1e-9);
  assert.ok(Math.abs(s.energyLostJoules - 24.0) < 1e-9);
});

test('F-03 counterexample: mid-run mass1 edit invalidates isSystemIsolated and RunSession never mixes conditions in one CSV', () => {
  // 1. Direct engine mutation without reset cannot keep isSystemIsolated === true
  const directEngine = new MomentumEngine();
  directEngine.init({ ...MOMENTUM_DEFAULT_PARAMS });
  assert.equal(directEngine.getState().totalMomentumX, 4.0);
  assert.equal(directEngine.getState().isSystemIsolated, true);
  directEngine.setParameters({ mass1: 4.0 });
  const mutated = directEngine.getState();
  assert.equal(mutated.totalMomentumX, 14.0);
  assert.equal(mutated.parameterMutatedMidRun, true);
  assert.equal(mutated.isSystemIsolated, false);

  // 2. ExperimentRunSession separates mass1=2 and mass1=4 into distinct immutable runs
  const sessionEngine = new MomentumEngine();
  sessionEngine.init({ ...MOMENTUM_DEFAULT_PARAMS });
  const session = new ExperimentRunSession(sessionEngine, {
    sampleRateHz: 10,
    buildVersion: '1.0.0-pilot.1',
    buildSha256: 'a'.repeat(64),
  });
  session.startRecording();
  session.recordCurrentState(true); // P_x = 4 kg*m/s with mass1=2

  // Changing mass1 to 4 archives Run #1 (mass1=2) and starts clean Run #2 (mass1=4)
  const archivedRun1 = session.updateInitialConditions({ mass1: 4.0 });
  assert.ok(archivedRun1 !== null);
  assert.equal(archivedRun1.runId, 1);
  assert.equal(archivedRun1.initialParameters.mass1, 2.0);
  assert.equal(archivedRun1.samples.length, 1);
  assert.equal(archivedRun1.samples[0].total_p_x, 4.0);

  const csv1 = formatRunToCSV(archivedRun1);
  assert.match(csv1, /# Run ID: 1/);
  assert.match(csv1, /# Initial Parameters \(Immutable\): .*mass1=2/);
  assert.doesNotMatch(csv1, /mass1=4/);

  session.startRecording();
  session.recordCurrentState(true); // P_x = 14 kg*m/s with mass1=4
  const run2 = session.getActiveRun();
  assert.equal(run2.runId, 2);
  assert.equal(run2.initialParameters.mass1, 4.0);
  assert.equal(run2.samples.length, 1);
  assert.equal(run2.samples[0].total_p_x, 14.0);
  assert.equal(sessionEngine.getState().isSystemIsolated, true);

  const csv2 = formatRunToCSV(run2);
  assert.match(csv2, /# Run ID: 2/);
  assert.match(csv2, /# Initial Parameters \(Immutable\): .*mass1=4/);
});

test('F-03 optional lock mode blocks initial condition edits while recording is active', () => {
  const engine = new MomentumEngine();
  engine.init({ ...MOMENTUM_DEFAULT_PARAMS });
  const session = new ExperimentRunSession(engine, {
    buildVersion: '1.0.0-pilot.1',
    buildSha256: 'b'.repeat(64),
    lockParametersWhileRecording: true,
  });
  session.startRecording();
  assert.throws(() => session.updateInitialConditions({ mass1: 4.0 }), ParameterLockedError);
  session.pauseRecording();
  session.updateInitialConditions({ mass1: 4.0 });
  assert.equal(engine.getParameters().mass1, 4.0);
});

test('F-04 counterexample: throwing localStorage getter in sandboxed realm falls back to null without crashing', () => {
  const fnSource = resolveBrowserStorage.toString().replace('JOURNAL_PROBE_KEY', "'probe'");
  const sandbox = vm.createContext({});
  Object.defineProperty(sandbox, 'localStorage', {
    get() {
      const err = new Error('The operation is insecure.');
      err.name = 'SecurityError';
      throw err;
    },
    enumerable: true,
    configurable: true,
  });
  const result = vm.runInContext(`(${fnSource})()`, sandbox);
  assert.equal(result, null);

  // Denied write also returns null safely
  const throwingWrite = {
    getItem: () => null,
    setItem: () => {
      throw new Error('QuotaExceededError');
    },
    removeItem: () => {},
  };
  assert.equal(resolveBrowserStorage(throwingWrite), null);
});

test('T-02 temporary notebook defaults to in-memory only with no cross-session bleed', () => {
  const nb1 = new StudentLabNotebook();
  assert.equal(nb1.isTemporaryMode(), true);
  assert.equal(nb1.getNotice(), TEMPORARY_NOTEBOOK_NOTICE);
  nb1.addEntry(1, 'Initial momentum equals final momentum before wall bounce.');
  assert.equal(nb1.listEntries().length, 1);
  assert.match(nb1.exportMarkdown('Momentum'), /Run #1/);

  // New session on same device starts completely empty (no shared origin localStorage)
  const nb2 = new StudentLabNotebook(new MemoryJournalPersistence());
  assert.equal(nb2.listEntries().length, 0);

  nb1.clear();
  assert.equal(nb1.listEntries().length, 0);
});

test('T-02 & T-05 built artifact removes standalone AI, pins SHA-256 manifest, and sets frame-ancestors CSP', () => {
  const distDir = path.join(rootDir, 'dist');
  const manifestPath = path.join(distDir, 'manifest.json');
  assert.ok(fs.existsSync(manifestPath), 'Run npm/bun build before testing built artifact');
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));

  assert.equal(manifest.lab_id, 'conservation-momentum');
  assert.equal(manifest.upstream_source.commit, '697025a14aabbd391072fbedc522d1d68bebcbc4');
  assert.equal(manifest.security_guarantees.standalone_browser_ai_removed, true);
  assert.equal(manifest.security_guarantees.service_worker_registered, false);

  const html = fs.readFileSync(path.join(distDir, 'index.html'));
  const jsBytes = fs.readFileSync(path.join(distDir, manifest.bundle_asset));
  const headers = fs.readFileSync(path.join(distDir, '_headers'));
  const artifactSha = crypto.createHash('sha256').update(html).update(jsBytes).update(headers).digest('hex');
  assert.equal(manifest.artifact_sha256, artifactSha);
  assert.equal(manifest.build_identity, 'studytomy-lab-source-input-sha256-v1');

  // Zero browser AI endpoints or API key collection in built bundle
  const bundleText = jsBytes.toString('utf8');
  assert.ok(bundleText.includes(manifest.build_sha256), 'Build identity is compiled into the immutable browser bundle');
  assert.doesNotMatch(bundleText, /unpinned-local-dev|__STUDYTOMY_LAB_BUILD_SHA256__/);
  assert.doesNotMatch(bundleText, /generativelanguage\.googleapis\.com/i);
  assert.doesNotMatch(bundleText, /geminiClient|playlistGenerator|GEMINI_API_KEY/i);
  assert.doesNotMatch(bundleText, /agentuto\.inquiry-log\.v1/i);

  // _headers replaces X-Frame-Options: SAMEORIGIN with approved frame-ancestors & connect-src 'none'
  const headerText = headers.toString('utf8');
  assert.doesNotMatch(headerText, /X-Frame-Options:\s*SAMEORIGIN/i);
  assert.match(headerText, /frame-ancestors 'self' https:\/\/lms\.studytomy\.com/);
  assert.match(headerText, /connect-src 'none'/);
  assert.equal((headerText.match(/Access-Control-Allow-Origin:/g) || []).length, 2);
  assert.doesNotMatch(headerText, /Access-Control-Allow-Credentials/i);
  assert.doesNotMatch(headerText, /\/\*\s*\n\s*Access-Control-Allow-Origin:/i);
  assert.match(headerText, new RegExp(`/${manifest.bundle_asset.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\n  Access-Control-Allow-Origin: \\*`));
  assert.ok(fs.existsSync(path.join(distDir, 'LICENSE')));

  // Versioned immutable path contains identical reviewed artifact set
  const versionedDir = path.join(distDir, manifest.lab_id, manifest.build_version);
  assert.ok(fs.existsSync(path.join(versionedDir, 'index.html')));
  assert.ok(fs.existsSync(path.join(versionedDir, manifest.bundle_asset)));
  assert.ok(fs.existsSync(path.join(versionedDir, 'manifest.json')));
  assert.ok(fs.existsSync(path.join(versionedDir, 'LICENSE')));
  assert.equal(manifest.versioned_entry_path, `${manifest.lab_id}/${manifest.build_version}/index.html`);

  const csvSession = new ExperimentRunSession(new MomentumEngine(), {
    buildVersion: manifest.build_version,
    buildSha256: manifest.build_sha256,
  });
  assert.match(formatRunToCSV(csvSession.getActiveRun()), new RegExp(`# Build SHA-256: ${manifest.build_sha256}`));
});
