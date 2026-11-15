# GitHub pilot submissions and output licences

Status: implemented on `issue12-github-pilots`, 15 November 2026. Refs [#12](https://github.com/forrtproject/reprocrowd-app/issues/12) (publication destination and licence policy only). Zenodo record versioning (one DOI family per reproduction) and wider publication policy remain open in #12.

## Purpose

A pilot is a public, reviewable record of a reproduction, made before anything is archived with a DOI. It reuses the existing package contract unchanged: the recorded `comparison.json`, `evidence/execution.json` and the archive ZIP. There is no second package model. A pilot is **unreviewed**. The issue, the release notes and `pilot.json` all say that agreement at the reported precision is not peer review, a certificate or an endorsement.

## Identity and keys

| Key | Scope | Where it lives | Used for |
|---|---|---|---|
| Paper DOI | the paper | package identifiers | metadata and links only; **not** identity |
| `reproduction_id` = `repro-<10 hex>` | one reproduction (one package) | `datapackage.json` → `reprocrowd.reproduction_id`, set on the first submission | one issue, found by the hidden marker `<!-- reprocrowd-pilot:<id> -->` |
| `run_key` = sha256(archive sha256 \| left-out groups)[:12] | one archive with one choice of files | the pilot record; the release tag `<reproduction_id>-<run_key>` | idempotency: one pre-release and one comment (`<!-- reprocrowd-pilot-run:<id>:<key> -->`) per run key |
| `version` (v1, v2, …) | runs within a reproduction | the release name, assigned once at creation | human-readable order; "latest" in the issue is the highest version on GitHub |
| Zenodo concept DOI (future) | one reproduction | not implemented | should map 1:1 to `reproduction_id`; each Zenodo version would correspond to a run key |

Independent reproductions of one paper therefore get separate issues. Copying a package folder copies its `reproduction_id`. A deliberate fork that should become a separate reproduction needs the field cleared; there is no UI for that yet.

## Destination and flow

- **Destination:** `[github] pilots_repo` (default `forrtproject/reprocrowd-pilots`) and `api_url`. A request may name another repository.
- **Token:** sent with each request and never stored by the service. The desktop app keeps it in the system keyring. `rc` reads `$REPROCROWD_GITHUB_TOKEN`, then `gh auth token`.
- **Write access** to the repository is required, because creating releases and setting labels needs it. A fine-grained token needs *Contents: Read and write* and *Issues: Read and write*.

**Preconditions** are those of a Zenodo deposit:

- `database.Prepared`: no job running, a DOI, the last run succeeded, the recorded comparison (refused with `report_stale` when results, targets or the latest run changed), an up-to-date archive, and the OpenAlex record.
- The paper's text was retrieved for the DOI rather than taken from a local file (`local_paper_text`).
- The left-out groups follow the Zenodo rules: the paper's material is excluded unless OpenAlex reports an open licence.

All of these checks, and the 2 GiB asset limit, run before any GitHub write.

**Steps.** Each step first looks for what an earlier attempt made, so a retry resumes rather than duplicates:

1. `GET /repos/{repo}`: refuse without `permissions.push`. Assign the `reproduction_id` if needed and save a `partial` record.
2. **Issue.** Look in the local record, then list `label:reprocrowd-pilot, state=all` (paginated, skipping pull requests) and match the marker. Create the issue only if neither finds it. The search API is not used, because its index lags.
3. **Release.** List releases (drafts included) and match the tag. Otherwise create a **draft pre-release** with `make_latest=false`.
   - In a draft, an asset whose state isn't `uploaded`, or whose size or SHA-256 `digest` differs, is deleted and uploaded again.
   - Verify every digest, then publish.
   - A published release whose files differ is refused (`pilot_release_mismatch`) and never changed. Immutable releases are enabled on the pilots repository.
4. **Comment.** Paginate the issue's comments and match the run marker. Create the comment only if it is missing.
5. **Issue description.** Rebuild it from the published releases on GitHub, which include runs submitted from elsewhere: banner, latest run (highest version), all runs, licences. Mark the local record `published`.

**Files per run**, all release assets:

- the ZIP: the archive itself, or the copy without the left-out groups, which includes `excluded-files.json`. It is built once per run key and kept at `archive/pilot-<run_key>/`, so a retry uploads identical bytes.
- `report.html`, `comparison.json`, `execution.json` and `licenses.json`, copied out of that ZIP so that their hashes match its contents.
- `pilot.json` (CC0): the deterministic record of status, keys, comparison policy and counts, licences and every file's SHA-256.

GitHub has no API for issue attachments (25 MB in the browser), so files go to release assets (each under 2 GiB, verified by the REST `digest`). Committing binaries to git was rejected.

**Bodies** are clipped to GitHub's 65,536-character limit. The comparison table shows at most 60 rows and points to `comparison.json` for the rest.

**Errors:**

| Code | Meaning |
|---|---|
| `github_token_missing` | no token sent and none configured |
| `github_token_invalid` | GitHub rejected the token |
| `github_forbidden` | no write access, or the token lacks the permissions |
| `github_rejected` | GitHub refused the request (HTTP 422) |
| `github_rate_limited` | rate limit reached |
| `github_unreachable`, `github_error` | network failure or an unexpected answer |
| `pilot_asset_too_large` | a file is 2 GiB or more |
| `pilot_release_mismatch` | the run's release on GitHub holds different files |

A failed step leaves the record `partial` with its error message; submitting again resumes.

**Clients:**

- API: `GET /v1/github-pilots`, `POST /v1/github-pilots:check`, `POST …/pilots:preview` (no GitHub contact), `POST …/pilots`, `GET …/pilots`.
- CLI: `rc pilot status|check|preview|submit|list`.
- App: Settings → GitHub pilots; step 8 → *Pilot on GitHub*.

Zenodo behaviour is unchanged apart from one description sentence on licences.

## Licences

Policy `reprocrowd-licensing/1`, written as `licenses.json` into every archive, which covers local ZIPs, Zenodo uploads and pilots alike:

| Category | Files | Licence |
|---|---|---|
| Metadata | `datapackage.json`, OpenAlex record, targets/actions, plan, schemas, `inputs.json`, `licenses.json`, `pilot.json` | CC0-1.0 |
| Outputs | report, comparison, execution evidence, run log, results, reproduced figures | CC-BY-4.0 |
| New analysis code | master script and other code written for the reproduction | `[licensing] code`: `CC-BY-4.0` (current default) or `MIT` |
| Third-party | the article's full texts, extractions and original figures; input data and code | not relicensed (`license: null`); the article's licence from OpenAlex and the input's source are recorded |

**Exceptions** are recorded per file:

- Quoted article passages in targets, plan and report remain under the article's terms.
- Original figures shown in the report keep the article's licence.
- Outputs computed from third-party data are also subject to that data's terms.

The app's own source stays MIT.

### New analysis code: CC BY 4.0 or MIT (open decision)

The project's tentative preference is CC BY 4.0, for simplicity; `[licensing] code` makes the choice one setting. The case for MIT for analysis code:

- **The licence stewards advise against it.** Creative Commons [recommends against its licences for software](https://creativecommons.org/faq/#can-i-apply-a-creative-commons-license-to-software). CC licences say nothing about source-code distribution, are not compatible with the major software licences, and leave patents aside. The FSF lists CC BY 4.0 as a free licence compatible with the GPL, but [says CC licences should not be used for software](https://www.gnu.org/licenses/license-list.html).
- **Reuse venues exclude it.** CC BY 4.0 is not [OSI-approved](https://opensource.org/licenses). [JOSS](https://joss.readthedocs.io/en/latest/submitting.html) requires an OSI-approved licence, and [rOpenSci](https://devguide.ropensci.org/pkg_building.html) requires a CRAN- or OSI-accepted one. Someone who folds a recreated analysis into a package or a software paper would have to ask for relicensing.
- **CC BY adds attribution friction for code.** It requires indicating changes and linking to the licence, which is awkward when individual functions are copied. MIT needs only the notice to be kept.
- **MIT adds no real complexity.** Every package already carries three regimes (CC0, CC BY and third-party terms), and `licenses.json` states the licence per file. The app itself is MIT.

What CC BY 4.0 has going for it: one licence for everything we author, and the attribution expectations of scholarly reuse. Neither licence grants patents; Apache-2.0 would. Copyright in largely LLM-generated code is uncertain in some jurisdictions, which lowers the stakes either way.

## Verification

- `backend/tests/test_pilots.py` runs offline against a stateful fake GitHub with pagination and failure injection. It covers:
  - preview without network writes;
  - create, then retrying the same archive with no change;
  - a rerun refused as `report_stale` before any write;
  - v2 on the same issue, with v1 kept;
  - a failure after each step (issue, mid-upload, release body, publish, comment, issue description), with no duplicates on resume;
  - a lost local record (the issue is found on GitHub);
  - a published release with different files (refused, not changed);
  - no write access;
  - `licenses.json` covering every archived file;
  - the CLI path;
  - the token never written to disk.
- `backend/tests/pilot_demo.py` is the opt-in live demo, using the synthetic scenario fixture only.

## Deferred

- Review workflow, trust levels, moderation, signing and attestations.
- A pilot → Zenodo version mapping.
- Separate-reproduction forking UI.
- Org-level automation in the pilots repository.
- Policy on short article quotations in public reports.
