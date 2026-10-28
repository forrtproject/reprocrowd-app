# MetaReader CLI

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
cp config.example.toml ~/.config/metareader/config.toml   # optional: LLM profiles, OpenAlex key, …
uv run mr serve                                            # http://127.0.0.1:8770/docs
```

In another terminal:

```sh
mr package create ~/papers/phacking --id 10.1371/journal.pbio.1002106 --fetch
mr fulltext add phacking ~/Downloads/paper.pdf
mr fulltext candidates phacking                  # URLs found through OpenAlex / Europe PMC
mr fulltext fetch phacking c-6226b070            # download one (integrity-checked)
mr extract parse phacking ft-europepmc-jats      # → ex-europepmc-jats (common document schema)
mr extract show phacking ex-europepmc-jats --outline
mr extract reconcile phacking ft-europepmc-jats ft-paper-pdf --profile claude
mr input add phacking ~/Downloads/replication.zip
mr target generate phacking ex-europepmc-jats --section sec6
mr target add phacking -f type=number -f description="Mean RT" -f expected=512.4 --ref ex-europepmc-jats:sec3.p1.s2
mr target edit phacking T004 --set 'description="…"'      # LLM version kept; `mr target restore` undoes
mr action generate phacking ex-europepmc-jats --section sec5
mr script create phacking --template r
mr validate phacking
```

`mr --help` lists all commands. `MR_URL` and `MR_TOKEN` point the client at another service.

## Development

```sh
uv run pytest                 # tests (a fake LLM CLI stands in for real profiles; OpenAlex is mocked)
uv run mr-schemas export      # regenerate schemas/, docs/schemas.md, docs/openapi.json after model/API changes
```
