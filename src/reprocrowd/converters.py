"""Converters: PDF → a full-text format the parsers read. Each converter is configured in the service
(``[converters.<name>]``) and registered in CONVERTERS; it writes its output to a given file.

- grobid: POST to a GROBID service (processFulltextDocument) with sentence segmentation and coordinates → TEI
- docling: run the docling command → Docling JSON
"""
from __future__ import annotations

import os
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from .config import expand_argv, settings
from .errors import ApiError

# Elements GROBID should give coordinates for (used for highlighting and figure crops).
TEI_COORDINATES = ["s", "p", "head", "figure", "formula", "biblStruct", "title", "persName"]


@dataclass(frozen=True)
class Converter:
    name: str
    output: str  # file name suffix of the result, e.g. ".tei.xml"
    configured: Callable[[], bool]
    detail: Callable[[], str]
    run: Callable[[Path, Path], None]  # (pdf, output file)


def _grobid(pdf: Path, out: Path) -> None:
    cfg = settings()
    url = cfg.grobid_url.rstrip("/") + "/api/processFulltextDocument"
    data = {"segmentSentences": "1", "consolidateHeader": "0", "includeRawCitations": "1", "teiCoordinates": TEI_COORDINATES}
    try:
        with pdf.open("rb") as f:
            r = httpx.post(url, data=data, files={"input": ("input.pdf", f, "application/pdf")}, timeout=cfg.grobid_timeout_s)
    except httpx.HTTPError as e:
        raise ApiError(502, "conversion_failed", f"could not reach GROBID at {url}: {e}")
    if r.status_code != 200:
        raise ApiError(502, "conversion_failed", f"GROBID returned HTTP {r.status_code}", {"body": r.text[:1000]})
    out.write_bytes(r.content)


def _docling(pdf: Path, out: Path) -> None:
    cfg = settings()
    with tempfile.TemporaryDirectory(prefix="rc-docling-") as tmp:
        argv = expand_argv(cfg.docling_argv, {"input": str(pdf), "outdir": tmp})
        try:
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        except OSError as e:
            raise ApiError(502, "conversion_failed", f"could not start docling: {e}", {"argv": argv})
        try:
            stdout, stderr = proc.communicate(timeout=cfg.docling_timeout_s)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise ApiError(504, "conversion_timeout", f"docling exceeded {cfg.docling_timeout_s} s")
        if proc.returncode != 0:
            raise ApiError(502, "conversion_failed", f"docling exited with {proc.returncode}", {"stderr": stderr[-2000:]})
        produced = sorted(Path(tmp).glob("*.json"))
        if not produced:
            raise ApiError(502, "conversion_failed", "docling produced no JSON file", {"stdout": stdout[-1000:]})
        out.write_bytes(produced[0].read_bytes())


CONVERTERS: dict[str, Converter] = {
    "grobid": Converter("grobid", ".tei.xml", lambda: bool(settings().grobid_url), lambda: settings().grobid_url, _grobid),
    "docling": Converter("docling", ".docling.json", lambda: bool(settings().docling_argv), lambda: " ".join(settings().docling_argv), _docling),
}
