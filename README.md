# ReproCrowd (desktop app)

> **PLEASE NOTE:** This is a vibecoded prototype. Use under your own risk, etc.

A self-contained Electron app for AI-assisted computational reproductions of
scholarly papers, in the visual identity of
[forrt.org/reprocrowd](https://forrt.org/reprocrowd/). It brings together the
ReproCrowd backend (`backend/`, a REST service and the `rc` command line, also
usable on its own) and the workflow UI of the ReproCrowd Zotero plugin, without
Zotero. Finished packages are
**deposited on Zenodo** instead of the ReproCrowd Database.

## Install

You need [Node.js](https://nodejs.org) 20 or newer and [uv](https://docs.astral.sh/uv/)
(`curl -LsSf https://astral.sh/uv/install.sh | sh`). Then, in a terminal:

```sh
git clone https://github.com/forrtproject/reprocrowd-app
reprocrowd-app/scripts/install.sh
```

That builds the app with its own Python and backend, and installs it for you: on
Linux in the app menu (and as the `reprocrowd` command), on macOS in
`~/Applications`. It takes a few minutes the first time. Run the same script
again after `git pull` to update; `scripts/install.sh --uninstall` removes the
app (your reproductions and settings stay). Windows: use WSL. macOS is untested.

For the AI steps, install [Claude Code](https://claude.com/claude-code) and sign
in once (`claude`), or configure another LLM command in Settings → Backend. To
run reproductions you also need what the paper's analysis uses (R, Python, …).

## Using it

- **Library**: your reproductions. **New reproduction** takes a DOI, looked up
  in OpenAlex. **Only articles with JATS XML are supported at this time**: the
  app checks that Europe PMC serves the article's full-text XML (the open-access
  subset of PubMed Central) and refuses the paper otherwise. It also checks for
  ReproCrowd records of that paper already on Zenodo. The package goes in a
  folder (default `~/ReproCrowd/<doi>`); the JATS XML is downloaded and parsed
  right away. **Open a package folder…** adds an existing package.
- **A reproduction** is a numbered workflow: 1 Inputs, 2 Targets, 3 Analysis
  plan, 4 Master script, 5 Code generation, 6 Run & compare, 7 Report &
  deposit. The paper's text is its JATS XML; there is no PDF view. The study's
  data and code are added as input files from this computer (file dialogs, drag
  and drop, folders).
- **Steps 2 and 3, Reader view**: the paper, with each target (or analysis
  step) as a card in a sidebar, level with the passage it comes from and joined
  to it by a line. Hovering a card lights up its passage, and the other way
  round. Click sentences, figures or tables to mark them, then **Add … from the
  marked text** or **Generate … from the marked text** (the LLM reads only
  those). Cards can be switched off, edited, restored or deleted; items without
  a passage are listed at the top. The **Table** view shows every field,
  grouped by LLM run.
- **Step 7, Deposit on Zenodo**: after the report and the ZIP, the package is
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
backend/        the ReproCrowd backend (Python: FastAPI service + `rc` CLI), see backend/README.md
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
scripts/        build.mjs, bundle-backend.mjs, install.sh
test/e2e.mjs    end-to-end check with a fake Zenodo and the backend tests' fake LLM
```

The backend is chosen at start: Settings → Advanced start command; else the
bundled Python runtime (`build/backend`, or `resources/backend` when
packaged); else, in a development checkout, `uv run --project backend`. The app's data (settings, tokens, backend config, the
package registry, per-package UI state) lives in Electron's userData folder
(`~/.config/ReproCrowd` on Linux; `REPROCROWD_USER_DATA` overrides).

## Build

```sh
npm install
npm start                 # build and run (uses ../reprocrowd-cli through uv if there is no bundled backend)
npm run bundle-backend    # build/backend: a standalone Python with reprocrowd installed
npm run dist              # installers for this system in dist/ (with the bundled backend)
node test/e2e.mjs         # after npm run build
npm run test:backend      # the backend's tests (pytest)
npm run schemas           # regenerate backend/schemas and docs after model or API changes
```

`backend/` came from the standalone reprocrowd-cli repository (which the
ReproCrowd Zotero plugin also uses) with `git subtree`, history included.
Changes can go either way:

```sh
git subtree pull --prefix=backend ../reprocrowd-cli master   # take the standalone repo's changes
git subtree push --prefix=backend ../reprocrowd-cli app-sync # offer this repo's changes as a branch there
```
