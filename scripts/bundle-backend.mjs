#!/usr/bin/env node
// Bundle the ReproCrowd backend into build/backend: a relocatable CPython (python-build-standalone, as uv installs
// it) with the backend (backend/) and its locked dependencies installed into it. The packaged app runs
// build/backend/python/bin/python3 (Windows: python/python.exe) -m reprocrowd serve, so users need neither Python
// nor uv. Runs on Linux, macOS and Windows (npm can't run .sh scripts on Windows).
//
//   node scripts/bundle-backend.mjs [path/to/backend]   (default: backend/)
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const CLI = path.resolve(root, process.argv[2] || "backend");
const readOr = (f, d) => (fs.existsSync(f) ? fs.readFileSync(f, "utf8").trim() || d : d);
const PYVER = readOr(path.join(CLI, ".python-version"), "3.13");
const OUT = path.join(root, "build", "backend");
const PYOUT = path.join(OUT, "python");
const WORK = fs.mkdtempSync(path.join(os.tmpdir(), "rc-backend-"));
// Slow or flaky networks: wait longer than uv's 30 s default and retry, unless the caller set these already.
const env = { ...process.env, UV_HTTP_TIMEOUT: process.env.UV_HTTP_TIMEOUT || "300", UV_HTTP_RETRIES: process.env.UV_HTTP_RETRIES || "5" };

const run = (cmd, args, opts = {}) => {
  const r = spawnSync(cmd, args, { stdio: "inherit", env, ...opts });
  if (r.error) throw new Error(`${cmd}: ${r.error.message}`);
  if (r.status !== 0) throw new Error(`${cmd} ${args.join(" ")} exited with ${r.status}`);
};
const capture = (cmd, args, opts = {}) => {
  const r = spawnSync(cmd, args, { encoding: "utf8", env, ...opts });
  if (r.error) throw new Error(`${cmd}: ${r.error.message}`);
  if (r.status !== 0) throw new Error(`${cmd} ${args.join(" ")} exited with ${r.status}: ${r.stderr}`);
  return r.stdout.trim();
};
// Up to 3 attempts; downloads that finished stay in uv's cache, so a retry only fetches what is missing.
const retry = (fn) => {
  for (let n = 1; ; n++) {
    try {
      return fn();
    } catch (err) {
      if (n === 3) throw err;
      console.error("  (network hiccup, retrying…)");
    }
  }
};
const rmIf = (p) => fs.rmSync(p, { recursive: true, force: true });
/** Visit every entry under dir; return false from visit() to not descend into a directory. */
const walk = (dir, visit) => {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (visit(p, e) !== false && e.isDirectory()) walk(p, visit);
  }
};

try {
  console.log(`• standalone Python ${PYVER}`);
  retry(() => run("uv", ["python", "install", "--quiet", "--no-bin", PYVER]));
  // realpath: uv may hand out a link; the real binary is <prefix>/bin/python3.X (Unix) or <prefix>/python.exe (Windows).
  const pybin = fs.realpathSync(capture("uv", ["python", "find", "--no-project", PYVER], { cwd: WORK, env: { ...env, UV_MANAGED_PYTHON: "1" } }));
  const pyhome = /[/\\]bin[/\\]python3(\.\d+)?$/.test(pybin) ? path.dirname(path.dirname(pybin)) : path.dirname(pybin);
  if (fs.existsSync(path.join(pyhome, "pyvenv.cfg"))) throw new Error(`found a virtualenv instead of a standalone Python: ${pyhome}`);

  rmIf(OUT);
  fs.mkdirSync(OUT, { recursive: true });
  // verbatimSymlinks: keep bin/python3 -> python3.X relative. Without it Node rewrites links to absolute paths into
  // uv's store, and the "bundled" Python would really be the build machine's.
  fs.cpSync(pyhome, PYOUT, { recursive: true, verbatimSymlinks: true });
  const pyUnix = path.join(PYOUT, "bin", "python3");
  const py = fs.existsSync(pyUnix) ? pyUnix : path.join(PYOUT, "python.exe");
  if (!fs.existsSync(py)) throw new Error(`no Python interpreter in ${PYOUT} (copied from ${pyhome})`);
  // EXTERNALLY-MANAGED (lib/python3.X/ on Unix, Lib/ on Windows) makes installers refuse this interpreter.
  walk(PYOUT, (p, e) => void (e.name === "EXTERNALLY-MANAGED" && fs.rmSync(p, { force: true })));

  console.log(`• reprocrowd from ${CLI} (locked dependencies)`);
  const req = path.join(WORK, "requirements.txt");
  fs.writeFileSync(req, capture("uv", ["export", "--quiet", "--frozen", "--no-dev", "--no-hashes", "--no-emit-project", "--format", "requirements-txt"], { cwd: CLI }) + "\n");
  run("uv", ["build", "--quiet", "--wheel", "--out-dir", path.join(WORK, "dist")], { cwd: CLI });
  const wheels = fs.readdirSync(path.join(WORK, "dist")).filter((f) => f.endsWith(".whl")).map((f) => path.join(WORK, "dist", f));
  if (!wheels.length) throw new Error("uv build produced no wheel");
  try {
    retry(() => run("uv", ["pip", "install", "--quiet", "--python", py, "--link-mode=copy", "-r", req]));
    run("uv", ["pip", "install", "--quiet", "--python", py, "--link-mode=copy", "--no-deps", ...wheels]);
  } catch (err) {
    // Fallback: the interpreter's own pip (some uv versions refuse a non-virtualenv interpreter).
    console.error(`  uv pip failed (${err.message}); using the bundled Python's pip instead`);
    run(py, ["-m", "ensurepip"]);
    retry(() => run(py, ["-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--retries", "5", "--timeout", "300", "-r", req]));
    run(py, ["-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--no-deps", ...wheels]);
  }

  console.log("• trimming");
  const libName = fs.readdirSync(PYOUT).find((e) => /^lib$/i.test(e)); // lib (Unix) or Lib (Windows)
  const libPath = libName ? path.join(PYOUT, libName) : null;
  const pyX = libPath ? fs.readdirSync(libPath).find((e) => /^python3\.\d+$/.test(e)) : null;
  const LIB = pyX ? path.join(libPath, pyX) : libPath; // the standard library
  if (LIB) for (const d of ["test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3"]) rmIf(path.join(LIB, d));
  if (libPath) for (const e of fs.readdirSync(libPath)) if (/^(libtcl|libtk|tcl\d|tk\d)/i.test(e)) rmIf(path.join(libPath, e));
  rmIf(path.join(PYOUT, "share"));
  walk(PYOUT, (p, e) => (e.isDirectory() && e.name === "__pycache__" ? (rmIf(p), false) : true));
  if (LIB) spawnSync(py, ["-m", "compileall", "-q", "-j", "0", LIB], { stdio: "ignore" }); // best effort

  console.log("• check");
  run(py, ["-c", "import reprocrowd, reprocrowd.api.app, sys; print('reprocrowd', reprocrowd.__version__, 'on Python', sys.version.split()[0])"]);
  try {
    fs.writeFileSync(path.join(OUT, "REVISION"), capture("git", ["-C", CLI, "rev-parse", "--short", "HEAD"]) + "\n");
  } catch {}
  console.log(`• ${path.relative(root, OUT)}`);
} finally {
  rmIf(WORK);
}
