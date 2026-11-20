import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';

const rootDir = path.resolve(import.meta.dirname, '..');
const distDir = path.join(rootDir, 'dist');

const MIME_BY_EXT = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
};

function parseArgs(argv) {
  const opts = { host: '127.0.0.1', port: 4173 };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--host' && argv[i + 1]) {
      opts.host = argv[i + 1];
      i += 1;
    } else if (argv[i] === '--port' && argv[i + 1]) {
      opts.port = Number(argv[i + 1]);
      i += 1;
    }
  }
  return opts;
}

const cspHeader =
  "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'none'; form-action 'none'; base-uri 'none'; frame-ancestors 'self' https://lms.studytomy.com http://localhost:3000";

const opts = parseArgs(process.argv.slice(2));

const server = http.createServer((req, res) => {
  if (!req.url || (req.method !== 'GET' && req.method !== 'HEAD')) {
    res.writeHead(405, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('Method Not Allowed');
    return;
  }
  const urlPath = decodeURIComponent(req.url.split('?')[0]);
  const cleanRel = path.posix.normalize(urlPath).replace(/^\/+/, '');
  if (cleanRel.includes('..') || cleanRel === '_headers') {
    res.writeHead(403, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('Forbidden');
    return;
  }
  const target = path.resolve(distDir, cleanRel || 'index.html');
  if (!target.startsWith(distDir + path.sep) && target !== path.join(distDir, 'index.html')) {
    res.writeHead(403, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('Forbidden');
    return;
  }
  if (!fs.existsSync(target) || fs.statSync(target).isDirectory()) {
    res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('Not Found');
    return;
  }
  const ext = path.extname(target).toLowerCase();
  const isImmutableAsset = cleanRel.includes('/assets/') || cleanRel.startsWith('assets/');
  res.writeHead(200, {
    'Content-Type': MIME_BY_EXT[ext] || 'application/octet-stream',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
    'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), payment=()',
    'Content-Security-Policy': cspHeader,
    'Cache-Control': isImmutableAsset ? 'public, max-age=31536000, immutable' : 'public, max-age=0, must-revalidate',
  });
  if (req.method === 'HEAD') {
    res.end();
    return;
  }
  fs.createReadStream(target).pipe(res);
});

server.listen(opts.port, opts.host, () => {
  console.log(`Curated lab static host listening at http://${opts.host}:${opts.port}`);
});
