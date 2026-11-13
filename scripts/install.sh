#!/usr/bin/env bash
# Build ReproCrowd from source and install it for the current user, so it starts from the app menu / Launchpad.
#
#   scripts/install.sh                              build and install
#   scripts/install.sh --uninstall                  remove the app (your packages and settings stay)
#
# Needs: Node.js >= 20 (with npm) and uv. Linux and macOS; on Windows, use WSL.
# Installs to:  Linux  ~/.local/share/reprocrowd/app, a menu entry, and ~/.local/bin/reprocrowd
#               macOS  ~/Applications/ReproCrowd.app
set -euo pipefail
cd "$(dirname "$0")/.."

DATA="${REPROCROWD_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}}"
LINUX_DIR="$DATA/reprocrowd/app"
DESKTOP="$DATA/applications/reprocrowd.desktop"
BIN="${REPROCROWD_BIN_DIR:-$HOME/.local/bin}/reprocrowd"
MAC_APP="${REPROCROWD_MAC_APPS:-$HOME/Applications}/ReproCrowd.app"
OS="$(uname -s)"

say() { printf '\033[1;35m•\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }

if [ "${1:-}" = "--uninstall" ]; then
  rm -rf "$LINUX_DIR" "$DESKTOP" "$BIN" "$MAC_APP"
  say "ReproCrowd removed. Your reproductions and settings were kept."
  [ -f /etc/apparmor.d/reprocrowd ] && say "Its AppArmor profile stays; remove it with:  sudo rm /etc/apparmor.d/reprocrowd"
  exit 0
fi

# ---------- prerequisites ----------
case "$OS" in Linux | Darwin) ;; *) die "This script is for Linux and macOS. On Windows, run it in WSL." ;; esac
command -v node >/dev/null && command -v npm >/dev/null || die "Node.js is missing. Install Node.js 20 or newer: https://nodejs.org (macOS: brew install node)."
[ "$(node -p 'process.versions.node.split(".")[0]')" -ge 20 ] || die "Node.js $(node -v) is too old; ReproCrowd needs 20 or newer: https://nodejs.org"
command -v uv >/dev/null || die "uv is missing. Install it with:  curl -LsSf https://astral.sh/uv/install.sh | sh   (then open a new terminal)"

# ---------- build ----------
say "Installing the build tools (npm)…"
npm ci --no-audit --no-fund --loglevel=error
say "Building the app…"
npm run --silent build
say "Bundling the backend with its own Python (takes a minute)…"
node scripts/bundle-backend.mjs backend
say "Fetching Electron (~100 MB, once; kept for later installs)…"
# electron-builder downloads Electron in one request that it abandons after 10 minutes, so on a slow network it
# can never finish. curl resumes where it stopped, and the checked zip is handed over with electronDist.
EVER="$(node -p 'require("electron/package.json").version')"
case "$(uname -m)" in arm64 | aarch64) EARCH=arm64 ;; x86_64) EARCH=x64 ;; *) die "Unsupported CPU: $(uname -m)" ;; esac
[ "$OS" = Linux ] && EPLAT=linux || EPLAT=darwin
ECACHE="${ELECTRON_CACHE:-$([ "$OS" = Darwin ] && echo "$HOME/Library/Caches" || echo "${XDG_CACHE_HOME:-$HOME/.cache}")/electron}"
EZIP="$ECACHE/electron-v$EVER-$EPLAT-$EARCH.zip"
EURL="${ELECTRON_MIRROR:-https://github.com/electron/electron/releases/download/}v$EVER"
mkdir -p "$ECACHE"
# sha256sum (coreutils) on Linux; shasum ships with Perl, which minimal Linux images lack, and is what macOS has.
sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }
EWANT="$(curl -fsSL --retry 5 "$EURL/SHASUMS256.txt" | grep " \*$(basename "$EZIP")\$" | cut -d' ' -f1)" ||
  die "Could not fetch Electron's checksums from $EURL"
if [ ! -f "$EZIP" ] || [ "$(sha256 "$EZIP")" != "$EWANT" ]; then
  rm -f "$EZIP"
  for n in 1 2 3 4 5; do
    curl -fL --retry 5 --retry-all-errors --progress-bar -C - -o "$EZIP.part" "$EURL/$(basename "$EZIP")" && break
    [ "$n" = 5 ] && die "Could not download Electron $EVER; run the installer again to resume."
    echo "  (network hiccup, resuming…)" >&2
  done
  [ "$(sha256 "$EZIP.part")" = "$EWANT" ] || { rm -f "$EZIP.part"; die "The Electron download was corrupt; run the installer again."; }
  mv "$EZIP.part" "$EZIP"
fi

say "Packaging…"
export CSC_IDENTITY_AUTO_DISCOVERY=false # a local build needs no signing certificate
LOG="$(mktemp)"
[ "$OS" = Linux ] && TARGET=--linux || TARGET=--mac
npx electron-builder "$TARGET" dir --publish never -c.electronDist="$EZIP" >"$LOG" 2>&1 ||
  { tail -20 "$LOG" >&2; die "Packaging failed (full log: $LOG)."; }
rm -f "$LOG"

# ---------- install ----------
if [ "$OS" = Linux ]; then
  rm -rf "$LINUX_DIR"
  mkdir -p "$(dirname "$LINUX_DIR")" "$(dirname "$DESKTOP")" "$(dirname "$BIN")"
  # electron-builder names the folder linux-unpacked on x64 and linux-<arch>-unpacked on every other CPU
  UNPACKED=dist/linux-unpacked
  [ "$EARCH" = x64 ] || UNPACKED="dist/linux-$EARCH-unpacked"
  [ -d "$UNPACKED" ] || die "The build produced no $UNPACKED."
  cp -a "$UNPACKED" "$LINUX_DIR"
  ln -sf "$LINUX_DIR/reprocrowd-app" "$BIN"
  cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=ReproCrowd
GenericName=Computational reproductions
Comment=Crowdsourcing AI reproductions of scholarly papers
Exec="$LINUX_DIR/reprocrowd-app" %U
Icon=$LINUX_DIR/resources/hex-sticker.png
Terminal=false
Categories=Science;Education;
StartupWMClass=reprocrowd
EOF
  command -v update-desktop-database >/dev/null && update-desktop-database "$(dirname "$DESKTOP")" 2>/dev/null || true

  # ---------- can it start here? ----------
  # Electron needs GTK 3, NSS, GBM and ALSA from the system; minimal systems and container images lack them.
  MISSING="$(ldd "$LINUX_DIR/reprocrowd-app" 2>/dev/null | awk '/not found/ {print $1}' | tr '\n' ' ')"
  if [ -n "$MISSING" ]; then
    warn "ReproCrowd won't start yet: these system libraries are missing: $MISSING"
    if command -v apt-cache >/dev/null; then
      ALSA="$(apt-cache show libasound2t64 >/dev/null 2>&1 && echo libasound2t64 || echo libasound2)" # renamed in Ubuntu 24.04 / Debian 13
      warn "Install them with:  sudo apt install libgtk-3-0 libnss3 libgbm1 $ALSA"
    else
      warn "Install GTK 3, NSS, libgbm and ALSA with your package manager."
    fi
  fi
  # Ubuntu 23.10+ lets programs create the user namespaces Chromium's sandbox needs only with an AppArmor profile that
  # allows it; without one the app aborts at start ("The SUID sandbox helper binary was found, but is not configured
  # correctly"). Containers can't use the sandbox at all (see the README: start with --no-sandbox).
  if [ -f /.dockerenv ] || [ -f /run/.containerenv ]; then
    warn "In a container, start ReproCrowd with:  reprocrowd --no-sandbox   (the Chromium sandbox can't work there)"
  elif [ "$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns 2>/dev/null)" = 1 ]; then
    PROFILE=/etc/apparmor.d/reprocrowd
    EXE="$LINUX_DIR/reprocrowd-app"
    case "$EXE" in *" "*) EXE="\"$EXE\"" ;; esac
    WANT="# Lets the ReproCrowd desktop app (Electron) use its Chromium sandbox on Ubuntu 23.10+,
# which blocks unprivileged user namespaces unless an AppArmor profile allows them.
abi <abi/4.0>,
include <tunables/global>

profile reprocrowd $EXE flags=(unconfined) {
  userns,

  include if exists <local/reprocrowd>
}"
    if [ "$(cat "$PROFILE" 2>/dev/null)" != "$WANT" ]; then
      say "This system only lets ReproCrowd use its sandbox with an AppArmor profile; adding $PROFILE (needs sudo)…"
      if printf '%s\n' "$WANT" | sudo tee "$PROFILE" >/dev/null && sudo apparmor_parser -r "$PROFILE"; then
        say "AppArmor profile loaded."
      else
        warn "Could not add the AppArmor profile, so ReproCrowd will stop at start. Ask an administrator to run"
        warn "  scripts/install.sh  again with sudo rights, or start it with:  reprocrowd --no-sandbox"
      fi
    fi
  fi

  say "Installed. Start ReproCrowd from your app menu, or run: reprocrowd"
  case ":$PATH:" in *":$(dirname "$BIN"):"*) ;; *) warn "The reprocrowd command is in $(dirname "$BIN"), which is not on your PATH; add it there to use the command." ;; esac
else
  APP="$(find dist -maxdepth 2 -name 'ReproCrowd.app' -type d | head -1)"
  [ -n "$APP" ] || die "The build produced no ReproCrowd.app."
  rm -rf "$MAC_APP"
  mkdir -p "$(dirname "$MAC_APP")"
  cp -R "$APP" "$MAC_APP"
  codesign --force --deep --sign - "$MAC_APP" 2>/dev/null || true # ad-hoc signature, required on Apple Silicon
  say "Installed to $MAC_APP. Start ReproCrowd from Launchpad or Spotlight."
fi
