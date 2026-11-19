You are working in a computational reproduction package: the folder that holds this file, which is also your working directory.

Goal: implement the analysis so that running `Rscript analysis.R` from the package root reproduces the paper's results.

Read first:
- analysis.R: master R script with one section per analysis step. Each section has the step summary and the text of the paper it implements.
- analysis_plan.json: the same steps as structured data (ids, inputs, dependencies, target ids each step produces).
- results_schema.json: the reproduction targets, in "results" (ids, descriptions, expected values). Their "refs" point at the passages of the paper that report them: an extraction (extractions/<name>.json, the paper's text) and a sentence, figure or table id in it.
- schemas/results-output.schema.json: the required format of outputs/results.json.
- datapackage.json: every file of the package. Input files are the resources with "reprocrowd": {"kind": "input"}; their "role" (data / code / other) and "note" describe them. Never modify files in inputs/, and ignore inputs with "used": false.
- Data files:
  - inputs/gl2003-replication-raw-2023-03-28.csv
  - inputs/gl2003-replication-processed-2023-03-28.csv
  - inputs/gl2003-replication-processed-2024-05-23.csv
  - inputs/readme.txt
- Other material:
  - inputs/glrep_hypothesis.png
  - inputs/questionnaire_german.pdf
  - inputs/AEMreplication_preprint.pdf
  - inputs/GLeffectsizes.jpg

Do not open inputs whose role is code (the original authors' code). Rebuild the analysis from the article and data.
Author code was excluded from this prompt, not isolated from the package folder.
The saved AGENT_PROMPT.md is the default with execution allowed; jobs/<id>/stdin.txt records the prompt actually sent.

Rules:
1. Write JSON with jsonlite::write_json(x, path, auto_unbox = TRUE, digits = NA) to keep maximum precision (about 15 significant digits); never round results. Implement each step section of analysis.R in place, in order.
2. Replace every `TODO(LLM):` marker with working code and delete the marker. Keep the step header comments (ids, summaries, source text) intact and in order.
3. Use paths relative to the package root.
4. Compute every result from the data. NEVER copy expected values from results_schema.json into the output; the point is to test whether they reproduce.
5. Write values at full precision; never round reproduced values. outputs/results.json: {"results": [{"id": "<target id>", "value": <unrounded number or string>, "step": "<step id>"}, ...]}. Use "table" (array of rows, same layout as expected, header row included) for table targets, and "file" for figure targets (save figures as outputs/figures/<target id>.png). If a result cannot be computed, emit it with "value": null and a "note" explaining why.
6. List required packages in install.R (install.packages(...) calls).
7. If something in the paper is ambiguous, pick the most plausible reading and document it in a comment starting with "# ASSUMPTION:".
8. When finished, run the pipeline once if you can and fix errors.
