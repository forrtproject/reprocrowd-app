You are reconciling several machine-extracted versions of the same scholarly article into one clean, faithful version.

Source files (paths relative to the current directory; read them with the Read tool):
$files

Rules:
- If a PDF is listed, treat it as ground truth for wording. The other files are extraction attempts that may contain errors: broken hyphenation, merged or split paragraphs, missing sections, OCR noise, misplaced figure captions, mangled tables.
- Keep the article's actual wording. Do not summarize, paraphrase or invent content.
- Keep the section order and hierarchy (level 1 = top-level section, 2 = subsection, …), paragraph boundaries, every figure with its caption, and every table as rows of cell strings (header rows included).
- Split every paragraph and caption into sentences, one string per sentence.
- Put each figure and table into the section where it is first shown or mentioned.
- Leave out the reference list, page headers and footers, and journal boilerplate.
$instructions
Output ONLY one JSON object, with no commentary and no code fences, that validates against this JSON Schema:

$schema
