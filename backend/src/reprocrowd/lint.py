"""Lightweight, confidence-gated static code checks for generated reproduction scripts.

Checks only the package's own scripts and code (never original author code in ``inputs/``),
plus cross-platform package hygiene (case/Unicode filename clashes, runtime startup files).
Every rule emits advisory warnings rather than blocking errors; false positives are avoided by
parsing Python with ``ast`` and stripping comments/masking strings accurately in R.
"""
from __future__ import annotations

import ast
import codecs
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .package import Package
from .scripts import TODO_RE

Severity = Literal["warning", "info"]

# R base + recommended packages shipped with R (never need listing in install.R).
_R_SHIPPED = frozenset({
    "base", "compiler", "datasets", "graphics", "grDevices", "grid", "methods", "parallel",
    "splines", "stats", "stats4", "tcltk", "tools", "utils", "boot", "class", "cluster",
    "codetools", "foreign", "KernSmooth", "lattice", "MASS", "Matrix", "mgcv", "nlme",
    "nnet", "rpart", "spatial", "survival",
})

# Common Python import-name -> PyPI distribution-name aliases (normalized lowercase with hyphens).
_PY_IMPORT_TO_DIST: dict[str, str] = {
    "sklearn": "scikit-learn",
    "cv2": "opencv-python",
    "pil": "pillow",
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "dateutil": "python-dateutil",
    "skimage": "scikit-image",
    "docx": "python-docx",
    "fitz": "pymupdf",
    "Bio": "biopython",
    "bio": "biopython",
    "umap": "umap-learn",
    "pingouin": "pingouin",
    "pyreadr": "pyreadr",
    "pyreadstat": "pyreadstat",
}

_STARTUP_FILENAMES = frozenset({
    ".Rprofile", ".Renviron", "Rprofile.site", "Renviron.site",
    "sitecustomize.py", "usercustomize.py", "profile.do",
})

_TEXT_DATA_SUFFIXES = frozenset({".csv", ".tsv", ".txt"})
_UTF8_CHUNK = 64 * 1024

# --- R regexes (applied to comment-stripped, string-masked R code unless noted) ---
_R_LIB_CALL = re.compile(r"""\b(?:library|require)\s*\(\s*["']?([A-Za-z][A-Za-z0-9.]*)["']?\s*(?:,|\))""")
_R_PACMAN = re.compile(r"""\bp_load\s*\(([^)]*)\)""")
_R_NAMESPACED = re.compile(r"""(?<![\w.])([A-Za-z][A-Za-z0-9.]*)::[A-Za-z.]""")
_R_INSTALL = re.compile(
    r"""\b(?:install\.packages|BiocManager::install|pak::pkg_install|pak::pak|"""
    r"""remotes::install_\w+|devtools::install_\w+|renv::(?:restore|install|hydrate|activate)|"""
    r"""p_install|p_load_gh)\s*\("""
)
_R_IO_CALL = re.compile(
    r"""\b(?:read\.csv2?|read\.table|read\.delim2?|read_csv2?|read_tsv|read_delim|read_table|"""
    r"""read_excel|read_xlsx|read_xls|read_dta|read_sav|read_sas|read_spss|read_rds|readRDS|"""
    r"""fread|load|source|download\.file|url|file|readLines|scan|st_read|"""
    r"""write\.csv2?|write\.table|write_csv|saveRDS|write_dta|ggsave|png|pdf|svg|jpeg|tiff|setwd)\s*\("""
)
_R_URL_IO_CALL = re.compile(
    r"""\b(?:read\.csv2?|read\.table|read\.delim2?|read_csv2?|read_tsv|read_delim|read_table|"""
    r"""read_excel|read_dta|read_sav|read_rds|readRDS|fread|load|source|download\.file|url|"""
    r"""readLines|scan|GET|POST|request|curl_download|curl_fetch_\w+)\s*\("""
)
_R_STOCHASTIC = re.compile(
    r"""\b(?:sample|sample\.int|rnorm|runif|rbinom|rpois|rexp|rgamma|rbeta|rchisq|rt|rf|"""
    r"""rmultinom|mvrnorm|rmvnorm|boot|mice|amelia|kmeans|randomForest|ranger)\s*\("""
)
_R_SEED = re.compile(r"""\b(?:set\.seed|clusterSetRNGStream)\s*\(|\bfuture\.seed\s*=|\bseed\s*=""")
_R_FORK_PARALLEL = re.compile(r"""\b(?:mclapply|mcmapply|mcMap|pvec|mcparallel|makeForkCluster)\s*\(""")
_R_INTERACTIVE = re.compile(
    r"""\b(?:View|file\.choose|choose\.files|choose\.dir|readline|menu|askYesNo|select\.list|winDialog)\s*\(|"""
    r"""\brstudioapi::"""
)
_R_SAVE_CACHE = re.compile(r"""\b(?:save\.image|savehistory)\s*\(""")
_R_ENCODING_OPT = re.compile(r"""\b(?:fileEncoding|encoding)\s*=|\blocale\s*\([^)]*encoding\s*=""")
_R_INSTALL_R_PKG = re.compile(r"""["']([A-Za-z][A-Za-z0-9.]*)["']""")

_URL_RE = re.compile(r"""^(?:https?|ftp)://""", re.IGNORECASE)
_ABS_OR_HOME_PATH = re.compile(r"""^(?:/(?:Users|home|tmp|var|etc|opt|mnt|Volumes|root|srv)/|~/|[A-Za-z]:[/\\]|\\\\[^\\])""")
_PY_IO_FUNCS = frozenset({
    "open", "read_csv", "read_table", "read_excel", "read_stata", "read_spss", "read_sas",
    "read_parquet", "read_feather", "read_json", "read_pickle", "loadtxt", "genfromtxt",
    "load", "chdir", "savefig", "to_csv", "to_parquet", "to_excel", "Path", "read_text",
    "read_bytes", "write_text", "write_bytes",
})
_PY_NET_FUNCS = frozenset({
    "read_csv", "read_table", "read_excel", "read_stata", "read_parquet", "read_json",
    "urlopen", "urlretrieve", "get", "post", "request", "download",
})
_PY_STOCHASTIC_RANDOM = frozenset({
    "random", "uniform", "randint", "randrange", "choice", "choices", "sample", "shuffle",
    "gauss", "normalvariate", "betavariate", "expovariate", "gammavariate",
})
_PY_STOCHASTIC_NUMPY = frozenset({
    "rand", "randn", "randint", "random", "random_sample", "choice", "permutation",
    "shuffle", "normal", "uniform", "binomial", "poisson", "exponential", "beta", "gamma",
    "multivariate_normal", "multinomial",
})
_PY_INTERACTIVE_FUNCS = frozenset({"input", "getpass", "breakpoint", "set_trace"})
_PY_PKG_INSTALL_RE = re.compile(
    r"""\b(?:pip3?|conda|mamba)\s+install\b|\buv\s+(?:pip\s+install|add)\b|\bensurepip\b""",
    re.IGNORECASE,
)


class CodeFinding(BaseModel):
    """One confidence-gated static finding on the package's generated scripts or archive hygiene."""

    rule: str = Field(description="stable rule identifier (e.g. undeclared_dependency, external_path)")
    severity: Severity = "warning"
    file: str | None = Field(default=None, description="package-relative file path, if applicable")
    line: int | None = Field(default=None, ge=1, description="1-based line number, if applicable")
    message: str
    suggestion: str | None = None


class CodeCheckReport(BaseModel):
    """Result of static checks on the package's scripts and code."""

    checked_files: list[str] = Field(default_factory=list, description="package-relative script/code files checked")
    detected_libraries: dict[str, list[str]] = Field(
        default_factory=lambda: {"r": [], "python": []},
        description="non-base R and non-stdlib Python packages referenced in scripts",
    )
    declared_libraries: dict[str, list[str]] = Field(
        default_factory=lambda: {"r": [], "python": []},
        description="packages declared in install.R / requirements.txt",
    )
    findings: list[CodeFinding] = Field(default_factory=list)


def _norm_py_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower().strip()


def _strip_r_comment(line: str) -> str:
    """Remove trailing R ``#`` comment while preserving ``#`` inside quoted strings."""
    out: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < len(line):
                out.append(line[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
        else:
            if ch in ("'", '"'):
                quote = ch
                out.append(ch)
            elif ch == "#":
                break
            else:
                out.append(ch)
        i += 1
    return "".join(out)


def _mask_r_strings(code: str) -> str:
    """Replace contents of quoted strings with spaces so call regexes never match inside literals."""
    out: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(code):
        ch = code[i]
        if quote:
            if ch == "\\" and i + 1 < len(code):
                out.extend((" ", " "))
                i += 2
                continue
            if ch == quote:
                out.append(ch)
                quote = None
            else:
                out.append(" ")
        else:
            if ch in ("'", '"'):
                quote = ch
            out.append(ch)
        i += 1
    return "".join(out)


def _extract_quoted_strings(text: str) -> list[str]:
    """Return unescaped contents of single/double-quoted literals in R code."""
    results: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in ("'", '"'):
            quote = ch
            i += 1
            buf: list[str] = []
            while i < n:
                c = text[i]
                if c == "\\" and i + 1 < n:
                    buf.append(text[i + 1])
                    i += 2
                    continue
                if c == quote:
                    i += 1
                    break
                buf.append(c)
                i += 1
            results.append("".join(buf))
        else:
            i += 1
    return results


def _r_call_unit(code_lines: list[str], masked_lines: list[str], start_idx: int, col: int) -> tuple[str, str]:
    """Collect a balanced-paren R call starting at ``(start_idx, col)`` across physical lines.

    Returns ``(unmasked_call, masked_call)`` so callers can inspect syntax on masked text and extract
    string literals from the unmasked text.
    """
    raw_parts: list[str] = []
    mask_parts: list[str] = []
    depth = 0
    started = False
    for idx in range(start_idx, len(code_lines)):
        raw = code_lines[idx][col:] if idx == start_idx else code_lines[idx]
        msk = masked_lines[idx][col:] if idx == start_idx else masked_lines[idx]
        cut = len(msk)
        for j, ch in enumerate(msk):
            if ch == "(":
                depth += 1
                started = True
            elif ch == ")":
                depth -= 1
                if started and depth <= 0:
                    cut = j + 1
                    break
        raw_parts.append(raw[:cut])
        mask_parts.append(msk[:cut])
        if started and depth <= 0:
            break
    return "\n".join(raw_parts), "\n".join(mask_parts)


def _is_external_path(s: str, script_dir: Path, pkg_root: Path) -> bool:
    """High-confidence check that a literal path points outside the package root."""
    val = s.strip()
    if not val or _URL_RE.match(val):
        return False
    if _ABS_OR_HOME_PATH.match(val):
        return True
    if val == ".." or val.startswith(("../", "..\\")) or "/../" in val or "\\..\\" in val:
        norm = val.replace("\\", "/")
        root_resolved = pkg_root.resolve()
        from_root = (root_resolved / norm).resolve()
        from_script = (script_dir.resolve() / norm).resolve()
        if not from_root.is_relative_to(root_resolved) and not from_script.is_relative_to(root_resolved):
            return True
    return False


def _declared_python_packages(root: Path) -> tuple[bool, set[str]]:
    req = root / "requirements.txt"
    if not req.is_file():
        return False, set()
    declared: set[str] = set()
    for raw in req.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        if m:
            declared.add(_norm_py_name(m.group(1)))
    return True, declared


def _declared_r_packages(root: Path) -> tuple[bool, set[str]]:
    inst = root / "install.R"
    if not inst.is_file():
        return False, set()
    declared: set[str] = set()
    for raw in inst.read_text(encoding="utf-8", errors="replace").splitlines():
        code = _strip_r_comment(raw)
        if not code.strip():
            continue
        for m in _R_INSTALL_R_PKG.finditer(code):
            pkg_name = m.group(1)
            if pkg_name.lower() in {"cran", "true", "false", "source", "binary", "both"}:
                continue
            declared.add(pkg_name)
    return True, declared


def _local_py_modules(root: Path, script_paths: list[str]) -> set[str]:
    names = {Path(p).stem for p in script_paths if p.endswith(".py")}
    for p in root.rglob("__init__.py"):
        if ".git" not in p.parts:
            names.add(p.parent.name)
    return names


def _is_valid_utf8(path: Path) -> bool:
    """Stream-validate a file as UTF-8 in 64 KiB chunks so large CSVs never exhaust memory."""
    dec = codecs.getincrementaldecoder("utf-8")()
    with path.open("rb") as fh:
        while chunk := fh.read(_UTF8_CHUNK):
            dec.decode(chunk, final=False)
        dec.decode(b"", final=True)
    return True


def _non_utf8_text_inputs(pkg: Package) -> list[str]:
    """Return used .csv/.tsv/.txt input paths whose bytes are not valid UTF-8."""
    m = pkg.load()
    bad: list[str] = []
    for r in m.by_kind("input"):
        if not r.reprocrowd.used or r.reprocrowd.role == "code":
            continue
        p = pkg.path(r.path)
        if p.suffix.lower() not in _TEXT_DATA_SUFFIXES or not p.is_file():
            continue
        try:
            _is_valid_utf8(p)
        except UnicodeDecodeError:
            bad.append(r.path)
        except OSError:
            continue
    return sorted(bad)


def _attr_chain(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        left = _attr_chain(node.value)
        return f"{left}.{node.attr}" if left else node.attr
    return ""


def _has_explicit_non_none_seed(node: ast.Call, kw_names: tuple[str, ...]) -> bool:
    """True when ``seed(...)`` passes an explicit non-``None`` seed argument."""
    if node.args:
        arg0 = node.args[0]
        return not (isinstance(arg0, ast.Constant) and arg0.value is None)
    for kw in node.keywords:
        if kw.arg in kw_names:
            return not (isinstance(kw.value, ast.Constant) and kw.value.value is None)
    return False


def _check_python_file(rel: str, text: str, pkg_root: Path, non_utf8_inputs: list[str],
                       py_imports: set[str], findings: list[CodeFinding]) -> None:
    try:
        tree = ast.parse(text, filename=rel)
    except SyntaxError:
        return

    script_dir = (pkg_root / rel).parent
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                py_imports.add(top)
                aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                top = node.module.split(".")[0]
                py_imports.add(top)
                for alias in node.names:
                    aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    def resolve_call(node: ast.Call) -> str:
        chain = _attr_chain(node.func)
        if not chain:
            return ""
        parts = chain.split(".")
        if parts[0] in aliases:
            return ".".join([aliases[parts[0]], *parts[1:]])
        return chain

    first_random_seed_line: int | None = None
    first_np_seed_line: int | None = None
    stochastic_hits: list[tuple[int, str, str]] = []  # (line, kind, expr)
    read_calls: list[tuple[int, str, bool]] = []      # (line, literal, has_encoding_kw)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fq = resolve_call(node)
        leaf = fq.rsplit(".", 1)[-1]
        lineno = getattr(node, "lineno", 1)

        if fq == "random.seed" and _has_explicit_non_none_seed(node, ("a",)):
            first_random_seed_line = lineno if first_random_seed_line is None else min(first_random_seed_line, lineno)
        if fq in {"numpy.random.seed", "np.random.seed"} and _has_explicit_non_none_seed(node, ("seed",)):
            first_np_seed_line = lineno if first_np_seed_line is None else min(first_np_seed_line, lineno)
        call_has_encoding = any(kw.arg == "encoding" for kw in node.keywords)

        # Interactive calls
        if leaf in _PY_INTERACTIVE_FUNCS and (
            fq in {"input", "builtins.input", "getpass.getpass", "getpass", "breakpoint", "builtins.breakpoint", "pdb.set_trace"}
        ):
            findings.append(CodeFinding(
                rule="interactive_call",
                file=rel,
                line=lineno,
                message=f"interactive call `{fq}()` blocks non-interactive runs",
                suggestion="Remove the interactive call or guard it behind `if sys.stdin.isatty():`.",
            ))
        elif fq == "webbrowser.open":
            findings.append(CodeFinding(
                rule="interactive_call",
                file=rel,
                line=lineno,
                message="interactive call `webbrowser.open()` requires a desktop browser",
                suggestion="Remove `webbrowser.open()` from the analysis script.",
            ))

        # Runtime package installs
        if fq in {"subprocess.run", "subprocess.call", "subprocess.check_call", "subprocess.check_output", "subprocess.Popen", "os.system"}:
            cmd_text = _extract_subprocess_text(node)
            if cmd_text and _PY_PKG_INSTALL_RE.search(cmd_text):
                findings.append(CodeFinding(
                    rule="runtime_install",
                    file=rel,
                    line=lineno,
                    message=f"runtime package installation inside script (`{cmd_text[:80]}`)",
                    suggestion="Remove runtime package installation and declare dependencies in `requirements.txt` instead.",
                ))
        elif fq in {"pip.main", "pip._internal.main", "ensurepip.bootstrap"}:
            findings.append(CodeFinding(
                rule="runtime_install",
                file=rel,
                line=lineno,
                message=f"runtime package installation via `{fq}()`",
                suggestion="Remove runtime package installation and declare dependencies in `requirements.txt` instead.",
            ))

        # Unseeded stochasticity
        if fq.startswith("random.") and leaf in _PY_STOCHASTIC_RANDOM:
            stochastic_hits.append((lineno, "random", fq))
        elif fq.startswith("numpy.random.") and leaf in _PY_STOCHASTIC_NUMPY:
            stochastic_hits.append((lineno, "numpy", fq))
        elif fq == "numpy.random.default_rng" and not _has_explicit_non_none_seed(node, ("seed",)):
            findings.append(CodeFinding(
                rule="unseeded_stochastic",
                file=rel,
                line=lineno,
                message="`numpy.random.default_rng()` called without an explicit seed",
                suggestion="Pass an explicit integer seed, e.g. `np.random.default_rng(12345)`.",
            ))
        elif fq == "scipy.stats.bootstrap" and not any(kw.arg in {"random_state", "rng"} for kw in node.keywords):
            findings.append(CodeFinding(
                rule="unseeded_stochastic",
                file=rel,
                line=lineno,
                message="`scipy.stats.bootstrap()` called without `random_state` / `rng`",
                suggestion="Pass `random_state=np.random.default_rng(12345)` for reproducible resampling.",
            ))

        # String literal arguments for path, network & per-call encoding checks
        str_args = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        str_args += [kw.value.value for kw in node.keywords
                     if kw.arg in {"file", "path", "filepath_or_buffer", "url", "io", "fname"}
                     and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)]
        for s in str_args:
            if leaf in _PY_NET_FUNCS and _URL_RE.match(s.strip()):
                findings.append(CodeFinding(
                    rule="network_read",
                    file=rel,
                    line=lineno,
                    message=f"script fetches a remote URL at runtime (`{s[:80]}`)",
                    suggestion="Download the file into `inputs/` and read it via a package-relative path.",
                ))
            elif leaf in _PY_IO_FUNCS:
                read_calls.append((lineno, s, call_has_encoding))
                if _is_external_path(s, script_dir, pkg_root):
                    findings.append(CodeFinding(
                        rule="external_path",
                        file=rel,
                        line=lineno,
                        message=f"path points outside the package folder (`{s}`)",
                        suggestion="Use a path relative to the package root (e.g. `inputs/...` or `outputs/...`).",
                    ))

    # Require the seed to precede the first stochastic draw for each RNG family
    for kind, seed_line, fix in (
        ("random", first_random_seed_line, "Call `random.seed(<int>)` before the first random draw."),
        ("numpy", first_np_seed_line, "Call `np.random.seed(<int>)` before the first draw or use `np.random.default_rng(<int>)`."),
    ):
        hits = [h for h in stochastic_hits if h[1] == kind]
        if hits:
            draw_line, _, expr = min(hits)
            if seed_line is None or seed_line > draw_line:
                detail = "with no preceding" if seed_line is not None else "with no"
                findings.append(CodeFinding(
                    rule="unseeded_stochastic",
                    file=rel,
                    line=draw_line,
                    message=f"stochastic call `{expr}()` {detail} `{kind}` seed in this script",
                    suggestion=fix,
                ))

    # Non-UTF-8 text inputs read without explicit encoding= on the matching read call
    for bad in non_utf8_inputs:
        bname = Path(bad).name
        for ln, s, has_enc in read_calls:
            if (bad in s or bname in s) and not has_enc:
                findings.append(CodeFinding(
                    rule="non_utf8_input_encoding",
                    file=rel,
                    line=ln,
                    message=f"reads non-UTF-8 text input `{bad}` without specifying `encoding=`",
                    suggestion="Specify the file's encoding explicitly, e.g. `encoding='latin-1'` or `encoding='cp1252'`.",
                ))
                break


def _extract_subprocess_text(node: ast.Call) -> str:
    if not node.args:
        return ""
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    if isinstance(first, (ast.List, ast.Tuple)):
        parts = [elt.value for elt in first.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)]
        return " ".join(parts)
    return ""


def _check_r_file(rel: str, text: str, pkg_root: Path, non_utf8_inputs: list[str],
                  r_libs: set[str], findings: list[CodeFinding]) -> None:
    is_install_r = Path(rel).name == "install.R"
    script_dir = (pkg_root / rel).parent
    raw_lines = text.splitlines()
    code_lines = [_strip_r_comment(ln) for ln in raw_lines]
    masked_lines = [_mask_r_strings(ln) for ln in code_lines]

    first_seed_line: int | None = None
    first_lecuyer_line: int | None = None
    first_stochastic: tuple[int, str] | None = None
    first_fork: tuple[int, str] | None = None
    io_calls: list[tuple[int, list[str], bool]] = []  # (line, literals, has_encoding_opt)
    seen_io_findings: set[tuple[str, int, str]] = set()

    for idx, (code, masked) in enumerate(zip(code_lines, masked_lines), start=1):
        if not code.strip():
            continue

        # Library extraction (skip install.R itself)
        if not is_install_r:
            for m in _R_LIB_CALL.finditer(code):
                r_libs.add(m.group(1))
            for m in _R_NAMESPACED.finditer(masked):
                r_libs.add(m.group(1))
            for m in _R_PACMAN.finditer(code):
                for item in m.group(1).split(","):
                    item = item.strip().strip("'\"")
                    if item and "=" not in item and re.match(r"^[A-Za-z][A-Za-z0-9.]*$", item):
                        r_libs.add(item)

        # Runtime installs (skip install.R)
        if not is_install_r and _R_INSTALL.search(masked):
            findings.append(CodeFinding(
                rule="runtime_install",
                file=rel,
                line=idx,
                message="runtime package installation inside analysis script",
                suggestion="Move package installations to `install.R` so the analysis script can run offline.",
            ))

        # Runtime cache writes (save.image / savehistory)
        if _R_SAVE_CACHE.search(masked):
            findings.append(CodeFinding(
                rule="runtime_startup_file",
                file=rel,
                line=idx,
                message="script calls `save.image()` or `savehistory()`, leaving workspace cache files behind",
                suggestion="Remove the call, or save specific objects to an explicit path under `outputs/`.",
            ))

        # Interactive calls (ignore if guarded by `interactive()` on the same line)
        if _R_INTERACTIVE.search(masked) and "interactive()" not in masked:
            findings.append(CodeFinding(
                rule="interactive_call",
                file=rel,
                line=idx,
                message="interactive R call blocks non-interactive `Rscript` execution",
                suggestion="Remove the interactive call or guard it with `if (interactive()) { ... }`.",
            ))

        # Seed & L'Ecuyer-CMRG ordering
        if first_seed_line is None and _R_SEED.search(masked):
            first_seed_line = idx
        if first_lecuyer_line is None and ("L'Ecuyer-CMRG" in code or 'L\\"Ecuyer-CMRG' in code):
            first_lecuyer_line = idx

        # Stochasticity & forked parallel RNG (matched on masked code so strings don't false-fire)
        if first_stochastic is None and not is_install_r:
            ms = _R_STOCHASTIC.search(masked)
            if ms:
                # An inline `seed = ...` on the stochastic call itself (e.g. mice(..., seed = 1)) counts as seeded
                _, m_unit = _r_call_unit(code_lines, masked_lines, idx - 1, ms.start())
                if not _R_SEED.search(m_unit):
                    first_stochastic = (idx, ms.group(0).rstrip("(").strip())
        if first_fork is None and not is_install_r:
            mf = _R_FORK_PARALLEL.search(masked)
            if mf:
                first_fork = (idx, mf.group(0).rstrip("(").strip())

        # Multiline R I/O & URL checks
        for pat, is_url in ((_R_URL_IO_CALL, True), (_R_IO_CALL, False)):
            for m in pat.finditer(masked):
                raw_unit, mask_unit = _r_call_unit(code_lines, masked_lines, idx - 1, m.start())
                lits = _extract_quoted_strings(raw_unit)
                if not is_url:
                    io_calls.append((idx, lits, bool(_R_ENCODING_OPT.search(mask_unit))))
                for s in lits:
                    if is_url and _URL_RE.match(s.strip()):
                        key = ("network_read", idx, s)
                        if key not in seen_io_findings:
                            seen_io_findings.add(key)
                            findings.append(CodeFinding(
                                rule="network_read",
                                file=rel,
                                line=idx,
                                message=f"script fetches a remote URL at runtime (`{s[:80]}`)",
                                suggestion="Download the file into `inputs/` and read it via a package-relative path.",
                            ))
                    elif not is_url and _is_external_path(s, script_dir, pkg_root):
                        key = ("external_path", idx, s)
                        if key not in seen_io_findings:
                            seen_io_findings.add(key)
                            findings.append(CodeFinding(
                                rule="external_path",
                                file=rel,
                                line=idx,
                                message=f"path points outside the package folder (`{s}`)",
                                suggestion="Use a path relative to the package root (e.g. `inputs/...` or `outputs/...`).",
                            ))

    if first_stochastic and (first_seed_line is None or first_seed_line > first_stochastic[0]):
        detail = "with no preceding" if first_seed_line is not None else "with no"
        findings.append(CodeFinding(
            rule="unseeded_stochastic",
            file=rel,
            line=first_stochastic[0],
            message=f"stochastic call `{first_stochastic[1]}()` {detail} `set.seed()` in this script",
            suggestion="Set an explicit seed (`set.seed(<int>)`) before the first random draw.",
        ))

    if first_fork and (first_lecuyer_line is None or first_lecuyer_line > first_fork[0]):
        detail = "without a preceding" if first_lecuyer_line is not None else "without"
        findings.append(CodeFinding(
            rule="parallel_rng_hazard",
            file=rel,
            line=first_fork[0],
            message=f"forked parallel call `{first_fork[1]}()` {detail} `RNGkind(\"L'Ecuyer-CMRG\")`",
            suggestion="Call `RNGkind(\"L'Ecuyer-CMRG\")` before `set.seed(<int>)` and before forked workers start.",
        ))

    for bad in non_utf8_inputs:
        bname = Path(bad).name
        for ln, lits, has_enc in io_calls:
            if any(bad in s or bname in s for s in lits) and not has_enc:
                findings.append(CodeFinding(
                    rule="non_utf8_input_encoding",
                    file=rel,
                    line=ln,
                    message=f"reads non-UTF-8 text input `{bad}` without specifying `fileEncoding=` / `encoding=`",
                    suggestion="Pass `fileEncoding = \"latin1\"` (base R) or `locale = locale(encoding = \"latin1\")` (readr).",
                ))
                break


def _check_package_hygiene(pkg: Package, findings: list[CodeFinding]) -> None:
    root = pkg.root.resolve()
    m = pkg.load()

    skip_dirs = {".git", "jobs", "llm-calls", "archive"}
    for current, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in skip_dirs)
        for name in sorted(names):
            if name in _STARTUP_FILENAMES:
                rel = (Path(current) / name).relative_to(root).as_posix()
                findings.append(CodeFinding(
                    rule="runtime_startup_file",
                    file=rel,
                    message=f"runtime startup file `{rel}` can silently alter R/Python behavior across machines",
                    suggestion="Remove startup files from the package and put required configuration directly in the analysis script.",
                ))

    seen: dict[str, str] = {}
    for r in sorted(m.resources, key=lambda x: x.path):
        norm = unicodedata.normalize("NFC", r.path).casefold()
        if norm in seen and seen[norm] != r.path:
            findings.append(CodeFinding(
                rule="case_collision",
                file=r.path,
                message=f"path `{r.path}` collides with `{seen[norm]}` on case-insensitive filesystems (macOS/Windows)",
                suggestion="Rename one of the files so names are distinct regardless of letter case or Unicode form.",
            ))
        else:
            seen[norm] = r.path


def check_package(pkg: Package) -> CodeCheckReport:
    """Run all confidence-gated static code and portability checks on a package."""
    m = pkg.load()
    root = pkg.root.resolve()
    findings: list[CodeFinding] = []
    checked: list[str] = []
    py_imports: set[str] = set()
    r_libs: set[str] = set()
    non_utf8 = _non_utf8_text_inputs(pkg)

    script_resources = sorted(
        (r for r in m.resources if r.reprocrowd.kind in ("script", "code")),
        key=lambda r: r.path,
    )
    script_paths = [r.path for r in script_resources]
    has_pending_todos = False

    for r in script_resources:
        p = pkg.path(r.path)
        if not p.is_file():
            continue
        suffix = p.suffix.lower()
        if suffix not in {".py", ".r"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        checked.append(r.path)
        if r.reprocrowd.kind == "script" and TODO_RE.search(text):
            has_pending_todos = True
        if suffix == ".py":
            _check_python_file(r.path, text, root, non_utf8, py_imports, findings)
        elif suffix == ".r":
            _check_r_file(r.path, text, root, non_utf8, r_libs, findings)

    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    local_py = _local_py_modules(root, script_paths)
    detected_py = sorted(n for n in py_imports if n and n not in stdlib and n not in local_py and n != "__future__")
    detected_r = sorted(n for n in r_libs if n and n not in _R_SHIPPED)

    has_req, declared_py = _declared_python_packages(root)
    has_install_r, declared_r = _declared_r_packages(root)

    if detected_py and (has_req or not has_pending_todos):
        missing_py = []
        for imp in detected_py:
            norm = _norm_py_name(imp)
            mapped = _norm_py_name(_PY_IMPORT_TO_DIST.get(imp, _PY_IMPORT_TO_DIST.get(imp.lower(), imp)))
            if norm not in declared_py and mapped not in declared_py:
                missing_py.append(imp)
        if missing_py:
            note = " (`requirements.txt` not found)" if not has_req else ""
            findings.append(CodeFinding(
                rule="undeclared_dependency",
                file="requirements.txt" if has_req else None,
                message=f"Python package(s) imported in scripts but not listed in `requirements.txt`: {', '.join(missing_py)}{note}",
                suggestion="Add the missing package(s) to `requirements.txt` so others can install the same environment.",
            ))

    if detected_r and (has_install_r or not has_pending_todos):
        missing_r = [p for p in detected_r if p not in declared_r]
        if missing_r:
            note = " (`install.R` not found)" if not has_install_r else ""
            findings.append(CodeFinding(
                rule="undeclared_dependency",
                file="install.R" if has_install_r else None,
                message=f"R package(s) used in scripts but not listed in `install.R`: {', '.join(missing_r)}{note}",
                suggestion="Add `install.packages(...)` entries for the missing package(s) in `install.R`.",
            ))

    _check_package_hygiene(pkg, findings)

    return CodeCheckReport(
        checked_files=checked,
        detected_libraries={"r": detected_r, "python": detected_py},
        declared_libraries={"r": sorted(declared_r), "python": sorted(declared_py)},
        findings=findings,
    )
