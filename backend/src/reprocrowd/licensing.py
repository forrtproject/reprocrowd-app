"""Which licence applies to which file of a package (licenses.json, written with the archive).

Policy ``reprocrowd-licensing/1``:
  metadata  (datapackage.json, OpenAlex record, targets/actions, plan, schemas, exports)  CC0-1.0
  outputs   (report, comparison, execution evidence, run log, results, reproduced figures) CC-BY-4.0
  code      (master script and other new analysis code)                       [licensing] code (default MIT; or CC-BY-4.0)
  third-party (the article's full texts, extractions and original figures; input data and code): not relicensed.
Quoted passages of the article inside our files (targets, plan, report) remain under the article's terms. Our
licences cover only our own contribution: outputs computed from third-party data are also subject to that data's terms.
The app's own source code stays MIT-licensed; this policy is about what a reproduction produces.
"""
from __future__ import annotations

from typing import Any

from .config import settings
from .package import MANIFEST, Package
from .pilot_copy import THIRD_PARTY_KINDS  # one definition, also used by the pilots repository's Action
from .schemas.exports import LicenseEntry, LicenseMap
from .schemas.manifest import Resource

POLICY = "reprocrowd-licensing/1"
METADATA, OUTPUTS = "CC0-1.0", "CC-BY-4.0"
THIRD_PARTY = THIRD_PARTY_KINDS
FILE = "licenses.json"
OWN_LISTS = {"inputs.json", FILE}
QUOTES = "may quote the article; quoted passages remain under the article's terms"


def code_license() -> str:
    return settings().license_code


def rules() -> dict[str, str]:
    return {
        "metadata": f"{METADATA}: datapackage.json, bibliographic metadata, targets, actions, plan, schemas and other descriptive files",
        "outputs": f"{OUTPUTS}: report, comparison, execution evidence, run log, results and reproduced figures",
        "code": f"{code_license()}: the master script and other analysis code written for this reproduction",
        "third_party": "not relicensed: the article's full texts, extractions and original figures keep the article's licence; "
                       "input data and code keep the licence under which they were published at their source",
        "quotes": "passages of the article quoted in our files (targets, plan, report) remain under the article's terms",
        "derived": "our licences cover our own contribution only; outputs computed from third-party data are also subject to that data's terms",
    }


def paper_license(pkg: Package) -> str | None:
    if not pkg.load().get("metadata-openalex"):
        return None
    from .zenodo import paper_license as from_work

    try:
        return from_work(pkg.read_json("metadata/openalex.json"))
    except (OSError, ValueError):
        return None


def _entry(r: Resource, paper: str | None, doi: str | None) -> LicenseEntry:
    kind = r.reprocrowd.kind
    if kind == "input":
        o = r.reprocrowd.origin
        src = getattr(o, "id", None) or getattr(o, "url", None)  # never a local path or file name: this file is public
        return LicenseEntry(path=r.path, kind=kind, rights="third-party", license=None, source=src,
                            note="third-party or supplied by the user; the licence at the source applies, if known (not checked by ReproCrowd)")
    if kind in THIRD_PARTY:
        return LicenseEntry(path=r.path, kind=kind, rights="third-party", license=None, source_license=paper,
                            source=f"doi:{doi}" if doi else None, note="the article's material (or derived from its text)")
    if kind in ("script", "code"):
        return LicenseEntry(path=r.path, kind=kind, rights="ours", license=code_license())
    if kind in ("output", "report"):
        note = QUOTES + "; original figures shown in it keep the article's licence" if r.path == "report.html" else ""
        return LicenseEntry(path=r.path, kind=kind, rights="ours", license=OUTPUTS, note=note)
    note = QUOTES if kind in ("curation", "export") and r.path not in OWN_LISTS else ""
    return LicenseEntry(path=r.path, kind=kind, rights="ours", license=METADATA, note=note)


def license_map(pkg: Package) -> LicenseMap:
    """The mapping for datapackage.json and every resource of the package."""
    m = pkg.load()
    doi = m.reprocrowd.identifiers.get("doi")
    paper = paper_license(pkg)
    files = [LicenseEntry(path=MANIFEST, kind="manifest", rights="ours", license=METADATA)]
    files += [_entry(r, paper, doi) for r in m.resources if r.path != FILE]
    files.append(LicenseEntry(path=FILE, kind="export", rights="ours", license=METADATA))
    files.sort(key=lambda f: f.path)
    return LicenseMap(policy=POLICY, rules=rules(), paper_license=paper, files=files)


def summary() -> dict[str, Any]:
    return {"policy": POLICY, "metadata": METADATA, "outputs": OUTPUTS, "code": code_license(),
            "third_party": "original licences retained; see licenses.json"}
