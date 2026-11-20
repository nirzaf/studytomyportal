import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const rootDir = path.resolve(import.meta.dirname, '..');
const distDir = path.join(rootDir, 'dist');
const assetsDir = path.join(distDir, 'assets');
const buildVersion = '1.0.0-pilot.1';
const labId = 'conservation-momentum';
const declaredBunVersion = fs.readFileSync(path.resolve(rootDir, '../../..', '.bun-version'), 'utf8').trim();
const bunVersion = execFileSync('bun', ['--version'], { encoding: 'utf8' }).trim();

function collectTypeScriptFiles(dir, parent = '') {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const rel = path.posix.join(parent, entry.name);
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return collectTypeScriptFiles(full, rel);
    return entry.isFile() && entry.name.endsWith('.ts') ? [rel] : [];
  }).sort();
}

// Build identity covers source and declared build inputs only. Output bytes are
// hashed separately below so the identity is not circular.
const identityHash = crypto.createHash('sha256');
identityHash.update('studytomy-lab-source-input-sha256-v1\0');
for (const relativePath of [
  ...collectTypeScriptFiles(path.join(rootDir, 'src')).map((file) => `src/${file}`),
  'package.json',
  'public/_headers',
  'scripts/build.mjs',
].sort()) {
  identityHash.update(relativePath).update('\0');
  identityHash.update(fs.readFileSync(path.join(rootDir, relativePath))).update('\0');
}
identityHash.update(`lab_id=${labId}\nbuild_version=${buildVersion}\n`);
identityHash.update(`declared_bun_version=${declaredBunVersion}\nbuild_bun_version=${bunVersion}\n`);
const buildSha = identityHash.digest('hex');

fs.rmSync(distDir, { recursive: true, force: true });
fs.mkdirSync(assetsDir, { recursive: true });

// Bundle zero-dependency browser JS with Bun (already pinned in repo)
const bundleOut = path.join(assetsDir, 'momentum-lab.js');
execFileSync(
  'bun',
  [
    'build',
    path.join(rootDir, 'src', 'browserEntry.ts'),
    '--outfile',
    bundleOut,
    '--target',
    'browser',
    '--format',
    'esm',
    '--minify',
    '--define',
    `__STUDYTOMY_LAB_BUILD_VERSION__=${JSON.stringify(buildVersion)}`,
    '--define',
    `__STUDYTOMY_LAB_BUILD_SHA256__=${JSON.stringify(buildSha)}`,
  ],
  { stdio: 'inherit' }
);

const jsBytes = fs.readFileSync(bundleOut);
const jsSha = crypto.createHash('sha256').update(jsBytes).digest('hex');
const hashedJsName = `momentum-lab.${jsSha.slice(0, 12)}.js`;
fs.renameSync(bundleOut, path.join(assetsDir, hashedJsName));

const versionedDir = path.join(distDir, labId, buildVersion);
const versionedAssetsDir = path.join(versionedDir, 'assets');
fs.mkdirSync(versionedAssetsDir, { recursive: true });

const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Studytomy Virtual Physics Lab — Conservation of Linear Momentum</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, -apple-system, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; background: #020617; color: #e2e8f0; }
    .lab-shell { max-width: 1200px; margin: 0 auto; padding: 14px; display: grid; gap: 14px; }
    .lab-header { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; border-bottom: 1px solid #1e293b; padding-bottom: 10px; }
    .lab-header h1 { margin: 0; font-size: 1.1rem; }
    .lab-subtitle { margin: 4px 0 0; font-size: 0.8rem; color: #94a3b8; }
    .lab-actions { display: flex; flex-wrap: wrap; gap: 8px; }
    .lab-grid { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(280px, 1fr); gap: 14px; }
    @media (max-width: 820px) { .lab-grid { grid-template-columns: 1fr; } }
    .stage-panel { display: grid; gap: 8px; align-content: start; }
    #momentum-canvas { width: 100%; height: 420px; border-radius: 10px; border: 1px solid #1e293b; display: block; }
    .status-bar { font-family: ui-monospace, monospace; font-size: 0.8rem; background: #0f172a; border: 1px solid #1e293b; border-radius: 8px; padding: 8px 10px; }
    .controls-panel { display: grid; gap: 12px; align-content: start; }
    .card { background: #0f172a; border: 1px solid #1e293b; border-radius: 10px; padding: 12px; display: grid; gap: 10px; }
    .card h2 { margin: 0; font-size: 0.95rem; }
    .card h3 { margin: 4px 0 0; font-size: 0.85rem; color: #94a3b8; }
    .hint { margin: 0; font-size: 0.78rem; color: #94a3b8; line-height: 1.4; }
    .param-list { display: grid; gap: 8px; }
    .param-row { display: grid; gap: 4px; font-size: 0.82rem; }
    .param-label { display: flex; justify-content: space-between; }
    .btn { min-height: 36px; padding: 6px 12px; border-radius: 8px; border: 1px solid #334155; background: #1e293b; color: #f8fafc; cursor: pointer; font-size: 0.82rem; font-weight: 600; }
    .btn.primary { background: #0284c7; border-color: #38bdf8; }
    .btn.small { min-height: 30px; padding: 4px 8px; font-size: 0.76rem; }
    .archived-runs { display: flex; flex-wrap: wrap; gap: 6px; }
    .note-form { display: grid; gap: 8px; }
    textarea { width: 100%; border-radius: 8px; border: 1px solid #334155; background: #020617; color: #f8fafc; padding: 8px; font: inherit; font-size: 0.82rem; }
    .note-buttons { display: flex; flex-wrap: wrap; gap: 6px; }
    .note-list { margin: 0; padding-left: 18px; display: grid; gap: 4px; font-size: 0.8rem; }
    .sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0,0,0,0); }
  </style>
</head>
<body>
  <div id="studytomy-lab-root"></div>
  <script type="module" src="./assets/${hashedJsName}"></script>
</body>
</html>
`;

fs.writeFileSync(path.join(distDir, 'index.html'), html, 'utf8');
const baseHeaders = fs.readFileSync(path.join(rootDir, 'public', '_headers'), 'utf8').trimEnd();
const rootAssetPath = `/assets/${hashedJsName}`;
const versionedAssetPath = `/${labId}/${buildVersion}/assets/${hashedJsName}`;
const headers = `${baseHeaders}\n\n${rootAssetPath}\n  Access-Control-Allow-Origin: *\n  Cache-Control: public, max-age=31536000, immutable\n\n${versionedAssetPath}\n  Access-Control-Allow-Origin: *\n  Cache-Control: public, max-age=31536000, immutable\n`;
fs.writeFileSync(path.join(distDir, '_headers'), headers, 'utf8');
fs.copyFileSync(path.join(rootDir, 'LICENSE'), path.join(distDir, 'LICENSE'));

// Mirror immutable, versioned hosting directory under dist/<lab_id>/<build_version>/
fs.writeFileSync(path.join(versionedDir, 'index.html'), html, 'utf8');
fs.copyFileSync(path.join(assetsDir, hashedJsName), path.join(versionedAssetsDir, hashedJsName));
fs.writeFileSync(path.join(versionedDir, '_headers'), headers, 'utf8');
fs.copyFileSync(path.join(rootDir, 'LICENSE'), path.join(versionedDir, 'LICENSE'));

// Artifact digest is for byte integrity only; build_sha256 is the independent
// source/input-derived identity embedded in this bundle and recorded in CSV.
const artifactSha = crypto
  .createHash('sha256')
  .update(fs.readFileSync(path.join(distDir, 'index.html')))
  .update(jsBytes)
  .update(fs.readFileSync(path.join(distDir, '_headers')))
  .digest('hex');

const licenseSha = crypto
  .createHash('sha256')
  .update(fs.readFileSync(path.join(distDir, 'LICENSE')))
  .digest('hex');

const manifest = {
  schema: 'studytomy_virtual_lab_manifest_v1',
  lab_id: labId,
  experiment_id: labId,
  build_version: buildVersion,
  versioned_entry_path: `${labId}/${buildVersion}/index.html`,
  build_identity: 'studytomy-lab-source-input-sha256-v1',
  build_sha256: buildSha,
  build_tool: { name: 'Bun', version: bunVersion, repository_pin: declaredBunVersion },
  artifact_sha256: artifactSha,
  bundle_asset: `assets/${hashedJsName}`,
  bundle_sha256: jsSha,
  license_sha256: licenseSha,
  upstream_source: {
    repository: 'https://github.com/danielw-sudo/agentuto',
    commit: '697025a14aabbd391072fbedc522d1d68bebcbc4',
    license: 'MIT',
  },
  security_guarantees: {
    standalone_browser_ai_removed: true,
    temporary_in_memory_notebook_only: true,
    single_condition_run_recording: true,
    service_worker_registered: false,
  },
};

const manifestJson = JSON.stringify(manifest, null, 2) + '\n';
fs.writeFileSync(path.join(distDir, 'manifest.json'), manifestJson, 'utf8');
fs.writeFileSync(path.join(versionedDir, 'manifest.json'), manifestJson, 'utf8');
fs.writeFileSync(path.join(rootDir, 'manifest.json'), manifestJson, 'utf8');
console.log(`Built curated momentum pilot ${buildVersion} (${buildSha}; artifact ${artifactSha})`);
