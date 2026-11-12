# ReproCrowd (desktop app)

> **PLEASE NOTE:** This is a vibecoded prototype. Use under your own risk, etc.

A self-contained Electron app for AI-assisted computational reproductions of
scholarly papers, in the visual identity of
[forrt.org/reprocrowd](https://forrt.org/reprocrowd/). It brings together the
ReproCrowd backend (`../reprocrowd-cli`, bundled) and the workflow UI of the
Zotero plugin (`../reprocrowd-zotero`), without Zotero. Finished packages are
**deposited on Zenodo** instead of the ReproCrowd Database.

## Using it

- **Library**: your reproductions. **New reproduction** takes a DOI (looked up
  in OpenAlex, with a check for ReproCrowd records of that paper already on
  Zenodo), optionally the paper's PDF, and a folder (default
  `~/ReproCrowd/<doi>`). **Open a package folder…** adds an existing package.
- **A reproduction** is the numbered workflow of the Zotero plugin: 1 Text
  (PDF next to the paper's text), 2 Inputs, 3 Targets, 4 Analysis plan,
  5 Master script, 6 Code generation, 7 Run & compare, 8 Report & deposit.
  Files are added by path from this computer (file dialogs, drag and drop),
  folders of inputs too.
- **Step 8, Deposit on Zenodo**: after the report and the ZIP, the package is
  uploaded as a **draft** with a reserved DOI, signed with your name (and
  ORCID). Review it on Zenodo, then **Publish…** (here or on Zenodo):
  publishing registers the DOI, and a published record can't be deleted.
  *What is uploaded* can be chosen: the paper's own material (full texts,
  extractions, original figures) is included by default only when OpenAlex
  reports an open (CC/public-domain) license; the input files are included by
  default. Left-out files are listed with their hashes in the ZIP.
- **⚙ Settings**: your name / ORCID / affiliation for records; Zenodo server
  (**sandbox by default**, for testing) and a personal access token for each
  server (scopes `deposit:write`, `deposit:actions`; kept encrypted with the
  system keyring); an optional Zenodo community; the packages folder and code
  editor; the backend: status, output, restart, and its configuration (LLM and
  coding-agent profiles, GROBID/Docling, OpenAlex key) as TOML.

LLM calls and the coding agent run the commands configured in the backend
configuration, by default Claude Code (`claude`) on your PATH. Runs of the
master script use your R / Python.

## How it fits together

```
src/main/       Electron main process
  index.ts        windows, app:// protocol for the UI, IPC, quit guard (asks while jobs run)
  backend.ts      starts the bundled backend on a free port with a random token; writes its config
  service.ts      HTTP client of the backend (problem+json errors)
  api.ts          what the UI may call: library, settings, the window's package, Zenodo (token added here)
  settings.ts     settings.json and the Zenodo tokens (safeStorage)
src/preload/    the bridge: call(method, args), pathForFile(file)
src/renderer/   the UI (vite): main.ts routes to library.ts or package.ts (the workflow,
                from reprocrowd-zotero/ui), repro/report.ts is the Zenodo step, style.css the theme
resources/      the ReproCrowd hex sticker (app icon and logo)
scripts/        build.sh, bundle-backend.sh
test/e2e.mjs    end-to-end check with a fake Zenodo and the backend tests' fake LLM
```

The backend is chosen at start: Settings → Advanced start command; else the
bundled Python runtime (`build/backend`, or `resources/backend` when
packaged); else, in a development checkout, `uv run --project
../reprocrowd-cli`. The app's data (settings, tokens, backend config, the
package registry, per-package UI state) lives in Electron's userData folder
(`~/.config/ReproCrowd` on Linux; `REPROCROWD_USER_DATA` overrides).

## Build

```sh
npm install
npm start                 # build and run (uses ../reprocrowd-cli through uv if there is no bundled backend)
npm run bundle-backend    # build/backend: a standalone Python with reprocrowd installed
npm run dist              # Linux AppImage + unpacked dir in dist/ (with the bundled backend)
node test/e2e.mjs         # after npm run build
```
