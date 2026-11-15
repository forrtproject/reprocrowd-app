// Fast checks of model scoping and the local profile settings transaction. No Electron, processes or settings writes.
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { parse } from 'smol-toml';

async function load(file) {
  const bundled = await build({ entryPoints: [file], bundle: true, write: false, format: 'esm', platform: 'node' });
  return import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString('base64')}`);
}
globalThis.window = { reprocrowd: {} };
const { modelCompatible } = await load('src/renderer/api.ts');
const claude = { cli: 'claude', id: 'claude/sonnet' };
const open = { cli: 'opencode', id: 'vendor/model' };
const t3 = { cli: 't3', id: 'vendor/model' };
assert(modelCompatible(claude, ['/usr/local/bin/claude']));
assert(modelCompatible(open, ['C:\\tools\\opencode.cmd']));
assert(!modelCompatible(t3, ['opencode']));
assert(!modelCompatible(open, ['claude']));
assert(!modelCompatible(claude, ['wrapper', 'claude'])); // no promise about an unrecognized wrapper
assert(modelCompatible(open, ['opencode']) && modelCompatible(open, ['/other/opencode'])); // preserve across compatible profile changes
const codex = { cli: 'codex', id: 'codex/gpt-x' };
assert(modelCompatible(codex, ['codex']) && !modelCompatible(codex, ['opencode']) && !modelCompatible(open, ['codex']));
const { cliModel } = await load('src/renderer/api.ts');
assert.equal(cliModel('codex/gpt-x'), 'gpt-x'); assert.equal(cliModel('claude/opus'), 'opus'); assert.equal(cliModel('deepseek/deepseek-v4-pro'), 'deepseek/deepseek-v4-pro');
const { parseCodexModels } = await load('src/main/models.ts');
assert.deepEqual(parseCodexModels(JSON.stringify({ models: [{ slug: 'gpt-x', display_name: 'GPT X', visibility: 'list' }, { slug: 'hidden', visibility: 'hide' }] })),
  [{ cli: 'codex', provider: 'codex', id: 'codex/gpt-x', name: 'GPT X' }]);
assert.deepEqual(parseCodexModels('not json'), []);
const { replaceProfile, configuredProfiles, applyProfileChange } = await load('src/main/profiles.ts');
const original = '# comment\n[openalex]\nmailto = "test@example.org"\n[llm]\ndefault_profile = "other"\n[llm.profiles.other]\nargv = ["other"]\noutput = "text"\n[agent.profiles.existing]\nargv = ["existing"]\nevents = "text"\n';
const profile = { argv: ['my-cli', '--model', '{model}'], model: '', effort: '', timeout_s: 10, output: 'text' };
const text = replaceProfile(original, 'llm', 'fixture', profile);
const before = parse(original), after = parse(text);
assert.deepEqual(after.openalex, before.openalex);
assert.deepEqual(after.agent, before.agent);
assert.deepEqual(after.llm.profiles.other, before.llm.profiles.other);
assert.equal(after.llm.default_profile, 'other');
assert.deepEqual({ ...configuredProfiles(text).llm.fixture }, { read_files_argv: [], json_schema_argv: [], ...profile });
assert.equal(configuredProfiles(original).agent.existing.timeout_s, 7200);
assert.equal(configuredProfiles(original).llm.other.model, "");
const codexProfile = { argv: ['codex', 'exec', '--json', '-'], model: '', effort: '', timeout_s: 10, events: 'codex-json', tools: [], exec_tools: [] };
assert.equal(parse(replaceProfile(original, 'agent', 'codex', codexProfile)).agent.profiles.codex.events, 'codex-json');
// backend.ts imports Electron, so its default configuration is read from the source text.
const DEFAULT_USER_CONFIG = (await import('node:fs')).readFileSync('src/main/backend.ts', 'utf8').match(/DEFAULT_USER_CONFIG = `([^`]*)`/)[1];
for (const kind of ['llm', 'agent']) assert.equal(configuredProfiles(DEFAULT_USER_CONFIG)[kind].codex.argv[0], 'codex', `default ${kind} codex profile`);
const { execRestriction } = await load('src/renderer/api.ts');
const shipped = configuredProfiles(DEFAULT_USER_CONFIG).agent;
assert.deepEqual(Object.fromEntries(Object.entries(shipped).map(([n, p]) => [n, execRestriction(p)])),
  { claude: 'tool_allowlist', opencode: 'cli_settings', codex: 'cli_settings' });
assert.equal(execRestriction({ argv: ['opencode', 'run'], model: '', effort: '', timeout_s: 1, tools: [], exec_tools: [], no_exec_argv: [], no_exec_env: {} }), 'unsupported');
const noExec = { ...codexProfile, no_exec_argv: ['--disable', 'shell_tool'], no_exec_env: { OPENCODE_PERMISSION: '{}' } };
assert.deepEqual({ ...parse(replaceProfile(original, 'agent', 'codex', noExec)).agent.profiles.codex.no_exec_env }, { OPENCODE_PERMISSION: '{}' });
assert.throws(() => replaceProfile(original, 'agent', 'codex', { ...noExec, no_exec_env: { OPENAI_API_KEY: 'x' } }), /credential/);
assert.throws(() => replaceProfile(original, 'agent', 'codex', { ...noExec, no_exec_env: { 'BAD NAME': 'x' } }));
assert.throws(() => replaceProfile(original, 'llm', 'fixture', { ...profile, no_exec_argv: ['x'] })); // agent-only field
for (const bad of [{ argv: [] }, { timeout_s: 0 }, { timeout_s: NaN }, { output: 'invented' }, { tools: ['Edit'] }, { api_key: 'fake' }]) {
  assert.throws(() => replaceProfile(original, 'llm', 'fixture', { ...profile, ...bad }));
}
let writes = [], backups = [], restarts = 0;
await assert.rejects(applyProfileChange(original, text, {
  backup: (x) => backups.push(x), write: (x) => writes.push(x),
  restart: async () => { if (++restarts === 1) throw new Error('fixture backend start failed'); },
}), /previous configuration was restored/);
assert.deepEqual(writes, [text, original]);
assert.deepEqual(backups, [original]);
assert.equal(restarts, 2);
writes = [];
assert.equal(await applyProfileChange(original, text, { backup: () => {}, write: (x) => writes.push(x), restart: async () => {} }), text);
assert.deepEqual(writes, [text]);
console.log('Model scoping, profile preservation/validation, and failed-restart rollback passed.');
