# pysqlsuggestions

Context-aware, schema-aware SQL completion for Python. A library, not a CLI and
not a language server — importable into a FastAPI service, a notebook kernel or
an internal reporting tool without dragging a process boundary along.

Zero runtime dependencies. PostgreSQL, ClickHouse and Trino, plus an `ansi`
fallback so an unknown backend degrades instead of failing.

## Status

The request pipeline is implemented: given SQL and a caret, the library decides
what should be suggested. Fetching and ranking those suggestions is next.

## Usage

```python
from pysqlsuggestions import derive_request
from pysqlsuggestions.dialects.postgres import POSTGRES

request = derive_request('SELECT id, na FROM users u', 13, POSTGRES)

request.prefix        # 'na'
request.clause        # 'SELECT'
request.replace_span  # (11, 13) — what the editor overwrites
request.kinds         # (Kind.COLUMN, Kind.FUNCTION, Kind.KEYWORD)
request.scope         # relations in view, built from the whole statement
```

The scope comes from the entire statement, not the text left of the caret — the
`FROM` clause that answers the question above sits to the right of it.

A qualifier collapses the answer:

```python
derive_request('SELECT * FROM users u WHERE u.', 30, POSTGRES).kinds
# (Kind.COLUMN,)  — no keywords, no functions, no tables
```

And one tuple per dialect gives three different answers to the same text:

```python
from pysqlsuggestions.dialects.trino import TRINO

sql = 'SELECT * FROM analytics.'
derive_request(sql, len(sql), POSTGRES).kinds  # (Kind.TABLE,)   analytics is a schema
derive_request(sql, len(sql), TRINO).kinds     # (Kind.SCHEMA,)  analytics is a catalog
```

## Design

See `docs/request-pipeline.md` for how the stages fit together, and
`docs/superpowers/specs/` for the full design.

## Development

```bash
uv sync
./scripts/check.sh   # ruff format, ruff check, mypy strict, pytest
```
