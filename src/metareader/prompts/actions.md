Turn the following excerpts of a scholarly paper's methods into an ordered list of ACTIONS (analysis steps) for a computational reproduction.

Every sentence, figure and table has an id in square brackets. Each action must cite the id(s) it implements in "refs".

Available input files (cite them by name in "inputs"):
$inputs

Reproduction targets (cite the ids an action computes in "produces"):
$targets

Guidelines:
- Give actions keys A1, A2, … in order. "depends_on" lists keys of EARLIER actions only.
- "kind": "transform" for data preparation, "analysis" for estimation, tests and figures.
- "summary": an imperative description of exactly what to compute, specific enough to code.
- "quote": the verbatim text the action implements.
- Do not add actions for loading the input files or writing the final results file; those are added automatically.
- Keep the paper's order where possible, split compound procedures into separate actions, and don't invent procedures the text doesn't describe.
$instructions
Output ONLY one JSON object, with no commentary and no code fences, that validates against this JSON Schema:

$schema

EXCERPTS:

$document
