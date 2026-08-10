#!/usr/bin/env bash
set -euo pipefail
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest -q
