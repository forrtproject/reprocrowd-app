"""Figures as published: the image file a JATS full text names, from Europe PMC's archive of the article's files, or
for PLOS's own JATS, whose <graphic> names the figure by its DOI, the image PLOS serves for that DOI.

JATS has no coordinates, but its <graphic> names the figure's file, so no PDF or crop is needed. A figure is connected to
its <fig> by caption and by label; they must not disagree. The archive is untrusted input: only the named image is read
from it, nothing is unpacked, and anything that goes wrong means "not available": the PDF can still provide the figure.
"""
from __future__ import annotations

import io
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from PIL import Image, UnidentifiedImageError

from . import formats, openalex
from .config import settings
from .coordinates import find_caption, normalize
from .errors import ApiError
from .formats.base import Graphic
from .package import Package
from .schemas.document import Figure

ORDER = (".tif", ".tiff", ".png", ".jpg", ".jpeg", ".webp")  # the same image in several formats: largest first
THUMBNAIL = ".gif"  # archives hold small GIFs next to the real images; only used if the full text names one
MAX_IMAGE_MB = 64
PNG_MODES = {"1", "L", "LA", "I", "I;16", "P", "RGB", "RGBA"}
# How PLOS's JATS names a figure (its DOI), and where PLOS serves it: the original (often TIFF) first, then a large PNG.
PLOS_FIGURE = re.compile(r"info:doi/(10\.1371/journal\.[a-z]+\.\d{7}\.g\d{3})")
PLOS_IMAGE = "https://journals.plos.org/plosone/article/figure/image"
PLOS_SIZES = ("original", "large")


class Unavailable(Exception):
    """A figure's published image can't be had, for the reason given."""


def _stem_and_extension(name: str) -> tuple[str, str]:
    p = Path(name.rsplit("/", 1)[-1].lower())
    return (p.stem, p.suffix) if p.suffix in (*ORDER, THUMBNAIL) else (p.name, "")


def _label_key(label: str) -> str:
    return normalize(label).replace("figure", "fig", 1)  # "Figure 1." and "Fig 1" are the same; "S1 Fig" is another


class Gallery:
    """The published images of a package's paper. The archive is downloaded when first needed, at most once."""

    def __init__(self, pkg: Package):
        m = pkg.load()
        self.graphics: dict[str, Graphic] = {}
        self.pmcid: str | None = None
        for ft in m.by_kind("fulltext"):
            fmt = formats.BY_NAME[ft.reprocrowd.flavor]
            if fmt.graphics:
                path = pkg.path(ft.path)
                self.graphics |= {g.file: g for g in fmt.graphics(path)}
                self.pmcid = self.pmcid or fmt.pmcid(path)
        self.pmcid = self.pmcid or m.reprocrowd.identifiers.get("pmcid")
        self._folder: tempfile.TemporaryDirectory | None = None
        self._archive: zipfile.ZipFile | None = None
        self._failed: str | None = None
        self._plos_images: dict[str, tuple[Any, str] | Unavailable] = {}  # by figure DOI: each is fetched at most once

    def __enter__(self) -> Gallery:
        return self

    def __exit__(self, *_) -> None:
        if self._archive:
            self._archive.close()
        if self._folder:
            self._folder.cleanup()

    def find(self, fig: Figure) -> Graphic | None:
        """The <fig> this figure is, if caption and label don't point at different ones."""
        graphics = list(self.graphics.values())
        i = find_caption(" ".join(s.text for s in fig.caption), [normalize(g.caption) for g in graphics])
        by_caption = None if i is None else graphics[i]
        labelled = [g for g in graphics if _label_key(g.label) == _label_key(fig.label)]
        by_label = labelled[0] if len(labelled) == 1 else None
        if by_caption and by_label and by_caption is not by_label:
            return None
        return by_caption or by_label

    @property
    def url(self) -> str:
        return f"{openalex.EUROPEPMC}/{self.pmcid}/supplementaryFiles"

    def _open(self) -> zipfile.ZipFile:
        if self._failed:
            raise Unavailable(self._failed)
        if self._archive:
            return self._archive
        try:
            if not self.pmcid:
                raise Unavailable("the JATS full text has no PMCID")
            self._folder = tempfile.TemporaryDirectory(prefix="reprocrowd-figures-")
            dest = Path(self._folder.name) / "article.zip"
            candidate = openalex.Candidate("published", f"Europe PMC files of {self.pmcid}", self.url, "zip", dest.name)
            try:
                openalex.download(candidate, dest, settings().max_download_mb * 2**20)
                self._archive = zipfile.ZipFile(dest)
            except ApiError as e:
                raise Unavailable(e.message)
            except zipfile.BadZipFile:
                raise Unavailable(f"Europe PMC has no archive of files for {self.pmcid}")
        except Unavailable as e:
            self._failed = str(e)
            raise
        return self._archive

    def _member(self, archive: zipfile.ZipFile, file: str) -> zipfile.ZipInfo:
        stem, extension = _stem_and_extension(file)
        found = {}
        for info in archive.infolist():
            member_stem, member_extension = _stem_and_extension(info.filename)
            if member_stem == stem and member_extension and not info.is_dir():
                found[member_extension] = info
        allowed = (*ORDER, THUMBNAIL) if extension == THUMBNAIL else ORDER
        best = next((found[e] for e in allowed if e in found), None)
        if best is None:
            raise Unavailable(f"{file} isn't in the archive of {self.pmcid}")
        if best.file_size > MAX_IMAGE_MB * 2**20:
            raise Unavailable(f"{best.filename} is larger than {MAX_IMAGE_MB} MB")
        return best

    def _plos(self, doi: str) -> tuple[Any, str]:
        """The figure PLOS serves for its DOI, and its URL. Fetched once: another target on the same figure, or a
        figure that couldn't be had, doesn't ask PLOS again."""
        if doi not in self._plos_images:
            try:
                self._plos_images[doi] = self._fetch_plos(doi)
            except Unavailable as e:
                self._plos_images[doi] = e
        found = self._plos_images[doi]
        if isinstance(found, Unavailable):
            raise Unavailable(str(found))
        return found

    def _fetch_plos(self, doi: str) -> tuple[Any, str]:
        if self._folder is None:
            self._folder = tempfile.TemporaryDirectory(prefix="reprocrowd-figures-")
        problems = []
        for size in PLOS_SIZES:
            url = f"{PLOS_IMAGE}?{urlencode({'size': size, 'id': doi})}"
            dest = Path(self._folder.name) / f"plos-{size}"
            try:
                openalex.download(openalex.Candidate("published", f"PLOS figure {doi}", url, "image", dest.name), dest, MAX_IMAGE_MB * 2**20)
                image = Image.open(io.BytesIO(dest.read_bytes()))  # not the file: it is deleted next, even on Windows
                image.load()
                return image, url
            except ApiError as e:
                problems.append(e.message)
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
                problems.append(f"PLOS's {size} image of {doi} can't be read: {e}")
            finally:
                dest.unlink(missing_ok=True)
        raise Unavailable("; ".join(problems))

    def image(self, fig: Figure) -> tuple[Any, str] | None:
        """(the image, where it came from); None if no <fig> corresponds; raises Unavailable if one does but can't be had."""
        graphic = self.find(fig)
        if graphic is None:
            return None
        if m := PLOS_FIGURE.fullmatch(graphic.file):
            image, url = self._plos(m[1])
            return (image if image.mode in PNG_MODES else image.convert("RGB")), url
        archive = self._open()
        member = self._member(archive, graphic.file)
        try:
            image = Image.open(io.BytesIO(archive.read(member)))
            image.load()
        except (UnidentifiedImageError, OSError, zipfile.BadZipFile, Image.DecompressionBombError) as e:
            raise Unavailable(f"{member.filename} can't be read: {e}")
        return (image if image.mode in PNG_MODES else image.convert("RGB")), f"{self.url}#{member.filename}"
