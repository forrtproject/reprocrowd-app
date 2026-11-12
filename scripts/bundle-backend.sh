#!/usr/bin/env bash
# Bundle the ReproCrowd backend into build/backend: a relocatable CPython (python-build-standalone, as uv installs
# it) with reprocrowd-cli and its locked dependencies installed into it. The packaged app runs
# build/backend/python/bin/python3 -m reprocrowd serve, so users need neither Python nor uv.
#
#   scripts/bundle-backend.sh [path/to/reprocrowd-cli]      (default: ../reprocrowd-cli)
set -euo pipefail
cd "$(dirname "$0")/.."
CLI="$(realpath "${1:-../reprocrowd-cli}")"
PYVER="$(cat "$CLI/.python-version" 2>/dev/null || echo 3.13)"
OUT=build/backend
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "• standalone Python $PYVER"
uv python install --quiet "$PYVER"
PYBIN="$(cd "$WORK" && UV_MANAGED_PYTHON=1 uv python find --no-project "$PYVER")"
PYHOME="$(dirname "$(dirname "$(realpath "$PYBIN")")")"
[ ! -f "$PYHOME/pyvenv.cfg" ] || { echo "found a virtualenv instead of a standalone Python: $PYHOME" >&2; exit 1; }

rm -rf "$OUT"
mkdir -p "$OUT"
cp -a "$PYHOME" "$OUT/python"
PY="$OUT/python/bin/python3"
[ -x "$PY" ] || PY="$OUT/python/python.exe"
rm -f "$OUT/python/lib/python3"*/EXTERNALLY-MANAGED

echo "• reprocrowd from $CLI (locked dependencies)"
(cd "$CLI" && uv export --quiet --frozen --no-dev --no-hashes --no-emit-project --format requirements-txt) > "$WORK/requirements.txt"
(cd "$CLI" && uv build --quiet --wheel --out-dir "$WORK/dist")
uv pip install --quiet --python "$PY" --no-cache -r "$WORK/requirements.txt"
uv pip install --quiet --python "$PY" --no-cache --no-deps "$WORK"/dist/*.whl

echo "• trimming"
LIB="$(echo "$OUT"/python/lib/python3.*)"
rm -rf "$LIB"/{test,idlelib,tkinter,turtledemo,ensurepip,lib2to3} "$OUT"/python/lib/{libtcl,libtk}* "$OUT"/python/lib/{tcl,tk}* "$OUT"/python/share 2>/dev/null || true
find "$OUT/python" -name "__pycache__" -type d -prune -exec rm -rf {} +
"$PY" -m compileall -q -j 0 "$LIB" >/dev/null 2>&1 || true

echo "• check"
"$PY" -c "import reprocrowd, reprocrowd.api.app, sys; print('reprocrowd', reprocrowd.__version__, 'on Python', sys.version.split()[0])"
git -C "$CLI" rev-parse --short HEAD > "$OUT/REVISION" 2>/dev/null || true
du -sh "$OUT"
