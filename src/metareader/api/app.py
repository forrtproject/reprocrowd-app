"""The HTTP API. Run with ``mr serve``; interactive docs at /docs (Swagger UI) and /redoc.

Conventions: versioned under /v1; JSON bodies; errors are RFC 9457 problem details; POST creates (201 +
Location), PUT replaces explicitly, PATCH is a JSON merge patch, DELETE returns 204; every package-scoped
response carries the package's ETag, and unsafe requests may send If-Match (412 on mismatch).
"""

import hashlib
import logging
import re
import secrets
import uuid
from pathlib import Path as FsPath
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, FastAPI, Path, Query, Request, Response, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import __version__, curation, extractions, fulltexts, inputs, metadata, scripts
from ..config import settings
from ..errors import ApiError, not_found
from ..extractions import Scope
from ..identifiers import parse_identifier
from ..package import MANIFEST, Package, Registry
from ..schemas.document import Document
from ..schemas.manifest import Resource
from ..validation import ValidationReport, validate_package
from . import models as M
from .problems import CATALOG, STATUS_CODES, Problem, ProblemType, problem_types

log = logging.getLogger("metareader")

TAGS = [
    {"name": "service", "description": "Health, configured LLM profiles, script templates, problem types."},
    {"name": "packages", "description": "Create, list and validate packages. A package is a folder with a Frictionless `datapackage.json`."},
    {"name": "metadata", "description": "OpenAlex metadata of the package's work."},
    {"name": "fulltexts", "description": "Full-text files (PDF, TEI, JATS, Docling JSON), accepted after an integrity check."},
    {"name": "extractions", "description": "Full texts in the common document schema: parsed, or reconciled by an LLM."},
    {"name": "inputs", "description": "Input files of the reproduction (data / code / other)."},
    {"name": "targets", "description": "Results to reproduce: added by hand or extracted by an LLM, with non-destructive edits."},
    {"name": "actions", "description": "Analysis steps described in the paper (the former analysis plan)."},
    {"name": "script", "description": "Master-script templates rendered from targets and actions."},
]

app = FastAPI(
    title="MetaReader API",
    version=__version__,
    summary="Build computational-reproducibility packages for scholarly papers.",
    description=__doc__,
    openapi_tags=TAGS,
)


# ---------- errors (RFC 9457) ----------


def problem(request: Request, status: int, code: str, detail: str, extra: Any = None) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"/v1/problems/{code}",
        "title": CATALOG.get(code, (status, code))[1],
        "status": status,
        "detail": detail,
        "instance": request.url.path,
        "code": code,
        "request_id": getattr(request.state, "request_id", None),
    }
    if isinstance(extra, dict):
        body |= {k: v for k, v in extra.items() if k not in body}
    elif extra is not None:
        body["details"] = extra
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


@app.exception_handler(ApiError)
async def _api_error(request: Request, e: ApiError):
    return problem(request, e.status, e.code, e.message, e.details)


@app.exception_handler(RequestValidationError)
async def _request_invalid(request: Request, e: RequestValidationError):
    errors = [{"loc": list(err["loc"]), "msg": err["msg"], "type": err["type"]} for err in e.errors()]
    return problem(request, 422, "request_invalid", "the request doesn't match the API schema", {"errors": errors})


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, e: StarletteHTTPException):
    return problem(request, e.status_code, STATUS_CODES.get(e.status_code, "not_found" if e.status_code == 404 else "request_invalid"), str(e.detail))


@app.exception_handler(Exception)
async def _unexpected(request: Request, e: Exception):
    log.exception("unhandled error")
    return problem(request, 500, "internal_error", f"{type(e).__name__}: {e}")


def errors(*codes: str) -> dict[int | str, dict[str, Any]]:
    """OpenAPI documentation of the problem responses an endpoint can return."""
    out: dict[int | str, dict[str, Any]] = {}
    for code in codes:
        status, title = CATALOG[code]
        entry = out.setdefault(status, {"model": Problem, "content": {"application/problem+json": {}}, "description": ""})
        entry["description"] = "; ".join(filter(None, [entry["description"], f"`{code}`: {title}"]))
    return out


# ---------- request id, ETag, auth ----------

PKG_PATH = re.compile(r"^/v1/packages/([^/]+)")


def etag(pkg: Package) -> str:
    return '"' + hashlib.sha256(pkg.path(MANIFEST).read_bytes()).hexdigest()[:20] + '"'


@app.middleware("http")
async def _headers(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex
    request.state.request_id = rid
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    m = PKG_PATH.match(request.url.path)
    if m and response.status_code < 400:
        try:  # fails after the package itself was unregistered: no ETag then
            response.headers["ETag"] = etag(Registry().get(m[1]))
        except (ApiError, OSError):
            pass
    return response


bearer = HTTPBearer(auto_error=False, description="Required only if the service config sets a token.")


def auth(creds: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)]) -> None:
    token = settings().token
    if token and not (creds and secrets.compare_digest(creds.credentials, token)):
        raise ApiError(401, "unauthorized", "missing or wrong bearer token")


SAFE = {"GET", "HEAD", "OPTIONS"}


def get_pkg(request: Request, pkg: Annotated[str, Path(description="package id")]) -> Package:
    p = Registry().get(pkg)
    if request.method not in SAFE and (match := request.headers.get("if-match")):
        if match.strip() != "*" and etag(p) not in [x.strip() for x in match.split(",")]:
            raise ApiError(412, "precondition_failed", "the package changed since you read it (ETag mismatch)", {"etag": etag(p)})
    return p


Pkg = Annotated[Package, Depends(get_pkg)]


def created(response: Response, location: str) -> None:
    response.status_code = 201
    response.headers["Location"] = location


# ---------- service ----------

service = APIRouter(prefix="/v1", tags=["service"])


@service.get("/health", response_model=M.Health, summary="Service health")
def health():
    return M.Health(version=__version__)


@service.get("/problems", response_model=list[ProblemType], summary="Problem types returned by this API")
def list_problems():
    return problem_types()


@service.get("/problems/{code}", response_model=ProblemType, summary="One problem type", responses=errors("not_found"))
def get_problem(code: str):
    pt = next((p for p in problem_types() if p.code == code), None)
    if pt is None:
        raise not_found("problem type", code)
    return pt


api = APIRouter(prefix="/v1", dependencies=[Security(auth)], responses=errors("unauthorized", "request_invalid"))


@api.get("/llm-profiles", tags=["service"], response_model=list[M.LlmProfileView], summary="Configured LLM profiles")
def llm_profiles():
    cfg = settings()
    return [M.LlmProfileView(name=n, default=n == cfg.llm_default_profile, argv=p.argv, model=p.model, effort=p.effort, timeout_s=p.timeout_s)
            for n, p in cfg.llm_profiles.items()]


@api.get("/templates", tags=["service"], response_model=list[M.TemplateView], summary="Master-script templates")
def list_templates():
    return [M.TemplateView(id=t.id, label=t.label, language=t.language, command=t.command, master=t.master) for t in scripts.templates().values()]


# ---------- packages ----------


@api.post("/packages", tags=["packages"], status_code=201, response_model=M.PackageSummary, summary="Create a package",
          description="Creates the folder with a minimal `datapackage.json`, schema copies and empty targets/actions, "
                      "and registers it. With `fetch_metadata`, OpenAlex is queried with the identifier.",
          responses=errors("package_exists", "folder_not_empty", "path_not_allowed", "invalid_identifier", "openalex_not_found", "openalex_failed"))
def create_package(body: M.PackageCreate, response: Response):
    ident = parse_identifier(body.identifier) if body.identifier else None
    pkg = Registry().create(FsPath(body.path), ident, adopt=body.adopt)
    if body.fetch_metadata:
        if ident is None:
            raise ApiError(400, "no_identifier", "fetch_metadata needs an identifier")
        metadata.fetch(pkg, None, overwrite=False)
    created(response, f"/v1/packages/{pkg.id}")
    return pkg.summary()


@api.get("/packages", tags=["packages"], response_model=list[M.PackageSummary], summary="List packages")
def list_packages():
    out = []
    for p in Registry().list():
        try:
            out.append(p.summary())
        except ApiError:
            continue  # folder gone; still registered
    return out


@api.get("/packages/{pkg}", tags=["packages"], response_model=M.PackageSummary, summary="Get a package", responses=errors("not_found", "package_missing"))
def get_package(pkg: Pkg):
    return pkg.summary()


@api.get("/packages/{pkg}/manifest", tags=["packages"], summary="The package's datapackage.json", response_model=dict[str, Any])
def get_manifest(pkg: Pkg):
    return pkg.read_json(MANIFEST)


@api.delete("/packages/{pkg}", tags=["packages"], status_code=204, summary="Unregister a package (files stay on disk)", responses=errors("not_found"))
def delete_package(pkg: Pkg):
    Registry().remove(pkg.id)


@api.get("/packages/{pkg}/validation", tags=["packages"], response_model=ValidationReport, summary="Validate the whole package",
         description="Manifest model, Frictionless integrity (bytes, sha256), JSON Schema and model validation of every JSON "
                     "resource, and semantic checks (references, inputs, dependency cycles, untracked files).")
def validate(pkg: Pkg):
    return validate_package(pkg)


# ---------- metadata ----------


@api.get("/packages/{pkg}/metadata", tags=["metadata"], response_model=dict[str, Any], summary="The OpenAlex work record", responses=errors("not_found"))
def get_metadata(pkg: Pkg):
    return metadata.load(pkg)


@api.post("/packages/{pkg}/metadata", tags=["metadata"], status_code=201, response_model=M.MetadataSummary, summary="Retrieve metadata from OpenAlex",
          responses=errors("metadata_exists", "identifier_conflict", "no_identifier", "invalid_identifier", "openalex_not_found", "openalex_failed"))
def post_metadata(pkg: Pkg, response: Response, body: M.MetadataRequest = Body(default_factory=M.MetadataRequest)):
    out = metadata.fetch(pkg, body.identifier, overwrite=False)
    created(response, f"/v1/packages/{pkg.id}/metadata")
    return out


@api.put("/packages/{pkg}/metadata", tags=["metadata"], response_model=M.MetadataSummary, summary="Retrieve metadata again, replacing it",
         description="The explicit overwrite: replaces existing metadata and, if given, the package identifier.",
         responses=errors("no_identifier", "invalid_identifier", "openalex_not_found", "openalex_failed"))
def put_metadata(pkg: Pkg, body: M.MetadataRequest = Body(default_factory=M.MetadataRequest)):
    return metadata.fetch(pkg, body.identifier, overwrite=True)


# ---------- full texts ----------


@api.get("/packages/{pkg}/fulltexts", tags=["fulltexts"], response_model=list[Resource], summary="List full texts")
def list_fulltexts(pkg: Pkg):
    return pkg.load().by_kind("fulltext")


@api.get("/packages/{pkg}/fulltexts/candidates", tags=["fulltexts"], response_model=M.CandidateList, summary="Download candidates from OpenAlex",
         description="URLs named in the OpenAlex record (PDF locations, OpenAlex cached content if an API key is configured) "
                     "and Europe PMC JATS when a PMCID is known.", responses=errors("metadata_missing"))
def candidates(pkg: Pkg):
    return fulltexts.list_candidates(pkg)


@api.post("/packages/{pkg}/fulltexts", tags=["fulltexts"], status_code=201, response_model=Resource, summary="Add a full text",
          description="From a local `path`, or by downloading a `candidate`. The file is accepted only if it passes its "
                      "format's integrity check.",
          responses=errors("integrity_check_failed", "duplicate_fulltext", "not_a_file", "path_not_allowed", "metadata_missing", "download_failed", "not_found"))
def add_fulltext(pkg: Pkg, body: M.FulltextCreate, response: Response):
    if isinstance(body, M.FulltextFromPath):
        r = fulltexts.add_local(pkg, body.path, body.name)
    else:
        r = fulltexts.add_candidate(pkg, body.candidate, body.name)
    created(response, f"/v1/packages/{pkg.id}/fulltexts/{r.name}")
    return r


@api.get("/packages/{pkg}/fulltexts/{name}", tags=["fulltexts"], response_model=Resource, summary="Get a full text's resource", responses=errors("not_found"))
def get_fulltext(pkg: Pkg, name: str):
    return fulltexts.get(pkg, name)


@api.get("/packages/{pkg}/fulltexts/{name}/content", tags=["fulltexts"], summary="Download the file", response_class=FileResponse, responses=errors("not_found"))
def fulltext_content(pkg: Pkg, name: str):
    r = fulltexts.get(pkg, name)
    return FileResponse(pkg.path(r.path), media_type=r.mediatype, filename=r.path.rsplit("/", 1)[-1])


@api.delete("/packages/{pkg}/fulltexts/{name}", tags=["fulltexts"], status_code=204, summary="Delete a full text", responses=errors("not_found", "in_use"))
def delete_fulltext(pkg: Pkg, name: str):
    fulltexts.delete(pkg, name)


# ---------- extractions ----------

EXTRACTION_ERRORS = errors("not_found", "not_parseable", "parse_failed", "extraction_exists", "no_sources", "unknown_profile",
                           "llm_call_failed", "llm_timeout", "llm_output_invalid")


def _make_extraction(pkg: Package, body, name: str | None, replace: bool) -> Resource:
    if isinstance(body, M.ExtractionParse):
        return extractions.parse(pkg, body.fulltext, name, replace)
    return extractions.reconcile(pkg, body.sources, prompt=body.prompt, instructions=body.instructions, profile=body.profile, name=name, replace=replace)


@api.get("/packages/{pkg}/extractions", tags=["extractions"], response_model=list[Resource], summary="List extractions")
def list_extractions(pkg: Pkg):
    return pkg.load().by_kind("extraction")


@api.post("/packages/{pkg}/extractions", tags=["extractions"], status_code=201, response_model=Resource, summary="Create an extraction",
          description="`method: parse` converts a TEI/JATS/Docling full text. `method: reconcile` asks an LLM (configured profile) "
                      "to merge several full texts; its output must validate against the document schema. LLM failures are "
                      "`llm_call_failed`/`llm_timeout`; unusable output is `llm_output_invalid` with the failing `stage`.",
          responses=EXTRACTION_ERRORS)
def create_extraction(pkg: Pkg, body: Annotated[M.ExtractionCreate, Body()], response: Response):
    r = _make_extraction(pkg, body, body.name, replace=False)
    created(response, f"/v1/packages/{pkg.id}/extractions/{r.name}")
    return r


@api.put("/packages/{pkg}/extractions/{name}", tags=["extractions"], response_model=Resource, summary="Create or replace an extraction",
         responses=EXTRACTION_ERRORS)
def put_extraction(pkg: Pkg, name: str, body: Annotated[M.ExtractionCreate, Body()]):
    return _make_extraction(pkg, body, name, replace=True)


@api.get("/packages/{pkg}/extractions/{name}", tags=["extractions"], response_model=Document, summary="Get an extraction",
         description="`view=document` (default): the document JSON. `view=outline`: plain text with every id in brackets.",
         responses={**errors("not_found"), 200: {"content": {"application/json": {}, "text/plain": {}}}})
def get_extraction(pkg: Pkg, name: str, view: Literal["document", "outline"] = "document"):
    _, doc = extractions.get(pkg, name)
    if view == "outline":
        return PlainTextResponse(extractions.listing(doc))
    return doc


@api.delete("/packages/{pkg}/extractions/{name}", tags=["extractions"], status_code=204, summary="Delete an extraction", responses=errors("not_found", "in_use"))
def delete_extraction(pkg: Pkg, name: str):
    extractions.delete(pkg, name)


# ---------- inputs ----------


@api.get("/packages/{pkg}/inputs", tags=["inputs"], response_model=list[Resource], summary="List inputs")
def list_inputs(pkg: Pkg):
    return pkg.load().by_kind("input")


@api.post("/packages/{pkg}/inputs", tags=["inputs"], status_code=201, response_model=list[Resource], summary="Add an input file",
          description="Copies the file into `inputs/`. Zip archives are unpacked; the archive and every member become resources.",
          responses=errors("not_a_file", "path_not_allowed", "invalid_archive"))
def add_input(pkg: Pkg, body: M.InputCreate, response: Response):
    rs = inputs.add(pkg, body.path, body.role, body.note, body.unpack)
    created(response, f"/v1/packages/{pkg.id}/inputs/{rs[0].name}")
    return rs


@api.get("/packages/{pkg}/inputs/{name}", tags=["inputs"], response_model=Resource, summary="Get an input", responses=errors("not_found"))
def get_input(pkg: Pkg, name: str):
    return inputs.get(pkg.load(), name)


@api.patch("/packages/{pkg}/inputs/{name}", tags=["inputs"], response_model=Resource, summary="Change role, note or used",
           responses=errors("not_found", "schema_invalid"))
def patch_input(pkg: Pkg, name: str, body: M.InputPatch):
    return inputs.patch(pkg, name, body.model_dump(exclude_unset=True))


@api.delete("/packages/{pkg}/inputs/{name}", tags=["inputs"], status_code=204, summary="Delete an input (an archive with its members)",
            responses=errors("not_found", "archive_member", "in_use"))
def delete_input(pkg: Pkg, name: str):
    inputs.delete(pkg, name)


# ---------- targets and actions (same routes, different models) ----------


def curation_routes(kind: str, fields: type, view: type, patch_model: type) -> None:
    noun = kind[:-1]
    base = f"/packages/{{pkg}}/{kind}"
    run_errors = errors("not_found", "unknown_ids", "unknown_profile", "llm_call_failed", "llm_timeout", "llm_output_invalid")
    LlmRunModel = curation.LlmRun[curation.CURATIONS[kind].item]
    FileModel = curation.CURATIONS[kind].file_model

    @api.get(base, tags=[kind], response_model=list[view] | FileModel, summary=f"List {kind}",
             description=f"`view=effective` (default): manual {kind} and enabled LLM entries with edits applied, with their origin. "
                         f"`view=raw`: the stored {kind}.json (LLM versions, edits, runs).")
    def list_items(pkg: Pkg, view_: Annotated[Literal["effective", "raw"], Query(alias="view")] = "effective", include_disabled: bool = False):
        cur = curation.Curation(pkg, kind)
        if view_ == "raw":
            return cur.load()
        return [{"item": i, "origin": o} for i, o in cur.effective(include_disabled)]

    @api.post(base, tags=[kind], status_code=201, response_model=curation.CURATIONS[kind].item, summary=f"Add a {noun} by hand",
              description="Needs `refs` into an extraction, or `unreferenced: true` with a `note`.",
              responses=errors("schema_invalid", "invalid_reference"))
    def add_item(pkg: Pkg, body: fields, response: Response):  # type: ignore[valid-type]
        item = curation.Curation(pkg, kind).add(body.model_dump(exclude_unset=True))
        created(response, f"/v1/packages/{pkg.id}/{kind}/{item.id}")
        return item

    @api.get(base + "/runs", tags=[kind], response_model=list[LlmRunModel], summary="List LLM runs")
    def list_runs(pkg: Pkg):
        return curation.Curation(pkg, kind).load().llm_runs

    @api.post(base + "/runs", tags=[kind], status_code=201, response_model=LlmRunModel, summary=f"Extract {kind} with an LLM",
              description="Runs the default (or a custom) prompt over the extraction (or a scope of it) with a configured profile. "
                          "Items that fail validation are kept in the run's `rejected` list; the call itself fails only if the "
                          "output has no usable JSON (`llm_output_invalid`).", responses=run_errors)
    def create_run(pkg: Pkg, body: M.RunCreate, response: Response):
        run = curation.Curation(pkg, kind).generate(body.extraction, Scope(body.scope.sections, body.scope.items),
                                                    instructions=body.instructions, prompt=body.prompt, profile=body.profile)
        created(response, f"/v1/packages/{pkg.id}/{kind}/runs/{run.id}")
        return run

    @api.get(base + "/runs/{run}", tags=[kind], response_model=LlmRunModel, summary="Get an LLM run", responses=errors("not_found"))
    def get_run(pkg: Pkg, run: str):
        return curation.Curation(pkg, kind).get_run(run)

    @api.patch(base + "/runs/{run}", tags=[kind], response_model=LlmRunModel, summary="Enable or disable a whole run",
               responses=errors("not_found", "invalid_reference"))
    def patch_run(pkg: Pkg, run: str, body: M.RunPatch):
        return curation.Curation(pkg, kind).patch_run(run, body.disabled)

    @api.get(base + "/{item_id}", tags=[kind], response_model=view, summary=f"Get a {noun}", responses=errors("not_found"))
    def get_item(pkg: Pkg, item_id: str):
        for i, o in curation.Curation(pkg, kind).effective(include_disabled=True):
            if i.id == item_id:
                return {"item": i, "origin": o}
        raise not_found(noun, item_id)

    @api.patch(base + "/{item_id}", tags=[kind], response_model=view, summary=f"Edit or disable a {noun} (JSON merge patch)",
               description="Manual items are changed in place. LLM entries keep the LLM's version; the change is stored as an "
                           "edit on top of it (restore with `DELETE …/edit`). `disabled` applies to LLM entries only.",
               responses=errors("not_found", "schema_invalid", "invalid_reference", "manual_item"))
    def patch_item(pkg: Pkg, item_id: str, body: patch_model):  # type: ignore[valid-type]
        i, o = curation.Curation(pkg, kind).patch(item_id, body.model_dump(exclude_unset=True))
        return {"item": i, "origin": o}

    @api.delete(base + "/{item_id}/edit", tags=[kind], response_model=curation.CURATIONS[kind].item,
                summary="Restore an LLM entry (drop the user's edit, re-enable)", responses=errors("not_found", "manual_item", "invalid_reference"))
    def drop_edit(pkg: Pkg, item_id: str):
        return curation.Curation(pkg, kind).drop_overlay(item_id)

    @api.delete(base + "/{item_id}", tags=[kind], status_code=204, summary=f"Delete a manual {noun}",
                responses=errors("not_found", "llm_entry", "in_use"))
    def delete_item(pkg: Pkg, item_id: str):
        curation.Curation(pkg, kind).delete(item_id)


curation_routes("targets", curation.TargetFields, M.TargetView, M.TargetPatch)
curation_routes("actions", curation.ActionFields, M.ActionView, M.ActionPatch)


# ---------- script ----------


@api.get("/packages/{pkg}/script", tags=["script"], response_model=list[Resource], summary="Files written by the script step")
def get_script(pkg: Pkg):
    m = pkg.load()
    return m.by_kind("script") + m.by_kind("export")


@api.put("/packages/{pkg}/script", tags=["script"], response_model=M.ScriptResult, summary="Create or replace the master script",
         description="Resolves the plan (load steps for used data inputs → enabled actions → results step), writes "
                     "`analysis_plan.json`, `results_schema.json`, the template's files and README.md. Returns 201 the first "
                     "time, 200 when replacing. Files changed since they were generated are only overwritten with `force`.",
         responses={201: {"model": M.ScriptResult, "description": "created"}, **errors("unknown_template", "nothing_to_script", "script_modified")})
def put_script(pkg: Pkg, body: M.ScriptRequest, response: Response):
    out = scripts.write(pkg, body.template, body.force)
    if out["created"]:
        created(response, f"/v1/packages/{pkg.id}/script")
    return out


app.include_router(service)
app.include_router(api)
