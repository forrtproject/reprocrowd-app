"""Generate schemas/*.schema.json, docs/schemas.md (from the models) and docs/openapi.json (from the API).

    reprocrowd-schemas export [REPO_ROOT]     write the files
    reprocrowd-schemas check  [REPO_ROOT]     exit 1 if they are out of date
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import SCHEMAS, json_schema, json_schema_text


def _type(p: dict[str, Any]) -> str:
    if "$ref" in p:
        name = p["$ref"].split("/")[-1]
        return f"[{name}](#{name.lower()})"
    if "const" in p:
        return f"`{p['const']!r}`".replace("'", '"')
    if "enum" in p:
        return " \\| ".join(f'`"{v}"`' if isinstance(v, str) else f"`{v}`" for v in p["enum"])
    for key in ("anyOf", "oneOf"):
        if key in p:
            return " \\| ".join(_type(x) for x in p[key])
    t = p.get("type")
    if t == "array":
        return f"list of {_type(p.get('items', {}))}"
    if t == "object" and "additionalProperties" in p and isinstance(p["additionalProperties"], dict):
        return f"map of {_type(p['additionalProperties'])}"
    if t == "string" and p.get("format"):
        return f"string ({p['format']})"
    return str(t or "any")


def _table(model: dict[str, Any]) -> list[str]:
    props = model.get("properties", {})
    if not props:
        return []
    required = set(model.get("required", []))
    rows = ["| Field | Type | Req. | Description |", "|---|---|---|---|"]
    for name, p in props.items():
        notes = [p.get("description", "")]
        if "pattern" in p:
            notes.append(f"pattern `{p['pattern']}`")
        if "default" in p and p["default"] not in (None, [], {}):
            notes.append(f"default `{p['default']}`")
        text = "; ".join(n for n in notes if n).replace("|", "\\|").replace("\n", " ")
        rows.append(f"| `{name}` | {_type(p)} | {'yes' if name in required else ''} | {text} |")
    return rows


def _model_doc(name: str, model: dict[str, Any], level: str) -> list[str]:
    out = [f"{level} {name}", ""]
    if model.get("description"):
        out += [model["description"].strip(), ""]
    table = _table(model)
    if not table and "discriminator" in model:
        out += ["One of: " + ", ".join(_type(x) for x in model.get("oneOf", [])), ""]
    return out + table + [""]


def schemas_markdown() -> str:
    out = [
        "# Schemas",
        "",
        "Generated from the Pydantic models in `src/reprocrowd/schemas/` by `reprocrowd-schemas export`; do not edit.",
        "The JSON Schemas are in `schemas/`; every package gets a copy in its own `schemas/` folder.",
        "",
        "Validation beyond what JSON Schema expresses (references resolve, ids unique, no dependency cycles) is",
        "done by `GET /packages/{p}/validation` (src/reprocrowd/validation.py).",
        "",
    ]
    documented: set[str] = set()
    for name, (_, describes) in SCHEMAS.items():
        s = json_schema(name)
        out += [f"## `{name}`: {describes}", "", f"File: `schemas/{name}.schema.json`", ""]
        out += _model_doc("Root", s, "###")
        for def_name, d in s.get("$defs", {}).items():
            if def_name in documented:  # shared definitions are documented once, at first use
                continue
            documented.add(def_name)
            out += _model_doc(def_name, d, "###")
    return "\n".join(out).rstrip() + "\n"


def outputs(root: Path) -> dict[Path, str]:
    files = {root / "schemas" / f"{n}.schema.json": json_schema_text(n) for n in SCHEMAS}
    files[root / "docs" / "schemas.md"] = schemas_markdown()
    from ..api.app import app  # the HTTP API description, also served live at /openapi.json

    files[root / "docs" / "openapi.json"] = json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"
    return files


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    cmd = args[0] if args else "export"
    root = Path(args[1] if len(args) > 1 else ".")
    stale = []
    for path, text in outputs(root).items():
        if not path.exists() or path.read_text() != text:
            stale.append(path)
            if cmd == "export":
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
    if cmd == "check" and stale:
        print("out of date:", *map(str, stale), sep="\n  ")
        return 1
    if cmd == "export":
        print(f"wrote {len(stale)} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
