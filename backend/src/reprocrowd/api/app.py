"""The HTTP API. Run with ``rc serve``; interactive docs at /docs (Swagger UI) and /redoc.

Conventions: versioned under /v1; JSON bodies; errors are RFC 9457 problem details; POST creates (201 +
Location), PUT replaces explicitly, PATCH is a JSON merge patch, DELETE returns 204; every package-scoped
response carries the package's ETag, and unsafe requests may send If-Match (412 on mismatch).
"""

import asyncio
from contextlib import asynccontextmanager
import hashlib
import logging
import re
import secrets
import uuid
from pathlib import Path as FsPath
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, FastAPI, Path, Query, Request, Response, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import TypeAdapter, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import __version__, acquire, openalex, zenodo, claims, codegen, coordinates, curation, database, figures, report, run, extractions, files, fulltexts, inputs, jobs, llm, metadata, scripts
from ..config import settings
from ..errors import ApiError, not_found
from ..extractions import Scope
from ..identifiers import parse_identifier
from ..package import MANIFEST, Package, Registry
from ..events import EventReader, read_events
from ..schemas.document import Document
from ..schemas.claims import ClaimStatus, ClaimsTrace
from ..schemas.comparison import Comparison, CompareSettings
from ..schemas.jobs import Job, JobEvent
from ..schemas.manifest import ArchiveInfo, Resource, Submission, ZenodoDeposit
from ..validation import ValidationReport, validate_package
from . import models as M
from .problems import CATALOG, STATUS_CODES, Problem, ProblemType, problem_types

log = logging.getLogger("reprocrowd")

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
    {"name": "jobs", "description": "Long-running work (coding agent, pipeline run) followed asynchronously, with live progress events."},
    {"name": "comparison", "description": "Reproduced results compared with the targets."},
    {"name": "report", "description": "The reproduction report and the ZIP of the package."},
    {"name": "database", "description": "Submission to the package database (optional)."},
    {"name": "zenodo", "description": "Deposits on Zenodo: a draft with the package ZIP and a reserved DOI, published in a separate step."},
]

@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    await run_in_threadpool(jobs.stop_all)  # no job outlives the service


app = FastAPI(
    lifespan=lifespan,
    title="ReproCrowd API",
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

PKG_PATH = re.compile(r"^/v1/packages/([^/:]+)")


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


# ---------- request bodies: JSON or a multipart upload ----------

EXTRA_SCHEMAS: dict[str, tuple[Any, bool]] = {}  # name → (type, is a multipart form with a file part); merged into components


def json_or_multipart(json_name: str, json_type: Any, form_model: type) -> dict[str, Any]:
    """OpenAPI: the endpoint takes either a JSON body or a multipart form with a `file`."""
    EXTRA_SCHEMAS[json_name] = (json_type, False)
    EXTRA_SCHEMAS[form_model.__name__] = (form_model, True)
    return {"requestBody": {"required": True, "content": {
        "application/json": {"schema": {"$ref": f"#/components/schemas/{json_name}"}},
        "multipart/form-data": {"schema": {"$ref": f"#/components/schemas/{form_model.__name__}"}}}}}


async def json_or_upload(request: Request, json_type: Any, form_model: type):
    """(validated JSON body, None) or (None, (upload, validated form fields)); invalid input → request_invalid."""
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        form = await request.form(max_files=1, max_fields=20)
        upload = form.get("file")
        if not isinstance(upload, StarletteUploadFile):
            raise RequestValidationError([{"loc": ("body", "file"), "msg": "a file is required", "type": "missing"}])
        try:
            fields = form_model.model_validate({k: v for k, v in form.items() if k != "file"})
        except ValidationError as e:
            raise RequestValidationError(e.errors())
        return None, (upload, fields)
    try:
        data = await request.json()
    except ValueError:
        raise RequestValidationError([{"loc": ("body",), "msg": "not valid JSON", "type": "json_invalid"}])
    try:
        return TypeAdapter(json_type).validate_python(data), None
    except ValidationError as e:
        raise RequestValidationError(e.errors())


def _openapi() -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema
    spec = FastAPI.openapi(app)
    comps = spec.setdefault("components", {}).setdefault("schemas", {})
    for name, (tp, upload) in EXTRA_SCHEMAS.items():
        schema = TypeAdapter(tp).json_schema(ref_template="#/components/schemas/{model}")
        for k, v in schema.pop("$defs", {}).items():
            comps.setdefault(k, v)
        if upload:
            schema["properties"] = {"file": {"type": "string", "format": "binary", "description": "the file"}, **schema.get("properties", {})}
            schema["required"] = ["file", *schema.get("required", [])]
        comps[name] = schema
    return spec


app.openapi = _openapi


# ---------- pagination (opt-in) ----------


class Page:
    """?limit=&offset= on list endpoints. Bodies stay plain arrays; X-Total-Count and RFC 8288 Link headers
    (first, prev, next, last) describe the pages. Without limit, the whole list is returned."""

    def __init__(self, request: Request, response: Response,
                 limit: Annotated[int | None, Query(ge=1, le=1000, description="page size; omit for the whole list")] = None,
                 offset: Annotated[int, Query(ge=0, description="items to skip")] = 0):
        self.request, self.response, self.limit, self.offset = request, response, limit, offset

    def __call__(self, items: list) -> list:
        total = len(items)
        self.response.headers["X-Total-Count"] = str(total)
        if self.limit is None:
            return items[self.offset:]
        lim, off = self.limit, self.offset
        url = lambda o: str(self.request.url.include_query_params(limit=lim, offset=o))  # noqa: E731
        links = {"first": 0, "last": max(0, (total - 1) // lim * lim)}
        if off + lim < total:
            links["next"] = off + lim
        if off > 0:
            links["prev"] = max(0, off - lim)
        self.response.headers["Link"] = ", ".join(f'<{url(o)}>; rel="{rel}"' for rel, o in links.items())
        return items[off:off + lim]


Paged = Annotated[Page, Depends()]


def created(response: Response, location: str) -> None:
    response.status_code = 201
    response.headers["Location"] = location


# ---------- service ----------

service = APIRouter(prefix="/v1", tags=["service"])


@service.get("/health", response_model=M.Health, summary="Service health")
def health():
    return M.Health(version=__version__)


@service.get("/problems", response_model=list[ProblemType], summary="Problem types returned by this API")
def list_problems(page: Paged):
    return page(problem_types())


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


@api.get("/converters", tags=["service"], response_model=list[M.ConverterView], summary="PDF converters and whether they are configured")
def list_converters():
    from ..converters import CONVERTERS

    return [M.ConverterView(name=c.name, configured=c.configured(), output=c.output, detail=c.detail()) for c in CONVERTERS.values()]


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
def list_packages(page: Paged):
    out = []
    for p in Registry().list():
        try:
            out.append(p.summary())
        except ApiError:
            continue  # folder gone; still registered
    return page(out)


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
def list_fulltexts(pkg: Pkg, page: Paged):
    return page(pkg.load().by_kind("fulltext"))


@api.get("/jats", tags=["fulltexts"], response_model=M.JatsAvailability, summary="Is the paper's JATS XML available?",
         description="Looks the DOI up in OpenAlex and Europe PMC and fetches the start of Europe PMC's full-text XML, before "
                     "any package exists. Only papers in the open-access subset of PubMed Central have it.",
         responses=errors("openalex_not_found", "openalex_failed", "jats_check_failed"))
def jats_availability(doi: Annotated[str, Query(min_length=3)]):
    ident = parse_identifier(doi)
    if ident.scheme != "doi":
        raise ApiError(400, "invalid_identifier", f"not a DOI: {doi}")
    return openalex.jats_availability(ident.value)


@api.get("/packages/{pkg}/fulltexts/candidates", tags=["fulltexts"], response_model=M.CandidateList, summary="Download candidates from OpenAlex",
         description="URLs named in the OpenAlex record (PDF locations, OpenAlex cached content if an API key is configured) "
                     "and Europe PMC JATS when a PMCID is known.", responses=errors("metadata_missing"))
def candidates(pkg: Pkg):
    return fulltexts.list_candidates(pkg)


@api.post("/packages/{pkg}/fulltexts", tags=["fulltexts"], status_code=201, response_model=Resource, summary="Add a full text",
          description="JSON: from a local `path` (on the service's machine), by downloading a `candidate`, or by converting a "
                      "PDF full text (`source` + `converter`). multipart/form-data: an uploaded `file`. The file is accepted "
                      "only if it passes its format's integrity check.",
          openapi_extra=json_or_multipart("FulltextCreate", M.FulltextCreate, M.FulltextUploadForm),
          responses=errors("integrity_check_failed", "duplicate_fulltext", "not_a_file", "path_not_allowed", "metadata_missing", "download_failed",
                           "not_found", "unknown_converter", "converter_not_configured", "not_convertible", "conversion_failed", "conversion_timeout",
                           "invalid_filename", "upload_too_large"))
async def add_fulltext(pkg: Pkg, request: Request, response: Response):
    body, form = await json_or_upload(request, M.FulltextCreate, M.FulltextUploadForm)
    if form is not None:
        upload, fields = form
        r = await run_in_threadpool(fulltexts.add_upload, pkg, upload.filename, upload.file, fields.name)
    elif isinstance(body, M.FulltextFromPath):
        r = await run_in_threadpool(fulltexts.add_local, pkg, body.path, body.name)
    elif isinstance(body, M.FulltextFromConversion):
        r = await run_in_threadpool(fulltexts.convert, pkg, body.source, body.converter, body.name)
    else:
        r = await run_in_threadpool(fulltexts.add_candidate, pkg, body.candidate, body.name)
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


def _llm_preview(prompt: str, profile: str | None, *, read_files: bool = False, schema: str) -> M.PromptPreview:
    name, p, argv, structured = llm.command(profile, read_files=read_files, schema=schema)
    return M.PromptPreview(prompt=prompt, argv=argv, profile=name, model=p.model, effort=p.effort, output_schema=structured)


def _make_extraction(pkg: Package, body, name: str | None, replace: bool) -> Resource:
    if isinstance(body, M.ExtractionParse):
        return extractions.parse(pkg, body.fulltext, name, replace)
    return extractions.reconcile(pkg, body.sources, prompt=body.prompt, instructions=body.instructions, profile=body.profile, name=name, replace=replace)


@api.get("/packages/{pkg}/extractions", tags=["extractions"], response_model=list[Resource], summary="List extractions")
def list_extractions(pkg: Pkg, page: Paged):
    return page(pkg.load().by_kind("extraction"))


@api.post("/packages/{pkg}/extractions", tags=["extractions"], status_code=201, response_model=Resource, summary="Create an extraction",
          description="`method: parse` converts a TEI/JATS/Docling full text. `method: reconcile` asks an LLM (configured profile) "
                      "to merge several full texts; its output must validate against the document schema. LLM failures are "
                      "`llm_call_failed`/`llm_timeout`; unusable output is `llm_output_invalid` with the failing `stage`.",
          responses=EXTRACTION_ERRORS)
def create_extraction(pkg: Pkg, body: Annotated[M.ExtractionCreate, Body()], response: Response):
    r = _make_extraction(pkg, body, body.name, replace=False)
    created(response, f"/v1/packages/{pkg.id}/extractions/{r.name}")
    return r


@api.post("/packages/{pkg}/extractions:preview", tags=["extractions"], response_model=M.PromptPreview, summary="Show the prompt of a reconcile call",
          description="Takes the body of `POST …/extractions` with `method: reconcile` and returns what would be sent. Runs nothing and stores nothing.",
          responses=errors("not_found", "no_sources", "unknown_profile"))
def preview_reconcile(pkg: Pkg, body: M.ExtractionReconcile):
    _, text = extractions.reconcile_prompt(pkg, body.sources, prompt=body.prompt, instructions=body.instructions)
    return _llm_preview(text, body.profile, read_files=True, schema=extractions.RECONCILE_SCHEMA)


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
def list_inputs(pkg: Pkg, page: Paged):
    return page(pkg.load().by_kind("input"))


@api.post("/packages/{pkg}/inputs", tags=["inputs"], status_code=201, response_model=list[Resource], summary="Add input files",
          description="JSON: a local `path` (on the service's machine): a file, or a directory (every regular file in it; hidden "
                      "files and symlinks skipped). multipart/form-data: an uploaded `file`. Files go into `inputs/`; zip archives "
                      "are unpacked, the archive and every member become resources.",
          openapi_extra=json_or_multipart("InputCreate", M.InputCreate, M.InputUploadForm),
          responses=errors("not_a_file", "path_in_package", "path_not_allowed", "invalid_archive", "directory_too_large", "directory_empty", "invalid_filename", "upload_too_large"))
async def add_input(pkg: Pkg, request: Request, response: Response):
    body, form = await json_or_upload(request, M.InputCreate, M.InputUploadForm)
    if form is not None:
        upload, f = form
        rs = await run_in_threadpool(inputs.add_upload, pkg, upload.filename, upload.file, f.role, f.note, f.unpack)
    else:
        rs = await run_in_threadpool(inputs.add, pkg, body.path, body.role, body.note, body.unpack)
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


@api.post("/packages/{pkg}/acquire", tags=["inputs"], response_model=M.AcquireResult, summary="Fetch data & code from the manuscript",
          description="Scans a full text for links to data/code repositories (Zenodo, OSF, GitHub, GitLab, Figshare) and downloads "
                      "the files as inputs, so users don't have to upload anything by hand (metacheck-style).",
          responses=errors("no_fulltext", "acquire_failed", "acquire_not_found", "acquire_too_large"))
def acquire_inputs(pkg: Pkg, body: M.AcquireCreate):
    return acquire.acquire(pkg, body.fulltext, body.role, body.note, body.unpack, body.repos)


# ---------- targets, actions and claims (same routes, different models) ----------


def curation_routes(kind: str, fields: type, view: type, patch_model: type) -> None:
    noun = kind[:-1]
    base = f"/packages/{{pkg}}/{kind}"
    run_errors = errors("not_found", "unknown_ids", "unknown_profile", "llm_call_failed", "llm_timeout", "llm_output_invalid")
    LlmRunModel = curation.LlmRun[curation.CURATIONS[kind].item]
    FileModel = curation.CURATIONS[kind].file_model

    @api.get(base, tags=[kind], response_model=list[view] | FileModel, summary=f"List {kind}",
             description=f"`view=effective` (default): manual {kind} and enabled LLM entries with edits applied, with their origin. "
                         f"`view=raw`: the stored {kind}.json (LLM versions, edits, runs).")
    def list_items(pkg: Pkg, page: Paged, view_: Annotated[Literal["effective", "raw"], Query(alias="view")] = "effective", include_disabled: bool = False):
        cur = curation.Curation(pkg, kind)
        if view_ == "raw":
            return cur.load()
        return page([{"item": i, "origin": o} for i, o in cur.effective(include_disabled)])

    @api.post(base, tags=[kind], status_code=201, response_model=curation.CURATIONS[kind].item, summary=f"Add a {noun} by hand",
              description="Needs `refs` into an extraction, or `unreferenced: true` with a `note`.",
              responses=errors("schema_invalid", "invalid_reference"))
    def add_item(pkg: Pkg, body: fields, response: Response):  # type: ignore[valid-type]
        item = curation.Curation(pkg, kind).add(body.model_dump(exclude_unset=True))
        created(response, f"/v1/packages/{pkg.id}/{kind}/{item.id}")
        return item

    @api.get(base + "/runs", tags=[kind], response_model=list[LlmRunModel], summary="List LLM runs")
    def list_runs(pkg: Pkg, page: Paged):
        return page(curation.Curation(pkg, kind).load().llm_runs)

    @api.post(base + "/runs", tags=[kind], status_code=201, response_model=LlmRunModel, summary=f"Extract {kind} with an LLM",
              description="Runs the default (or a custom) prompt over the extraction (or a scope of it) with a configured profile. "
                          "Items that fail validation are kept in the run's `rejected` list; the call itself fails only if the "
                          "output has no usable JSON (`llm_output_invalid`).", responses=run_errors)
    def create_run(pkg: Pkg, body: M.RunCreate, response: Response):
        gen = claims.generate if kind == "claims" else curation.Curation(pkg, kind).generate
        if kind == "claims":
            run = gen(pkg, body.extraction, Scope(body.scope.sections, body.scope.items),
                      instructions=body.instructions, prompt=body.prompt, profile=body.profile)
        else:
            run = gen(body.extraction, Scope(body.scope.sections, body.scope.items),
                      instructions=body.instructions, prompt=body.prompt, profile=body.profile)
        created(response, f"/v1/packages/{pkg.id}/{kind}/runs/{run.id}")
        return run

    @api.post(base + "/runs:preview", tags=[kind], response_model=M.PromptPreview, summary=f"Show the prompt of a {noun} extraction",
              description=f"Takes the body of `POST …/{kind}/runs` and returns what would be sent. Runs nothing and stores nothing.",
              responses=errors("not_found", "unknown_ids", "unknown_profile"))
    def preview_run(pkg: Pkg, body: M.RunCreate):
        c = curation.Curation(pkg, kind)
        scope = Scope(body.scope.sections, body.scope.items)
        if kind == "claims":
            text = claims.render_prompt(pkg, body.extraction, scope, instructions=body.instructions, prompt=body.prompt)
        else:
            text = c.render_prompt(body.extraction, scope, instructions=body.instructions, prompt=body.prompt)
        return _llm_preview(text, body.profile, schema=c.output_schema)

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
curation_routes("claims", curation.ClaimFields, M.ClaimView, M.ClaimPatch)


# ---------- script ----------


@api.get("/packages/{pkg}/script", tags=["script"], response_model=M.ScriptState, summary="The master script and its remaining TODO(LLM): markers")
def get_script(pkg: Pkg):
    m = pkg.load()
    tpl = scripts.current_template(pkg)
    return M.ScriptState(template=tpl.id if tpl else None, command=tpl.command if tpl else None, master=tpl.master if tpl else None,
                         agent_prompt=scripts.AGENT_PROMPT if tpl else None, todo=scripts.todo_status(pkg, tpl) if tpl else None,
                         files=m.by_kind("script") + m.by_kind("export"))


@api.put("/packages/{pkg}/script", tags=["script"], response_model=M.ScriptResult, summary="Create or replace the master script",
         description="Resolves the plan (load steps for used data inputs → enabled actions → results step), writes "
                     "`analysis_plan.json`, `results_schema.json`, the template's files and README.md. Returns 201 the first "
                     "time, 200 when replacing. Files edited since they were generated (e.g. by the agent) are "
                     "protected: `on_modified` = refuse (409), keep (regenerate the rest) or overwrite. Claims mode (the package "
                     "has claims): the plan covers the ticked claims; the master file is a generated runner that runs the ticked "
                     "claims whose file has no `TODO(LLM):` markers left; the shared preparation file is regenerated while "
                     "unedited and kept once edited; each ticked claim gets its own file (`claims/C1.py`), regenerated when "
                     "the claim changed and the file is unedited, kept (and listed in `kept`, out of date: the agent prompt "
                     "asks to revise it) once edited. `on_modified` applies only to an edited monolithic master the runner "
                     "would replace: refuse (409), keep (moved to `<stem>.pre-claims.<ext>`) or overwrite.",
         responses={201: {"model": M.ScriptResult, "description": "created"},
                    **errors("unknown_template", "template_not_claims_ready", "nothing_to_script", "script_modified")})
def put_script(pkg: Pkg, body: M.ScriptRequest, response: Response):
    out = scripts.write(pkg, body.template, body.on_modified)
    if out["created"]:
        created(response, f"/v1/packages/{pkg.id}/script")
    return out


# ---------- jobs ----------

JOB_ERRORS = errors("not_found")


@api.get("/agent-profiles", tags=["service"], response_model=list[M.AgentProfileView], summary="Configured coding-agent profiles")
def agent_profiles():
    cfg = settings()
    return [M.AgentProfileView(name=n, default=n == cfg.agent_default_profile, argv=p.argv, tools=p.tools, exec_tools=p.exec_tools,
                               model=p.model, effort=p.effort, timeout_s=p.timeout_s, events=p.events) for n, p in cfg.agent_profiles.items()]


@api.post("/packages/{pkg}/jobs", tags=["jobs"], status_code=202, response_model=Job, summary="Start a job",
          description="`type: codegen` runs the configured coding agent in the package folder to implement the master script's "
                      "`TODO(LLM):` markers. `type: run` runs the master script with the template's command (output and "
                      "errors as `log` events), then checks `outputs/results.json`. The package is committed to git before "
                      "and after. Returns 202 at once; follow the job at its `Location`, its progress at `…/events`.",
          responses=errors("script_missing", "job_running", "unknown_profile", "job_start_failed", "not_found", "claim_not_checked",
                           "claim_not_testable", "claim_no_evidence"))
def create_job(pkg: Pkg, body: Annotated[M.JobCreate, Body()], response: Response):
    if isinstance(body, M.RunJobCreate):
        job = run.start(pkg)
    else:
        job = codegen.start(pkg, profile=body.profile, allow_exec=body.allow_exec, instructions=body.instructions,
                             claim=body.claim)
    response.headers["Location"] = f"/v1/packages/{pkg.id}/jobs/{job.id}"
    return job


@api.get("/jobs", tags=["jobs"], response_model=list[M.RunningJob], summary="Jobs running now, in all packages",
         description="Only jobs that are running in this service, oldest first (finished jobs are listed per package). "
                     "Clients use it to warn before stopping the service, which ends them as `interrupted`.")
def running_jobs(page: Paged):
    ids = {p.root.resolve(): p.id for p in Registry().list()}
    out = []
    for root, jid in jobs.running():
        pid = ids.get(root.resolve())
        if pid is None:
            continue  # the package was unregistered while its job runs
        job = jobs.load(Package(pid, root), jid)
        if job.status == "running":
            out.append(M.RunningJob(package=pid, job=job))
    return page(sorted(out, key=lambda r: r.job.created_at))


@api.post("/packages/{pkg}/jobs:preview", tags=["jobs"], response_model=M.PromptPreview, summary="Show the prompt of a code-generation job",
          description="Takes the body of `POST …/jobs` with `type: codegen` and returns what would be sent. Runs nothing and stores nothing.",
          responses=errors("script_missing", "unknown_profile", "not_found", "claim_not_checked", "claim_not_testable",
                           "claim_no_evidence"))
def preview_codegen(pkg: Pkg, body: M.CodegenJobCreate):
    p = codegen.plan(pkg, profile=body.profile, allow_exec=body.allow_exec, instructions=body.instructions, claim=body.claim)
    return M.PromptPreview(prompt=p.prompt, argv=p.argv, profile=p.name, model=p.profile.model, effort=p.profile.effort)


@api.get("/packages/{pkg}/jobs", tags=["jobs"], response_model=list[Job], summary="List jobs (oldest first)")
def list_jobs(pkg: Pkg, page: Paged):
    return page(jobs.list_jobs(pkg))


@api.get("/packages/{pkg}/jobs/{job}", tags=["jobs"], response_model=Job, summary="Get a job", responses=JOB_ERRORS)
def get_job(pkg: Pkg, job: str):
    return jobs.load(pkg, job)


@api.delete("/packages/{pkg}/jobs/{job}", tags=["jobs"], status_code=202, response_model=Job, summary="Cancel a running job",
            description="Stops the whole process group (SIGTERM, then SIGKILL). The job then ends with status `cancelled`; "
                        "the record is kept.", responses=errors("not_found", "job_finished"))
def cancel_job(pkg: Pkg, job: str):
    return jobs.cancel(pkg, job)


@api.get("/packages/{pkg}/jobs/{job}/events", tags=["jobs"], response_model=list[JobEvent], summary="Progress events",
         description="The agent's messages, tool calls and tool results, parsed from its output. JSON (`?after=N` returns "
                     "events with seq > N), or with `Accept: text/event-stream` a Server-Sent Events stream that follows the "
                     "job live and ends with an `end` event carrying the final job record (`Last-Event-ID` resumes).",
         responses={**JOB_ERRORS, 200: {"content": {"application/json": {}, "text/event-stream": {}}}})
def job_events(pkg: Pkg, job: str, request: Request, after: Annotated[int, Query(ge=0)] = 0):
    j = jobs.load(pkg, job)
    path = pkg.path(j.stdout)
    if "text/event-stream" not in request.headers.get("accept", ""):
        return read_events(path, j.events_format, after, final=j.status != "running")
    last = int(request.headers.get("last-event-id") or after)
    return StreamingResponse(_sse(pkg, job, path, j.events_format, last), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _sse(pkg: Package, jid: str, path, fmt: str, last: int):
    reader = EventReader(path, fmt)
    idle = 0.0
    while True:
        finished = jobs.load(pkg, jid).status != "running"
        for e in reader.poll(final=finished):
            if e.seq > last:
                yield f"id: {e.seq}\nevent: {e.kind}\ndata: {e.model_dump_json(exclude_none=True)}\n\n"
                idle = 0.0
        if finished:
            final = jobs.load(pkg, jid)
            if final.finished_at:
                yield f"event: end\ndata: {final.model_dump_json(exclude_none=True)}\n\n"
                return
        await asyncio.sleep(0.3)
        idle += 0.3
        if idle >= 15:
            yield ": keep-alive\n\n"
            idle = 0.0


@api.get("/packages/{pkg}/jobs/{job}/stdout", tags=["jobs"], response_class=PlainTextResponse, summary="Raw stdout of the job", responses=JOB_ERRORS)
def job_stdout(pkg: Pkg, job: str):
    j = jobs.load(pkg, job)
    p = pkg.path(j.stdout)
    return PlainTextResponse(p.read_text(encoding="utf-8", errors="replace") if p.exists() else "")


# ---------- comparison ----------


@api.get("/packages/{pkg}/comparison", tags=["comparison"], response_model=Comparison, summary="Compare reproduced results with the targets",
         description="Reads `outputs/results.json` and compares it with the targets of `results_schema.json` (the ones the code "
                     "was written against). Numbers are rounded to the reported decimals (`rounding=reported`) or to a fixed "
                     "number; a deviation up to `threshold` % counts as within tolerance; tables are compared cell by cell.",
         responses=errors("script_missing", "results_missing"))
def get_comparison(pkg: Pkg, rounding: Annotated[str, Query(pattern=r"^(reported|\d{1,2})$")] = "reported",
                   threshold: Annotated[float, Query(ge=0)] = 5):
    st = CompareSettings(rounding="reported" if rounding == "reported" else int(rounding), threshold=threshold)
    return run.comparison(pkg, st)


def _settings_for(pkg, rounding: str | None, threshold: float | None) -> CompareSettings:
    base = claims.settings(pkg)
    return CompareSettings(rounding=base.rounding if rounding is None else ("reported" if rounding == "reported" else int(rounding)),
                           threshold=base.threshold if threshold is None else threshold)


CLAIM_SETTINGS = ("`rounding` and `threshold` work as in `GET …/comparison`; left out, they are the last report's settings "
                  "(the defaults without a report), so the app, the report, the trace and the deposit agree.")


@api.get("/packages/{pkg}/claims:status", tags=["comparison"], response_model=list[ClaimStatus], summary="State and result of every claim",
         description="`state` is `not_testable`; `running` (a claim's codegen job, or any run job for the ticked claims); the "
                     "verdict (`reproduced`, `partly`, `not_reproduced`, `not_computed` when no value was computed) when the claim ran and has not changed since; "
                     "otherwise `ready` (ticked) or `review`. A changed claim is `stale` and keeps its last `result`. A "
                     "claim's verdict pools all the evidence of the claim and its sub-claims; `by_sub_claim` lists theirs. A "
                     "ticked claim whose file still has `TODO(LLM):` markers is not run. " + CLAIM_SETTINGS)
def claims_status(pkg: Pkg, rounding: Annotated[str | None, Query(pattern=r"^(reported|\d{1,2})$")] = None,
                  threshold: Annotated[float | None, Query(ge=0)] = None):
    return claims.statuses(pkg, _settings_for(pkg, rounding, threshold))


@api.get("/packages/{pkg}/claims:trace", tags=["comparison"], response_model=ClaimsTrace, summary="The claims trace (claims_trace.json)",
         description="Every claim in the trace format: the paper's DOI and title, the claim's id, text, mode, links, sub-claims "
                     "and evidence (reported value, comparator, decimals, readable source; no tolerance: values are compared "
                     "with the package-wide rule), methods, the coding agent's `# ASSUMPTION:` comments in the claim's file and "
                     "the prepare file, its state and (last) result. `skipped`: the sentences the enabled claims run passed over. "
                     "The report and the archive write the same object to `claims_trace.json`. Empty without claims. " + CLAIM_SETTINGS)
def claims_trace(pkg: Pkg, rounding: Annotated[str | None, Query(pattern=r"^(reported|\d{1,2})$")] = None,
                 threshold: Annotated[float | None, Query(ge=0)] = None):
    return claims.trace(pkg, _settings_for(pkg, rounding, threshold))


# ---------- figure crops ----------

CROP_ERRORS = errors("not_found", "not_a_figure_target", "no_figure_reference", "no_coordinates", "pdf_unknown", "not_convertible")


@api.get("/packages/{pkg}/coordinates", tags=["report"], response_model=list[M.CoordinateReport], summary="What each full text locates on the PDF",
         description="Read from the full-text files: which can carry coordinates, how many real figures and tables they locate, "
                     "and on which PDF, confirmed by the hash GROBID and Docling record where possible.")
def get_coordinates(pkg: Pkg):
    return coordinates.report(pkg)


@api.put("/packages/{pkg}/figures/{target}", tags=["report"], response_model=M.CropResult, summary="Get the original figure of a figure target",
         description="Writes `targets/figures/<target>.png`, best first: the image as published, when a JATS full text names it and Europe PMC "
                     "has it; the figure's area, from a full text that locates it (matched by caption when the target refers to an extraction "
                     "without coordinates, such as JATS or a reconciled one); the whole page with the figure's caption. `method` says which, and "
                     "`note` why the published image wasn't available if that failed. Naming a `pdf` skips the published image. "
                     "The report shows the original next to the reproduced figure. "
                     "201 the first time, 200 when replacing.",
         responses={201: {"model": M.CropResult, "description": "created"}, **CROP_ERRORS})
def put_figure(pkg: Pkg, target: str, response: Response, body: M.CropRequest = Body(default_factory=M.CropRequest)):
    first = pkg.load().get(f"figure-{target.lower()}") is None
    out = figures.crop(pkg, target, body.pdf, body.scale)
    if first:
        created(response, f"/v1/packages/{pkg.id}/figures/{target}")
    return out


@api.put("/packages/{pkg}/figures", tags=["report"], response_model=list[M.CropOutcome], summary="Get the original figures of all figure targets",
         description="Like `PUT …/figures/{target}` for every enabled figure target; problems are reported per target.")
def put_figures(pkg: Pkg, body: M.CropRequest = Body(default_factory=M.CropRequest)):
    return figures.crop_all(pkg, body.pdf, body.scale)


@api.get("/packages/{pkg}/figures", tags=["report"], response_model=list[M.FigureOriginal], summary="The originals made so far")
def list_figures(pkg: Pkg):
    return figures.listing(pkg)


@api.get("/packages/{pkg}/figures/{target}", tags=["report"], response_class=FileResponse, summary="The crop (PNG)", responses=errors("not_found"))
def get_figure(pkg: Pkg, target: str):
    r = pkg.load().get(f"figure-{target.lower()}")
    if r is None:
        raise not_found("figure crop", target)
    return FileResponse(pkg.path(r.path), media_type="image/png")


# ---------- report and archive ----------


@api.put("/packages/{pkg}/report", tags=["report"], response_model=M.ReportResult, summary="Create or replace the report",
         description="Writes `report.html` (results comparison, provenance diagram, sources and LLM provenance, inputs, plan, "
                     "every file with its hash) and `comparison.json`. 201 the first time, 200 when replacing.",
         responses={201: {"model": M.ReportResult, "description": "created"}, **errors("script_missing", "results_missing", "job_running")})
def put_report(pkg: Pkg, body: M.ReportRequest, response: Response):
    first = pkg.load().get("report") is None
    out = report.write_report(pkg, CompareSettings(rounding=body.rounding, threshold=body.threshold))
    if first:
        created(response, f"/v1/packages/{pkg.id}/report")
    return out


@api.get("/packages/{pkg}/report", tags=["report"], response_class=FileResponse, summary="The report (HTML)", responses=errors("report_missing"))
def get_report(pkg: Pkg):
    if not pkg.load().get("report"):
        raise ApiError(409, "report_missing", "create the report first (PUT …/report)")
    return FileResponse(pkg.path(report.REPORT), media_type="text/html")


@api.put("/packages/{pkg}/archive", tags=["report"], response_model=ArchiveInfo, summary="Create or replace the ZIP of the package",
         description="Zips `datapackage.json` and every resource it lists (a Frictionless zipped data package) into "
                     "`archive/`. Only for a package that validates and has a report; the archive's hash is recorded in the manifest.",
         responses={201: {"model": ArchiveInfo, "description": "created"}, **errors("package_invalid", "report_missing", "job_running")})
def put_archive(pkg: Pkg, response: Response):
    first = pkg.load().reprocrowd.archive is None
    info = report.write_archive(pkg)
    if first:
        created(response, f"/v1/packages/{pkg.id}/archive")
    return info


@api.get("/packages/{pkg}/archive", tags=["report"], response_class=FileResponse, summary="Download the ZIP", responses=errors("archive_missing"))
def get_archive(pkg: Pkg):
    path = report.archive_path(pkg)
    return FileResponse(path, media_type="application/zip", filename=path.name)


# ---------- package database ----------


@api.get("/database", tags=["database"], response_model=M.DatabaseStatus, summary="The configured package database and whether it is reachable")
def database_status():
    return database.status()


@api.get("/database/packages", tags=["database"], response_model=list[dict[str, Any]], summary="Packages in the database for a DOI",
         responses=errors("database_not_configured", "database_unreachable", "database_error"))
def database_packages(doi: Annotated[str, Query(min_length=3)]):
    return database.find(doi)


@api.post("/packages/{pkg}/submissions", tags=["database"], status_code=201, response_model=Submission, summary="Submit the archive to the package database",
          description="Uploads the archive with bibliographic metadata looked up in OpenAlex by DOI now. Needs a DOI, a last "
                      "run that succeeded and an up-to-date archive. With `database_token` (the user's token for the database) "
                      "the package is published at once (`status: submitted`). Without it, the submission waits for the user "
                      "to sign in and confirm at `finish_url`; `GET …/submissions/{id}` reports whether that happened.",
          responses=errors("database_not_configured", "no_doi", "run_not_succeeded", "archive_missing", "archive_stale", "openalex_record_missing",
                           "duplicate_submission", "submission_rejected", "database_token_invalid", "database_forbidden",
                           "database_unreachable", "database_error", "job_running"))
def submit(pkg: Pkg, response: Response, body: M.SubmissionCreate = Body(default_factory=M.SubmissionCreate)):
    sub = database.submit(pkg, body.note, body.database_token.get_secret_value() if body.database_token else None)
    created(response, f"/v1/packages/{pkg.id}/submissions/{sub.id}")
    return sub


@api.get("/packages/{pkg}/submissions", tags=["database"], response_model=list[Submission], summary="Submissions of this package")
def list_submissions(pkg: Pkg, page: Paged):
    return page(pkg.load().reprocrowd.submissions)


@api.get("/packages/{pkg}/submissions/{sub}", tags=["database"], response_model=Submission, summary="A submission, with its status refreshed from the database",
         responses=errors("not_found", "database_unreachable", "database_error"))
def get_submission(pkg: Pkg, sub: str):
    return database.refresh(pkg, sub)


# ---------- Zenodo ----------

DEPOSIT_PRECONDITIONS = ("no_doi", "run_not_succeeded", "archive_missing", "archive_stale", "openalex_record_missing", "job_running")


def _tok(body: M.ZenodoAuth) -> str | None:
    return body.zenodo_token.get_secret_value() if body.zenodo_token else None


@api.get("/zenodo", tags=["zenodo"], response_model=M.ZenodoStatus, summary="The Zenodo server deposits go to, and whether it is reachable")
def zenodo_status(sandbox: bool | None = None):
    return zenodo.status(sandbox)


@api.post("/zenodo:verify-token", tags=["zenodo"], response_model=M.ZenodoTokenResult, summary="Check a Zenodo access token",
          responses=errors("zenodo_token_missing", "zenodo_token_invalid", "zenodo_unreachable", "zenodo_error"))
def zenodo_verify(body: M.ZenodoTokenCheck = Body(default_factory=M.ZenodoTokenCheck)):
    return zenodo.verify_token(_tok(body), body.sandbox)


@api.get("/zenodo/records", tags=["zenodo"], response_model=list[M.ZenodoRecord], summary="Published ReproCrowd records for a paper",
         description="Public records on Zenodo with the keyword ReproCrowd that relate to the DOI.", responses=errors("zenodo_unreachable", "zenodo_error"))
def zenodo_records(doi: Annotated[str, Query(min_length=3)], sandbox: bool | None = None):
    return zenodo.find(doi, sandbox)


@api.get("/packages/{pkg}/deposits", tags=["zenodo"], response_model=list[ZenodoDeposit], summary="Zenodo deposits of this package")
def list_deposits(pkg: Pkg, page: Paged):
    return page(pkg.load().reprocrowd.deposits)


@api.post("/packages/{pkg}/deposits:preview", tags=["zenodo"], response_model=M.DepositPreview, summary="Show what a deposit would send",
          description="The metadata and the groups of files, without contacting Zenodo.", responses=errors(*DEPOSIT_PRECONDITIONS))
def preview_deposit(pkg: Pkg, body: M.DepositOptions = Body(default_factory=M.DepositOptions)):
    return zenodo.preview(pkg, body.exclude, body.note, body.creators, body.community)


@api.post("/packages/{pkg}/deposits", tags=["zenodo"], status_code=201, response_model=ZenodoDeposit, summary="Deposit the package on Zenodo",
          description="Creates a draft on Zenodo with the metadata (from OpenAlex, looked up now) and the package ZIP, without the excluded "
                      "groups of files, and reserves a DOI. With `publish: true` it is published at once; otherwise publish it on Zenodo "
                      "or with `…/deposits/{id}:publish`. Same preconditions as a database submission. With claims: at least "
                      "one claim must have been run (`no_claim_run`), `claims` lists the ones that were, and a package with a "
                      "published deposit on the server gets a new version of that record (`version_of`).",
          responses=errors(*DEPOSIT_PRECONDITIONS, "duplicate_submission", "no_claim_run", "zenodo_token_missing", "zenodo_token_invalid",
                           "zenodo_creators_missing", "zenodo_rejected", "zenodo_unreachable", "zenodo_error"))
def create_deposit(pkg: Pkg, response: Response, body: M.DepositCreate = Body(default_factory=M.DepositCreate)):
    d = zenodo.create(pkg, token=_tok(body), sandbox=body.sandbox, exclude=body.exclude, note=body.note, creators=body.creators,
                      community=body.community, publish=body.publish)
    created(response, f"/v1/packages/{pkg.id}/deposits/{d.id}")
    return d


@api.get("/packages/{pkg}/deposits/{dep}", tags=["zenodo"], response_model=ZenodoDeposit, summary="A deposit, as recorded",
         responses=errors("not_found"))
def get_deposit(pkg: Pkg, dep: str):
    return zenodo._find(pkg, dep)


@api.post("/packages/{pkg}/deposits/{dep}:refresh", tags=["zenodo"], response_model=ZenodoDeposit, summary="Update a deposit's status from Zenodo",
          description="A draft may have been published or deleted on the website.",
          responses=errors("not_found", "zenodo_token_missing", "zenodo_token_invalid", "zenodo_unreachable", "zenodo_error"))
def refresh_deposit(pkg: Pkg, dep: str, body: M.ZenodoAuth = Body(default_factory=M.ZenodoAuth)):
    return zenodo.refresh(pkg, dep, _tok(body))


@api.post("/packages/{pkg}/deposits/{dep}:publish", tags=["zenodo"], response_model=ZenodoDeposit, summary="Publish a draft deposit",
          description="Registers the DOI. A published record can't be deleted, only versioned.",
          responses=errors("not_found", "deposit_not_draft", "zenodo_token_missing", "zenodo_token_invalid", "zenodo_rejected",
                           "zenodo_unreachable", "zenodo_error"))
def publish_deposit(pkg: Pkg, dep: str, body: M.ZenodoAuth = Body(default_factory=M.ZenodoAuth)):
    return zenodo.publish_deposit(pkg, dep, _tok(body))


@api.post("/packages/{pkg}/deposits/{dep}:discard", tags=["zenodo"], response_model=ZenodoDeposit, summary="Delete a draft deposit on Zenodo",
          responses=errors("not_found", "deposit_not_draft", "zenodo_token_missing", "zenodo_token_invalid", "zenodo_unreachable", "zenodo_error"))
def discard_deposit(pkg: Pkg, dep: str, body: M.ZenodoAuth = Body(default_factory=M.ZenodoAuth)):
    return zenodo.discard(pkg, dep, _tok(body))


# ---------- files ----------


@api.get("/packages/{pkg}/files", tags=["packages"], response_model=list[M.FileEntry], summary="Files on disk (most recent first)",
         description="Everything in the package folder except bookkeeping (jobs/, llm-calls/, hidden files), with the resource "
                     "each file belongs to. Useful to watch an agent at work.")
def list_files(pkg: Pkg, page: Paged):
    return page(files.listing(pkg))


@api.get("/packages/{pkg}/files/{path:path}", tags=["packages"], response_class=FileResponse, summary="Download one file of the package",
         description="Any file `GET …/files` lists (not bookkeeping such as jobs/, llm-calls/ or hidden files), e.g. the master "
                     "script, `AGENT_PROMPT.md`, the run log or a reproduced figure.", responses=errors("not_found"))
def get_file(pkg: Pkg, path: str):
    p = files.file_path(pkg, path)
    if p is None:
        raise not_found("file", path)
    return FileResponse(p, media_type=files.mediatype(path))


@api.post("/packages/{pkg}:sync", tags=["packages"], response_model=M.SyncReport, summary="Register files changed outside the API",
          description="Refreshes hashes of script, code and output resources and registers new files (outputs/ → output, "
                      "elsewhere → code). Inputs, full texts and extractions are never refreshed: changes to them stay "
                      "visible as integrity errors. Jobs do this automatically when they finish.", responses=errors("job_running"))
def sync_files(pkg: Pkg):
    jobs.ensure_idle(pkg)
    return files.sync(pkg)


app.include_router(service)
app.include_router(api)
