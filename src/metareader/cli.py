"""``mr``: command-line client of the MetaReader API (plus ``mr serve`` to run the service).

Every command is one HTTP request; output is the JSON response. Errors (RFC 9457 problem details) are
printed to stderr with exit code 1. Server: --url / $MR_URL (default http://127.0.0.1:8770/v1);
token: --token / $MR_TOKEN.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

import httpx
import typer

app = typer.Typer(help="Client of the MetaReader API. Every command is one HTTP request and prints the JSON response; "
                  "errors go to stderr with exit code 1. Start the service with `mr serve`.",
                  no_args_is_help=True, pretty_exceptions_enable=False)
_state: dict[str, Any] = {}

LLM_TIMEOUT = None  # LLM calls can take many minutes; the service enforces the profile timeout


@app.callback()
def _main(
    url: str = typer.Option(os.environ.get("MR_URL", "http://127.0.0.1:8770/v1"), help="API base URL ($MR_URL)"),
    token: Optional[str] = typer.Option(os.environ.get("MR_TOKEN"), help="bearer token ($MR_TOKEN)"),
):
    _state["url"], _state["token"] = url.rstrip("/"), token


def call(method: str, path: str, body: Any = None, params: dict | None = None, timeout: float | None = 60, raw: bool = False) -> Any:
    headers = {"Authorization": f"Bearer {_state['token']}"} if _state.get("token") else {}
    if method == "PATCH":
        headers["Content-Type"] = "application/merge-patch+json"
    try:
        r = httpx.request(method, _state["url"] + path, json=body, params=params, headers=headers, timeout=timeout)
    except httpx.HTTPError as e:
        typer.echo(f"cannot reach {_state['url']}: {e} (is `mr serve` running?)", err=True)
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
    uvicorn.run("metareader.api.app:app", host=host or cfg.host, port=port or cfg.port, reload=reload)


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
def fulltext_add(pkg: str, file: str, name: Optional[str] = None):
    """Add a local PDF / TEI / JATS / Docling JSON file (integrity-checked)."""
    out(call("POST", f"/packages/{pkg}/fulltexts", {"path": abspath(file), "name": name}))


@ft.command("candidates")
def fulltext_candidates(pkg: str):
    """Download candidates found through OpenAlex."""
    out(call("GET", f"/packages/{pkg}/fulltexts/candidates"))


@ft.command("fetch")
def fulltext_fetch(pkg: str, candidate: str, name: Optional[str] = None):
    out(call("POST", f"/packages/{pkg}/fulltexts", {"candidate": candidate, "name": name}, timeout=600))


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
              note: str = "", unpack: bool = typer.Option(True, help="unpack zip archives")):
    out(call("POST", f"/packages/{pkg}/inputs", {"path": abspath(file), "role": role, "note": note, "unpack": unpack}))


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
def script_create(pkg: str, template: str = typer.Option(..., help="template id (mr script templates)"),
                  force: bool = typer.Option(False, help="overwrite generated files that were edited")):
    out(call("PUT", f"/packages/{pkg}/script", {"template": template, "force": force}))


@sc.command("show")
def script_show(pkg: str):
    out(call("GET", f"/packages/{pkg}/script"))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
