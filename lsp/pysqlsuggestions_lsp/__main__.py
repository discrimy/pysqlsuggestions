"""`python -m pysqlsuggestions_lsp` — the server on stdio."""

from __future__ import annotations

from pysqlsuggestions_lsp.server import create_server


def main() -> None:
    """Serve on stdin and stdout until the client goes away."""
    create_server().start_io()


if __name__ == '__main__':
    main()
