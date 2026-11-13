You are working in a computational reproduction package at:
C:\Users\lroesele.IVV5NET\ReproCrowd\260707-aemreplication-rev1

Goal: implement the analysis so that running `Rscript analysis.R` from the package root reproduces the paper's results.

Read first:
- analysis.R: master R script with one section per analysis step. Each section has the step summary and the verbatim quote from the paper it implements.
- analysis_plan.json: the same steps as structured data (ids, inputs, dependencies, target ids each step produces).
- results_schema.json: the reproduction targets, in "results" (ids, descriptions, quotes, expected values).
- schemas/results-output.schema.json: the required format of outputs/results.json.
- datapackage.json: every file of the package. Input files are the resources with "reprocrowd": {"kind": "input"}; their "role" (data / code / other) and "note" describe them. Never modify files in inputs/, and ignore inputs with "used": false.
- Data files:
  - inputs/gl2003-replication-raw-2023-03-28.csv
  - inputs/gl2003-replication-processed-2023-03-28.csv
  - inputs/gl2003-replication-processed-2024-05-23.csv
  - inputs/readme.txt
- The original authors' code (prefer reusing their logic; cite the file you took it from in a comment):
  - inputs/analyses_prereg.R
  - inputs/analyses_final.R
  - inputs/esconversion_red.R
  - inputs/analyses_final_rev1.R
  - inputs/g&l_effectsizes.R
- Other material:
  - inputs/glrep_hypothesis.png
  - inputs/questionnaire_german.pdf
  - inputs/Reproducibility report 4320 - Meta-psych.pdf
  - inputs/230221_StudentPoster.pptx
  - inputs/AEMreplication_preprint.pdf
  - inputs/GLeffectsizes.jpg
  - inputs/AEM Studie.docx
  - inputs/240523_AEMreplication.docx
  - inputs/240524_AEMreplication.docx
  - inputs/240702_AEMreplication.docx

Rules:
1. Implement each step section of analysis.R in place, in order.
2. Replace every `TODO(LLM):` marker with working code and delete the marker. Keep the step header comments (ids, summaries, quotes) intact and in order.
3. Use paths relative to the package root.
4. Compute every result from the data. NEVER copy expected values from results_schema.json into the output; the point is to test whether they reproduce.
5. outputs/results.json: {"results": [{"id": "<target id>", "value": <unrounded number or string>, "step": "<step id>"}, ...]}. Use "table" (array of rows, same layout as expected, header row included) for table targets, and "file" for figure targets (save figures as outputs/figures/<target id>.png). If a result cannot be computed, emit it with "value": null and a "note" explaining why.
6. List required packages in install.R (install.packages(...) calls).
7. If something in the paper is ambiguous, pick the most plausible reading and document it in a comment starting with "# ASSUMPTION:".
8. When finished, run the pipeline once if you can and fix errors.
