"""
The browser demo's transport, which the shared payload cannot police.

`demo/payload.py` exists so the server and the browser build answer in the same
shape. What it cannot see is the call that reaches it: under Pyodide the page
hands its request across a language boundary, and a field lost on the way in is
lost silently, because every field has a default on the Python side.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from demo.browser import Demo

DRIVER = Path(__file__).resolve().parents[1] / 'demo' / 'static' / 'browser.js'


def body(**fields: Any) -> str:
    """The request the page sends, as JSON."""
    sent = {'sql': '', 'caret': 0, 'backend': 'postgres', 'limit': 25, 'pending': [], **fields}
    return json.dumps(sent)


def test_a_template_blank_still_outstanding_crosses_the_boundary() -> None:
    """
    Filling the relation blank has to move the caret to the alias blank.

    The page sends the outstanding blanks with every request, and this transport
    once dropped them on the way in — by naming the fields one at a time and
    forgetting one. Nothing failed: `pending` defaults to empty, so the plan had
    no blank to advance to and a table left the caret dead where it was, with
    the list shut. Two symptoms, one missing argument.
    """
    answer = json.loads(Demo().suggest(body(sql='SELECT  FROM  AS ', caret=13, pending=[17, 7])))
    table = next(s for s in answer['suggestions'] if s['kind'] == 'table')
    plan = table['insertion']

    assert plan['caret'] == 17 + len(table['text']), 'the alias blank, moved along by the table name'
    assert plan['pending'] == [7], 'the relation blank is consumed; the select-list blank is not'
    assert plan['reopen'] is True, 'and the next blank wants the list open'


def test_the_body_is_read_whole_rather_than_field_by_field() -> None:
    """
    A body missing everything optional still answers, and one naming a backend
    gets that backend.

    Both are properties of parsing the request rather than unpacking it into
    parameters, which is the point: a field the page adds later arrives without
    this file being edited, and one it omits cannot silently become a default
    that changes behaviour.
    """
    assert json.loads(Demo().suggest(json.dumps({'sql': 'SELECT ', 'caret': 7})))['suggestions']

    trino = json.loads(Demo().suggest(body(sql='SELECT * FROM ', caret=14, backend='trino')))
    assert [s['text'] for s in trino['suggestions'] if s['kind'] == 'schema'] == ['events', 'warehouse']

    assert json.loads(Demo().suggest(body(backend='nonesuch')))['error'] == "unknown backend 'nonesuch'"


def test_the_page_hands_the_body_over_intact() -> None:
    """
    The other half of the boundary, which no Python test can reach.

    Asserting on source text is a poor tool and the right one here: this single
    call is the only place the page's request can lose a field, the loss is
    silent by construction, and the suite runs no JavaScript. Rebuilding a field
    list — `demo.suggest(body.sql, body.caret, …)` — is the shape that broke and
    the shape this refuses.
    """
    assert 'demo.suggest(JSON.stringify(body))' in DRIVER.read_text()


def test_a_namespace_is_called_what_the_backend_calls_it() -> None:
    """
    One `Kind.SCHEMA` covers every level of a dotted path, because they behave
    identically. The word for it does not: labelling a Trino catalog `schema`
    tells the reader something untrue about the server they are connected to.

    Which word depends on how much of the path is written, so the same dialect
    answers differently at different depths — and the dialects have carried
    these words all along, so nothing here decides them.
    """
    trino = json.loads(Demo().suggest(body(sql='SELECT * FROM ', caret=14, backend='trino')))
    assert trino['kind_words']['schema'] == 'catalog'

    deeper = json.loads(Demo().suggest(body(sql='SELECT * FROM events.', caret=21, backend='trino')))
    assert deeper['kind_words']['schema'] == 'schema', 'the second level of three really is a schema'

    house = json.loads(Demo().suggest(body(sql='SELECT * FROM ', caret=14, backend='clickhouse')))
    assert house['kind_words']['schema'] == 'database'

    postgres = json.loads(Demo().suggest(body(sql='SELECT * FROM ', caret=14)))
    assert postgres['kind_words']['schema'] == 'schema'
