"""The claim's "checked" tick: what a tick covers and when it is reset.

A tick approves the claim's content and the targets and actions it points to. Editing any of them resets it. Notes,
refs and the tick itself are not content (readable locations don't reset it).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from . import llm
from .errors import pydantic_errors
from .extractions import Scope, check_scope, listing
from .extractions import get as get_extraction
from .package import Package
from .schemas import json_schema
from .schemas.claims import (
    ClaimResult,
    ClaimState,
    ClaimStatus,
    ClaimsTrace,
    ClaimTrace,
    EvidenceResult,
    SubClaimResult,
    TraceEvidence,
    TraceMethod,
    TracePaper,
    TraceSubClaim,
    Verdict,
)
from .schemas.common import now
from .schemas.document import Document, ref_kind
from .schemas.comparison import CompareSettings, Comparison, Status
from .schemas.curation import (
    Action,
    Claim,
    ClaimLlmItem,
    ClaimRunRecord,
    LlmEntry,
    LlmRun,
    MethodLlmItem,
    PrepLlmItem,
    Ref,
    Rejected,
    SkippedLlmItem,
    SkippedSentence,
    SubClaim,
    Target,
    TargetLlmItem,
)

RESET_FIELDS = {"text", "mode", "original_quote", "testable", "method", "evidence", "sub_claims"}


def _owners(claim: Claim) -> list:
    return [claim, *claim.sub_claims]


_LOCATION = {"refs", "unreferenced", "note"}


def content(claim: dict) -> dict:
    """The part of a claim (as a dict) that a tick covers: RESET_FIELDS without the locations of the claim's sub-claims."""
    out = {k: v for k, v in claim.items() if k in RESET_FIELDS}
    out["sub_claims"] = [{k: v for k, v in sc.items() if k not in _LOCATION} for sc in out.get("sub_claims", [])]
    return out


def content_hash(pkg: Package, claim: Claim) -> str:
    from .curation import Curation  # curation.py calls uncheck_users

    own = content(claim.model_dump(mode="json"))
    tids = {t for o in _owners(claim) for t in o.evidence}
    aids = {o.method for o in _owners(claim) if o.method}
    targets = {i.id: i.model_dump(mode="json", exclude={"refs"}) for i, _ in Curation(pkg, "targets").effective(include_disabled=True) if i.id in tids}
    actions = {i.id: i.model_dump(mode="json", exclude={"refs"}) for i, _ in Curation(pkg, "actions").effective(include_disabled=True) if i.id in aids}
    blob = json.dumps({"claim": own, "targets": targets, "actions": actions}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()


def uncheck_users(pkg: Package, kind: Literal["targets", "actions"], item_id: str) -> list[str]:
    """Untick every claim whose evidence (targets) or method (actions) is item_id; returns their ids."""
    from .curation import Curation

    claims = Curation(pkg, "claims")
    hit = []
    for c, _ in claims.effective(include_disabled=True):
        used = any(item_id in (o.evidence if kind == "targets" else [o.method]) for o in _owners(c))
        if used:
            hit.append(c.id)
            if c.checked:
                claims.patch(c.id, {"checked": False})
    return hit


# ---------- which claims the master script runs ----------


@dataclass(frozen=True)
class ClaimScope:
    """What the master script covers in claims mode."""

    claims: list[Claim]  # ticked, testable, with at least one enabled evidence target; in id order
    targets: set[str]  # their enabled evidence targets
    actions: set[str]  # their enabled methods, plus every effective transform action
    preparation: list[str]  # the transform action ids


def evidence(claim: Claim) -> list[str]:
    """The claim's evidence target ids: its own and its sub-claims', in order."""
    return list(dict.fromkeys(t for o in _owners(claim) for t in o.evidence))


def methods(claim: Claim) -> list[str]:
    """The claim's method action ids: its own and its sub-claims', in order."""
    return list(dict.fromkeys(o.method for o in _owners(claim) if o.method))


def claim_scope(pkg: Package) -> ClaimScope | None:
    """None when the package has no effective claim: the master script is the monolithic one, as before claims."""
    from .curation import Curation

    all_claims = [c for c, _ in Curation(pkg, "claims").effective()]
    if not all_claims:
        return None
    enabled_t = {t.id for t, _ in Curation(pkg, "targets").effective()}
    actions = [a for a, _ in Curation(pkg, "actions").effective()]
    enabled_a = {a.id for a in actions}
    picked = [c for c in sorted(all_claims, key=lambda c: int(c.id[1:]))
              if c.checked and c.testable and any(t in enabled_t for t in evidence(c))]
    preparation = [a.id for a in actions if a.kind == "transform"]
    return ClaimScope(claims=picked,
                      targets={t for c in picked for t in evidence(c) if t in enabled_t},
                      actions={a for c in picked for a in methods(c) if a in enabled_a} | set(preparation),
                      preparation=preparation)


def claim_files(pkg: Package, claim_id: str) -> list[str]:
    """Paths of the master-script files generated for the claim (claims/C1.py)."""
    return [r.path for r in pkg.load().by_kind("script") if getattr(r.reprocrowd, "claim", None) == claim_id]


# ---------- results and statuses ----------

_TESTED = {"match", "within", "deviation"}


def verdict(statuses: list[Status]) -> tuple[Verdict, int, int]:
    """(verdict, matched, tested): missing, unparsable and figure rows are not computed, so they are not tested."""
    tested = sum(s in _TESTED for s in statuses)
    matched = sum(s in ("match", "within") for s in statuses)
    if tested == 0 or matched == 0:
        return "not_reproduced", matched, tested
    return ("reproduced" if matched == tested else "partly"), matched, tested


def _evidence_results(cmp: Comparison, ids: list[str]) -> list[EvidenceResult]:
    rows = {r.target: r for r in cmp.rows}
    return [EvidenceResult(target=i, status=rows[i].status, expected_text=rows[i].expected_text, reproduced_text=rows[i].reproduced_text)
            for i in ids if i in rows]


def results(pkg: Package, cmp: Comparison) -> list[ClaimResult]:
    """The result of each testable claim that has evidence in the comparison. A claim's verdict pools every evidence value
    of the claim and its sub-claims; each sub-claim's own result is listed beside it."""
    from .curation import Curation

    out = []
    for c, _ in Curation(pkg, "claims").effective():
        if not c.testable:
            continue
        pooled = _evidence_results(cmp, evidence(c))
        if not pooled:
            continue
        v, matched, tested = verdict([e.status for e in pooled])
        subs = []
        for sc in c.sub_claims:
            ev = _evidence_results(cmp, list(dict.fromkeys(sc.evidence)))
            sv, sm, st = verdict([e.status for e in ev])
            subs.append(SubClaimResult(id=sc.id, verdict=sv, matched=sm, tested=st, evidence=ev))
        out.append(ClaimResult(claim=c.id, verdict=v, matched=matched, tested=tested, by_sub_claim=subs,
                               evidence=_evidence_results(cmp, list(dict.fromkeys(c.evidence)))))
    return sorted(out, key=lambda r: int(r.claim[1:]))


def _comparison(pkg: Package, st: CompareSettings | None = None) -> Comparison | None:
    from . import run
    from .errors import ApiError

    try:
        return run.comparison(pkg, st or CompareSettings())
    except ApiError:
        return None  # no script or no results file yet


def record_run(pkg: Package, job_id: str) -> None:
    """After a run: note which claims it covered, with each one's result. A no-op outside claims mode."""
    from .curation import Curation

    scope = claim_scope(pkg)
    if scope is None:
        return
    cmp = _comparison(pkg)
    snap = {r.claim: r for r in results(pkg, cmp)} if cmp else {}
    with pkg.locked():
        cur = Curation(pkg, "claims")
        f = cur.load()
        at = now()
        for c in scope.claims:
            f.ran[c.id] = ClaimRunRecord(job=job_id, at=at, content_hash=content_hash(pkg, c), result=snap.get(c.id))
        cur._save(f)


def statuses(pkg: Package, st: CompareSettings | None = None) -> list[ClaimStatus]:
    """The state of every effective claim, decided in this order: not testable; running (its codegen job, or any run job
    for a ticked claim); the verdict when it ran and is unchanged; ready (ticked) or review. A changed claim is stale and
    keeps its last result."""
    from . import jobs
    from .curation import Curation

    cur = Curation(pkg, "claims")
    ran = cur.load().ran
    claims = sorted((c for c, _ in cur.effective()), key=lambda c: int(c.id[1:]))
    job = jobs.active(pkg)
    running: set[str] = set()
    if job is not None:
        if job.type == "run":
            scope = claim_scope(pkg)
            running = {c.id for c in scope.claims} if scope else set()
        elif job.request.get("claim"):
            running = {job.request["claim"]}
    cmp = _comparison(pkg, st)  # the live result of a current claim; None: the default settings
    live = {r.claim: r for r in results(pkg, cmp)} if cmp else {}
    out = []
    for c in claims:
        rec = ran.get(c.id)
        stale = rec is not None and (rec.content_hash != content_hash(pkg, c) or not c.checked)
        if not c.testable:
            out.append(ClaimStatus(claim=c.id, state="not_testable", stale=False, ran_at=None, result=None))
            continue
        result = None
        if rec is not None:  # the snapshot of that run, unless the claim is current and ticked: then the live comparison
            result = rec.result if stale else live.get(c.id, rec.result)
        state: ClaimState = "ready" if c.checked else "review"
        if c.id in running:
            state = "running"
        elif rec is not None and not stale and result is not None:
            state = result.verdict
        out.append(ClaimStatus(claim=c.id, state=state, stale=stale, ran_at=rec.at if rec else None, result=result))
    return out


# ---------- the claims trace ----------

ASSUMPTION_RE = re.compile(r"^\s*#\s*ASSUMPTION:\s*(.*?)\s*$", re.MULTILINE)


def assumptions(pkg: Package, claim_id: str) -> list[str]:
    """The coding agent's flagged guesses: `# ASSUMPTION:` comment lines in the claim's file and the prepare file."""
    m = pkg.load()
    paths = claim_files(pkg, claim_id) + [r.path for r in m.by_kind("script") if getattr(r.reprocrowd, "claims_role", None) == "prepare"]
    out: list[str] = []
    for path in paths:
        f = pkg.path(path)
        if f.is_file():
            out += [a for a in ASSUMPTION_RE.findall(f.read_text(errors="replace")) if a]
    return list(dict.fromkeys(out))


def _location(docs: dict[str, Document | None], ref: Ref) -> str:
    """A readable location of a reference: the table's or figure's label, or the sentence's section title."""
    doc = docs.get(ref.extraction)
    head = ref.id.split(".")[0]
    if doc is not None:
        for x in [*doc.figures, *doc.tables]:
            if x.id == head and x.label:
                return x.label
        if ref_kind(ref.id) == "sentence":
            sec = next((s for s in doc.sections if s.id == head), None)
            if sec is not None and sec.title:
                return sec.title
    return ref.id


VERDICT_LABEL: dict[str, str] = {"reproduced": "Reproduced", "partly": "Partly reproduced", "not_reproduced": "Not reproduced"}


def status_words(c: ClaimTrace) -> str:
    """A claim's status in words for the report and the deposit: "Reproduced 14/18", "Not run", "Not testable"."""
    if not c.testable:
        return "Not testable"
    if c.result is None:
        return "Not run"
    words = f"{VERDICT_LABEL[c.result.verdict]} {c.result.matched}/{c.result.tested}"
    return words if c.status in VERDICT_LABEL else words + " (claim changed since)"


def ran(pkg: Package) -> list[str]:
    """Ids of the effective claims that have been run (have a run record), in id order."""
    from .curation import Curation

    cur = Curation(pkg, "claims")
    done = cur.load().ran
    return sorted((c.id for c, _ in cur.effective() if c.id in done), key=lambda i: int(i[1:]))


def mode(pkg: Package) -> bool:
    """Claims mode: the package has at least one effective claim."""
    from .curation import Curation

    return bool(Curation(pkg, "claims").effective())


def trace(pkg: Package, st: CompareSettings | None = None) -> ClaimsTrace:
    """Every effective claim in the trace format, with its state and (last) result. No `tolerance`: values are compared
    with the package-wide rule."""
    from .curation import Curation

    m = pkg.load()
    doi = m.reprocrowd.identifiers.get("doi") or (m.reprocrowd.identifier.value if m.reprocrowd.identifier and m.reprocrowd.identifier.scheme == "doi" else None)
    work = pkg.read_json("metadata/openalex.json") if m.get("metadata-openalex") else {}
    title = (work.get("title") if isinstance(work, dict) else None) or m.title
    cur = Curation(pkg, "claims")
    f = cur.load()
    claims = sorted((c for c, _ in cur.effective()), key=lambda c: int(c.id[1:]))
    targets = {t.id: t for t, _ in Curation(pkg, "targets").effective()}
    actions = {a.id: a for a, _ in Curation(pkg, "actions").effective()}
    docs: dict[str, Document | None] = {}
    for t in targets.values():
        for r in t.refs[:1]:
            if r.extraction not in docs:
                try:
                    docs[r.extraction] = get_extraction(pkg, r.extraction)[1]
                except Exception:  # unreadable or removed extraction: the ref id is the location
                    docs[r.extraction] = None
    state = {s.claim: s for s in statuses(pkg, st)}

    def ev(ids: list[str]) -> list[TraceEvidence]:
        return [TraceEvidence(id=t.id, label=t.description, value=t.expected, comparator=t.comparator or "=", unit=t.unit,
                              decimals=t.decimals, source=_location(docs, t.refs[0]) if t.refs else t.note)
                for t in (targets[i] for i in dict.fromkeys(ids) if i in targets)]

    out = []
    for c in claims:
        st = state[c.id]
        out.append(ClaimTrace(
            id=doi, title=title, claim_id=c.id, main_claim=c.text, mode=c.mode, original_quote=c.original_quote,
            testable=c.testable, depends_on=c.depends_on, traced_under=c.traced_under,
            sub_claims=[TraceSubClaim(id=sc.id, claim=sc.text, method=sc.method, quantitative_evidence=ev(sc.evidence)) for sc in c.sub_claims],
            quantitative_evidence=ev(c.evidence),
            methods=[TraceMethod(id=a.id, summary=a.summary, quote=a.quote) for a in (actions[i] for i in methods(c) if i in actions)],
            assumptions=assumptions(pkg, c.id), status=st.state, result=st.result))
    skipped = [s for r in f.llm_runs if not r.disabled for s in f.skipped.get(r.id, [])]
    return ClaimsTrace(paper=TracePaper(doi=doi, title=title), claims=out, skipped=skipped)


# ---------- extraction: one LLM call writes claims, targets and actions ----------

OUTPUT_SCHEMA = "claim-llm-output"


def render_prompt(pkg: Package, extraction: str, scope: Scope, *, instructions: str, prompt: str | None) -> str:
    """The full prompt of a claims run; nothing is written."""
    _, doc = get_extraction(pkg, extraction)
    check_scope(doc, scope)
    values = {
        "document": listing(doc, scope),
        "schema": json.dumps(json_schema(OUTPUT_SCHEMA), indent=1),
        "instructions": f"\nAdditional instructions:\n{instructions.strip()}\n" if instructions.strip() else "",
    }
    return llm.render(prompt if prompt is not None else llm.load_prompt("claims"), values, required=("document", "schema"))


def _refs(extraction: str, ids: list[str]) -> list[dict]:
    return [{"extraction": extraction, "id": i} for i in ids]


def _target(raw: dict, tid: str, extraction: str) -> Target:
    from .curation import _normalize_target

    it = TargetLlmItem.model_validate(raw)
    return Target.model_validate(_normalize_target(it.model_dump(exclude={"refs"}) | {"id": tid, "refs": _refs(extraction, it.refs)}))


def _validated(model, raw):
    """(item, errors) of one LLM item; a bad item must not sink the rest of the output."""
    try:
        return model.model_validate(raw), []
    except ValidationError as e:
        return None, pydantic_errors(e)


def generate(pkg: Package, extraction: str, scope: Scope, *, instructions: str, prompt: str | None, profile: str | None) -> LlmRun[Claim]:
    """Extract claims with their evidence (targets) and methods and preparation (actions) in one call.

    Writes a run to each of claims.json, targets.json and actions.json under one lock. Evidence that fails validation is
    rejected and left out of its claim; a claim whose own references fail is rejected as a whole.
    """
    from .curation import Context, Curation

    text = render_prompt(pkg, extraction, scope, instructions=instructions, prompt=prompt)
    call = llm.run(pkg, "claims", text, profile_name=profile, template="custom" if prompt is not None else "default",
                   instructions=instructions, schema=OUTPUT_SCHEMA)
    try:
        raw = llm.output_json(call)
    except ValueError as e:
        raise llm.output_invalid(call, "json", [str(e)])
    if not isinstance(raw, dict) or not isinstance(raw.get("claims"), list):
        raise llm.output_invalid(call, "schema", ["expected an object with a 'claims' list"])
    lists = {k: raw.get(k) if isinstance(raw.get(k), list) else [] for k in ("methods", "preparation", "skipped")}

    with pkg.locked():
        claims_c, targets_c, actions_c = (Curation(pkg, k) for k in ("claims", "targets", "actions"))
        cf, tf, af = claims_c.load(), targets_c.load(), actions_c.load()
        tf_before, af_before = tf.model_copy(deep=True), af.model_copy(deep=True)
        ctx = Context(pkg)
        disabled = any(not r.disabled for r in cf.llm_runs)  # "A new run's claims arrive switched off"
        run_no = lambda f: max((int(r.id[3:]) for r in f.llm_runs), default=0) + 1  # noqa: E731
        claims_run_id = f"run{run_no(cf)}"
        request = {"via": "claims", "claims_run": claims_run_id, "extraction": extraction}
        actions_rejected: list[Rejected] = []
        targets_rejected: list[Rejected] = []
        claims_rejected: list[Rejected] = []

        # 1. methods and preparation steps; those with bad references are rejected
        def valid_steps(model, items):
            out = {}
            for r in items:
                it, errors = _validated(model, r)
                if it is not None:
                    errors = ctx.ref_errors([Ref(extraction=extraction, id=i) for i in it.refs])
                if errors:
                    actions_rejected.append(Rejected(item=r, errors=errors))
                elif it.key in out:
                    actions_rejected.append(Rejected(item=r, errors=[f"key: {it.key!r} is used twice"]))
                else:
                    out[it.key] = it
            return out

        preps = valid_steps(PrepLlmItem, lists["preparation"])
        methods = valid_steps(MethodLlmItem, lists["methods"])

        # 2. claims: validate everything that belongs to the claim itself (not its evidence), so that a rejected claim
        # leaves no targets behind
        accepted: list[tuple[int, dict, ClaimLlmItem]] = []
        for pos, r in enumerate(raw["claims"], 1):
            shell = dict(r) if isinstance(r, dict) else r
            evidence_raw: list = []
            if isinstance(shell, dict):
                evidence_raw = shell.get("evidence") if isinstance(shell.get("evidence"), list) else []
                shell["evidence"] = []
                if isinstance(shell.get("sub_claims"), list):
                    shell["sub_claims"] = [{**sc, "evidence": []} if isinstance(sc, dict) else sc for sc in shell["sub_claims"]]
            it, errors = _validated(ClaimLlmItem, shell)
            if it is not None:
                it_full = it.model_copy(deep=True)
                sub_evidence = [sc.get("evidence") if isinstance(sc.get("evidence"), list) else [] for sc in r.get("sub_claims", [])]
                owners = [it, *it.sub_claims]
                errors = ctx.ref_errors([Ref(extraction=extraction, id=i) for o in owners for i in o.refs])
                errors += [f"method: no method {o.method!r}" for o in owners if o.method is not None and o.method not in methods]
                if not errors:  # the model's own rules (untestable claims, sub-claims, paraphrases), on a probe
                    probe = {"id": "C1", "text": it.text, "mode": it.mode, "original_quote": it.original_quote, "testable": it.testable,
                             "refs": [{"extraction": extraction, "id": i} for i in it.refs],
                             "method": "A1" if it.method else None, "evidence": ["T001"] if evidence_raw else [],
                             "sub_claims": [{"id": f"C1.{n}", "text": sc.text, "refs": _refs(extraction, sc.refs)} for n, sc in enumerate(it.sub_claims, 1)]}
                    _, errors = _validated(Claim, probe)
                if not errors:
                    accepted.append((pos, {"evidence": evidence_raw, "sub_evidence": sub_evidence}, it_full))
                    continue
            claims_rejected.append(Rejected(item=r, errors=errors))

        # 3. evidence becomes targets; a bad item is rejected and dropped from its claim
        n_evidence = sum(len(x["evidence"]) + sum(map(len, x["sub_evidence"])) for _, x, _ in accepted)
        tids = iter(targets_c._next_ids(tf, n_evidence))
        target_entries: list[LlmEntry[Target]] = []
        by_method: dict[str, list[str]] = {}

        def make_evidence(raws: list, method: str | None) -> list[str]:
            out = []
            for r in raws:
                it, errors = _validated(TargetLlmItem, r)
                if it is not None:
                    errors = ctx.ref_errors([Ref(extraction=extraction, id=i) for i in it.refs])
                    if not errors:
                        try:
                            t = _target(r, next(tids), extraction)
                        except ValidationError as e:
                            errors = pydantic_errors(e)
                if errors:
                    targets_rejected.append(Rejected(item=r, errors=errors))
                    continue
                target_entries.append(LlmEntry[Target](base=t))
                out.append(t.id)
                if method:
                    by_method.setdefault(method, []).append(t.id)
            return out

        built: list[tuple[int, ClaimLlmItem, list[str], list[list[str]]]] = []
        for pos, x, it in accepted:
            own = make_evidence(x["evidence"], it.method)
            subs = [make_evidence(ev, sc.method) for sc, ev in zip(it.sub_claims, x["sub_evidence"])]
            built.append((pos, it, own, subs))
        t_run = LlmRun[Target](id=f"run{run_no(tf)}", disabled=disabled, request=request, call=call, entries=target_entries, rejected=targets_rejected)
        tf.llm_runs.append(t_run)

        # 4. actions: preparation first, then each method (created once), producing its evidence
        aids = iter(actions_c._next_ids(af, len(preps) + len(methods)))
        action_entries: list[LlmEntry[Action]] = []
        key_to_id: dict[str, str] = {}
        for key, p in preps.items():
            key_to_id[key] = next(aids)
            action_entries.append(LlmEntry[Action](base=Action(id=key_to_id[key], kind="transform", summary=p.summary, quote=p.quote,
                                                               refs=_refs(extraction, p.refs))))
        for key, m in methods.items():
            key_to_id[key] = next(aids)
            action_entries.append(LlmEntry[Action](base=Action(
                id=key_to_id[key], kind="analysis", summary=m.summary, quote=m.quote, refs=_refs(extraction, m.refs),
                depends_on=[key_to_id[k] for k in preps], produces=by_method.get(key, []))))
        a_run = LlmRun[Action](id=f"run{run_no(af)}", disabled=disabled, request=request, call=call, entries=action_entries, rejected=actions_rejected)
        af.llm_runs.append(a_run)

        # 5. claims, with ids; positions in depends_on / traced_under point at accepted claims only
        cids = iter(claims_c._next_ids(cf, len(built)))
        new_id = {pos: next(cids) for pos, *_ in built}
        entries: list[LlmEntry[Claim]] = []
        for pos, it, own, subs in built:
            cid = new_id[pos]
            sub_claims = [SubClaim(id=f"{cid}.{n}", text=sc.text, refs=_refs(extraction, sc.refs),
                                   method=key_to_id[sc.method] if sc.method else None, evidence=ev)
                          for n, (sc, ev) in enumerate(zip(it.sub_claims, subs), 1)]
            entries.append(LlmEntry[Claim](base=Claim(
                id=cid, text=it.text, mode=it.mode, original_quote=it.original_quote, testable=it.testable, refs=_refs(extraction, it.refs),
                method=key_to_id[it.method] if it.method else None, evidence=own, sub_claims=sub_claims,
                depends_on=[new_id[p] for p in it.depends_on if p in new_id and p != pos],
                traced_under=[new_id[p] for p in it.traced_under if p in new_id and p != pos])))
        c_run = LlmRun[Claim](id=claims_run_id, disabled=disabled, request=request | {"scope": {"sections": scope.sections, "items": scope.items}},
                              call=call, entries=entries, rejected=claims_rejected)
        cf.llm_runs.append(c_run)
        skipped = []
        for r in lists["skipped"]:
            s, _ = _validated(SkippedLlmItem, r)
            if s is not None and not ctx.ref_errors([Ref(extraction=extraction, id=s.ref)]):
                skipped.append(SkippedSentence(ref=Ref(extraction=extraction, id=s.ref), reason=s.reason))
        cf.skipped[claims_run_id] = skipped

        # 6. everything is built; write the three files back to back (undone if one write fails)
        saved = []
        try:
            for cur, f, before in ((targets_c, tf, tf_before), (actions_c, af, af_before), (claims_c, cf, None)):
                cur._save(f)
                saved.append((cur, before))
        except Exception:
            for cur, before in saved:
                if before is not None:
                    cur._save(before)
            raise
        return c_run
