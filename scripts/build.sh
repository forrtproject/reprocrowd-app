#!/usr/bin/env bash
# Build the app into build/: renderer (vite), main and preload (esbuild). The bundled backend (build/backend) is made
# separately by scripts/bundle-backend.sh and is kept.
set -euo pipefail
cd "$(dirname "$0")/.."
npx tsc --noEmit -p src/renderer
npx tsc --noEmit -p src/main
npx vite build --logLevel warn
npx esbuild src/main/index.ts --bundle --platform=node --format=cjs --target=node22 --external:electron \
  --log-level=warning --outfile=build/main/index.js
npx esbuild src/preload/index.ts --bundle --platform=node --format=cjs --target=node22 --external:electron \
  --log-level=warning --outfile=build/preload/index.js
echo "built build/{renderer,main,preload}"
