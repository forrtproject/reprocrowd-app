# Reproduction package: Förster et al.: Gummy bear perceptions (preprint)

| Path | Contents |
|---|---|
| `analysis.py` | Master script: Monolithic Python script |
| `analysis_plan.json` | Steps: load inputs, the paper's actions, collect results |
| `results_schema.json` | Reproduction targets with expected values |
| `targets.json`, `actions.json` | Curated targets and actions, with LLM provenance |
| `extractions/` | The paper's text (sections > paragraphs > sentences, figures, tables) |
| `inputs/` | Input files: data, the authors' original code, other material |
| `schemas/` | JSON Schemas of every JSON file in the package |
| `datapackage.json` | Manifest of all files, with sizes and SHA-256 hashes (Frictionless Data Package) |

Run: `python3 analysis.py`
