"""Problem types (RFC 9457). Every error response is application/problem+json with
``type`` = /v1/problems/<code>, which resolves to the entry below."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# code → (HTTP status, title)
CATALOG: dict[str, tuple[int, str]] = {
    # requests
    "request_invalid": (422, "The request body or parameters don't match the API schema"),
    "unauthorized": (401, "Missing or wrong bearer token"),
    "precondition_failed": (412, "If-Match doesn't match the package's current ETag"),
    "not_found": (404, "No such resource"),
    "method_not_allowed": (405, "Method not allowed on this resource"),
    "internal_error": (500, "Unexpected server error"),
    # packages and paths
    "invalid_path": (400, "Invalid package-relative path"),
    "path_not_allowed": (403, "Path is outside the configured allowed roots"),
    "not_a_file": (400, "The path is not a readable file"),
    "package_exists": (409, "A package is already registered or present at this path"),
    "folder_not_empty": (409, "The folder exists and is not empty"),
    "package_missing": (410, "The registered package folder no longer exists"),
    # metadata
    "invalid_identifier": (400, "Unrecognized work identifier"),
    "no_identifier": (400, "No identifier given and the package has none"),
    "metadata_exists": (409, "Metadata was already retrieved; replace it with PUT"),
    "identifier_conflict": (409, "The package was created for a different identifier; replace it with PUT"),
    "metadata_missing": (409, "Retrieve the OpenAlex metadata first"),
    "openalex_not_found": (404, "OpenAlex has no work for this identifier"),
    "openalex_failed": (502, "OpenAlex could not be reached or returned an error"),
    # full texts and extractions
    "integrity_check_failed": (422, "The file failed its format's integrity check"),
    "duplicate_fulltext": (409, "An identical full text is already in the package"),
    "download_failed": (502, "The candidate could not be downloaded"),
    "in_use": (409, "Other items depend on this resource"),
    "unknown_converter": (400, "No such converter"),
    "converter_not_configured": (409, "The converter is not configured in the service"),
    "not_convertible": (422, "Converters take PDF full texts"),
    "conversion_failed": (502, "The converter failed"),
    "conversion_timeout": (504, "The converter exceeded its timeout"),
    "not_parseable": (422, "This format can't be parsed into the document schema"),
    "parse_failed": (422, "The file could not be converted into a valid document"),
    "extraction_exists": (409, "An extraction with this name exists; replace it with PUT"),
    "name_taken": (409, "The name belongs to a different kind of resource"),
    "no_sources": (400, "No source full texts given"),
    "unknown_ids": (422, "Ids are not in the extraction"),
    # LLM calls
    "unknown_profile": (400, "No LLM profile with this name is configured"),
    "llm_call_failed": (502, "The LLM command could not start or exited with an error"),
    "llm_timeout": (504, "The LLM call exceeded the profile's timeout"),
    "llm_output_invalid": (422, "The LLM call succeeded but its output is not valid (see stage and errors)"),
    # inputs
    "invalid_archive": (422, "The zip archive is corrupt or unsafe"),
    "archive_member": (409, "Archive members can't be deleted; mark them unused"),
    # targets and actions
    "schema_invalid": (422, "The item doesn't satisfy its schema"),
    "invalid_reference": (422, "References (text, inputs, targets, actions) don't resolve"),
    "llm_entry": (409, "LLM entries are disabled, not deleted"),
    "manual_item": (409, "Not applicable to items added by hand"),
    # scripts
    "unknown_template": (400, "No such master-script template"),
    "nothing_to_script": (422, "At least one enabled target and one enabled action are needed"),
    "script_modified": (409, "Generated files were changed since; pass force to overwrite"),
    "script_missing": (409, "Create the master script first"),
    # jobs
    "job_running": (409, "Another job is running in this package"),
    "job_finished": (409, "The job is not running"),
    "job_start_failed": (502, "The job's command could not be started"),
}

STATUS_CODES = {404: "not_found", 405: "method_not_allowed", 401: "unauthorized"}


class Problem(BaseModel):
    """RFC 9457 problem details, with MetaReader extension members."""

    model_config = ConfigDict(extra="allow")

    type: str = Field(description="URI of the problem type: /v1/problems/<code>")
    title: str
    status: int
    detail: str
    instance: str | None = Field(default=None, description="request path")
    code: str = Field(description="machine-readable problem code")
    request_id: str | None = None


class ProblemType(BaseModel):
    code: str
    status: int
    title: str


def problem_types() -> list[ProblemType]:
    return [ProblemType(code=c, status=s, title=t) for c, (s, t) in CATALOG.items()]
