#!/usr/bin/env node
// Build the app into build/: renderer (vite), main and preload (esbuild).
// Cross-platform replacement for scripts/build.sh (npm can't run .sh on Windows).
import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const run = (cmd) => execSync(cmd, { cwd: root, stdio: "inherit" });

run("npx tsc --noEmit -p src/renderer");
run("npx tsc --noEmit -p src/main");
run("npx vite build --logLevel warn");
run("npx esbuild src/main/index.ts --bundle --platform=node --format=cjs --target=node22 --external:electron --log-level=warning --outfile=build/main/index.js");
run("npx esbuild src/preload/index.ts --bundle --platform=node --format=cjs --target=node22 --external:electron --log-level=warning --outfile=build/preload/index.js");
console.log("built build/{renderer,main,preload}");
