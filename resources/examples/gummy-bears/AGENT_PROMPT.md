You are working in a computational reproduction package at:
C:\Users\lroesele.IVV5NET\AppData\Local\Temp\opencode\reprocrowd-issue22\resources\examples\gummy-bears

Goal: implement the analysis so that running `python3 analysis.py` from the package root reproduces the paper's results.

Read first:
- analysis.py: master Python script with one section per analysis step. Each section has the step summary and the verbatim quote from the paper it implements.
- analysis_plan.json: the same steps as structured data (ids, inputs, dependencies, target ids each step produces).
- results_schema.json: the reproduction targets, in "results" (ids, descriptions, quotes, expected values).
- schemas/results-output.schema.json: the required format of outputs/results.json.
- datapackage.json: every file of the package. Input files are the resources with "reprocrowd": {"kind": "input"}; their "role" (data / code / other) and "note" describe them. Never modify files in inputs/, and ignore inputs with "used": false.
- Data files:
  - inputs/gummibear_trials.csv: 100 trials: 4 participants x 25 bears, raw responses
  - inputs/Gummibear_data.xlsx: Full Excel workbook: raw, floor, corrected components
  - inputs/gummibear_apriori_long.csv: A-priori expectations, long format
  - inputs/gummibear_apriori.csv: A-priori accuracy expectations
  - inputs/py_auc_by_cell.csv: Authors' AUC by cell (Python)
  - inputs/r_auc_by_cell.csv: Authors' AUC by cell (R)
  - inputs/r_controls_summary.csv: Authors' control analyses summary (R)
- The original authors' code (prefer reusing their logic; cite the file you took it from in a comment):
  - inputs/gummibear_analyses.py: Authors' main analyses (Python)
  - inputs/build_dataset.py: Authors' dataset builder (Python)
  - inputs/make_figures.py: Authors' figure generation (Python)
  - inputs/control_analyses.R: Authors' control analyses (R)
  - inputs/make_supplement.sh: Authors' supplement builder (shell)
  - inputs/setup-r-sandbox.sh: Authors' R sandbox setup (shell)
- Other material:
  - inputs/codebook.md: Codebook for the data files
  - inputs/info_gummibear_data.txt: Notes on the data files
  - inputs/results_2026-08-17.md: Authors' reference results write-up
  - inputs/r_sessioninfo.txt: Authors' R session info
  - inputs/README.md: Zenodo data/code package README

Rules:
1. Implement each step section of analysis.py in place, in order.
2. Replace every `TODO(LLM):` marker with working code and delete the marker. Keep the step header comments (ids, summaries, quotes) intact and in order.
3. Use paths relative to the package root.
4. Compute every result from the data. NEVER copy expected values from results_schema.json into the output; the point is to test whether they reproduce.
5. outputs/results.json: {"results": [{"id": "<target id>", "value": <unrounded number or string>, "step": "<step id>"}, ...]}. Use "table" (array of rows, same layout as expected, header row included) for table targets, and "file" for figure targets (save figures as outputs/figures/<target id>.png). If a result cannot be computed, emit it with "value": null and a "note" explaining why.
6. List required packages in requirements.txt.
7. If something in the paper is ambiguous, pick the most plausible reading and document it in a comment starting with "# ASSUMPTION:".
8. When finished, run the pipeline once if you can and fix errors.
