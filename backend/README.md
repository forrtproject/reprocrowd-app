# ReproCrowd CLI

A REST service and command-line client for building computational-reproducibility packages for scholarly
papers. It follows the workflow of the MetaReader Zotero plugin without needing Zotero.

- Design and status: [docs/design.md](docs/design.md)
- Schemas of every file in a package: [docs/schemas.md](docs/schemas.md) (generated), JSON Schemas in
  [schemas/](schemas/)
- HTTP API: interactive docs at `/docs` (Swagger UI) and `/redoc` while the service runs;
  [docs/openapi.json](docs/openapi.json) (generated)

## Quick start

```sh
uv sync
cp config.example.toml ~/.config/reprocrowd/config.toml   # optional: LLM profiles, OpenAlex key, …
uv run rc serve                                            # http://127.0.0.1:8770/docs
```

In another terminal:

```sh
rc package create ~/papers/phacking --id 10.1371/journal.pbio.1002106 --fetch
rc fulltext add phacking ~/Downloads/paper.pdf
rc fulltext candidates phacking                  # URLs found through OpenAlex / Europe PMC
rc fulltext fetch phacking c-6226b070            # download one (integrity-checked)
rc fulltext convert phacking ft-paper-pdf --converter grobid   # PDF → TEI (or docling → Docling JSON)
rc extract parse phacking ft-europepmc-jats      # → ex-europepmc-jats (common document schema)
rc extract show phacking ex-europepmc-jats --outline
rc extract reconcile phacking ft-europepmc-jats ft-paper-pdf --profile claude
rc input add phacking ~/Downloads/replication.zip
rc target generate phacking ex-europepmc-jats --section sec6
rc target add phacking -f type=number -f description="Mean RT" -f expected=512.4 --ref ex-europepmc-jats:sec3.p1.s2
rc target edit phacking T004 --set 'description="…"'      # LLM version kept; `rc target restore` undoes
rc action generate phacking ex-europepmc-jats --section sec5
rc script create phacking --template r
rc codegen phacking                              # coding agent implements the TODO(LLM): markers, live progress
rc script status phacking                        # markers left
rc run phacking                                  # run the master script (live log), check outputs/results.json
rc compare phacking                              # reproduced vs. expected, per target
rc coordinates phacking                          # what each full text locates on the PDF
rc figures phacking                              # the original figures: as published, cropped, or their page
rc report phacking --open                        # report.html with the provenance of every target
rc archive phacking --download phacking.zip      # the package as a ZIP (must validate)
rc submit phacking                               # upload to the package database; confirm in the browser
REPROCROWD_ZENODO_TOKEN=… rc zenodo deposit phacking --sandbox --creator "Doe, Jane"   # or: a draft on Zenodo
rc validate phacking
```

`rc --help` lists all commands. `REPROCROWD_URL` and `REPROCROWD_TOKEN` point the client at another service; with a service on
another machine, `rc fulltext add … --upload` and `rc input add … --upload` send the file's content.

## Development

```sh
uv run pytest                 # tests (a fake LLM CLI stands in for real profiles; OpenAlex is mocked)
uv run reprocrowd-schemas export      # regenerate schemas/, docs/schemas.md, docs/openapi.json after model/API changes
```
