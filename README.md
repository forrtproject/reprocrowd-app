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

For the AI steps, install one of the supported coding CLIs and sign in once in a
terminal: [Claude Code](https://claude.com/claude-code) (`claude`, then sign in),
[Codex CLI](https://developers.openai.com/codex) (`codex login`) or
[OpenCode](https://opencode.ai) (`opencode auth login`, e.g. for DeepSeek). The app
uses that sign-in; it never asks for API keys. Claude Code and Codex then run on your
subscription, unless `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or `CODEX_API_KEY` is set
in the environment the app starts from (the CLIs then bill that API key per use). An
OpenCode provider you add with an API key (such as DeepSeek) is billed per use to that
account. To run reproductions you also need
what the paper's analysis uses (R, Python, …).

### Choosing an AI tool

1. Start the app. The **LLM** button next to Settings shows a light: green when a CLI
   is installed, signed in and run by a profile; yellow when CLIs are installed but none
   is usable yet; red when none is installed. It opens a dialog listing each CLI's state
   (with a button that opens Claude Code's or Codex's login) and the models each offers,
   which you can turn off. OpenCode lists the providers it can use: its free models and
   those you have signed in to.
2. In that dialog, open **Coding and extraction profiles…**. Pick the
   operation (code generation or extraction), choose a preset (**Claude Code**,
   **Codex CLI**, **OpenCode** or another plain-text CLI) and save. The form says
   whether the command was found, offers only that CLI's models, and states whether
   the profile can honour "don't run the analysis". Saving restarts the backend and
   restores the previous settings if that fails. Nothing is sent to a model while you
   edit.
3. In a reproduction, choose the profile and model in step 2/3 ("Generate with LLM")
   and in step 5 (coding agent). **Show prompt** shows the exact command and prompt
   without running it. Unticking "let it run the analysis" lets the agent read and
   edit files but not run code: Claude Code by denying its Bash tool
   (`--disallowedTools Bash`), OpenCode through a permission setting that denies its
   command tools, and Codex by removing its shell and giving it ReproCrowd's read-only
   file tools instead (none of these is an OS sandbox).

### Linux notes

The installer checks these and tells you what to do:

- **System libraries.** The app needs GTK 3, NSS, libgbm and ALSA. Desktop
  systems have them; on minimal systems install them with
  `sudo apt install libgtk-3-0 libnss3 libgbm1 libasound2` (`libasound2t64` on
  Ubuntu 24.04 and Debian 13).
- **Ubuntu 23.10 and later** only allow the app's (Chromium's) sandbox with an
  AppArmor profile. There the installer asks for your password once, after
  building, installs the app to `/opt/reprocrowd` (owned by root, so no other
  program can take the place of the binary the profile trusts) and adds
  `/etc/apparmor.d/reprocrowd`. Without the profile the app stops at start with
  "The SUID sandbox helper binary was found, but is not configured correctly".
- **`reprocrowd: command not found`**: the command is in `~/.local/bin`; add
  that folder to your `PATH`.

**In a container** (Docker, Podman), the app needs a display and can't use its
sandbox. Start an X server (e.g. Xvfb, with x11vnc/noVNC to see it) and run
`reprocrowd --no-sandbox`. Give the container more shared memory
(`--shm-size=1g`). Messages about D-Bus and dconf are harmless. Docker on
Apple silicon Macs runs ARM64 Linux, which works like any other Linux.

## Using it

- **Library**: your reproductions. **New reproduction** takes a DOI, looked up
  in OpenAlex. **Only articles with JATS XML are supported at this time**: the
  app checks that Europe PMC serves the article's full-text XML (the open-access
  subset of PubMed Central), or for PLOS articles that PLOS does, and refuses
  the paper otherwise. It also checks for
  ReproCrowd records of that paper already on Zenodo. The package goes in a
  folder (default `~/ReproCrowd/<doi>`); the JATS XML is downloaded and parsed
  right away. **Open a package folder…** adds an existing package.
- **A reproduction** is a numbered workflow: 1 Inputs, 2 Targets, 3 Analysis
  plan, 4 Master script, 5 Code generation, 6 Run & compare, 7 Report &
  deposit. The paper's text is its JATS XML; there is no PDF view. The study's
  published data are added as input files from this computer (file dialogs, drag
  and drop, folders). The analysis is rebuilt from the procedures described in
  the paper; the authors' code is not used in this version.
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
- **Pilot on GitHub** (same step): a lighter, public, **unreviewed** record before
  Zenodo, in [forrtproject/reprocrowd-pilots](https://github.com/forrtproject/reprocrowd-pilots)
  by default. Each reproduction gets one issue. Each run gets a pre-release with the ZIP, `report.html`,
  `comparison.json`, `execution.json`, `licenses.json` and `pilot.json`, verified by SHA-256, plus a comment on
  the issue. Submitting the same ZIP again creates nothing new, and an interrupted
  submission resumes. A new run becomes v2 on the same issue. Any GitHub account can
  submit: the files go to your fork of the repository (its owner, who can't fork it, uploads a
  draft release there instead), and its Action copies them into its own immutable release. The app uses the GitHub CLI login (`gh auth login`). New from DOI and this
  step list the pilots others have opened for the same paper, as they do for Zenodo records. A numeric match is
  not a scientific certificate. See [the contract](docs/contracts.md#github-pilots-and-output-licences).
- **⚙ Settings**: your name / ORCID / affiliation for records; Zenodo server
  (**sandbox by default**, for testing) and a personal access token for each
  server (scopes `deposit:write`, `deposit:actions`; kept encrypted with the
  system keyring); an optional Zenodo community; the packages folder and code
  editor; the backend: status, output, restart, and its configuration (LLM and
  coding-agent profiles, GROBID/Docling, OpenAlex key) as TOML.

LLM calls and the coding agent run the commands configured in the backend
configuration, by default Claude Code (`claude`) on your PATH. The **LLM** button →
**Coding and extraction profiles…** adds or edits a profile without writing TOML.
Profiles expose their output protocol and execution restrictions; model selection never
switches the configured command. Runs of the
master script use your R / Python.

See [the implemented workflow, comparison/evidence and provider contracts](docs/contracts.md)
for printed precision, recreation versus original-code tracks, setup and offline rerun checks.

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
test/quit-early.mjs  quitting while the backend starts closes the app and stops the backend
test/smoke.mjs  offline check of a packaged app: start, example, new from file, run, report, ZIP, quit (CI, all systems)
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
node test/quit-early.mjs  # likewise; or give it a packaged app (dist/linux-unpacked/reprocrowd-app)
node test/smoke.mjs       # after npm run dist: the offline check of the packaged app in dist/
npm run test:backend      # the backend's tests (pytest)
npm run schemas           # regenerate backend/schemas and docs after model or API changes
```

## Checking Windows, macOS and Linux before merging

The test workflow runs on every pull request, on Linux only. To check that the
packaged app works on all three systems, add the label `build-all` to the pull
request. The Release workflow then builds the installers on each system and runs
`test/smoke.mjs` twice: on the unpacked app, and on the copy its installer puts in
place (NSIS installer, dmg, AppImage). The check starts the app, opens a bundled
example, creates a reproduction from a local paper, runs a small Python analysis,
builds the report and ZIP, and quits, all without network access. It runs again
after each later push and takes about 5 minutes.

Do this before merging changes to packaging, `package.json` or the lock files,
`src/main/`, `scripts/` or the backend's dependencies. You can also run the Release
workflow by hand on any branch from the Actions tab; that builds without releasing.

## Releases and updates

Bump `version` in package.json, commit, and push a tag `v<version>`: the Release
workflow builds the Windows installer, the Linux AppImage and the macOS dmg and
attaches them to a GitHub Release. Installed copies check that release a little
after start (Settings → Updates; Help → Check for Updates…):

- Windows (installer) and Linux (AppImage) download the new version and install it on restart.
- macOS and Linux installs from `scripts/install.sh` are told about the new version and where
  to get it. On macOS the updater always works this way (`src/main/updater.ts`); installing
  updates itself would need a Developer ID signature and a change to the updater.
- The Windows installer isn't code-signed, and the macOS app is signed but not notarized. On
  Windows, SmartScreen asks first ("More info → Run anyway"). On macOS (Apple silicon only), use
  System Settings → Privacy & Security → Open Anyway, or, if the app is reported as "damaged",
  `xattr -cr /Applications/ReproCrowd.app`. The release notes repeat this for users.
- Released macOS apps are signed with an Apple Development certificate from a free Xcode Personal
  Team (`scripts/sign-mac.sh`). Every release then has the same Team ID, so macOS lets updates read
  the app's keychain item ("ReproCrowd Safe Storage") without asking again; ad-hoc builds would ask
  after every update. The certificate is in the secrets `MAC_SIGNING_P12_BASE64` and
  `MAC_SIGNING_P12_PASSWORD` of the `mac-signing` environment, which only `main` and `v*` tags may
  use. Builds from other branches and pull requests stay ad-hoc signed, and a tag release fails
  without the certificate. It expires on 20 November 2027: renew it in Xcode (Settings → Accounts →
  Manage Certificates), export it with Apple's WWDR G3 intermediate as a `.p12`, and replace both
  secrets. The Team ID stays the same, so users aren't asked again.
- A development checkout doesn't check. `REPROCROWD_UPDATES=off` turns checks off;
  `REPROCROWD_UPDATE_SERVER=<url>` points them at a test server (see `src/main/updater.ts`).

`backend/` came from the standalone reprocrowd-cli repository (which the
ReproCrowd Zotero plugin also uses) with `git subtree`, history included.
Changes can go either way:

```sh
git subtree pull --prefix=backend ../reprocrowd-cli master   # take the standalone repo's changes
git subtree push --prefix=backend ../reprocrowd-cli app-sync # offer this repo's changes as a branch there
```

## License

The app's source code is MIT, see [LICENSE](LICENSE). What a reproduction produces is licensed separately and
recorded per file in its archive's `licenses.json`. Metadata is CC0-1.0. Reports, figures and outputs are CC-BY-4.0,
except quoted passages and original figures from the article, which keep the article's terms; outputs computed from
third-party data are also subject to that data's terms. New analysis code follows `[licensing] code` (MIT by default).
Third-party articles, data and code keep their own licences.
See [the contract](docs/contracts.md#github-pilots-and-output-licences).
