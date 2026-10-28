"""Targets and actions: manual items, LLM runs, and non-destructive edits (see schemas/curation.py).

Both kinds share all logic; a ``Spec`` says what differs (models, id prefix, prompt). Semantic checks that no
schema can express (references resolve, inputs exist, produced targets exist, no dependency cycles) are in
``Context`` and are also used by validation.py.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from . import llm
from .errors import ApiError, conflict, invalid, not_found, pydantic_errors
from .extractions import Scope, check_scope, listing
from .extractions import get as get_extraction
from .package import Package
from .schemas import json_schema
from .schemas.curation import (
    Action,
    ActionFields,
    ActionLlmItem,
    ActionsFile,
    LlmEntry,
    LlmRun,
    Ref,
    Rejected,
    Target,
    TargetFields,
    TargetLlmItem,
    TargetsFile,
)
from .schemas.document import Document

Kind = Literal["targets", "actions"]


@dataclass(frozen=True)
class Spec:
    kind: Kind
    file_model: type[BaseModel]
    item: type[BaseModel]
    fields: type[BaseModel]
    llm_item: type[BaseModel]
    output_key: str  # list key in the LLM output
    prefix: str
    width: int


CURATIONS: dict[str, Spec] = {
    "targets": Spec("targets", TargetsFile, Target, TargetFields, TargetLlmItem, "targets", "T", 3),
    "actions": Spec("actions", ActionsFile, Action, ActionFields, ActionLlmItem, "actions", "A", 2),
}


# ---------- semantic checks ----------


class Context:
    """Looks things up for semantic checks; loads each extraction at most once."""

    def __init__(self, pkg: Package):
        self.pkg = pkg
        self.manifest = pkg.load()
        self._docs: dict[str, Document | None] = {}

    def doc(self, name: str) -> Document | None:
        if name not in self._docs:
            r = self.manifest.get(name)
            self._docs[name] = get_extraction(self.pkg, name)[1] if r and r.metareader.kind == "extraction" else None
        return self._docs[name]

    def ref_errors(self, refs: list[Ref]) -> list[str]:
        errors = []
        for ref in refs:
            doc = self.doc(ref.extraction)
            if doc is None:
                errors.append(f"refs: no extraction {ref.extraction!r}")
            elif ref.id not in doc.index():
                errors.append(f"refs: {ref.id!r} is not in {ref.extraction}")
        return errors

    def input_names(self) -> set[str]:
        return {r.name for r in self.manifest.by_kind("input")}

    def item_errors(self, spec: Spec, item: BaseModel, actions: list[Action] | None = None) -> list[str]:
        errors = self.ref_errors(item.refs)
        if spec.kind == "actions":
            inputs = self.input_names()
            errors += [f"inputs: no input {i!r}" for i in item.inputs if i not in inputs]
            target_ids = {t.id for t, _ in Curation(self.pkg, "targets").effective(include_disabled=True)}
            errors += [f"produces: no target {t!r}" for t in item.produces if t not in target_ids]
            if actions is not None:
                ids = {a.id for a in actions}
                errors += [f"depends_on: no action {d!r}" for d in item.depends_on if d not in ids]
        return errors


def find_cycle(actions: list[Action]) -> list[str] | None:
    deps = {a.id: [d for d in a.depends_on] for a in actions}
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(n: str) -> list[str] | None:
        state[n] = 1
        stack.append(n)
        for d in deps.get(n, []):
            if state.get(d) == 1:
                return stack[stack.index(d):] + [d]
            if d in deps and not state.get(d) and (c := visit(d)):
                return c
        stack.pop()
        state[n] = 2
        return None

    for n in deps:
        if not state.get(n) and (c := visit(n)):
            return c
    return None


# ---------- the curated collection ----------


class Curation:
    def __init__(self, pkg: Package, kind: str):
        if kind not in CURATIONS:
            raise not_found("collection", kind)
        self.pkg, self.spec = pkg, CURATIONS[kind]

    @property
    def rel(self) -> str:
        return f"{self.spec.kind}.json"

    def load(self) -> Any:
        return self.spec.file_model.model_validate(self.pkg.read_json(self.rel))

    def _save(self, f: BaseModel) -> None:
        with self.pkg.edit() as m:
            meta = m.get(self.spec.kind).metareader
            self.pkg.write_json(m, self.spec.kind, self.rel, f, schema=self.spec.kind, meta=meta)

    # ----- views -----

    def _effective_of(self, f, include_disabled: bool) -> list[tuple[Any, dict]]:
        out: list[tuple[Any, dict]] = [(i, {"source": "manual"}) for i in f.manual]
        for run in f.llm_runs:
            for e in run.entries:
                disabled = run.disabled or e.disabled
                if disabled and not include_disabled:
                    continue
                item = self.spec.item.model_validate(e.base.model_dump() | (e.edit or {}))
                out.append((item, {"source": "llm", "run": run.id, "edited": bool(e.edit), "disabled": disabled}))
        return out

    def effective(self, include_disabled: bool = False) -> list[tuple[Any, dict]]:
        """Manual items plus enabled LLM entries with their edits applied; origin says where each came from."""
        return self._effective_of(self.load(), include_disabled)

    def _all_ids(self, f) -> set[str]:
        return {i.id for i in f.manual} | {e.base.id for r in f.llm_runs for e in r.entries}

    def _next_ids(self, f, n: int) -> list[str]:
        nums = [int(i[1:]) for i in self._all_ids(f) if re.fullmatch(rf"{self.spec.prefix}\d+", i)]
        start = max(nums, default=0) + 1  # ids are never reused
        return [f"{self.spec.prefix}{k:0{self.spec.width}d}" for k in range(start, start + n)]

    def _locate(self, f, item_id: str) -> tuple[Any, LlmRun | None, LlmEntry | None]:
        for i in f.manual:
            if i.id == item_id:
                return i, None, None
        for run in f.llm_runs:
            for e in run.entries:
                if e.base.id == item_id:
                    return None, run, e
        raise not_found(self.spec.kind[:-1], item_id)

    def _check(self, f, item: BaseModel, ctx: Context | None = None) -> None:
        ctx = ctx or Context(self.pkg)
        actions = None
        if self.spec.kind == "actions":
            actions = [a for a, _ in self._effective_of(f, include_disabled=True)]
        errors = ctx.item_errors(self.spec, item, actions)
        if not errors and self.spec.kind == "actions":
            if item.id in item.depends_on:
                errors.append("depends_on: an action can't depend on itself")
            elif cycle := find_cycle([a for a, _ in self._effective_of(f, include_disabled=False)]):
                errors.append(f"depends_on: cycle {' → '.join(cycle)}")
        if errors:
            raise invalid("invalid_reference", f"{item.id}: references don't resolve", {"errors": errors})

    # ----- manual items -----

    def add(self, data: dict[str, Any]) -> Any:
        with self.pkg.locked():
            f = self.load()
            try:
                fields = self.spec.fields.model_validate(data)
                item = self.spec.item.model_validate(fields.model_dump() | {"id": self._next_ids(f, 1)[0]})
            except ValidationError as e:
                raise invalid("schema_invalid", f"invalid {self.spec.kind[:-1]}", {"errors": pydantic_errors(e)})
            f.manual.append(item)
            self._check(f, item)
            self._save(f)
            return item

    def delete(self, item_id: str) -> None:
        with self.pkg.locked():
            f = self.load()
            item, run, _ = self._locate(f, item_id)
            if run is not None:
                raise conflict("llm_entry", f"{item_id} comes from LLM run {run.id}; disable it instead (PATCH disabled: true)")
            users = self._users(item_id)
            if users:
                raise conflict("in_use", f"{item_id} is used by {', '.join(users)}", {"used_by": users})
            f.manual = [i for i in f.manual if i.id != item_id]
            self._save(f)

    def _users(self, item_id: str) -> list[str]:
        actions = [a for a, _ in Curation(self.pkg, "actions").effective(include_disabled=True)]
        if self.spec.kind == "targets":
            return [a.id for a in actions if item_id in a.produces]
        return [a.id for a in actions if item_id in a.depends_on]

    # ----- edits (JSON merge patch) -----

    def patch(self, item_id: str, patch: dict[str, Any]) -> tuple[Any, dict]:
        editable = set(self.spec.fields.model_fields)
        unknown = set(patch) - editable - {"disabled"}
        if unknown:
            raise invalid("schema_invalid", f"cannot change {', '.join(sorted(unknown))}", {"editable": sorted(editable | {'disabled'})})
        changes = {k: v for k, v in patch.items() if k != "disabled"}
        with self.pkg.locked():
            f = self.load()
            item, run, entry = self._locate(f, item_id)
            try:
                if run is None:
                    if "disabled" in patch:
                        raise conflict("manual_item", "manual items are deleted, not disabled")
                    new = self.spec.item.model_validate(item.model_dump() | changes)
                    f.manual = [new if i.id == item_id else i for i in f.manual]
                else:
                    base = entry.base.model_dump()
                    edit = {**(entry.edit or {}), **changes}
                    edit = {k: v for k, v in edit.items() if base.get(k) != v}  # an edit back to the LLM value is no edit
                    new = self.spec.item.model_validate(base | edit)
                    entry.edit = edit or None
                    if "disabled" in patch:
                        entry.disabled = bool(patch["disabled"])
            except ValidationError as e:
                raise invalid("schema_invalid", f"{item_id}: the edited item is invalid", {"errors": pydantic_errors(e)})
            self._check(f, new)
            self._save(f)
            return next((i, o) for i, o in self._effective_of(f, True) if i.id == item_id)

    def drop_overlay(self, item_id: str) -> Any:
        """Restore an LLM entry: drop the user's edit and re-enable it."""
        with self.pkg.locked():
            f = self.load()
            _, run, entry = self._locate(f, item_id)
            if run is None:
                raise conflict("manual_item", f"{item_id} was added by hand; it has no LLM version to restore")
            entry.edit, entry.disabled = None, False
            self._check(f, entry.base)
            self._save(f)
            return entry.base

    # ----- LLM runs -----

    def get_run(self, run_id: str) -> LlmRun:
        run = next((r for r in self.load().llm_runs if r.id == run_id), None)
        if run is None:
            raise not_found("run", run_id)
        return run

    def patch_run(self, run_id: str, disabled: bool) -> LlmRun:
        with self.pkg.locked():
            f = self.load()
            run = next((r for r in f.llm_runs if r.id == run_id), None)
            if run is None:
                raise not_found("run", run_id)
            run.disabled = disabled
            if self.spec.kind == "actions" and (cycle := find_cycle([a for a, _ in self._effective_of(f, False)])):
                raise invalid("invalid_reference", f"enabling {run_id} creates a dependency cycle", {"cycle": cycle})
            self._save(f)
            return run

    def generate(self, extraction: str, scope: Scope, *, instructions: str, prompt: str | None, profile: str | None) -> LlmRun:
        _, doc = get_extraction(self.pkg, extraction)
        check_scope(doc, scope)
        values = {
            "document": listing(doc, scope),
            "schema": json.dumps(json_schema(f"{self.spec.kind[:-1]}-llm-output"), indent=1),
            "instructions": f"\nAdditional instructions:\n{instructions.strip()}\n" if instructions.strip() else "",
        }
        if self.spec.kind == "actions":
            values |= {"inputs": self._inputs_listing(), "targets": self._targets_listing()}
        required = ("document", "schema") + (("inputs", "targets") if self.spec.kind == "actions" else ())
        text = llm.render(prompt if prompt is not None else llm.load_prompt(self.spec.kind), values, required=required)
        call = llm.run(self.pkg, self.spec.kind, text, profile_name=profile, template="custom" if prompt is not None else "default",
                       instructions=instructions)
        # The call succeeded; problems from here on are the output's fault.
        try:
            raw = llm.extract_json(call.output)
        except ValueError as e:
            raise llm.output_invalid(call, "json", [str(e)])
        items = raw.get(self.spec.output_key) if isinstance(raw, dict) else None
        if not isinstance(items, list):
            raise llm.output_invalid(call, "schema", [f"expected an object with a {self.spec.output_key!r} list"])
        request = {"extraction": extraction, "scope": {"sections": scope.sections, "items": scope.items}}

        with self.pkg.locked():
            f = self.load()
            ids = iter(self._next_ids(f, len(items)))  # consumed by accepted items only: no gaps
            new_id = next(ids, None)
            ctx = Context(self.pkg)
            existing_actions = [a for a, _ in self._effective_of(f, True)] if self.spec.kind == "actions" else []
            key_to_id: dict[str, str] = {}
            entries, rejected = [], []
            for raw_item in items:
                try:
                    item = self._from_llm(raw_item, new_id, extraction, key_to_id)
                    accepted = existing_actions + [e.base for e in entries]
                    errors = ctx.item_errors(self.spec, item, accepted if self.spec.kind == "actions" else None)
                except ValidationError as e:
                    errors = pydantic_errors(e)
                except ValueError as e:
                    errors = [str(e)]
                if errors:
                    rejected.append(Rejected(item=raw_item, errors=errors))
                else:
                    entries.append(LlmEntry[self.spec.item](base=item))
                    new_id = next(ids, None)
            run_no = max((int(r.id[3:]) for r in f.llm_runs), default=0) + 1
            run = LlmRun[self.spec.item](id=f"run{run_no}", request=request, call=call, entries=entries, rejected=rejected)
            f.llm_runs.append(run)
            self._save(f)
            return run

    def _from_llm(self, raw: Any, new_id: str, extraction: str, key_to_id: dict[str, str]) -> BaseModel:
        it = self.spec.llm_item.model_validate(raw)
        data = it.model_dump(exclude={"refs", "key"}) | {"id": new_id, "refs": [{"extraction": extraction, "id": r} for r in it.refs]}
        if self.spec.kind == "targets":
            data = _normalize_target(data)
        else:
            unknown = [d for d in it.depends_on if d not in key_to_id]
            if unknown:
                raise ValueError(f"depends_on: {', '.join(unknown)} are not keys of earlier actions")
            data["depends_on"] = [key_to_id[d] for d in it.depends_on]
            by_path = {r.path.removeprefix("inputs/"): r.name for r in Context(self.pkg).manifest.by_kind("input")}
            data["inputs"] = [by_path.get(i.removeprefix("inputs/"), i) for i in it.inputs]
        item = self.spec.item.model_validate(data)
        if self.spec.kind == "actions":
            key_to_id[it.key] = new_id
        return item

    def _inputs_listing(self) -> str:
        rows = [f"- {r.name}: {r.path} [{r.metareader.role}]{' — ' + r.metareader.note if r.metareader.note else ''}"
                for r in Context(self.pkg).manifest.by_kind("input") if r.metareader.used]
        return "\n".join(rows) or "(no input files)"

    def _targets_listing(self) -> str:
        rows = [f"- {t.id} ({t.type}): {t.description}" for t, _ in Curation(self.pkg, "targets").effective()]
        return "\n".join(rows) or "(no targets defined yet)"


def _normalize_target(d: dict[str, Any]) -> dict[str, Any]:
    """Light clean-up of common LLM slips: numbers given as strings, "=" as comparator."""
    if d.get("comparator") == "=":
        d["comparator"] = None
    if d["type"] == "number" and isinstance(d.get("expected"), str):
        s = d["expected"].strip().replace("−", "-").replace(",", "")
        try:
            d["expected"] = float(s) if any(c in s for c in ".eE") else int(s)
            if d.get("decimals") is None and "." in s:
                d["decimals"] = len(s.split(".")[1])
        except ValueError:
            pass  # leave it; validation rejects the item with a clear message
    if d["type"] != "number":
        d["comparator"], d["decimals"] = None, None
    return d
