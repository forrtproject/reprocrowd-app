"""All custom schemas. The Pydantic models are the single source of truth: the JSON Schemas in
``schemas/`` and the reference in ``docs/schemas.md`` are generated from them (``reprocrowd-schemas export``).
To add a schema, define the model in one of the modules here and list it in SCHEMAS."""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from .curation import ActionLlmOutput, ClaimLlmOutput, ActionsFile, ClaimsFile, TargetLlmOutput, TargetsFile
from .document import Document, DocumentDraft
from .exports import AnalysisPlan, InputsList, ReproducedOutput, ResultsSchema
from .claims import ClaimsTrace
from .comparison import Comparison
from .jobs import Job
from .manifest import Manifest

# name → (model, file it describes)
SCHEMAS: dict[str, tuple[type[BaseModel], str]] = {
    "datapackage": (Manifest, "datapackage.json"),
    "document": (Document, "extractions/<id>.json"),
    "document-draft": (DocumentDraft, "parser / reconcile-LLM output, before ids are assigned"),
    "targets": (TargetsFile, "targets.json"),
    "actions": (ActionsFile, "actions.json"),
    "claims": (ClaimsFile, "claims.json"),
    "target-llm-output": (TargetLlmOutput, "output of the target-extraction prompt"),
    "action-llm-output": (ActionLlmOutput, "output of the action-extraction prompt"),
    "claim-llm-output": (ClaimLlmOutput, "output of the claims-extraction prompt"),
    "results-schema": (ResultsSchema, "results_schema.json"),
    "analysis-plan": (AnalysisPlan, "analysis_plan.json"),
    "inputs": (InputsList, "inputs.json (written with the archive)"),
    "results-output": (ReproducedOutput, "outputs/results.json"),
    "job": (Job, "jobs/<id>.json"),
    "comparison": (Comparison, "comparison.json (written with the report; also GET …/comparison)"),
    "claims-trace": (ClaimsTrace, "claims_trace.json (written with the report and the archive; also GET …/claims:trace)"),
}


def schema_path(name: str) -> str:
    """Package-relative path of a schema copy (used as a resource's jsonSchema)."""
    return f"schemas/{name}.schema.json"


def json_schema(name: str) -> dict[str, Any]:
    model, describes = SCHEMAS[name]
    s = model.model_json_schema()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://reprocrowd.example/schemas/{name}.schema.json",
        "title": f"{s.get('title', name)} ({describes})",
        **{k: v for k, v in s.items() if k != "title"},
    }


def json_schema_text(name: str) -> str:
    return json.dumps(json_schema(name), indent=2, ensure_ascii=False) + "\n"
