#!/usr/bin/env node
// Bundle the ReproCrowd backend into build/backend: a relocatable CPython (python-build-standalone, as uv installs
// it) with the backend (backend/) and its locked dependencies installed into it. The packaged app runs
// build/backend/python/bin/python3 -m reprocrowd serve, so users need neither Python nor uv.
//
// Cross-platform replacement for scripts/bundle-backend.sh (npm can't run .sh on Windows).
//   node scripts/bundle-backend.mjs [path/to/backend]   (default: backend/)
import { execSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const CLI = path.resolve(root, process.argv[2] || "backend");
const PYVER = fs.readFileSync(path.join(CLI, ".python-version"), "utf8").trim() || "3.13";
const OUT = path.join(root, "build", "backend");
const WORK = fs.mkdtempSync(path.join(os.tmpdir(), "rc-backend-"));

const run = (cmd, args, opts = {}) => {
  const r = spawnSync(cmd, args, { stdio: "inherit", ...opts });
  if (r.status !== 0) throw new Error(`${cmd} ${args.join(" ")} exited with ${r.status}`);
  return r;
};
const capture = (cmd, args, opts = {}) => {
  const r = spawnSync(cmd, args, { encoding: "utf8", ...opts });
  if (r.status !== 0) throw new Error(`${cmd} ${args.join(" ")} exited with ${r.status}: ${r.stderr}`);
  return r.stdout.trim();
};
const rmIf = (p) => fs.rmSync(p, { recursive: true, force: true });

try {
  console.log(`• standalone Python ${PYVER}`);
  run("uv", ["python", "install", "--quiet", "--no-bin", PYVER]);
  let pybin = capture("uv", ["python", "find", "--no-project", PYVER], { cwd: WORK, env: { ...process.env, UV_MANAGED_PYTHON: "1" } });
  pybin = fs.realpathSync(pybin);
  const pyhome = /bin[/\\]python3$/.test(pybin) ? path.dirname(path.dirname(pybin)) : path.dirname(pybin);
  if (fs.existsSync(path.join(pyhome, "pyvenv.cfg"))) throw new Error(`found a virtualenv instead of a standalone Python: ${pyhome}`);

  rmIf(OUT);
  fs.mkdirSync(OUT, { recursive: true });
  fs.cpSync(pyhome, path.join(OUT, "python"), { recursive: true });
  const pyUnix = path.join(OUT, "python", "bin", "python3");
  const py = fs.existsSync(pyUnix) ? pyUnix : path.join(OUT, "python", "python.exe");

  // Remove EXTERNALLY-MANAGED (it blocks ensurepip on a standalone Python).
  const walk = (dir) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) walk(p);
      else if (e.name === "EXTERNALLY-MANAGED") fs.rmSync(p, { force: true });
    }
  };
  walk(path.join(OUT, "python"));

  console.log(`• reprocrowd from ${CLI} (locked dependencies)`);
  const req = capture("uv", ["export", "--quiet", "--frozen", "--no-dev", "--no-hashes", "--no-emit-project", "--format", "requirements-txt"], { cwd: CLI });
  fs.writeFileSync(path.join(WORK, "requirements.txt"), req + "\n");
  run("uv", ["build", "--quiet", "--wheel", "--out-dir", path.join(WORK, "dist")], { cwd: CLI });
  // uv pip install --python rejects a non-venv standalone Python (uv 0.5+); use the interpreter's own pip.
  run(py, ["-m", "ensurepip"]);
  run(py, ["-m", "pip", "install", "--quiet", "--no-cache-dir", "-r", path.join(WORK, "requirements.txt")]);
  const wheels = fs.readdirSync(path.join(WORK, "dist")).filter((f) => f.endsWith(".whl")).map((f) => path.join(WORK, "dist", f));
  if (!wheels.length) throw new Error("uv build produced no wheel");
  run(py, ["-m", "pip", "install", "--quiet", "--no-cache-dir", "--no-deps", ...wheels]);

  console.log("• trimming");
  const pyHome2 = path.join(OUT, "python");
  const libName = fs.readdirSync(pyHome2).find((e) => /^lib$/i.test(e));
  const libPath = libName ? path.join(pyHome2, libName) : null;
  const pyX = libPath ? fs.readdirSync(libPath).find((e) => /^python3\.\d+$/.test(e)) : null;
  const LIB = pyX ? path.join(libPath, pyX) : libPath;
  if (LIB) for (const d of ["test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3"]) rmIf(path.join(LIB, d));
  if (libPath) for (const e of fs.readdirSync(libPath)) if (/^(libtcl|libtk|tcl|tk)/i.test(e)) rmIf(path.join(libPath, e));
  rmIf(path.join(pyHome2, "share"));
  const rmPycache = (dir) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) {
        if (e.name === "__pycache__") rmIf(p);
        else rmPycache(p);
      }
    }
  };
  if (LIB) rmPycache(LIB);

  if (LIB) run(py, ["-m", "compileall", "-q", "-j", "0", LIB], { stdio: "ignore" }).status;
  run(py, ["-c", "import reprocrowd, reprocrowd.api.app, sys; print('reprocrowd', reprocrowd.__version__, 'on Python', sys.version.split()[0])"]);
  try {
    fs.writeFileSync(path.join(OUT, "REVISION"), capture("git", ["-C", CLI, "rev-parse", "--short", "HEAD"]));
  } catch {}
  console.log(`• ${OUT}`);
} finally {
  rmIf(WORK);
}
