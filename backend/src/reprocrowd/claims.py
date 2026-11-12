"""The claim's "checked" tick: what a tick covers and when it is reset.

A tick approves the claim's content and the targets and actions it points to. Editing any of them resets it. Notes,
refs and the tick itself are not content (readable locations don't reset it).
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from .package import Package
from .schemas.curation import Claim

RESET_FIELDS = {"text", "mode", "original_quote", "testable", "method", "evidence", "sub_claims"}


def _owners(claim: Claim) -> list:
    return [claim, *claim.sub_claims]


def content_hash(pkg: Package, claim: Claim) -> str:
    from .curation import Curation  # curation.py calls uncheck_users

    own = claim.model_dump(mode="json", include=RESET_FIELDS)
    for s in own["sub_claims"]:
        s.pop("refs", None)  # a sub-claim's refs are locations, not content
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
