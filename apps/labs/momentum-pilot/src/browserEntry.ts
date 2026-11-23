/* Curated Student Virtual Lab — Conservation of Linear Momentum (Pilot 01)
 * Zero browser AI / zero API keys / temporary in-memory notebook / coherent single-condition runs.
 */

import {
  MOMENTUM_DEFAULT_PARAMS,
  MOMENTUM_METADATA,
  MOMENTUM_PARAM_DESCRIPTORS,
  type MomentumParams,
} from './MomentumConfig.ts';
import { MomentumEngine } from './MomentumEngine.ts';
import { MomentumRenderer2D } from './MomentumRenderer2D.ts';
import { ExperimentRunSession, formatRunToCSV, type ExperimentRun } from './RunRecorder.ts';
import { StudentLabNotebook } from './journalStorage.ts';

declare const __STUDYTOMY_LAB_BUILD_VERSION__: string;
declare const __STUDYTOMY_LAB_BUILD_SHA256__: string;

function downloadTextFile(filename: string, content: string, mimeType: string): void {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.style.display = 'none';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

export function mountMomentumLab(root: HTMLElement): void {
  const buildInfo = {
    buildVersion: __STUDYTOMY_LAB_BUILD_VERSION__,
    buildSha256: __STUDYTOMY_LAB_BUILD_SHA256__,
  };

  const engine = new MomentumEngine();
  engine.init({ ...MOMENTUM_DEFAULT_PARAMS });
  const session = new ExperimentRunSession(engine, {
    sampleRateHz: 25,
    maxSamples: 5000,
    buildVersion: buildInfo.buildVersion,
    buildSha256: buildInfo.buildSha256,
  });
  const notebook = new StudentLabNotebook();
  const renderer = new MomentumRenderer2D();

  let running = false;
  let rafId = 0;
  let lastFrame = 0;

  root.innerHTML = '';
  const wrapper = document.createElement('div');
  wrapper.className = 'lab-shell';
  wrapper.innerHTML = `
    <header class="lab-header">
      <div>
        <h1>${MOMENTUM_METADATA.title}</h1>
        <p class="lab-subtitle">${MOMENTUM_METADATA.curriculum}</p>
      </div>
      <div class="lab-actions">
        <button type="button" id="btn-play" class="btn primary">Start</button>
        <button type="button" id="btn-record" class="btn">Start Recording</button>
        <button type="button" id="btn-reset" class="btn">Reset Run</button>
        <button type="button" id="btn-export-csv" class="btn">Export Active Run CSV</button>
        <button type="button" id="btn-fullscreen" class="btn">Fullscreen</button>
      </div>
    </header>
    <main class="lab-grid">
      <section class="stage-panel" aria-label="Simulation viewport">
        <canvas id="momentum-canvas" aria-label="Conservation of linear momentum 2D simulation"></canvas>
        <div id="live-status" class="status-bar" role="status" aria-live="polite"></div>
      </section>
      <aside class="controls-panel" aria-label="Initial conditions and notebook">
        <section class="card">
          <h2>Initial Conditions (SI Units)</h2>
          <p class="hint">Changing any initial condition seals any recorded samples into an archived run and starts a fresh run so CSV files never mix conditions.</p>
          <div id="param-controls" class="param-list"></div>
          <div id="archived-runs" class="archived-runs"></div>
        </section>
        <section class="card">
          <h2>Temporary Lab Notebook</h2>
          <p class="hint" id="notebook-notice"></p>
          <form id="note-form" class="note-form">
            <label for="note-input" class="sr-only">Observation note</label>
            <textarea id="note-input" rows="3" maxlength="2000" placeholder="Record your predictions, readings, and observations..."></textarea>
            <div class="note-buttons">
              <button type="submit" class="btn primary">Add Note</button>
              <button type="button" id="btn-export-notes" class="btn">Export Notes</button>
              <button type="button" id="btn-clear-notes" class="btn">Clear Notes</button>
            </div>
          </form>
          <ul id="note-list" class="note-list"></ul>
        </section>
      </aside>
    </main>
  `;
  root.appendChild(wrapper);

  const canvas = wrapper.querySelector('#momentum-canvas') as HTMLCanvasElement;
  const statusEl = wrapper.querySelector('#live-status') as HTMLElement;
  const paramControlsEl = wrapper.querySelector('#param-controls') as HTMLElement;
  const archivedRunsEl = wrapper.querySelector('#archived-runs') as HTMLElement;
  const noticeEl = wrapper.querySelector('#notebook-notice') as HTMLElement;
  const noteForm = wrapper.querySelector('#note-form') as HTMLFormElement;
  const noteInput = wrapper.querySelector('#note-input') as HTMLTextAreaElement;
  const noteList = wrapper.querySelector('#note-list') as HTMLUListElement;

  const btnPlay = wrapper.querySelector('#btn-play') as HTMLButtonElement;
  const btnRecord = wrapper.querySelector('#btn-record') as HTMLButtonElement;
  const btnReset = wrapper.querySelector('#btn-reset') as HTMLButtonElement;
  const btnExportCsv = wrapper.querySelector('#btn-export-csv') as HTMLButtonElement;
  const btnFullscreen = wrapper.querySelector('#btn-fullscreen') as HTMLButtonElement;
  const btnExportNotes = wrapper.querySelector('#btn-export-notes') as HTMLButtonElement;
  const btnClearNotes = wrapper.querySelector('#btn-clear-notes') as HTMLButtonElement;

  noticeEl.textContent = notebook.getNotice();
  renderer.mount(canvas);

  const renderArchivedRuns = () => {
    const runs = session.getArchivedRuns();
    archivedRunsEl.innerHTML = '';
    if (runs.length === 0) return;
    const heading = document.createElement('h3');
    heading.textContent = `Archived Runs (${runs.length})`;
    archivedRunsEl.appendChild(heading);
    for (const run of runs) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn small';
      btn.textContent = `Export Run #${run.runId} (${run.samples.length} pts)`;
      btn.addEventListener('click', () => {
        downloadTextFile(
          `conservation-momentum-run-${run.runId}.csv`,
          formatRunToCSV(run),
          'text/csv;charset=utf-8;'
        );
      });
      archivedRunsEl.appendChild(btn);
    }
  };

  const renderNotes = () => {
    noteList.innerHTML = '';
    for (const entry of notebook.listEntries()) {
      const li = document.createElement('li');
      li.textContent = `[Run #${entry.runId}] ${entry.note}`;
      noteList.appendChild(li);
    }
  };

  const updateHud = () => {
    const s = engine.getState();
    const run = session.getActiveRun();
    renderer.render(s);
    statusEl.textContent =
      `Run #${run.runId} | t=${s.time.toFixed(2)} s | ` +
      `P_x=${s.totalMomentumX.toFixed(2)} kg·m/s | KE=${s.totalKineticEnergy.toFixed(2)} J | ` +
      `Isolated: ${s.isSystemIsolated ? 'Yes' : 'No'} | Recorded: ${run.samples.length} pts`;
    btnPlay.textContent = running ? 'Pause' : 'Start';
    btnRecord.textContent = session.isRecordingActive() ? 'Stop Recording' : 'Start Recording';
  };

  for (const desc of MOMENTUM_PARAM_DESCRIPTORS) {
    const row = document.createElement('label');
    row.className = 'param-row';
    const top = document.createElement('span');
    top.className = 'param-label';
    const valSpan = document.createElement('span');
    valSpan.textContent = `${desc.defaultValue} ${desc.unit}`;
    top.textContent = `${desc.label}: `;
    top.appendChild(valSpan);

    const input = document.createElement('input');
    input.type = 'range';
    input.min = String(desc.min);
    input.max = String(desc.max);
    input.step = String(desc.step);
    input.value = String(desc.defaultValue);
    input.setAttribute('aria-label', `${desc.label} (${desc.unit})`);

    input.addEventListener('input', () => {
      const num = Number(input.value);
      valSpan.textContent = `${num} ${desc.unit}`;
      running = false;
      session.updateInitialConditions({ [desc.id]: num } as Partial<MomentumParams>);
      renderer.clearTrail();
      renderArchivedRuns();
      updateHud();
    });

    row.appendChild(top);
    row.appendChild(input);
    paramControlsEl.appendChild(row);
  }

  btnPlay.addEventListener('click', () => {
    running = !running;
    if (running) {
      lastFrame = performance.now();
      session.recordCurrentState(true);
    }
    updateHud();
  });

  btnRecord.addEventListener('click', () => {
    if (session.isRecordingActive()) {
      session.pauseRecording();
    } else {
      session.startRecording();
      session.recordCurrentState(true);
    }
    updateHud();
  });

  btnReset.addEventListener('click', () => {
    running = false;
    session.resetRun({ keepArchived: true });
    renderer.clearTrail();
    renderArchivedRuns();
    updateHud();
  });

  btnExportCsv.addEventListener('click', () => {
    const run: ExperimentRun = session.getActiveRun();
    if (run.samples.length === 0) {
      session.recordCurrentState(true);
    }
    const latest = session.getActiveRun();
    downloadTextFile(
      `conservation-momentum-run-${latest.runId}.csv`,
      formatRunToCSV(latest),
      'text/csv;charset=utf-8;'
    );
  });

  btnFullscreen.addEventListener('click', () => {
    if (!document.fullscreenElement) {
      void wrapper.requestFullscreen?.().catch(() => {});
    } else {
      void document.exitFullscreen?.().catch(() => {});
    }
  });

  noteForm.addEventListener('submit', (event) => {
    event.preventDefault();
    const text = noteInput.value.trim();
    if (!text) return;
    notebook.addEntry(session.getActiveRun().runId, text);
    noteInput.value = '';
    renderNotes();
  });

  btnExportNotes.addEventListener('click', () => {
    downloadTextFile(
      'conservation-momentum-notes.md',
      notebook.exportMarkdown(MOMENTUM_METADATA.title),
      'text/markdown;charset=utf-8;'
    );
  });

  btnClearNotes.addEventListener('click', () => {
    notebook.clear();
    renderNotes();
  });

  const handleResize = () => {
    const rect = canvas.parentElement?.getBoundingClientRect();
    if (rect) {
      renderer.resize(rect.width, Math.min(460, Math.max(260, rect.width * 0.56)));
      renderer.render(engine.getState());
    }
  };
  window.addEventListener('resize', handleResize);
  handleResize();
  updateHud();

  const tick = (now: number) => {
    if (running) {
      const dt = Math.min(0.05, Math.max(0.001, (now - lastFrame) / 1000));
      lastFrame = now;
      engine.step(dt);
      session.recordCurrentState();
      updateHud();
    }
    rafId = window.requestAnimationFrame(tick);
  };
  rafId = window.requestAnimationFrame(tick);

  window.addEventListener('beforeunload', () => {
    window.cancelAnimationFrame(rafId);
    renderer.unmount();
  });
}

if (typeof document !== 'undefined') {
  const root = document.getElementById('studytomy-lab-root');
  if (root) {
    mountMomentumLab(root);
  }
}
