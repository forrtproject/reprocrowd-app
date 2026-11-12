"""``rc``: command-line client of the ReproCrowd API (plus ``rc serve`` to run the service).

Every command is one HTTP request; output is the JSON response. Errors (RFC 9457 problem details) are
printed to stderr with exit code 1. Server: --url / $REPROCROWD_URL (default http://127.0.0.1:8770/v1);
token: --token / $REPROCROWD_TOKEN.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

import httpx
import typer

app = typer.Typer(help="Client of the ReproCrowd API. Every command is one HTTP request and prints the JSON response; "
                  "errors go to stderr with exit code 1. Start the service with `rc serve`.",
                  no_args_is_help=True, pretty_exceptions_enable=False)
_state: dict[str, Any] = {}

LLM_TIMEOUT = None  # LLM calls can take many minutes; the service enforces the profile timeout


@app.callback()
def _main(
    url: str = typer.Option(os.environ.get("REPROCROWD_URL", "http://127.0.0.1:8770/v1"), help="API base URL ($REPROCROWD_URL)"),
    token: Optional[str] = typer.Option(os.environ.get("REPROCROWD_TOKEN"), help="bearer token ($REPROCROWD_TOKEN)"),
):
    _state["url"], _state["token"] = url.rstrip("/"), token


def call(method: str, path: str, body: Any = None, params: dict | None = None, timeout: float | None = 60, raw: bool = False,
         upload: Path | None = None) -> Any:
    """One request. With ``upload``, the file is sent as multipart/form-data and ``body`` as its form fields."""
    headers = {"Authorization": f"Bearer {_state['token']}"} if _state.get("token") else {}
    if method == "PATCH":
        headers["Content-Type"] = "application/merge-patch+json"
    try:
        if upload is not None:
            fields = {k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in (body or {}).items() if v is not None}
            with upload.open("rb") as f:
                r = httpx.request(method, _state["url"] + path, data=fields, files={"file": (upload.name, f)}, params=params, headers=headers, timeout=timeout)
        else:
            r = httpx.request(method, _state["url"] + path, json=body, params=params, headers=headers, timeout=timeout)
    except httpx.HTTPError as e:
        typer.echo(f"cannot reach {_state['url']}: {e} (is `rc serve` running?)", err=True)
        raise typer.Exit(2)
    if r.status_code >= 400:
        try:
            p = r.json()
            typer.echo(f"error {p.get('status')} {p.get('code')}: {p.get('detail')}", err=True)
            extra = {k: v for k, v in p.items() if k not in {"type", "title", "status", "detail", "instance", "code", "request_id"}}
            if extra:
                typer.echo(json.dumps(extra, indent=2, ensure_ascii=False), err=True)
        except ValueError:
            typer.echo(f"error {r.status_code}: {r.text}", err=True)
        raise typer.Exit(1)
    if r.status_code == 204:
        return None
    return r.text if raw else r.json()


def out(data: Any) -> None:
    if data is None:
        return
    typer.echo(data if isinstance(data, str) else json.dumps(data, indent=2, ensure_ascii=False))


def abspath(p: str) -> str:
    return str(Path(p).expanduser().resolve())


def parse_value(v: str) -> Any:
    try:
        return json.loads(v)
    except ValueError:
        return v


def key_values(pairs: list[str]) -> dict[str, Any]:
    outd = {}
    for kv in pairs:
        if "=" not in kv:
            raise typer.BadParameter(f"expected key=value, got {kv!r}")
        k, v = kv.split("=", 1)
        outd[k] = parse_value(v)
    return outd


def read_text(path: Optional[Path]) -> Optional[str]:
    if path is None:
        return None
    return sys.stdin.read() if str(path) == "-" else path.read_text(encoding="utf-8")


# ---------- service ----------


@app.command()
def serve(host: Optional[str] = None, port: Optional[int] = None, reload: bool = False):
    """Run the API service (docs at /docs and /redoc)."""
    import uvicorn

    from .config import settings

    cfg = settings()
    uvicorn.run("reprocrowd.api.app:app", host=host or cfg.host, port=port or cfg.port, reload=reload)


@app.command()
def profiles():
    """Configured LLM profiles."""
    out(call("GET", "/llm-profiles"))


@app.command()
def validate(pkg: str):
    """Validate a whole package (integrity, schemas, references)."""
    report = call("GET", f"/packages/{pkg}/validation")
    out(report)
    raise typer.Exit(0 if report["valid"] else 1)


# ---------- packages ----------

package = typer.Typer(help="Create, list and inspect packages.", no_args_is_help=True)
app.add_typer(package, name="package")


@package.command("create")
def package_create(path: str, id: Optional[str] = typer.Option(None, "--id", help="DOI, OpenAlex W…, pmid:…, pmcid:…"),
                   fetch: bool = typer.Option(False, help="query OpenAlex now"), adopt: bool = typer.Option(False, help="register an existing package")):
    out(call("POST", "/packages", {"path": abspath(path), "identifier": id, "fetch_metadata": fetch, "adopt": adopt}))


@package.command("list")
def package_list():
    out(call("GET", "/packages"))


@package.command("show")
def package_show(pkg: str, manifest: bool = typer.Option(False, help="print datapackage.json")):
    out(call("GET", f"/packages/{pkg}" + ("/manifest" if manifest else "")))


@package.command("rm")
def package_rm(pkg: str):
    """Unregister (files stay)."""
    out(call("DELETE", f"/packages/{pkg}"))


# ---------- metadata ----------

meta = typer.Typer(help="OpenAlex metadata.", no_args_is_help=True)
app.add_typer(meta, name="metadata")


@meta.command("fetch")
def metadata_fetch(pkg: str, id: Optional[str] = typer.Option(None, "--id"), overwrite: bool = typer.Option(False, help="replace existing metadata")):
    out(call("PUT" if overwrite else "POST", f"/packages/{pkg}/metadata", {"identifier": id}))


@meta.command("show")
def metadata_show(pkg: str):
    out(call("GET", f"/packages/{pkg}/metadata"))


# ---------- full texts ----------

ft = typer.Typer(help="Full-text files.", no_args_is_help=True)
app.add_typer(ft, name="fulltext")


@ft.command("add")
def fulltext_add(pkg: str, file: str, name: Optional[str] = None,
                 upload: bool = typer.Option(False, "--upload", help="send the file's content (for a service on another machine)")):
    """Add a PDF / TEI / JATS / Docling JSON file (integrity-checked)."""
    if upload:
        out(call("POST", f"/packages/{pkg}/fulltexts", {"name": name}, upload=Path(file), timeout=None))
    else:
        out(call("POST", f"/packages/{pkg}/fulltexts", {"path": abspath(file), "name": name}))


@ft.command("candidates")
def fulltext_candidates(pkg: str):
    """Download candidates found through OpenAlex."""
    out(call("GET", f"/packages/{pkg}/fulltexts/candidates"))


@ft.command("fetch")
def fulltext_fetch(pkg: str, candidate: str, name: Optional[str] = None):
    out(call("POST", f"/packages/{pkg}/fulltexts", {"candidate": candidate, "name": name}, timeout=600))


@ft.command("convert")
def fulltext_convert(pkg: str, source: str, converter: str = typer.Option(..., help="grobid (→ TEI) or docling (→ Docling JSON)"), name: Optional[str] = None):
    """Convert a PDF full text with a converter configured in the service."""
    out(call("POST", f"/packages/{pkg}/fulltexts", {"source": source, "converter": converter, "name": name}, timeout=None))


@app.command()
def converters():
    """PDF converters and whether the service has them configured."""
    out(call("GET", "/converters"))


@ft.command("list")
def fulltext_list(pkg: str):
    out(call("GET", f"/packages/{pkg}/fulltexts"))


@ft.command("rm")
def fulltext_rm(pkg: str, name: str):
    out(call("DELETE", f"/packages/{pkg}/fulltexts/{name}"))


# ---------- extractions ----------

ex = typer.Typer(help="Extractions in the common document schema.", no_args_is_help=True)
app.add_typer(ex, name="extract")


@ex.command("parse")
def extract_parse(pkg: str, fulltext: str, name: Optional[str] = None, replace: bool = typer.Option(False, help="replace an existing extraction")):
    body = {"method": "parse", "fulltext": fulltext}
    if replace:
        target = name or "ex-" + fulltext.removeprefix("ft-")
        out(call("PUT", f"/packages/{pkg}/extractions/{target}", body))
    else:
        out(call("POST", f"/packages/{pkg}/extractions", body | {"name": name}))


@ex.command("reconcile")
def extract_reconcile(pkg: str, sources: list[str], prompt_file: Optional[Path] = typer.Option(None, help="custom prompt template ('-' = stdin)"),
                      instructions: str = "", profile: Optional[str] = None, name: Optional[str] = None,
                      replace: bool = typer.Option(False, help="replace the extraction --name")):
    """Merge full texts with an LLM into one validated document."""
    body = {"method": "reconcile", "sources": sources, "prompt": read_text(prompt_file), "instructions": instructions, "profile": profile}
    if replace:
        if not name:
            raise typer.BadParameter("--replace needs --name")
        out(call("PUT", f"/packages/{pkg}/extractions/{name}", body, timeout=LLM_TIMEOUT))
    else:
        out(call("POST", f"/packages/{pkg}/extractions", body | {"name": name}, timeout=LLM_TIMEOUT))


@ex.command("list")
def extract_list(pkg: str):
    out(call("GET", f"/packages/{pkg}/extractions"))


@ex.command("show")
def extract_show(pkg: str, name: str, outline: bool = typer.Option(False, help="readable text with ids")):
    out(call("GET", f"/packages/{pkg}/extractions/{name}", params={"view": "outline" if outline else "document"}, raw=outline))


@ex.command("rm")
def extract_rm(pkg: str, name: str):
    out(call("DELETE", f"/packages/{pkg}/extractions/{name}"))


# ---------- inputs ----------

inp = typer.Typer(help="Input files (data / code / other).", no_args_is_help=True)
app.add_typer(inp, name="input")


@inp.command("add")
def input_add(pkg: str, file: str, role: Optional[str] = typer.Option(None, help="data, code or other (default: guessed)"),
              note: str = "", unpack: bool = typer.Option(True, help="unpack zip archives"),
              upload: bool = typer.Option(False, "--upload", help="send the file's content (for a service on another machine)")):
    """Add an input file, zip archive or directory (directories: not with --upload)."""
    body = {"role": role, "note": note, "unpack": unpack}
    if upload:
        out(call("POST", f"/packages/{pkg}/inputs", body, upload=Path(file), timeout=None))
    else:
        out(call("POST", f"/packages/{pkg}/inputs", body | {"path": abspath(file)}))


@inp.command("list")
def input_list(pkg: str):
    out(call("GET", f"/packages/{pkg}/inputs"))


@inp.command("edit")
def input_edit(pkg: str, name: str, role: Optional[str] = None, note: Optional[str] = None, used: Optional[bool] = typer.Option(None, "--used/--unused")):
    body = {k: v for k, v in {"role": role, "note": note, "used": used}.items() if v is not None}
    out(call("PATCH", f"/packages/{pkg}/inputs/{name}", body))


@inp.command("rm")
def input_rm(pkg: str, name: str):
    out(call("DELETE", f"/packages/{pkg}/inputs/{name}"))


# ---------- targets and actions ----------


def curation_commands(kind: str) -> typer.Typer:
    noun = kind[:-1]
    t = typer.Typer(help=f"{kind.capitalize()}: manual, LLM-extracted, editable without losing the LLM version.", no_args_is_help=True)

    @t.command("list", help=f"Effective {kind} (manual + enabled LLM entries with edits), or the raw stored file.")
    def list_(pkg: str, raw: bool = typer.Option(False, help=f"the stored {kind}.json"), all: bool = typer.Option(False, "--all", help="include disabled")):
        out(call("GET", f"/packages/{pkg}/{kind}", params={"view": "raw" if raw else "effective", "include_disabled": all}))

    @t.command("show", help=f"One {noun} with its origin.")
    def show(pkg: str, item_id: str):
        out(call("GET", f"/packages/{pkg}/{kind}/{item_id}"))

    @t.command("add", help=f"Add a {noun} by hand: --json body and/or --field/--ref options.")
    def add(pkg: str, json_file: Optional[Path] = typer.Option(None, "--json", help="full body as JSON ('-' = stdin)"),
            field: list[str] = typer.Option([], "--field", "-f", help="key=value (value parsed as JSON if possible)"),
            ref: list[str] = typer.Option([], "--ref", help="EXTRACTION:ID, repeatable"),
            unreferenced: bool = typer.Option(False, help="no refs; requires --note"), note: Optional[str] = None):
        body = json.loads(read_text(json_file)) if json_file else {}
        body |= key_values(field)
        if ref:
            body["refs"] = [dict(zip(("extraction", "id"), r.split(":", 1))) for r in ref]
        if unreferenced:
            body["unreferenced"] = True
        if note is not None:
            body["note"] = note
        out(call("POST", f"/packages/{pkg}/{kind}", body))

    @t.command("generate", help=f"Extract {kind} with an LLM (default prompt unless --prompt-file).")
    def generate(pkg: str, extraction: str, section: list[str] = typer.Option([], help="section id, repeatable"),
                 item: list[str] = typer.Option([], help="sentence/figure/table id, repeatable"), instructions: str = "",
                 prompt_file: Optional[Path] = typer.Option(None, help="custom prompt template ('-' = stdin)"), profile: Optional[str] = None):
        body = {"extraction": extraction, "scope": {"sections": section, "items": item}, "instructions": instructions,
                "prompt": read_text(prompt_file), "profile": profile}
        out(call("POST", f"/packages/{pkg}/{kind}/runs", body, timeout=LLM_TIMEOUT))

    @t.command("edit", help="Change fields (LLM entries keep their original; restore undoes) or disable/enable.")
    def edit(pkg: str, item_id: str, set_: list[str] = typer.Option([], "--set", help="field=value (JSON-parsed), repeatable"),
             disabled: Optional[bool] = typer.Option(None, "--disable/--enable", help="LLM entries only")):
        body = key_values(set_)
        if disabled is not None:
            body["disabled"] = disabled
        out(call("PATCH", f"/packages/{pkg}/{kind}/{item_id}", body))

    @t.command("restore")
    def restore(pkg: str, item_id: str):
        """Drop your edits of an LLM entry and re-enable it."""
        out(call("DELETE", f"/packages/{pkg}/{kind}/{item_id}/edit"))

    @t.command("rm")
    def rm(pkg: str, item_id: str):
        """Delete a manual item (LLM entries are disabled instead)."""
        out(call("DELETE", f"/packages/{pkg}/{kind}/{item_id}"))

    @t.command("runs")
    def runs(pkg: str, run: Optional[str] = typer.Argument(None), disabled: Optional[bool] = typer.Option(None, "--disable/--enable")):
        """List LLM runs, show one, or enable/disable a whole run."""
        if run and disabled is not None:
            out(call("PATCH", f"/packages/{pkg}/{kind}/runs/{run}", {"disabled": disabled}))
        else:
            out(call("GET", f"/packages/{pkg}/{kind}/runs" + (f"/{run}" if run else "")))

    return t


app.add_typer(curation_commands("targets"), name="target")
app.add_typer(curation_commands("actions"), name="action")


# ---------- script ----------

sc = typer.Typer(help="Master-script templates.", no_args_is_help=True)
app.add_typer(sc, name="script")


@sc.command("templates")
def script_templates():
    out(call("GET", "/templates"))


@sc.command("create")
def script_create(pkg: str, template: str = typer.Option(..., help="template id (rc script templates)"),
                  on_modified: str = typer.Option("refuse", help="edited generated files (e.g. by the agent): refuse, keep (regenerate the rest), overwrite")):
    out(call("PUT", f"/packages/{pkg}/script", {"template": template, "on_modified": on_modified}))


@sc.command("show")
def script_show(pkg: str):
    """Template, remaining TODO(LLM): markers and generated files."""
    out(call("GET", f"/packages/{pkg}/script"))


@sc.command("status")
def script_status(pkg: str):
    """Remaining actionable TODO(LLM): markers, one line each."""
    st = call("GET", f"/packages/{pkg}/script")
    if not st["template"]:
        typer.echo("no master script yet")
        raise typer.Exit(1)
    typer.echo(f"{st['master']} ({st['template']}): {st['todo']['remaining']} TODO(LLM): marker(s) left")
    for f in st["todo"]["files"]:
        for line in f["lines"]:
            typer.echo(f"  {f['path']}:{line['line']}: {line['text']}")


# ---------- code generation and jobs ----------

ICONS = {"start": "▶", "text": "💬", "tool_use": "🔧", "tool_result": "  ↳", "result": "■", "log": " "}


_last_text: dict[str, str] = {}


def show_event(e: dict) -> None:
    text = e["text"] if e["kind"] in ("text", "result", "log") else e["text"].splitlines()[0][:200] if e["text"] else ""
    if e["kind"] == "text":
        _last_text["t"] = text
    if e["kind"] == "result" and text == _last_text.get("t"):
        text = "finished"  # Claude repeats its last message as the result
    mark = "✗" if e.get("is_error") else ICONS[e["kind"]]
    typer.echo(f"{mark} {text}")


def follow(pkg: str, jid: str, after: int = 0) -> dict:
    """Print the job's events live (Server-Sent Events) until it ends; returns the final job."""
    headers = {"Accept": "text/event-stream", "Last-Event-ID": str(after)}
    if _state.get("token"):
        headers["Authorization"] = f"Bearer {_state['token']}"
    final: dict = {}
    with httpx.stream("GET", f"{_state['url']}/packages/{pkg}/jobs/{jid}/events", headers=headers, timeout=None) as r:
        event = None
        for line in r.iter_lines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
                if event == "end":
                    final = data
                else:
                    show_event(data)
    return final


def summarize(job: dict) -> None:
    res = job.get("result") or {}
    typer.echo(f"\n{job['id']}: {job['status']} (exit {job.get('exit_code')}, {(job.get('duration_ms') or 0) / 1000:.0f} s)", err=True)
    if res.get("type") == "codegen":
        typer.echo(f"TODO(LLM): markers left: {res['todo']['remaining']}; changed: {', '.join(res['changed_files']) or '—'}; "
                   f"commit {res.get('commit_after') or '—'}", err=True)
    if job.get("error"):
        typer.echo(f"error: {job['error']}", err=True)


@app.command()
def codegen(pkg: str, profile: Optional[str] = typer.Option(None, help="agent profile (rc agents)"),
            exec_: bool = typer.Option(True, "--exec/--no-exec", help="let the agent run the analysis"),
            instructions: str = "", follow_: bool = typer.Option(True, "--follow/--detach", help="print progress until the agent ends")):
    """Let the coding agent implement the master script's TODO(LLM): markers (a job)."""
    job = call("POST", f"/packages/{pkg}/jobs", {"type": "codegen", "profile": profile, "allow_exec": exec_, "instructions": instructions})
    if not follow_:
        out(job)
        return
    typer.echo(f"{job['id']} started; Ctrl+C detaches (the agent keeps running; `rc job cancel` stops it)", err=True)
    summarize(follow(pkg, job["id"]) or call("GET", f"/packages/{pkg}/jobs/{job['id']}"))


@app.command("run")
def run_cmd(pkg: str, follow_: bool = typer.Option(True, "--follow/--detach", help="print the run log until it ends")):
    """Run the master script with the template's command (a job), then check outputs/results.json."""
    job = call("POST", f"/packages/{pkg}/jobs", {"type": "run"})
    if not follow_:
        out(job)
        return
    final = follow(pkg, job["id"]) or call("GET", f"/packages/{pkg}/jobs/{job['id']}")
    summarize(final)
    res = final.get("result") or {}
    for e in res.get("output_errors", []):
        typer.echo(f"results.json: {e}", err=True)
    if final["status"] != "succeeded":
        raise typer.Exit(1)


@app.command("compare")
def compare_cmd(pkg: str, rounding: str = typer.Option("reported", help='"reported" or a number of decimals'),
                threshold: float = typer.Option(5, help="% deviation counted as within tolerance"), as_json: bool = typer.Option(False, "--json")):
    """Compare the reproduced results with the targets."""
    c = call("GET", f"/packages/{pkg}/comparison", params={"rounding": rounding, "threshold": threshold})
    if as_json:
        out(c)
        return
    labels = {"match": "✓", "within": "≈", "deviation": "✗", "missing": "∅", "figure": "🖼", "unparsable": "?"}
    for r in c["rows"]:
        dev = f"  ({r['deviation']:.1f} %)" if r.get("deviation") not in (None, 0) else ""
        typer.echo(f"{labels[r['status']]} {r['target']:6} expected {r['expected_text']:<18} got {r['reproduced_text']}{dev}  {r['description'][:50]}")
    typer.echo("  ".join(f"{k}: {v}" for k, v in c["summary"].items()))
    for e in c["output_errors"] + c["output_warnings"]:
        typer.echo(f"results.json: {e}", err=True)


@app.command("figures")
def figures_cmd(pkg: str, target: Optional[str] = typer.Argument(None, help="one figure target; default: all"),
                pdf: Optional[str] = typer.Option(None, help="PDF full text to use (default: found by hash or provenance)"), scale: float = 2.0):
    """Get the original figures of figure targets: as published, cropped from the PDF, or its page (shown in the report)."""
    body = {"pdf": pdf, "scale": scale}
    out(call("PUT", f"/packages/{pkg}/figures" + (f"/{target}" if target else ""), body))


@app.command("coordinates")
def coordinates_cmd(pkg: str):
    """What each full text locates on the PDF: real figures and tables, and whether the PDF is confirmed."""
    out(call("GET", f"/packages/{pkg}/coordinates"))


@app.command("report")
def report_cmd(pkg: str, rounding: str = typer.Option("reported", help='"reported" or a number of decimals'), threshold: float = 5,
               open_: bool = typer.Option(False, "--open", help="open report.html in the browser")):
    """Create the report (report.html, comparison.json)."""
    r = call("PUT", f"/packages/{pkg}/report", {"rounding": rounding if rounding == "reported" else int(rounding), "threshold": threshold})
    out(r)
    if open_:
        import webbrowser

        webbrowser.open(Path(call("GET", f"/packages/{pkg}")["path"], r["path"]).as_uri())


@app.command("archive")
def archive_cmd(pkg: str, download: Optional[Path] = typer.Option(None, help="also save the ZIP here")):
    """Create the ZIP of the package (needs a valid package with a report)."""
    out(call("PUT", f"/packages/{pkg}/archive"))
    if download:
        headers = {"Authorization": f"Bearer {_state['token']}"} if _state.get("token") else {}
        with httpx.stream("GET", f"{_state['url']}/packages/{pkg}/archive", headers=headers, timeout=None) as r, download.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
        typer.echo(f"saved {download}", err=True)


@app.command()
def submit(pkg: str, note: str = "", open_: bool = typer.Option(True, "--open/--no-open", help="open the confirmation page in the browser")):
    """Submit the archive to the package database; confirm it in the browser."""
    sub = call("POST", f"/packages/{pkg}/submissions", {"note": note}, timeout=None)
    out(sub)
    typer.echo(f"Confirm the submission at {sub['finish_url']} (until {sub['expires']}); `rc submission {pkg} {sub['id']}` checks it.", err=True)
    if open_:
        import webbrowser

        webbrowser.open(sub["finish_url"])


@app.command()
def submission(pkg: str, sub: Optional[str] = typer.Argument(None)):
    """List submissions, or refresh one's status from the database."""
    out(call("GET", f"/packages/{pkg}/submissions" + (f"/{sub}" if sub else "")))


@app.command()
def agents():
    """Configured coding-agent profiles."""
    out(call("GET", "/agent-profiles"))


zen = typer.Typer(help="Deposits on Zenodo. The token comes from $REPROCROWD_ZENODO_TOKEN, else the service's [zenodo] token.",
                  no_args_is_help=True)
app.add_typer(zen, name="zenodo")


def _zauth(body: dict | None = None) -> dict:
    body = dict(body or {})
    if os.environ.get("REPROCROWD_ZENODO_TOKEN"):
        body["zenodo_token"] = os.environ["REPROCROWD_ZENODO_TOKEN"]
    return body


@zen.command("status")
def zen_status(sandbox: Optional[bool] = typer.Option(None, "--sandbox/--production")):
    """The Zenodo server deposits go to, and whether it is reachable."""
    out(call("GET", "/zenodo", params={} if sandbox is None else {"sandbox": sandbox}))


@zen.command("verify")
def zen_verify(sandbox: Optional[bool] = typer.Option(None, "--sandbox/--production")):
    """Check the Zenodo access token."""
    out(call("POST", "/zenodo:verify-token", _zauth({"sandbox": sandbox})))


@zen.command("records")
def zen_records(doi: str, sandbox: Optional[bool] = typer.Option(None, "--sandbox/--production")):
    """Published ReproCrowd records for a paper."""
    out(call("GET", "/zenodo/records", params={"doi": doi, **({} if sandbox is None else {"sandbox": sandbox})}))


def _deposit_options(note: str, exclude: Optional[list[str]], include_all: bool, creator: Optional[list[str]], orcid: Optional[list[str]],
                     affiliation: Optional[list[str]], community: Optional[str]) -> dict:
    body: dict[str, Any] = {"note": note}
    if include_all:
        body["exclude"] = []
    elif exclude:
        body["exclude"] = exclude
    if creator:
        body["creators"] = [{"name": n, **({"orcid": orcid[i]} if orcid and i < len(orcid) and orcid[i] else {}),
                             **({"affiliation": affiliation[i]} if affiliation and i < len(affiliation) and affiliation[i] else {})}
                            for i, n in enumerate(creator)]
    if community is not None:
        body["community"] = community
    return body


_EXCLUDE = typer.Option(None, "--exclude", help="leave out a group of files: paper, inputs (repeatable); default: paper unless openly licensed")
_ALL = typer.Option(False, "--include-all", help="upload everything")
_CREATOR = typer.Option(None, "--creator", help='"Family, Given" (repeatable; default: [zenodo] creators)')
_ORCID = typer.Option(None, "--orcid", help="ORCID of the creator at the same position")
_AFFIL = typer.Option(None, "--affiliation", help="affiliation of the creator at the same position")


@zen.command("preview")
def zen_preview(pkg: str, note: str = "", exclude: Optional[list[str]] = _EXCLUDE, include_all: bool = _ALL, creator: Optional[list[str]] = _CREATOR,
                orcid: Optional[list[str]] = _ORCID, affiliation: Optional[list[str]] = _AFFIL, community: Optional[str] = None):
    """Show the metadata and files a deposit would send."""
    out(call("POST", f"/packages/{pkg}/deposits:preview", _deposit_options(note, exclude, include_all, creator, orcid, affiliation, community), timeout=None))


@zen.command("deposit")
def zen_deposit(pkg: str, note: str = "", exclude: Optional[list[str]] = _EXCLUDE, include_all: bool = _ALL, creator: Optional[list[str]] = _CREATOR,
                orcid: Optional[list[str]] = _ORCID, affiliation: Optional[list[str]] = _AFFIL, community: Optional[str] = None,
                sandbox: Optional[bool] = typer.Option(None, "--sandbox/--production"),
                publish: bool = typer.Option(False, help="publish at once (registers the DOI for good)")):
    """Upload the package to Zenodo as a draft (or publish it with --publish)."""
    body = _zauth(_deposit_options(note, exclude, include_all, creator, orcid, affiliation, community)) | {"sandbox": sandbox, "publish": publish}
    d = call("POST", f"/packages/{pkg}/deposits", body, timeout=None)
    out(d)
    if d["status"] == "draft":
        typer.echo(f"Draft at {d['url']} (reserved DOI {d.get('doi')}); publish it there or with `rc zenodo publish {pkg} {d['id']}`.", err=True)


@zen.command("list")
def zen_list(pkg: str):
    """Deposits of a package."""
    out(call("GET", f"/packages/{pkg}/deposits"))


@zen.command("refresh")
def zen_refresh(pkg: str, dep: str):
    """Update a deposit's status from Zenodo."""
    out(call("POST", f"/packages/{pkg}/deposits/{dep}:refresh", _zauth()))


@zen.command("publish")
def zen_publish(pkg: str, dep: str, yes: bool = typer.Option(False, "--yes", help="don't ask")):
    """Publish a draft deposit (registers the DOI; it can't be deleted afterwards)."""
    if not yes:
        typer.confirm("Publishing registers the DOI, and the record can't be deleted afterwards. Publish?", abort=True)
    out(call("POST", f"/packages/{pkg}/deposits/{dep}:publish", _zauth(), timeout=None))


@zen.command("discard")
def zen_discard(pkg: str, dep: str):
    """Delete a draft deposit on Zenodo."""
    out(call("POST", f"/packages/{pkg}/deposits/{dep}:discard", _zauth()))


job_app = typer.Typer(help="Jobs (code generation): status, progress, cancel.", no_args_is_help=True)
app.add_typer(job_app, name="job")


@job_app.command("list")
def job_list(pkg: str):
    for j in call("GET", f"/packages/{pkg}/jobs"):
        typer.echo(f"{j['id']}  {j['type']:8} {j['status']:11} {j['created_at']}")


@job_app.command("show")
def job_show(pkg: str, job: str):
    out(call("GET", f"/packages/{pkg}/jobs/{job}"))


@job_app.command("events")
def job_events(pkg: str, job: str, after: int = 0, follow_: bool = typer.Option(False, "--follow", "-f", help="keep printing until the job ends")):
    if follow_:
        summarize(follow(pkg, job, after) or call("GET", f"/packages/{pkg}/jobs/{job}"))
    else:
        for e in call("GET", f"/packages/{pkg}/jobs/{job}/events", params={"after": after}):
            show_event(e)


@job_app.command("log")
def job_log(pkg: str, job: str):
    """Raw stdout of the job."""
    out(call("GET", f"/packages/{pkg}/jobs/{job}/stdout", raw=True))


@job_app.command("cancel")
def job_cancel(pkg: str, job: str):
    out(call("DELETE", f"/packages/{pkg}/jobs/{job}"))


@app.command("files")
def files_cmd(pkg: str):
    """Files in the package folder, most recently modified first."""
    for f in call("GET", f"/packages/{pkg}/files"):
        typer.echo(f"{f['kind'] or 'untracked':10} {f['size']:>10}  {f['path']}")


@app.command()
def sync(pkg: str):
    """Register files created or changed outside the API (agents, manual edits)."""
    out(call("POST", f"/packages/{pkg}:sync"))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
