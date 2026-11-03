"""Provenance of the reproduced targets: one graph, used for the report's diagram and per-target traces.

Layers, top to bottom (empty ones are left out):
    sources       the paper (identifier) and the data inputs used by the plan
    full texts    full texts of the paper (added from local files or downloaded via its metadata)
    converted     full texts converted from one of those PDFs
    extractions   full texts in the document schema
    passages      the sentences, figures and tables that steps and targets reference (text in the tooltip)
    steps …       the plan's steps, one layer per dependency depth
    targets       coloured by comparison status; always final nodes (nothing depends on a target)

Every edge points from what was used to what used it, so a target's ancestors are exactly the text, files
and procedures behind it (``trace``). Nodes that lead to no target are left out. The diagram has a fixed width: a layer with more nodes than fit wraps
into rows. Within a layer, nodes are ordered by the mean position of their parents (barycenter heuristic).
Data-flow edges are solid, reference edges (passage → step/target) dashed. In the report, clicking a node
highlights its lineage.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from typing import Any

from .schemas.comparison import STATUS_LABEL, Comparison
from .schemas.document import Document
from .schemas.exports import AnalysisPlan
from .schemas.manifest import Manifest

COLORS = {"match": "#2e7d32", "within": "#b58900", "deviation": "#c62828", "missing": "#757575", "figure": "#1565c0",
          "unparsable": "#6a1b9a", "paper": "#37474f", "fulltext": "#5d4037", "data": "#455a64", "extraction": "#00695c",
          "passage": "#8d6e63", "step": "#2a5aa0"}

WIDTH = 1040  # total width (fits the report column)
LABEL_W = 92  # left column with layer names
NODE_W, NODE_H, GAP_X, GAP_Y, LAYER_GAP = 176, 30, 12, 10, 34


@dataclass
class Node:
    id: str
    kind: str  # paper, fulltext, data, extraction, passage, step, target
    label: str
    title: str
    color: str
    parents: list[str] = field(default_factory=list)
    ref_parents: set[str] = field(default_factory=set)  # parents that are references (dashed edges)
    data: dict[str, Any] = field(default_factory=dict)
    x: float = 0
    y: float = 0


@dataclass
class Graph:
    nodes: dict[str, Node]
    layers: list[tuple[str, list[Node]]]

    def ancestors(self, node_id: str) -> list[Node]:
        seen: dict[str, Node] = {}
        stack = list(self.nodes[node_id].parents)
        while stack:
            p = stack.pop()
            if p in self.nodes and p not in seen:
                seen[p] = self.nodes[p]
                stack.extend(self.nodes[p].parents)
        return list(seen.values())

    def lineage(self) -> dict[str, list[str]]:
        """node → its ancestors and descendants (what the report highlights on click)."""
        children: dict[str, list[str]] = {}
        for n in self.nodes.values():
            for p in n.parents:
                children.setdefault(p, []).append(n.id)
        out = {}
        for nid in self.nodes:
            down, stack = set(), list(children.get(nid, []))
            while stack:
                c = stack.pop()
                if c not in down:
                    down.add(c)
                    stack.extend(children.get(c, []))
            out[nid] = sorted({a.id for a in self.ancestors(nid)} | down)
        return out


def build(m: Manifest, docs: dict[str, Document], plan: AnalysisPlan, cmp: Comparison, outputs: dict[str, str | None],
          target_refs: dict[str, list[tuple[str, str]]]) -> Graph:
    nodes: dict[str, Node] = {}

    def add(layer: list[Node], n: Node) -> Node:
        if n.id not in nodes:
            nodes[n.id] = n
            layer.append(n)
        return nodes[n.id]

    sources: list[Node] = []
    fulltexts: list[Node] = []
    converted: list[Node] = []
    ident = m.reprocrowd.identifier
    paper = None
    if ident:
        paper = add(sources, Node("paper", "paper", f"{ident.scheme}:{ident.value}", m.title or ident.value, COLORS["paper"],
                                  data={"identifier": f"{ident.scheme}:{ident.value}", "title": m.title}))
    for r in m.by_kind("fulltext"):
        o = r.reprocrowd.origin
        n = Node("ft:" + r.name, "fulltext", r.path.removeprefix("fulltext/"), f"{r.path} ({r.reprocrowd.flavor})", COLORS["fulltext"],
                 data={"path": r.path, "origin": o.type})
        if o.type == "conversion":
            n.parents = ["ft:" + o.source]
            add(converted, n)
        else:  # a file of the paper, local or downloaded
            n.parents = ["paper"] if paper else []
            add(fulltexts, n)
    for f in dict.fromkeys(f for s in plan.steps for f in s.inputs):
        add(sources, Node("in:" + f, "data", f.removeprefix("inputs/"), f, COLORS["data"], data={"path": f}))
    extractions: list[Node] = []
    for r in m.by_kind("extraction"):
        add(extractions, Node("ex:" + r.name, "extraction", r.name, f"{r.path} ({r.reprocrowd.method})", COLORS["extraction"],
                              ["ft:" + f for f in r.reprocrowd.fulltexts], data={"path": r.path, "method": r.reprocrowd.method}))
    passages: list[Node] = []

    def passage(extraction: str, item: str) -> str:
        doc = docs.get(extraction)
        found = doc.index().get(item) if doc else None
        obj = found[1] if found else None
        text = getattr(obj, "text", None) or (f"{obj.label}: " + " ".join(s.text for s in obj.caption) if obj is not None else "")
        n = add(passages, Node(f"p:{extraction}:{item}", "passage", f"{item} {text}", f"{extraction} {item}: {text}", COLORS["passage"],
                               ["ex:" + extraction], data={"extraction": extraction, "id": item, "text": text}))
        return n.id

    depth: dict[str, int] = {}
    for s in plan.steps:
        depth[s.id] = max([depth.get(d, 0) for d in s.depends_on] + [0]) + 1
    step_layers: dict[int, list[Node]] = {}
    for s in plan.steps:
        n = Node("s:" + s.id, "step", f"{s.id} {s.summary}", f"{s.id} [{s.kind}] {s.summary}", COLORS["step"],
                 ["in:" + f for f in s.inputs] + ["s:" + d for d in s.depends_on],
                 data={"id": s.id, "kind": s.kind, "summary": s.summary, "quote": s.quote, "depth": depth[s.id]})
        for ref in s.refs:
            pid = passage(ref.extraction, ref.id)
            n.parents.append(pid)
            n.ref_parents.add(pid)
        add(step_layers.setdefault(depth[s.id], []), n)
    produced_by: dict[str, str] = {}
    for s in plan.steps:
        for t in s.produces:
            if s.kind != "results" or t not in produced_by:
                produced_by[t] = s.id
    targets: list[Node] = []
    for row in cmp.rows:
        step = outputs.get(row.target) if outputs.get(row.target) in depth else produced_by.get(row.target)
        n = Node("t:" + row.target, "target", f"{row.target} {row.description}", f"{row.target}: {row.description} ({STATUS_LABEL[row.status]})",
                 COLORS[row.status], ["s:" + step] if step else [], data={"id": row.target, "status": row.status})
        for extraction, item in target_refs.get(row.target, []):
            pid = passage(extraction, item)
            n.parents.append(pid)
            n.ref_parents.add(pid)
        add(targets, n)

    layers = [("sources", sources), ("full texts", fulltexts), ("converted", converted), ("extractions", extractions), ("passages", passages)]
    layers += [(f"steps {d}" if len(step_layers) > 1 else "steps", step_layers[d]) for d in sorted(step_layers)]
    layers += [("targets", targets)]
    # Only what explains a target: drop branches no target depends on (e.g. a converted full text never used).
    g = Graph(nodes, layers)
    keep = {t.id for t in targets} | {a.id for t in targets for a in g.ancestors(t.id)}
    g.nodes = {k: n for k, n in nodes.items() if k in keep}
    g.layers = [(name, kept) for name, ns in layers if (kept := [n for n in ns if n.id in keep])]
    return g


def trace(g: Graph, target: str) -> dict[str, list[dict[str, Any]]]:
    """What went into a target, grouped: passages (with text), steps (in plan order), data files, full texts, sources."""
    anc = g.ancestors("t:" + target)
    by = {k: [n.data for n in anc if n.kind == k] for k in ("passage", "step", "data", "extraction", "fulltext", "paper")}
    by["step"].sort(key=lambda d: (d["depth"], d["id"]))
    by["passage"].sort(key=lambda d: (d["extraction"], d["id"]))
    return by


def _short(s: str, n: int = 27) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def svg(g: Graph) -> str:
    per_row = max(1, int((WIDTH - LABEL_W) // (NODE_W + GAP_X)))
    y = 10.0
    labels = []
    for name, layer in g.layers:
        def bary(n: Node) -> float:
            xs = [g.nodes[p].x for p in n.parents if p in g.nodes and g.nodes[p].y < y]
            return sum(xs) / len(xs) if xs else float("inf")

        order = sorted(range(len(layer)), key=lambda i: (bary(layer[i]), i))
        labels.append((name, y))
        for k, i in enumerate(order):
            row, col = divmod(k, per_row)
            in_row = min(per_row, len(order) - row * per_row)
            left = LABEL_W + ((WIDTH - LABEL_W) - (in_row * NODE_W + (in_row - 1) * GAP_X)) / 2
            layer[i].x, layer[i].y = left + col * (NODE_W + GAP_X), y + row * (NODE_H + GAP_Y)
        y += -(-len(layer) // per_row) * (NODE_H + GAP_Y) - GAP_Y + LAYER_GAP
    height = y - LAYER_GAP + 10

    paths = []
    for n in g.nodes.values():
        for p in dict.fromkeys(n.parents):
            if p not in g.nodes:
                continue
            a = g.nodes[p]
            x1, y1, x2, y2 = a.x + NODE_W / 2, a.y + NODE_H, n.x + NODE_W / 2, n.y
            my = (y1 + y2) / 2
            cls = "ref" if p in n.ref_parents else "flow"
            paths.append(f'<path class="{cls}" data-from="{escape(p)}" data-to="{escape(n.id)}" '
                         f'd="M{x1:.0f},{y1:.0f} C{x1:.0f},{my:.0f} {x2:.0f},{my:.0f} {x2:.0f},{y2:.0f}" />')
    boxes = [f'<g class="node" data-node="{escape(n.id)}"><title>{escape(n.title)}</title><rect x="{n.x:.0f}" y="{n.y:.0f}" width="{NODE_W}" '
             f'height="{NODE_H}" rx="5" fill="{n.color}" /><text x="{n.x + 7:.0f}" y="{n.y + NODE_H / 2 + 4:.0f}">{escape(_short(n.label))}</text></g>'
             for n in g.nodes.values()]
    names = [f'<text class="layer" x="4" y="{ly + NODE_H / 2 + 4:.0f}">{escape(name)}</text>' for name, ly in labels]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height:.0f}" viewBox="0 0 {WIDTH} {height:.0f}" class="dag">'
            "<style>.dag path{fill:none;stroke:#8a9a9a;stroke-width:1.2}.dag path.ref{stroke:#c8b8a8;stroke-dasharray:4 3}"
            ".dag text{fill:#fff;font:11px system-ui,sans-serif;pointer-events:none}.dag text.layer{fill:#555;font:600 11px system-ui,sans-serif}"
            ".dag .node{cursor:pointer}.dag.focus .node,.dag.focus path{opacity:.15}.dag.focus .on{opacity:1}"
            ".dag.focus path.on{stroke:#333;stroke-width:1.8}.dag .sel rect{stroke:#000;stroke-width:2}</style>"
            + "".join(paths) + "".join(boxes) + "".join(names) + "</svg>")
