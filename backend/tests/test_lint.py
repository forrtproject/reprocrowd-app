"""Positive and negative fixtures for confidence-gated static code checks (reprocrowd.lint)."""
from __future__ import annotations

from pathlib import Path

from test_run import package_with_targets


def _setup_py(client, pkg, code: str, *, extra_files: dict[str, bytes | str] | None = None, template: str = "python") -> Path:
    root = package_with_targets(client, pkg)
    P = f"/v1/packages/{pkg}"
    if template != "python":
        assert client.put(P + "/script", json={"template": template, "on_modified": "overwrite"}).status_code == 200
    script = "analysis.py" if template == "python" else "analysis.R"
    (root / script).write_text(code, encoding="utf-8")
    for rel, content in (extra_files or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8")
    assert client.post(f"{P}:sync").status_code == 200
    return root


def _rules(client, pkg) -> list[dict]:
    r = client.get(f"/v1/packages/{pkg}/code-checks")
    assert r.status_code == 200, r.text
    return r.json()["findings"]


# ---------- 1. undeclared_dependency ----------

def test_undeclared_dependency_python_positive_and_negative(client, pkg):
    # Positive: imports pandas and sklearn with only pandas in requirements.txt
    _setup_py(
        client, pkg,
        "import json\nimport pandas as pd\nfrom sklearn.linear_model import LinearRegression\nimport helper_mod\n",
        extra_files={"requirements.txt": "pandas>=2.0\n", "helper_mod.py": "X = 1\n"},
    )
    cc = client.get(f"/v1/packages/{pkg}/code-checks").json()
    assert cc["detected_libraries"]["python"] == ["pandas", "sklearn"]
    assert cc["declared_libraries"]["python"] == ["pandas"]
    undeclared = [f for f in cc["findings"] if f["rule"] == "undeclared_dependency"]
    assert len(undeclared) == 1 and "sklearn" in undeclared[0]["message"] and "pandas" not in undeclared[0]["message"]

    # Negative: declaring scikit-learn (import alias for sklearn) clears it
    root = Path(client.get(f"/v1/packages/{pkg}").json()["path"])
    (root / "requirements.txt").write_text("pandas>=2.0\nscikit-learn==1.5.0\n", encoding="utf-8")
    assert not [f for f in _rules(client, pkg) if f["rule"] == "undeclared_dependency"]


def test_undeclared_dependency_r_positive_and_negative(client, pkg):
    _setup_py(
        client, pkg,
        "library(stats)\nlibrary(dplyr)\njsonlite::write_json(list(), 'outputs/results.json')\n# library(fake_in_comment)\n",
        extra_files={"install.R": 'install.packages(c("jsonlite"))\n'},
        template="r",
    )
    cc = client.get(f"/v1/packages/{pkg}/code-checks").json()
    assert cc["detected_libraries"]["r"] == ["dplyr", "jsonlite"]  # stats is base R; comment ignored
    undeclared = [f for f in cc["findings"] if f["rule"] == "undeclared_dependency"]
    assert len(undeclared) == 1 and "dplyr" in undeclared[0]["message"] and "jsonlite" not in undeclared[0]["message"]

    # Negative: adding dplyr to install.R clears it
    root = Path(client.get(f"/v1/packages/{pkg}").json()["path"])
    (root / "install.R").write_text('install.packages(c("jsonlite", "dplyr"))\n', encoding="utf-8")
    assert not [f for f in _rules(client, pkg) if f["rule"] == "undeclared_dependency"]


def test_untouched_r_template_does_not_warn_before_codegen(client, pkg):
    package_with_targets(client, pkg)
    assert client.put(f"/v1/packages/{pkg}/script", json={"template": "r", "on_modified": "overwrite"}).status_code == 200
    assert not _rules(client, pkg)


# ---------- 2. runtime_install ----------

def test_runtime_install_positive_and_negative(client, pkg):
    _setup_py(
        client, pkg,
        "import subprocess\nsubprocess.run(['pip', 'install', 'scipy'])\n",
    )
    assert any(f["rule"] == "runtime_install" for f in _rules(client, pkg))

    # Negative: subprocess running non-install command or comment mentioning pip install
    root = Path(client.get(f"/v1/packages/{pkg}").json()["path"])
    (root / "analysis.py").write_text("# pip install scipy\nimport subprocess\nsubprocess.run(['echo', 'ok'])\n")
    assert not [f for f in _rules(client, pkg) if f["rule"] == "runtime_install"]


def test_runtime_install_r_ignores_install_r(client, pkg):
    _setup_py(
        client, pkg,
        'install.packages("dplyr")\nlibrary(dplyr)\n',
        extra_files={"install.R": 'install.packages("dplyr")\n'},
        template="r",
    )
    hits = [f for f in _rules(client, pkg) if f["rule"] == "runtime_install"]
    assert len(hits) == 1 and hits[0]["file"] == "analysis.R"

    # Negative: removing install.packages from analysis.R (keeping it in install.R) clears it
    root = Path(client.get(f"/v1/packages/{pkg}").json()["path"])
    (root / "analysis.R").write_text("library(dplyr)\n")
    assert not [f for f in _rules(client, pkg) if f["rule"] == "runtime_install"]


# ---------- 3. network_read ----------

def test_network_read_positive_and_negative(client, pkg):
    _setup_py(
        client, pkg,
        'import urllib.request\nurllib.request.urlopen("https://example.org/data.csv")\n',
    )
    assert any(f["rule"] == "network_read" for f in _rules(client, pkg))

    # Negative: URL in a comment or plain metadata string (not an I/O call)
    root = Path(client.get(f"/v1/packages/{pkg}").json()["path"])
    (root / "analysis.py").write_text('# source: https://example.org/data.csv\nSOURCE_URL = "https://example.org/paper"\n')
    assert not [f for f in _rules(client, pkg) if f["rule"] == "network_read"]


# ---------- 4. external_path ----------

def test_external_path_positive_and_negative(client, pkg):
    _setup_py(
        client, pkg,
        'open("/Users/alice/secret.csv")\nopen("../../outside.csv")\n',
    )
    hits = [f for f in _rules(client, pkg) if f["rule"] == "external_path"]
    assert len(hits) == 2

    # Negative: package-relative paths, or non-path strings like regexes
    root = Path(client.get(f"/v1/packages/{pkg}").json()["path"])
    (root / "analysis.py").write_text('import re\nopen("inputs/data.csv")\nre.sub(r"/Users/foo", "", "x")\n')
    assert not [f for f in _rules(client, pkg) if f["rule"] == "external_path"]


def test_external_path_r_ignores_regex_and_comments(client, pkg):
    _setup_py(
        client, pkg,
        'setwd("/home/alice/proj")\nx <- read.csv("~/data.csv")\n',
        template="r",
    )
    hits = [f for f in _rules(client, pkg) if f["rule"] == "external_path"]
    assert len(hits) == 2

    root = Path(client.get(f"/v1/packages/{pkg}").json()["path"])
    (root / "analysis.R").write_text('# setwd("/home/alice/proj")\nx <- read.csv("inputs/data.csv")\ny <- gsub("/Users/", "", "a")\n')
    assert not [f for f in _rules(client, pkg) if f["rule"] == "external_path"]


# ---------- 5. unseeded_stochastic & 6. parallel_rng_hazard ----------

def test_unseeded_stochastic_python_positive_and_negative(client, pkg):
    _setup_py(client, pkg, "import random\nx = random.sample([1, 2, 3], 2)\n")
    assert any(f["rule"] == "unseeded_stochastic" for f in _rules(client, pkg))

    root = Path(client.get(f"/v1/packages/{pkg}").json()["path"])
    (root / "analysis.py").write_text("import random\nrandom.seed(42)\nx = random.sample([1, 2, 3], 2)\n")
    assert not [f for f in _rules(client, pkg) if f["rule"] == "unseeded_stochastic"]


def test_r_stochastic_and_parallel_rng_positive_and_negative(client, pkg):
    _setup_py(
        client, pkg,
        "library(parallel)\nx <- rnorm(10)\nres <- mclapply(1:4, function(i) mean(rnorm(5)))\n",
        template="r",
    )
    rules = {f["rule"] for f in _rules(client, pkg)}
    assert {"unseeded_stochastic", "parallel_rng_hazard"} <= rules

    root = Path(client.get(f"/v1/packages/{pkg}").json()["path"])
    (root / "analysis.R").write_text('library(parallel)\nRNGkind("L\'Ecuyer-CMRG")\nset.seed(123)\nres <- mclapply(1:4, function(i) mean(rnorm(5)))\n')
    rules = {f["rule"] for f in _rules(client, pkg)}
    assert not ({"unseeded_stochastic", "parallel_rng_hazard"} & rules)


# ---------- 7. non_utf8_input_encoding ----------

def test_non_utf8_input_encoding_positive_and_negative(client, pkg, tmp_path):
    latin_csv = tmp_path / "latin.csv"
    latin_csv.write_bytes("name,val\ncaf\xe9,1\n".encode("latin-1"))
    root = package_with_targets(client, pkg)
    P = f"/v1/packages/{pkg}"
    assert client.post(P + "/inputs", json={"path": str(latin_csv)}).status_code == 201
    (root / "analysis.py").write_text('open("inputs/latin.csv").read()\n')
    assert any(f["rule"] == "non_utf8_input_encoding" for f in _rules(client, pkg))

    # Negative: explicit encoding= clears the warning
    (root / "analysis.py").write_text('open("inputs/latin.csv", encoding="latin-1").read()\n')
    assert not [f for f in _rules(client, pkg) if f["rule"] == "non_utf8_input_encoding"]


# ---------- 8. interactive_call ----------

def test_interactive_call_positive_and_negative(client, pkg):
    _setup_py(client, pkg, 'x = input("continue?")\n')
    assert any(f["rule"] == "interactive_call" for f in _rules(client, pkg))

    root = Path(client.get(f"/v1/packages/{pkg}").json()["path"])
    (root / "analysis.py").write_text("# input('not called')\nprint('ok')\n")
    assert not [f for f in _rules(client, pkg) if f["rule"] == "interactive_call"]


def test_interactive_call_r_guarded_negative(client, pkg):
    _setup_py(client, pkg, "View(mtcars)\n", template="r")
    assert any(f["rule"] == "interactive_call" for f in _rules(client, pkg))

    root = Path(client.get(f"/v1/packages/{pkg}").json()["path"])
    (root / "analysis.R").write_text("if (interactive()) View(mtcars)\n")
    assert not [f for f in _rules(client, pkg) if f["rule"] == "interactive_call"]


# ---------- 9. runtime_startup_file & 10. case_collision ----------

def test_startup_file_and_save_image(client, pkg):
    root = _setup_py(client, pkg, "x <- 1\nsave.image()\n", extra_files={".Rprofile": "options(scipen = 999)\n"}, template="r")
    hits = [f for f in _rules(client, pkg) if f["rule"] == "runtime_startup_file"]
    assert len(hits) == 2

    (root / ".Rprofile").unlink()
    (root / "analysis.R").write_text("x <- 1\n")
    assert not [f for f in _rules(client, pkg) if f["rule"] == "runtime_startup_file"]


def test_case_collision_and_author_code_ignored(client, pkg, tmp_path):
    # Author code in inputs/ must NEVER be linted
    author_script = tmp_path / "author.py"
    author_script.write_text('import random\nopen("/Users/author/x.csv")\ninput("hi")\n')
    root = _setup_py(client, pkg, "print('clean')\n")
    P = f"/v1/packages/{pkg}"
    assert client.post(P + "/inputs", json={"path": str(author_script), "role": "code"}).status_code == 201
    assert not _rules(client, pkg)

    # Validation surfaces code warnings under stage="code" without failing valid=True
    (root / "analysis.py").write_text('open("/Users/alice/bad.csv")\n')
    assert client.post(f"{P}:sync").status_code == 200
    val = client.get(P + "/validation").json()
    assert val["valid"]
    assert any(w["stage"] == "code" and "external_path" in w["message"] for w in val["warnings"])

    # Case-only collision in manifest (simulated directly on manifest so it works on macOS APFS too)
    from reprocrowd.package import Registry
    with Registry().get(pkg).edit() as m:
        sample = next(r for r in m.resources if r.path == "analysis.py")
        m.resources.append(sample.model_copy(update={"name": "code-dup", "path": "Analysis.py"}))
    assert any(f["rule"] == "case_collision" for f in _rules(client, pkg))
