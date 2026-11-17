Extract the quantitative and categorical RESULTS reported in the following excerpts of a scholarly paper. They are reproduction targets for a computational reproducibility check.

Every sentence, figure and table has an id in square brackets. Each result must cite the id(s) it comes from in "refs".

Guidelines:
- One item per distinct reported number: estimates, confidence-interval bounds (as separate items), test statistics, p-values, percentages, sample sizes, means, SDs. Skip numbers that are not results (years, citations, section numbers, parameter settings).
- "categorical": checkable qualitative findings. "expected" is REQUIRED: the finding as a short string (e.g. "significant", "not significant", "positive").
- "table": each table you are asked about becomes exactly ONE item. "expected" is the rows of cell strings exactly as printed, header row included. Cite the table id.
- "figure": only for figures whose content can be re-plotted. "expected" is null. Cite the figure id.
- "number": "expected" is a STRING exactly as printed, preserving trailing zeros and scientific notation ("76" with unit "%" for 76%). Use "comparator" for bounds ("p < .001" → "<" with ".001"; "approximately" → "~"). Leave "decimals" null for strings: the server derives precision from the exact printed token.
$instructions
Output ONLY one JSON object, with no commentary and no code fences, that validates against this JSON Schema:

$schema

EXCERPTS:

$document
