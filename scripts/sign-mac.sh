#!/usr/bin/env bash
# Sign the unpacked macOS app with an Apple Development certificate (a free Xcode Personal Team is enough). macOS lets an
# app read its keychain item ("ReproCrowd Safe Storage", Electron's encryption key) only from the same "partition": for
# Apple-issued certificates that is the Team ID, the same for every release; for ad-hoc or self-signed builds it is
# the build's own code hash, so every update would ask for the keychain password again. Gatekeeper still asks users to
# "Open Anyway": the app isn't notarized, which needs a paid Developer ID.
#
#   MAC_SIGNING_P12_BASE64=… MAC_SIGNING_P12_PASSWORD=… scripts/sign-mac.sh dist/mac-arm64/ReproCrowd.app
#
# The certificate goes into a temporary keychain, removed again afterwards; the user's keychain list is restored.
# Run after `electron-builder --mac --dir` (which flips the Electron fuses), before building the dmg with --prepackaged.
set -euo pipefail

app="${1:?usage: scripts/sign-mac.sh path/to/ReproCrowd.app}"
: "${MAC_SIGNING_P12_BASE64:?MAC_SIGNING_P12_BASE64 is not set}"
: "${MAC_SIGNING_P12_PASSWORD:?MAC_SIGNING_P12_PASSWORD is not set}"

work="$(mktemp -d)"
kc="$work/reprocrowd-signing.keychain-db"
kc_password="$(openssl rand -hex 16)"
original_keychains=()
while IFS= read -r line; do
  line="${line#"${line%%[![:space:]]*}"}"; line="${line#\"}"; line="${line%\"}"
  [ -n "$line" ] && original_keychains+=("$line")
done < <(security list-keychains -d user)

cleanup() {
  security list-keychains -d user -s "${original_keychains[@]}" || true
  security delete-keychain "$kc" 2>/dev/null || true
  rm -rf "$work"
}
trap cleanup EXIT

printf '%s' "$MAC_SIGNING_P12_BASE64" | base64 --decode > "$work/signing.p12"
security create-keychain -p "$kc_password" "$kc"
security set-keychain-settings -lut 3600 "$kc"
security unlock-keychain -p "$kc_password" "$kc"
security import "$work/signing.p12" -k "$kc" -P "$MAC_SIGNING_P12_PASSWORD" -T /usr/bin/codesign >/dev/null
# Lets codesign use the key without asking (a prompt would hang CI).
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$kc_password" "$kc" >/dev/null
security list-keychains -d user -s "$kc" "${original_keychains[@]}"

identity="$(security find-identity -v -p codesigning "$kc" | awk '/"Apple Development: / { print $2; exit }')"
[ -n "$identity" ] || { echo "No valid Apple Development identity in the p12 file (it needs the key and Apple's WWDR G3 intermediate)." >&2; exit 1; }

# No hardened runtime: it would need Electron's JIT entitlements, and is only required for notarization.
codesign --force --deep --timestamp=none --keychain "$kc" --sign "$identity" "$app"
codesign --verify --deep --strict "$app"
info="$(codesign -dv "$app" 2>&1)"
grep -E '^(Identifier|TeamIdentifier)=' <<<"$info"
# Without a Team ID, updates would not share a keychain partition: fail rather than release such a build.
grep -Eq '^TeamIdentifier=[A-Z0-9]{10}$' <<<"$info" || { echo "The signature has no Team ID." >&2; exit 1; }
