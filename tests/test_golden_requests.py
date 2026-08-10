"""The translated acceptance corpus, run against derive_request."""

from __future__ import annotations

import pytest

from pysqlsuggestions.dialects.ansi import ANSI
from pysqlsuggestions.dialects.clickhouse import CLICKHOUSE
from pysqlsuggestions.dialects.postgres import POSTGRES
from pysqlsuggestions.dialects.trino import TRINO
from pysqlsuggestions.engine.request import derive_request
from tests.corpus.cases import CASES, GoldenRequest, split_caret

DIALECTS = {'ansi': ANSI, 'postgres': POSTGRES, 'clickhouse': CLICKHOUSE, 'trino': TRINO}


def _params() -> list[object]:
    """Each case, marked xfail(strict=True) while it is still pending."""
    return [
        pytest.param(case, marks=pytest.mark.xfail(strict=True, reason=case.note or 'pending'))
        if case.pending
        else pytest.param(case)
        for case in CASES
    ]


@pytest.mark.parametrize('case', _params(), ids=[f'{c.dialect}: {c.sql}' for c in CASES])
def test_golden_request(case: GoldenRequest) -> None:
    """derive_request must reproduce the recorded Request exactly."""
    sql, caret = split_caret(case.sql)
    result = derive_request(sql, caret, DIALECTS[case.dialect])

    assert tuple(kind.value for kind in result.kinds) == case.kinds
    assert result.prefix == case.prefix
    assert result.qualifier == case.qualifier
    assert result.clause == case.clause

    relations = result.scope.visible() if result.scope else ()
    assert tuple(f'{r.alias or ""}:{".".join(r.path)}' for r in relations) == case.relations
