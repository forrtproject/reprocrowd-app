"""Whole-package validation in one go:

1. manifest   datapackage.json against the Manifest model
2. frictionless  file integrity (bytes, sha256) and the Data Package descriptor
3. jsonschema  every resource with a jsonSchema, against the schema copy in the package
4. model       the same JSON files against the Pydantic models (cross-field rules JSON Schema can't express)
5. semantic    references resolve, inputs/targets exist, no dependency cycles, untracked files
"""
from __future__ import annotations

import json
from typing import Literal

import jsonschema
from frictionless import validate as fl_validate
from pydantic import BaseModel, ValidationError

from .curation import CURATIONS, Context, Curation, find_cycle
from .errors import pydantic_errors
from .package import MANIFEST, Package
from .schemas import SCHEMAS, schema_path
from .schemas.manifest import Manifest

Stage = Literal["manifest", "frictionless", "jsonschema", "model", "semantic"]


class Issue(BaseModel):
    stage: Stage
    resource: str | None = None
    message: str


class ValidationReport(BaseModel):
    valid: bool
    errors: list[Issue]
    warnings: list[Issue]


IGNORED_DIRS = ("llm-calls/", "outputs/", ".git/")


def validate_package(pkg: Package) -> ValidationReport:
    errors: list[Issue] = []
    warnings: list[Issue] = []

    def err(stage: Stage, msg: str, res: str | None = None):
        errors.append(Issue(stage=stage, resource=res, message=msg))

    def warn(stage: Stage, msg: str, res: str | None = None):
        warnings.append(Issue(stage=stage, resource=res, message=msg))

    # 1. manifest
    try:
        m = Manifest.model_validate_json(pkg.path(MANIFEST).read_bytes())
    except (ValidationError, FileNotFoundError) as e:
        msgs = pydantic_errors(e) if isinstance(e, ValidationError) else [str(e)]
        for msg in msgs:
            err("manifest", msg)
        return ValidationReport(valid=False, errors=errors, warnings=warnings)

    # 2. frictionless: integrity of every file
    report = fl_validate(str(pkg.path(MANIFEST)))
    for task in report.tasks:
        for e in task.errors:
            err("frictionless", f"{e.type}: {e.note}", task.name)
    for e in report.errors:
        err("frictionless", f"{e.type}: {e.note}")

    # 3 + 4. JSON resources against their schema and model
    model_of = {schema_path(n): model for n, (model, _) in SCHEMAS.items()}
    for r in m.resources:
        if not r.jsonSchema:
            continue
        try:
            data = pkg.read_json(r.path)
            schema = pkg.read_json(r.jsonSchema)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            err("jsonschema", str(e), r.name)
            continue
        for e in jsonschema.Draft202012Validator(schema).iter_errors(data):
            err("jsonschema", f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}", r.name)
        if model := model_of.get(r.jsonSchema):
            try:
                model.model_validate(data)
            except ValidationError as e:
                for msg in pydantic_errors(e):
                    err("model", msg, r.name)
    if errors:  # semantic checks assume well-formed files
        return ValidationReport(valid=False, errors=errors, warnings=warnings)

    # 5. semantic
    ctx = Context(pkg)
    for r in m.by_kind("extraction"):
        for ft in r.metareader.fulltexts:
            if not m.get(ft):
                warn("semantic", f"source full text {ft} no longer exists", r.name)
    for kind in CURATIONS:
        cur = Curation(pkg, kind)
        everything = cur.effective(include_disabled=True)
        actions = [a for a, _ in everything] if kind == "actions" else None
        for item, origin in everything:
            for msg in ctx.item_errors(cur.spec, item, actions):
                (warn if origin.get("disabled") else err)("semantic", f"{item.id}: {msg}", kind)
        if kind == "actions":
            enabled = [a for a, o in everything if not o.get("disabled")]
            if cycle := find_cycle(enabled):
                err("semantic", f"dependency cycle {' → '.join(cycle)}", kind)
            enabled_targets = {t.id for t, _ in Curation(pkg, "targets").effective()}
            used_inputs = {r.name for r in m.by_kind("input") if r.metareader.used}
            for a in enabled:
                for t in a.produces:
                    if t not in enabled_targets:
                        warn("semantic", f"{a.id} produces {t}, which is disabled", kind)
                for i in a.inputs:
                    if i not in used_inputs:
                        warn("semantic", f"{a.id} uses input {i}, which is marked unused", kind)
    tracked = {r.path for r in m.resources} | {MANIFEST}
    for p in sorted(pkg.root.rglob("*")):
        rel = p.relative_to(pkg.root).as_posix()
        if p.is_file() and rel not in tracked and not rel.startswith(IGNORED_DIRS) and not p.name.startswith("."):
            warn("semantic", f"untracked file {rel}")
    return ValidationReport(valid=not errors, errors=errors, warnings=warnings)

