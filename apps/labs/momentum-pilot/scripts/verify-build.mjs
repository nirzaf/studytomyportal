import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const rootDir = path.resolve(import.meta.dirname, '..');

function sha256(bytes) {
  return crypto.createHash('sha256').update(bytes).digest('hex');
}

function collectTypeScriptFiles(dir, parent = '') {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const rel = path.posix.join(parent, entry.name);
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return collectTypeScriptFiles(full, rel);
    return entry.isFile() && entry.name.endsWith('.ts') ? [rel] : [];
  }).sort();
}

function sourceInputSha256(labRoot, manifest) {
  const hash = crypto.createHash('sha256');
  hash.update('studytomy-lab-source-input-sha256-v1\0');
  const paths = [
    ...collectTypeScriptFiles(path.join(labRoot, 'src')).map((file) => `src/${file}`),
    'package.json',
    'public/_headers',
    'scripts/build.mjs',
  ].sort();
  for (const relativePath of paths) {
    hash.update(relativePath).update('\0');
    hash.update(fs.readFileSync(path.join(labRoot, relativePath))).update('\0');
  }
  hash.update(`lab_id=${manifest.lab_id}\nbuild_version=${manifest.build_version}\n`);
  const declaredBunVersion = fs.readFileSync(path.resolve(labRoot, '../../..', '.bun-version'), 'utf8').trim();
  const bunVersion = execFileSync('bun', ['--version'], { encoding: 'utf8' }).trim();
  hash.update(`declared_bun_version=${declaredBunVersion}\nbuild_bun_version=${bunVersion}\n`);
  return hash.digest('hex');
}

function parseArgs(argv) {
  const opts = { url: null, portalOrigin: null };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--url' && argv[i + 1]) {
      opts.url = argv[i + 1].replace(/\/+$/, '');
      i += 1;
    } else if (argv[i] === '--portal-origin' && argv[i + 1]) {
      opts.portalOrigin = argv[i + 1];
      i += 1;
    }
  }
  return opts;
}

export function verifyLocalBuild(labRoot = rootDir) {
  const pinnedPath = path.join(labRoot, 'manifest.json');
  assert.ok(fs.existsSync(pinnedPath), `Missing pinned manifest: ${pinnedPath}`);
  const pinned = JSON.parse(fs.readFileSync(pinnedPath, 'utf8'));
  assert.equal(pinned.build_identity, 'studytomy-lab-source-input-sha256-v1');
  assert.equal(pinned.build_sha256, sourceInputSha256(labRoot, pinned), 'build_sha256 must derive from reviewed source and inputs');
  assert.deepEqual(pinned.build_tool, {
    name: 'Bun',
    version: execFileSync('bun', ['--version'], { encoding: 'utf8' }).trim(),
    repository_pin: fs.readFileSync(path.resolve(labRoot, '../../..', '.bun-version'), 'utf8').trim(),
  });

  const distDir = path.join(labRoot, 'dist');
  const versionedDir = path.join(distDir, pinned.lab_id, pinned.build_version);
  for (const dir of [distDir, versionedDir]) {
    assert.ok(fs.existsSync(dir), `Missing build output directory: ${dir}`);
    const manifestBytes = fs.readFileSync(path.join(dir, 'manifest.json'));
    const manifest = JSON.parse(manifestBytes.toString('utf8'));
    assert.deepEqual(manifest, pinned, `Manifest mismatch in ${dir}`);

    const htmlBytes = fs.readFileSync(path.join(dir, 'index.html'));
    const jsBytes = fs.readFileSync(path.join(dir, pinned.bundle_asset));
    const headersBytes = fs.readFileSync(path.join(dir, '_headers'));
    const licenseBytes = fs.readFileSync(path.join(dir, 'LICENSE'));

    assert.equal(sha256(jsBytes), pinned.bundle_sha256, `JS SHA-256 mismatch in ${dir}`);
    assert.equal(sha256(licenseBytes), pinned.license_sha256, `LICENSE SHA-256 mismatch in ${dir}`);

    const artifact = crypto
      .createHash('sha256')
      .update(htmlBytes)
      .update(jsBytes)
      .update(headersBytes)
      .digest('hex');
    assert.equal(artifact, pinned.artifact_sha256, `artifact_sha256 mismatch in ${dir}`);

    const htmlText = htmlBytes.toString('utf8');
    assert.ok(
      htmlText.includes(`./${pinned.bundle_asset}`),
      `index.html in ${dir} does not reference ${pinned.bundle_asset}`
    );

    const jsText = jsBytes.toString('utf8');
    assert.ok(jsText.includes(pinned.build_sha256), `Bundle does not embed manifest build identity in ${dir}`);
    assert.ok(jsText.includes(pinned.build_version), `Bundle does not embed manifest version in ${dir}`);
    assert.doesNotMatch(jsText, /generativelanguage\.googleapis\.com/i);
    assert.doesNotMatch(jsText, /geminiClient|playlistGenerator|GEMINI_API_KEY/i);

    const licenseText = licenseBytes.toString('utf8');
    assert.match(licenseText, /MIT License/i);
    assert.match(licenseText, /Daniel Wang \(DW\) & Agentuto Contributors/i);

    const headerText = headersBytes.toString('utf8');
    const assetPath = dir === distDir
      ? `/${pinned.bundle_asset}`
      : `/${pinned.lab_id}/${pinned.build_version}/${pinned.bundle_asset}`;
    assert.ok(headerText.includes(`${assetPath}\n  Access-Control-Allow-Origin: *`), `Missing exact hashed-asset CORS rule in ${dir}`);
    assert.equal((headerText.match(/Access-Control-Allow-Origin:/g) || []).length, 2, 'CORS must be limited to root and versioned hashed JS assets');
    assert.doesNotMatch(headerText, /Access-Control-Allow-Credentials/i);
    assert.doesNotMatch(headerText, /\/\*\s*\n\s*Access-Control-Allow-Origin:/i);
    assert.match(headerText, /connect-src 'none'/);
    assert.match(headerText, /frame-ancestors 'self' https:\/\/lms\.studytomy\.com/);
    assert.doesNotMatch(headerText, /X-Frame-Options:\s*SAMEORIGIN/i);
  }
  return pinned;
}

async function fetchVerifiedBytes(url, expectedSha = null, requiredHeaders = {}) {
  const res = await fetch(url, { redirect: 'error' });
  assert.equal(res.status, 200, `Expected HTTP 200 for ${url}, got ${res.status}`);
  if (requiredHeaders.csp) {
    const csp = res.headers.get('content-security-policy') || '';
    assert.match(csp, /connect-src 'none'/, `Missing connect-src 'none' on ${url}`);
    assert.match(csp, /frame-ancestors/, `Missing frame-ancestors CSP on ${url}`);
    if (requiredHeaders.portalOrigin) {
      assert.ok(
        csp.includes(requiredHeaders.portalOrigin),
        `CSP frame-ancestors on ${url} does not permit portal origin ${requiredHeaders.portalOrigin}: ${csp}`
      );
    }
  }
  const xfo = res.headers.get('x-frame-options');
  assert.ok(!xfo || !/SAMEORIGIN|DENY/i.test(xfo), `Disallowed X-Frame-Options on ${url}: ${xfo}`);
  const xcto = res.headers.get('x-content-type-options') || '';
  assert.equal(xcto.toLowerCase(), 'nosniff', `Missing X-Content-Type-Options: nosniff on ${url}`);
  const acao = res.headers.get('access-control-allow-origin') || '';
  if (requiredHeaders.cors) {
    assert.equal(acao, '*', `Missing Access-Control-Allow-Origin: * on immutable module asset ${url}`);
    assert.equal(res.headers.get('access-control-allow-credentials'), null, `Credentials must not be enabled on ${url}`);
    assert.match(res.headers.get('cache-control') || '', /immutable/i, `Missing immutable cache policy on ${url}`);
  } else {
    assert.equal(acao, '', `CORS must not be enabled for non-module response ${url}`);
  }

  const bytes = Buffer.from(await res.arrayBuffer());
  if (expectedSha) {
    assert.equal(sha256(bytes), expectedSha, `Hosted SHA-256 mismatch at ${url}`);
  }
  return bytes;
}

export async function verifyHostedDeployment(baseUrl, pinned, portalOrigin = null) {
  const prefix = `${baseUrl}/${pinned.lab_id}/${pinned.build_version}`;
  const manifestBytes = await fetchVerifiedBytes(`${prefix}/manifest.json`, null, { portalOrigin });
  const hostedManifest = JSON.parse(manifestBytes.toString('utf8'));
  assert.deepEqual(hostedManifest, pinned, 'Hosted manifest differs from reviewed repo manifest');

  const htmlBytes = await fetchVerifiedBytes(`${prefix}/index.html`, null, { csp: true, portalOrigin });
  const jsBytes = await fetchVerifiedBytes(`${prefix}/${pinned.bundle_asset}`, pinned.bundle_sha256, { portalOrigin, cors: true });
  await fetchVerifiedBytes(`${prefix}/LICENSE`, pinned.license_sha256, {});

  const htmlText = htmlBytes.toString('utf8');
  assert.ok(
    htmlText.includes(`./${pinned.bundle_asset}`),
    `Hosted index.html does not reference pinned asset ./${pinned.bundle_asset}`
  );
  const jsText = jsBytes.toString('utf8');
  assert.doesNotMatch(jsText, /generativelanguage\.googleapis\.com/i);

  const localHeaders = fs.readFileSync(path.join(rootDir, 'dist', pinned.lab_id, pinned.build_version, '_headers'));
  const artifact = crypto.createHash('sha256').update(htmlBytes).update(jsBytes).update(localHeaders).digest('hex');
  assert.equal(artifact, pinned.artifact_sha256, 'Hosted artifact bytes do not match reviewed artifact_sha256');
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === path.resolve(import.meta.filename);
if (isMain) {
  const opts = parseArgs(process.argv.slice(2));
  const pinned = verifyLocalBuild();
  if (opts.url) {
    await verifyHostedDeployment(opts.url, pinned, opts.portalOrigin);
    console.log(JSON.stringify({ status: 'ok', mode: 'local+remote', url: opts.url, build_version: pinned.build_version, build_sha256: pinned.build_sha256 }));
  } else {
    console.log(JSON.stringify({ status: 'ok', mode: 'local', build_version: pinned.build_version, build_sha256: pinned.build_sha256 }));
  }
}
