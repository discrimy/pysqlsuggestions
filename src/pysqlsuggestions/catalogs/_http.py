"""
The catalog readers' shared transport. Stdlib only, and deliberately thin.

`urllib` raises on a non-2xx status and hands the body back on the exception
rather than the response, which is exactly backwards for a caller whose best
error message is the database's own words. This normalises it: every HTTP answer
is a `Response`, and only a failure to get an answer at all raises.

The transport is a parameter wherever it is used, so both readers are testable
without a socket. That is the pattern `runtime.ts` already follows, for the same
reason — the behaviour that matters is what gets *sent*, and asserting on it
should not need a server.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

DEFAULT_TIMEOUT = 10.0
"""Seconds for one request. A completion that has not arrived by then is noise."""


class TransportError(Exception):
    """No HTTP answer was reached: DNS, TCP, TLS or a timeout."""


@dataclass(frozen=True, slots=True)
class Response:
    """One HTTP answer, whatever its status."""

    status: int
    body: bytes

    def json(self) -> Any:
        """The body parsed as JSON. Raises `ValueError` when it is not JSON."""
        return json.loads(self.body.decode('utf-8'))

    def text(self) -> str:
        """
        The body as text, whitespace collapsed.

        Collapsed because this becomes an error message in a tooltip, and both
        backends answer with multi-line text — ClickHouse's `DB::Exception`
        carries a stack, and a wrapped sentence reads as truncated.
        """
        return ' '.join(self.body.decode('utf-8', 'replace').split())


Transport = Callable[..., Response]
"""What both readers call, and what a test substitutes wholesale."""


def tls_context(url: str, *, verify: bool) -> ssl.SSLContext | None:
    """
    The TLS context for `url`, or None when it is not an HTTPS one.

    Verifying is the default and stays the default. `verify=False` is for the
    case it exists to serve — an internal ClickHouse or Trino behind a
    self-signed certificate, which is common enough that refusing to support it
    would mean users reaching for a worse workaround. It disables hostname
    checking too, because a self-signed certificate rarely names the host it is
    reached by and half-checking would fail on exactly those endpoints while
    reading as though something were still being verified.

    What it does not do is apply per-request or by default. It is a property of
    one configured connection, chosen deliberately, and the setting that carries
    it says what it costs.

    `check_hostname` is cleared before `verify_mode`: setting CERT_NONE while
    hostname checking is on raises ValueError, and the order is easy to get
    backwards.
    """
    if not url.startswith('https://'):
        return None
    context = ssl.create_default_context()
    if not verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def request(
    url: str,
    *,
    method: str = 'GET',
    data: bytes | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    verify: bool = True,
) -> Response:
    """One HTTP round trip. See `tls_context` for what `verify` governs."""
    built = urllib.request.Request(url, data=data, headers=dict(headers or {}), method=method)  # noqa: S310
    context = tls_context(url, verify=verify)
    try:
        with urllib.request.urlopen(built, timeout=timeout, context=context) as answer:  # noqa: S310
            return Response(status=int(answer.status), body=answer.read())
    except urllib.error.HTTPError as error:
        # A 4xx or 5xx is an answer, and its body is the database's own message.
        # HTTPError is caught before URLError because it is a subclass of it.
        return Response(status=int(error.code), body=error.read())
    except (urllib.error.URLError, OSError) as error:
        raise TransportError(str(error)) from error
