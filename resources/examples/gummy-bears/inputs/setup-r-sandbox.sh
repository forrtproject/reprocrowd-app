#!/usr/bin/env bash
# Bootstrap R in the Cowork analysis sandbox (no root, no apt).
# Verified working 2026-08-17 (session B): R 4.5.3, lme4 2.0.6, pROC 1.19.0.1,
# TripleR 1.5.5. Runtime ~8 minutes. Neither /tmp nor $HOME persists across
# sandbox sessions, so this must be re-run in every new session that needs R.
# Call it in the FOREGROUND with a long timeout (timeout_ms 600000); a
# backgrounded/nohup'd run dies when the calling shell exits and leaves a
# stale root-squashed tree that blocks /tmp (session B, 2026-08-17).
#
# The environment lives in $HOME/mamba (more space than /tmp; a stale /tmp
# tree cannot block it). TripleR's compiled dependencies (ggplot2, plyr,
# reshape2) come from conda-forge as binaries because source compilation
# fails in this sandbox; TripleR itself is pure R and installs from CRAN.
#
# After it finishes, run R scripts with:
#   $HOME/mamba/envs/renv/bin/Rscript <script.R>

set -euo pipefail
export MAMBA_ROOT_PREFIX="${MAMBA_ROOT_PREFIX:-$HOME/mamba}"
cd "$HOME"

MM=/tmp/bin/micromamba
if [ ! -x "$MM" ] || ! "$MM" --version >/dev/null 2>&1; then
  mkdir -p "$HOME/bin"
  curl -sS -L --max-time 120 -o "$HOME/mm.tar.bz2" \
    "https://micro.mamba.pm/api/micromamba/linux-64/latest"
  tar -xjf "$HOME/mm.tar.bz2" -C "$HOME/bin" --strip-components=1 bin/micromamba
  MM="$HOME/bin/micromamba"
fi

"$MM" create -y -n renv -c conda-forge \
  r-base r-lme4 r-proc r-ggplot2 r-plyr r-reshape2

"$HOME/mamba/envs/renv/bin/Rscript" -e \
  'install.packages("TripleR", repos="https://cloud.r-project.org", quiet=TRUE)'

"$HOME/mamba/envs/renv/bin/Rscript" -e \
  'library(lme4); library(pROC); library(TripleR); cat(R.version.string, "ready\n")'
