# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                                 # the workspace: the library plus lsp/
./scripts/check.sh                      # the gate: ruff format --check, ruff check, mypy strict, pytest
uv run pytest -m 'not integration'      # ~1000 tests, seconds, no docker
uv run pytest tests/queries/test_ctes.py::test_cte_explicit_select_list -q    # one test
uv run pytest -k 'star_expansion'       # by name
```

Integration tests need the three backends and skip (never fail) when they are unreachable:

```bash
docker compose -f docker/docker-compose.yml up -d --wait
docker compose -f docker/docker-compose.yml down -v
```

Other entry points:

```bash
uv run uvicorn demo.app:app --port 8000                 # server demo, needs docker up
uv build --wheel && uv run python -m scripts.build_pages    # browser demo into site/
uv run --with pip python -m scripts.build_vsix          # the extension's wheel bundle + VSIX
uv run python -m pysqlsuggestions_lsp                   # the language server, on stdio
cd editors/vscode && npm run check                      # tsc + node --test unit tests
cd editors/vscode && npm run test:integration           # downloads a VS Code, not run in CI
```

`scripts/build_pages.py` and `scripts/build_vsix.py` must be run as modules (`-m scripts.x`), not by
path: they import each other through the repository root on `sys.path`.

## Architecture

Five stages. The seam is `Request`: everything above it is pure text analysis, everything below is
catalog access.

```
lex → analyse → request   |   resolve → rank
  engine/, pure           |   resolve.py does all I/O
```

- `engine/lex.py` — tolerant, dialect-*syntax*-driven, never raises, never classifies keywords.
  Tokens carry paren `depth` and `terminated`, both load-bearing downstream.
- `engine/analyse.py` — pure functions over tokens: `statement_at`, `qualifier_and_prefix`,
  `clause_at`, `scope_of`. Scope comes from the whole statement, not from text left of the caret.
- `engine/request.py` — kind narrowing. Resolution order is **alias first, then namespace**.
- `resolve.py` — the only I/O in the library; also where every capability's *absence* is handled.
- `engine/rank.py` — scoring, casing, quoting; totally ordered so tests can assert on output.
- `engine/local.py` — candidates the query text already contains, merged in `api.py` before ranking.

`docs/request-pipeline.md` explains the non-obvious parts of the first three.

### Invariants the tests enforce

`tests/test_purity.py` fails the build on each of these, so they are not style preferences:

- **Zero runtime dependencies.** `import pysqlsuggestions` must pull in no driver.
- **`engine/` may not import `ports` or `resolve`.** Purity flows one direction only.
- **Versions agree.** The version appears in four places — `pyproject.toml`,
  `src/pysqlsuggestions/__init__.py`, `lsp/pyproject.toml`, `editors/vscode/package.json` — and
  three of them are checked against the first.

### Dialects are data

A dialect is a frozen `Dialect` record composed with `dataclasses.replace`, never a subclass —
ClickHouse and Trino share different subsets with ANSI, which no MRO expresses. Two traps:

- `Dialect.__post_init__` folds the clause model's vocabulary into `keywords`. A word the model can
  suggest but `keywords` omits reads as an identifier to the analyser, so never bypass this.
- `ClauseModel.extend` **replaces** a same-named clause rather than appending. `.without()` drops
  clauses a backend does not have.

Introspection SQL lives in each dialect as `CatalogQueries`, with neutral `$1` markers that
`catalogs/dbapi.py` rewrites for the driver's paramstyle. Third-party dialects register through the
`pysqlsuggestions.dialects` entry-point group and can self-check against
`pysqlsuggestions.testing.DialectConformance`, which is shipped in the wheel for exactly that.

### Ports and capabilities

`Catalog` stays at four methods, all **prefix-independent** so an adapter may cache per database.
Anything richer — and anything prefix-dependent, which cannot cache — is a separate `Supports*`
protocol detected with `isinstance` at runtime. Adding one means defining what happens when it is
absent, in `resolve.py`, not in each adapter.

### Rules that are decisions, not omissions

Do not "fix" these without reading the reasoning first (`docs/gaps.md`, `docs/superpowers/specs/`):

- **Never read table data.** Value suggestions come from planner statistics and self-enumerating
  types only. A completion engine may not start a scan.
- **Never infer a foreign key** from column names. A wrong join condition is valid SQL that
  silently returns wrong rows, so a backend with no declared constraints gets no join proposals.
- **Missing capability → fewer suggestions, never an error.** The same holds in `lsp/`: a
  completion request never fails.

## Layout

| Path | What it is |
| --- | --- |
| `src/pysqlsuggestions/` | the library — no dependencies, ever |
| `lsp/` | a *separate distribution* (uv workspace member); needs pygls and a driver, which is why it is not a subpackage |
| `editors/vscode/` | VS Code extension over that server; bundles pure-Python wheels only, so one VSIX serves every platform |
| `demo/`, `docker/`, `scripts/` | the demos, their fixtures, and the build scripts |
| `docs/superpowers/specs/`, `plans/` | design docs, one pair per feature, dated |

## Tests

| Path | What lives there |
| --- | --- |
| `tests/*.py` | unit tests, one module per engine concern |
| `tests/corpus/cases.py` | golden `Request`s from pgcli and a production suite; caret marked inline with `⌶`, `pending=True` means strict xfail. The burn-down prints on every run |
| `tests/queries/` | a production autocomplete suite this library replaced, with its harness in `harness.py` |
| `tests/lsp/`, `tests/integration/` | the server, and everything needing docker (`@pytest.mark.integration`) |

## Conventions

Ruff with `D` enabled and mypy `strict` over `src`, `tests` and `lsp`: every function needs a
docstring and full annotations. Single quotes, 120 columns.

The prose is the point here. Docstrings and comments record *why* a shape was chosen and which
alternative was rejected — see `types.py` or `dialects/base.py` for the register. Match it; a
change that adds behaviour without saying what it refused is out of keeping with the file it lands in.

Commits are `feat:`/`fix:`/`test:`/`docs:`/`refactor:`/`merge:`/`chore:` with a lowercase prose
summary and a body explaining the decision. `CHANGELOG.md` is grouped by what changes at a caret,
not by commit.
