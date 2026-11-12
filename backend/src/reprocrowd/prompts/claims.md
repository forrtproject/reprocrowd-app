Extract the CLAIMS of the following scholarly paper, with the evidence and the methods behind each claim, for a computational reproducibility check.

A paper makes a few broad claims, mostly in its abstract, and justifies each with quantitative evidence. Break each claim down as: broad claim, then sub-claims (unless the claim is small enough to keep whole), then evidence (reported values) and method (the analysis that produced them).

Every sentence, figure and table has an id in square brackets. Cite the id(s) each item comes from in "refs".

Claims:
- Broad claims come mostly from the abstract. Give each as a verbatim quote (mode "quote") or, when you shorten or combine, a paraphrase (mode "paraphrase") with the verbatim text in "original_quote".
- Decide for each claim whether to break it down. One test and one comparison stays whole (no sub_claims; the claim carries its own "method" and "evidence"). Several findings or methods become "sub_claims". A claim with sub-claims has no method or evidence of its own.
- Sub-claims are verbatim quotes from the paper (usually from the Results), each with its own "refs", "method" and "evidence".
- Assign each result passage to ONE claim. A claim that rests on results already traced under another claim lists that claim's position in "traced_under" instead of repeating the evidence.
- Recommendations and interpretations are claims with "testable": false, no method, no evidence and no sub_claims. List the claims they rest on in "depends_on". "depends_on" and "traced_under" are 1-based positions in your "claims" list.

Evidence (the values in "evidence"):
- One item per distinct reported number: estimates, confidence-interval bounds (as separate items), test statistics, p-values, percentages, sample sizes, means, SDs. Skip numbers that are not results (years, citations, section numbers, parameter settings).
- "categorical": checkable qualitative findings. "expected" is REQUIRED: the finding as a short string (e.g. "significant", "not significant", "positive").
- "table": each table becomes exactly ONE item. "expected" is the rows of cell strings exactly as printed, header row included. Cite the table id.
- "figure": only for figures whose content can be re-plotted. "expected" is null. Cite the figure id.
- "number": "expected" is the number as printed (76 with unit "%" for 76%). Use "comparator" for bounds ("p < .001" becomes "<" with 0.001; "approximately" becomes "~"). Set "decimals" to the decimals as printed.
- "quote" is the verbatim text that states the result.

Methods ("methods"):
- Name each statistical analysis once, with a key M1, M2, and so on. Claims and sub-claims name their method by key in "method"; sub-claims that rest on the same model or test share one key.
- "summary" is an imperative description specific enough to code (model, variables, subgroup, adjustments). "quote" and "refs" point to the Methods-section passage it comes from.

Preparation ("preparation"):
- Steps from the Methods section that every analysis shares: cohort selection, exclusions, cleaning, recoding (for example "patients with incomplete records were excluded"). Key them P1, P2, and so on, in order. Do not invent data files; describe the step as the paper does.

Skipped ("skipped"):
- List the abstract sentences you did NOT turn into a claim, with "ref" the sentence id and "reason": "background", "study_design" or "other". These help a reviewer spot a missed claim.
$instructions
Output ONLY one JSON object, with no commentary and no code fences, that validates against this JSON Schema:

$schema

EXCERPTS:

$document
