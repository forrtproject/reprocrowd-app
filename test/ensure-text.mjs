// Fast check of ensureText's fallback between JATS sources (Europe PMC, then PLOS), against a scripted backend.
// Electron is stubbed and the backend's HTTP client replaced, so nothing is started.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const anything = new Proxy(function () {}, { get: (_, k) => (k === Symbol.toPrimitive ? () => '' : anything), apply: () => anything, construct: () => anything });
globalThis.__anything = anything;
const stubs = {
  name: 'stubs',
  setup(b) {
    b.onResolve({ filter: /^electron(-updater)?$/ }, () => ({ path: 'electron', namespace: 'stub' }));
    b.onResolve({ filter: /^\.\/service$/ }, () => ({ path: 'service', namespace: 'stub' }));
    b.onLoad({ filter: /^electron$/, namespace: 'stub' }, () => ({ contents: 'module.exports = globalThis.__anything;', loader: 'js' }));
    b.onLoad({ filter: /^service$/, namespace: 'stub' }, () => ({
      loader: 'js',
      contents: `export class ServiceError extends Error { constructor(m, p) { super(m); this.problem = p; } }
        globalThis.__ServiceError = ServiceError;
        export const request = (method, p, opts = {}) => globalThis.__backend(method, p, opts);
        export const get = async (p, query) => (await request("GET", p, { query })).data;
        export const post = async (p, json) => (await request("POST", p, { json })).data;`,
    }));
  },
};
// Packages stay external, so the bundle goes where node_modules resolves (test-output/, not committed).
const outfile = path.resolve('test-output', `ensure-text-${process.pid}.mjs`);
await build({ entryPoints: [path.resolve('src/main/api.ts')], bundle: true, outfile, format: 'esm', platform: 'node', packages: 'external', plugins: [stubs], logLevel: 'error' });
let mod;
try {
  mod = await import(pathToFileURL(outfile).href);
} finally {
  fs.rmSync(outfile, { force: true });
}
const { ensureText } = mod;
const ServiceError = globalThis.__ServiceError;

const EPMC = { id: 'c-epmc', label: 'Europe PMC JATS (PMC1)', format: 'jats' };
const PLOS = { id: 'c-plos', label: 'PLOS JATS', format: 'jats' };
const fail = (code) => { throw new ServiceError(`${code}!`, { status: 422, code }); };

/** Runs ensureText on a package with no full text, against `answers` (candidate id → what downloading and parsing do). */
async function run(answers) {
  const calls = [];
  let n = 0;
  globalThis.__backend = async (method, p, opts) => {
    calls.push(`${method} ${p.replace('/v1/packages/pkg', '')}${opts.json?.candidate ? ` ${opts.json.candidate}` : ''}`);
    if (method === 'GET' && /\/(fulltexts|extractions)$/.test(p)) return { data: [] };
    if (p.endsWith('/fulltexts/candidates')) return { data: { candidates: [EPMC, PLOS], notes: [] } };
    if (method === 'POST' && p.endsWith('/fulltexts')) {
      const a = answers[opts.json.candidate];
      if (a.download) fail(a.download);
      return { data: { name: `ft-jats${n++ ? `-${n}` : ''}`, candidate: opts.json.candidate } };
    }
    if (method === 'POST' && p.endsWith('/extractions')) {
      const a = answers[calls.filter((c) => c.startsWith('POST /fulltexts')).at(-1).split(' ').at(-1)];
      if (a.parse) fail(a.parse);
      return { data: { name: 'ex-jats' } };
    }
    if (method === 'DELETE') return { data: null };
    throw new Error(`unexpected ${method} ${p}`);
  };
  try {
    return { name: await ensureText('pkg'), calls };
  } catch (e) {
    return { error: e, calls };
  }
}

// Europe PMC's file can't be parsed: it is deleted, and PLOS's is used.
let r = await run({ 'c-epmc': { parse: 'parse_failed' }, 'c-plos': {} });
assert.equal(r.name, 'ex-jats');
assert.deepEqual(r.calls.slice(3), ['POST /fulltexts c-epmc', 'POST /extractions', 'DELETE /fulltexts/ft-jats', 'POST /fulltexts c-plos', 'POST /extractions']);
// Europe PMC's can't be downloaded: PLOS's is used, nothing to delete.
r = await run({ 'c-epmc': { download: 'download_failed' }, 'c-plos': {} });
assert.equal(r.name, 'ex-jats');
assert.ok(!r.calls.some((c) => c.startsWith('DELETE')));
// Neither works: refused, and no unparseable file is left behind.
r = await run({ 'c-epmc': { download: 'integrity_check_failed' }, 'c-plos': { parse: 'parse_failed' } });
assert.equal(r.error.problem.code, 'jats_unavailable');
assert.match(r.error.message, /PLOS JATS: parse_failed!/);
assert.equal(r.calls.at(-1), 'DELETE /fulltexts/ft-jats');
// Any other failure isn't the source's fault: it stops there.
r = await run({ 'c-epmc': { parse: 'backend_timeout' }, 'c-plos': {} });
assert.equal(r.error.problem.code, 'backend_timeout');
assert.ok(!r.calls.includes('POST /fulltexts c-plos'));
console.log('ensureText falls back between JATS sources and cleans up after a parse failure.');
