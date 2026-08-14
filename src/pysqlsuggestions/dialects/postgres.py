"""PostgreSQL. Composed from ANSI; nothing here subclasses anything."""

from __future__ import annotations

from dataclasses import replace

from pysqlsuggestions.dialects.ansi import ANSI, COLUMN_EXPRESSION, EXPLAINABLE, RELATION_REFERENCE
from pysqlsuggestions.dialects.ansi import RESERVED as ANSI_RESERVED
from pysqlsuggestions.dialects.base import (
    CatalogQueries,
    Clause,
    LiteralArgument,
    Namespace,
    Placeholder,
    Query,
    Syntax,
)
from pysqlsuggestions.types import Column, ColumnValue, ForeignKey, Function, Kind, Table


def _ansi(name: str) -> Clause:
    """
    The clause ANSI declares under `name`, for a dialect that refines one field of it.

    `ClauseModel.extend` replaces a clause of the same name whole rather than
    merging into it, so refining one field otherwise means hand-copying the
    rest — which is how a restated `followed_by` falls behind the canonical
    clause order it was copied from. Paired with `dataclasses.replace`, the copy
    is never made.

    Raises rather than returning None: a name that is not in ANSI is a typo, and
    a silently absent clause would drop the refinement without a word.
    """
    clause = ANSI.clauses.get(name)
    if clause is None:  # pragma: no cover - a typo in this module, caught at import
        raise KeyError(f'ANSI has no clause named {name!r}')
    return clause


_PROKIND = {'f': 'function', 'a': 'aggregate', 'w': 'window', 'p': 'procedure'}

_RELKIND = {
    'r': 'table',
    'p': 'partitioned table',
    'v': 'view',
    'm': 'materialized view',
    'f': 'foreign table',
    'S': 'sequence',
    'i': 'index',
}

# `$1 = '' AND visible OR nspname = $1` reads as `($1='' AND visible) OR (nspname=$1)`,
# so an empty schema means "whatever is on the search path".
QUERIES = CatalogQueries(
    schemas=Query(
        sql="""
            SELECT nspname FROM pg_namespace
            WHERE nspname NOT LIKE 'pg\\_%' AND nspname <> 'information_schema' AND $1 = $1
            ORDER BY nspname
        """,
        row=lambda row: str(row[0]),
    ),
    tables=Query(
        sql="""
            SELECT n.nspname, c.relname, c.relkind, c.reltuples
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            -- 'S' is a sequence. It is fetched here rather than by a query of
            -- its own because it is a relation in every sense pg_class knows;
            -- `resolve` is what keeps it out of a FROM list.
            WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S', 'i')
              AND ($1 = '' AND pg_catalog.pg_table_is_visible(c.oid) OR n.nspname = $1)
              -- pg_table_is_visible is true for pg_catalog, so an unqualified
              -- position would otherwise open with pg_aggregate. Naming a system
              -- schema explicitly still works.
              AND ($1 <> '' OR n.nspname NOT LIKE 'pg\\_%' AND n.nspname <> 'information_schema')
            ORDER BY n.nspname, c.relname
        """,
        row=lambda row: Table(
            schema=str(row[0]),
            name=str(row[1]),
            kind=_RELKIND.get(str(row[2]), 'table'),
            # -1 is "never analysed", which is not the same as empty.
            rows=int(row[3]) if row[3] is not None and float(row[3]) >= 0 else None,
        ),
    ),
    columns=Query(
        sql="""
            SELECT n.nspname, c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnum
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE a.attnum > 0 AND NOT a.attisdropped AND c.relname = $2
              AND ($1 = '' AND pg_catalog.pg_table_is_visible(c.oid) OR n.nspname = $1)
            ORDER BY a.attnum
        """,
        row=lambda row: Column(
            schema=str(row[0]),
            table=str(row[1]),
            name=str(row[2]),
            type=str(row[3]),
            position=int(row[4]),
        ),
    ),
    # pg_proc holds ~3300 entries, most of them type I/O plumbing that cannot be
    # called from SQL — `anynonarray_in`, `RI_FKey_noaction_del`. Excluding
    # anything that takes or returns internal/cstring, and anything returning a
    # handler type, drops those without touching count, now, array_agg or
    # string_agg. The limit is a safety valve, not a filter: set low it silently
    # truncates the alphabet, which is how `now` went missing.
    functions=Query(
        sql="""
            SELECT n.nspname, p.proname,
                   pg_get_function_arguments(p.oid), pg_get_function_result(p.oid), p.prokind
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE ($1 = '' AND n.nspname IN ('pg_catalog', 'public') OR n.nspname = $1)
              AND p.prokind IN ('f', 'a', 'w', 'p')
              AND p.prorettype NOT IN (
                  'internal'::regtype, 'cstring'::regtype, 'trigger'::regtype,
                  'language_handler'::regtype, 'fdw_handler'::regtype,
                  'tsm_handler'::regtype, 'index_am_handler'::regtype, 'event_trigger'::regtype
              )
              AND NOT EXISTS (
                  SELECT 1 FROM unnest(p.proargtypes) a(oid)
                  WHERE a.oid IN ('internal'::regtype, 'cstring'::regtype)
              )
            ORDER BY p.proname
            LIMIT 10000
        """,
        row=lambda row: Function(
            schema=str(row[0]),
            name=str(row[1]),
            args=str(row[2]),
            # NULL for a procedure, which returns nothing. `str(None)` would put
            # the word `None` in a detail column a user reads.
            result=str(row[3]) if row[3] is not None else None,
            kind=_PROKIND.get(str(row[4]), 'function'),
        ),
    ),
    # Two sources, exhaustive first. An enum type lists every value it permits,
    # which no statistic can improve on; `format_type` reports only the type's
    # name, so the labels are a read of their own. Failing that, planner
    # statistics: `most_common_vals` is an anyarray ordered by frequency, so
    # WITH ORDINALITY is what carries that order through the unnest. Neither
    # touches the table, and pg_stats already restricts itself to what the
    # connected role may read.
    #
    # `source` picks one of them whole rather than ranking a mixture. An analysed
    # enum column answers both branches with the same labels, and merely ordering
    # by source leaves each value in the list twice — once named, once measured.
    values=Query(
        sql="""
            WITH candidates AS (
                SELECT e.enumlabel::text AS value, NULL::float8 AS freq, 0 AS source, e.enumsortorder::float8 AS ord
                FROM pg_attribute a
                JOIN pg_class c ON c.oid = a.attrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_enum e ON e.enumtypid = a.atttypid
                WHERE c.relname = $2 AND a.attname = $3 AND a.attnum > 0 AND NOT a.attisdropped
                  AND ($1 = '' AND pg_catalog.pg_table_is_visible(c.oid) OR n.nspname = $1)
                UNION ALL
                SELECT v.value, v.freq, 1 AS source, v.ord::float8
                FROM pg_stats s
                JOIN pg_class c ON c.relname = s.tablename
                JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = s.schemaname
                -- Two arrays unnested together, so each value keeps its own share.
                CROSS JOIN LATERAL unnest(s.most_common_vals::text::text[], s.most_common_freqs)
                    WITH ORDINALITY AS v(value, freq, ord)
                WHERE s.tablename = $2 AND s.attname = $3
                  AND ($1 = '' AND pg_catalog.pg_table_is_visible(c.oid) OR n.nspname = $1)
            )
            SELECT q.value, q.freq FROM candidates q
            WHERE q.source = (SELECT min(source) FROM candidates)
            ORDER BY q.ord
            LIMIT 50
        """,
        row=lambda row: ColumnValue(text=str(row[0]), frequency=float(row[1]) if row[1] is not None else None),
    ),
    # `position(... in ...)` rather than LIKE: a prefix comes from what the user
    # typed, and `_` is both a LIKE wildcard and the commonest character in a
    # column name — `user_` would match `usera`. Substring rather than prefix
    # because `mail` finding `email` is behaviour the suite this library
    # inherits already pins, and the ordering puts a true prefix first anyway.
    column_search=Query(
        sql="""
            SELECT n.nspname, c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnum
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE a.attnum > 0 AND NOT a.attisdropped AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
              AND n.nspname NOT LIKE 'pg\\_%' AND n.nspname <> 'information_schema'
              AND position(lower($1) in lower(a.attname)) > 0
            ORDER BY position(lower($1) in lower(a.attname)), length(a.attname), n.nspname, c.relname, a.attname
            LIMIT 500
        """,
        row=lambda row: Column(
            schema=str(row[0]),
            table=str(row[1]),
            name=str(row[2]),
            type=str(row[3]),
            position=int(row[4]),
        ),
    ),
    # No `pg_table_is_visible` here: reaching past the search path is the whole
    # point. The system-schema exclusion stays, because `pg_%` is not what
    # anybody means by `FROM ord`. ORDER BY before LIMIT is the port's contract
    # — the truncation happens before ranking sees the rows.
    relation_search=Query(
        sql="""
            SELECT n.nspname, c.relname, c.relkind, c.reltuples
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S', 'i')
              AND n.nspname NOT LIKE 'pg\\_%' AND n.nspname <> 'information_schema'
              AND position(lower($1) in lower(c.relname)) > 0
            ORDER BY position(lower($1) in lower(c.relname)), length(c.relname), n.nspname, c.relname
            LIMIT 200
        """,
        row=lambda row: Table(
            schema=str(row[0]),
            name=str(row[1]),
            kind=_RELKIND.get(str(row[2]), 'table'),
            # -1 is "never analysed", which is not the same as empty.
            rows=int(row[3]) if row[3] is not None and float(row[3]) >= 0 else None,
        ),
    ),
    foreign_keys=Query(
        sql="""
            SELECT n.nspname,
                   c.relname,
                   (SELECT array_agg(a.attname ORDER BY k.ord)
                      FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
                      JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum),
                   rn.nspname,
                   rc.relname,
                   (SELECT array_agg(a.attname ORDER BY k.ord)
                      FROM unnest(con.confkey) WITH ORDINALITY AS k(attnum, ord)
                      JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.attnum)
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_class rc ON rc.oid = con.confrelid
            JOIN pg_namespace rn ON rn.oid = rc.relnamespace
            WHERE con.contype = 'f'
              AND ($1 = '' AND pg_catalog.pg_table_is_visible(c.oid) OR n.nspname = $1)
            ORDER BY n.nspname, c.relname, con.conname
        """,
        # WITH ORDINALITY rather than a bare unnest: conkey and confkey correspond
        # position by position, and that correspondence is the whole content of a
        # composite key. Aggregating either side in an unspecified order would
        # produce an edge that looks right and joins the wrong columns together.
        row=lambda row: ForeignKey(
            schema=str(row[0]),
            table=str(row[1]),
            columns=tuple(str(name) for name in row[2]),
            ref_schema=str(row[3]),
            ref_table=str(row[4]),
            ref_columns=tuple(str(name) for name in row[5]),
        ),
    ),
)

RESERVED = ANSI_RESERVED | frozenset(
    """
    analyse analyze asymmetric both collate current_role current_user deferrable freeze ilike
    initially isnull lateral leading localtime localtimestamp notnull placing returning
    session_user similar symmetric trailing variadic verbose
    """.split(),
)

TYPES = (
    'text',
    'integer',
    'bigint',
    'boolean',
    'numeric',
    'date',
    'timestamptz',
    'timestamp',
    'interval',
    'jsonb',
    'json',
    'uuid',
    'smallint',
    'real',
    'double precision',
    'character varying',
    'bytea',
    'inet',
    'time',
    'money',
)

POSTGRES = replace(
    ANSI,
    name='postgres',
    syntax=Syntax(
        identifier_quotes=('"',),
        line_comments=('--',),
        nested_block_comments=True,
        string_escape_backslash=False,
        unquoted_case='lower',
        dollar_quoting=True,
        cast_operator='::',
        escape_string_prefix='E',
        unquoted_extra='$',
        unquoted_non_ascii=True,
        # No `?`. It is the JSONB existence operator, and `data ? 'key'` is a
        # predicate people write — reading it as a parameter would silence a
        # position that has a real answer.
        placeholders=(Placeholder(opens='$', body='digits'), Placeholder(opens=':')),
    ),
    namespace=Namespace(levels=('schema', 'table')),
    statement_start=(
        *ANSI.statement_start,
        'DROP SEQUENCE',
        'ALTER SEQUENCE',
        'DROP MATERIALIZED VIEW',
        'DROP INDEX',
    ),
    clauses=ANSI.clauses.extend(
        # Four clauses refined rather than restated. `extend` replaces a clause
        # of the same name whole, so a dialect adding one field to a shared
        # clause would otherwise hand-copy every other field — including the
        # slice of the canonical clause order that `_ORDER`'s docstring exists
        # to stop being hand-copied. `replace` on the record ANSI already built
        # keeps the copy honest by never making one.
        #
        # All four use `before_the_item`, which `request.py` gates behind a
        # non-empty prefix: a column belongs at `SELECT ⌶` and `GROUP BY ⌶`, and
        # a row count at `LIMIT ⌶`, so a rarely-wanted modifier is reached by
        # typing rather than ranked above every column in the schema.
        replace(_ansi('SELECT'), before_the_item=('DISTINCT', 'ALL')),
        # ClickHouse spells its grouping sets `GROUP BY … WITH ROLLUP`, so this
        # list is Postgres's rather than the baseline's. Trino agrees with
        # Postgres and could have them too; that is a change with its own
        # evidence to gather.
        replace(_ansi('GROUP BY'), before_the_item=('ALL', 'DISTINCT', 'ROLLUP', 'CUBE', 'GROUPING SETS')),
        # No kind, still: ansi.py records that giving LIMIT one made `LIMIT ⌶`
        # offer OFFSET, which goes after the number rather than instead of it.
        replace(_ansi('LIMIT'), before_the_item=('ALL',)),
        # Three more column constraints than the baseline, each verified against
        # the server: ClickHouse and Trino refuse all three, so they cannot go
        # in ANSI without offering words their parsers reject.
        replace(
            _ansi('CREATE TABLE'),
            defines_columns=(*_ansi('CREATE TABLE').defines_columns, 'UNIQUE', 'REFERENCES', 'CHECK'),
        ),
        # TABLESAMPLE is derived from `follows` for most clauses, but `FROM`'s
        # continuations are an explicit list and derivation only adds what that
        # list omits — so the word has to be named here to reach the caret after
        # a relation. Declaring the clause alone silences `TABLESAMPLE ⌶`; this
        # is what offers the word in the one place it can go.
        #
        # `WITH ORDINALITY` is deliberately not here. It applies to a function
        # item and `followed_by` is per clause rather than per item kind, so
        # naming it would offer it after `FROM users ⌶` too, where the server
        # refuses it.
        # Last in the list, because the list is ranked: TABLESAMPLE ahead of the
        # joins put a word almost nobody writes above the one almost everybody
        # does, which `tests/test_joins_resolve.py` caught immediately.
        replace(_ansi('FROM'), followed_by=(*_ansi('FROM').followed_by, 'TABLESAMPLE')),
        # Four two-word clause names rather than one `FOR` with continuations,
        # for the reason DROP SEQUENCE and ALTER TABLE record: a bare `FOR`
        # would make ('FOR',) a phrase in its own right, and
        # `_half_written_clauses` skips a head that is already a phrase — so
        # `FOR ⌶` would stop answering `UPDATE`.
        #
        # Until these existed `FOR` was not a clause at all, so the caret after
        # it was still governed by FROM: `SELECT * FROM users FOR ⌶` offered
        # `users`, and accepting wrote `FROM users FOR users`.
        *(
            Clause(
                name=name,
                follows=frozenset({'FROM', 'JOIN', 'WHERE', 'GROUP BY', 'HAVING', 'ORDER BY', 'LIMIT', 'OFFSET'}),
                statements=frozenset({'SELECT'}),
                suggests=(Kind.KEYWORD,),
                followed_by=('OF', 'NOWAIT', 'SKIP LOCKED'),
            )
            for name in ('FOR UPDATE', 'FOR NO KEY UPDATE', 'FOR SHARE', 'FOR KEY SHARE')
        ),
        # `OF` names a relation the statement already has, and nothing in `Kind`
        # means that. `Kind.TABLE` would offer every catalog relation, and once
        # the relation is aliased the server takes only the alias — so
        # `FROM users u FOR UPDATE OF users` is refused, and offering `users`
        # there would be a confident wrong answer where silence is available.
        # `Kind.ALIAS` does not serve either: it invents a name for the relation
        # just written rather than listing the ones in scope.
        Clause(
            name='OF',
            follows=frozenset({'FOR UPDATE', 'FOR NO KEY UPDATE', 'FOR SHARE', 'FOR KEY SHARE'}),
            statements=frozenset({'SELECT'}),
            suggests=(),
            followed_by=('NOWAIT', 'SKIP LOCKED'),
        ),
        # `TABLE t` is `SELECT * FROM t` and is deliberately *not* modelled.
        #
        # It was, and the acceptance suite caught what that costs: a statement
        # form is found by scanning for the first word that starts one, and
        # `TABLE` is a word inside `CREATE TABLE`. So `CREATE TABLE t (id ⌶`
        # began offering `users`, in a definition list where a relation cannot
        # go — trading the silence an unmodelled form correctly gives for a
        # wrong answer, in a statement written far more often than `TABLE t`.
        #
        # Modelling it needs `CREATE TABLE` modelled first, so that the longer
        # form wins the match. That is gap 1 in docs/gaps.md and a project of
        # its own.
        #
        # These three exist to make a caret stop answering, not to make it
        # answer. Until a word is a clause the analyser reads the caret after it
        # as still inside the clause before — so `FROM t TABLESAMPLE ⌶` offered
        # JOIN and WHERE, and `… CYCLE ⌶` offered the CTE body words. Declaring
        # the clause is the whole fix, and `trino.py` already does exactly this
        # for its own TABLESAMPLE.
        #
        # No sampling method is named. Postgres ships BERNOULLI and SYSTEM and
        # an extension may add more, so a static list here would go quietly
        # wrong on any installation that has one.
        Clause(name='TABLESAMPLE', follows=frozenset({'FROM', 'JOIN'}), suggests=(Kind.KEYWORD,)),
        # SEARCH and CYCLE follow a recursive CTE's body. BREADTH and DEPTH are
        # the only two words SEARCH takes, so it can answer as well as stop a
        # wrong one; CYCLE takes a column of the CTE, a scope this position
        # cannot see, so it says nothing.
        Clause(name='SEARCH', follows=frozenset({'WITH'}), suggests=(Kind.KEYWORD,), followed_by=('BREADTH', 'DEPTH')),
        Clause(name='CYCLE', follows=frozenset({'WITH'}), suggests=()),
        # `USING operator` is Postgres's alone — an explicit ordering operator,
        # where the standard has only ASC and DESC. The operator itself is not
        # offered: operators reach a caret through `Clause.operators`, which
        # marks a predicate clause, and ORDER BY is not one.
        replace(
            _ansi('ORDER BY'),
            followed_by=(
                'ASC',
                'DESC',
                'USING',
                *(word for word in _ansi('ORDER BY').followed_by if word not in {'ASC', 'DESC'}),
            ),
        ),
        Clause(
            name='LATERAL',
            follows=frozenset({'FROM', 'JOIN'}),
            opens_an_item=True,
            suggests=(Kind.TABLE, Kind.FUNCTION),
            # `[ LATERAL ] ( select )` takes a whole statement, the way a CTE
            # body does. Without this the paren was read as an ordinary FROM
            # position and answered with relations.
            opens_a_group=('SELECT',),
        ),
        # `ROWS FROM( f(), g() )` takes a list of function calls. Two words, so
        # `_half_written_clauses` answers `ROWS ⌶` with `FROM`; `opens_an_item`
        # because it begins a FROM item rather than following a finished one,
        # which is what LATERAL declares for the same reason.
        #
        # A clause rather than a case in `opens_a_name_list`: that rule reads
        # `Clause.aliases_with` so no SQL word enters engine/, and `ROWS FROM`
        # is Postgres spelling. Modelling it also answers the caret instead of
        # merely silencing it — the grammar puts a function there, and the
        # catalog has those.
        Clause(
            name='ROWS FROM',
            follows=frozenset({'FROM', 'JOIN'}),
            opens_an_item=True,
            suggests=(Kind.FUNCTION,),
        ),
        Clause(name='DISTINCT ON', follows=frozenset({'SELECT'}), suggests=(Kind.COLUMN, Kind.FUNCTION)),
        Clause(
            name='ON CONFLICT',
            follows=frozenset({'VALUES'}),
            statements=frozenset({'INSERT INTO'}),
            suggests=(Kind.COLUMN,),
        ),
        # Not in the ANSI base: ClickHouse and Trino have no RETURNING at all,
        # and it is a syntax error in a SELECT even here. Declaring where it may
        # follow is the whole of what this dialect has to say about it.
        Clause(
            name='RETURNING',
            follows=frozenset({'DELETE FROM', 'SET', 'WHERE', 'VALUES', 'ON CONFLICT'}),
            statements=frozenset({'DELETE FROM', 'INSERT INTO', 'UPDATE'}),
            suggests=COLUMN_EXPRESSION,
        ),
        # ANALYZE and VERBOSE stand between EXPLAIN and its statement, which is
        # what `before_the_item` means. `followed_by` would offer them after the
        # statement, where they cannot go.
        #
        # `extend` replaces a clause of the same name rather than merging into
        # it, so ANSI's `followed_by` has to be restated here.
        Clause(
            name='EXPLAIN',
            suggests=(Kind.SNIPPET, Kind.KEYWORD),
            followed_by=EXPLAINABLE,
            before_the_item=('ANALYZE', 'VERBOSE'),
        ),
        # Postgres's alone. Trino's parser lists what DROP accepts — CATALOG,
        # FUNCTION, MATERIALIZED, ROLE, SCHEMA, TABLE, VIEW — and SEQUENCE is
        # not among them; ClickHouse has no sequences at all. A form only one
        # shipped backend implements belongs to that one rather than to the
        # baseline they share.
        #
        # Two-word continuations, for the reason ALTER TABLE's are: a bare
        # `RENAME` would make ('RENAME',) a phrase in its own right, and
        # `_half_written_clauses` skips a head that is already a phrase.
        Clause(
            name='DROP SEQUENCE',
            suggests=(Kind.SEQUENCE, Kind.SCHEMA),
            followed_by=('CASCADE', 'RESTRICT'),
        ),
        Clause(
            name='ALTER SEQUENCE',
            suggests=(Kind.SEQUENCE, Kind.SCHEMA),
            followed_by=('RENAME TO', 'OWNED BY'),
        ),
        # Postgres's own relkind vocabulary, so the narrowing is expressible
        # here and not in ANSI — ClickHouse reports storage engines and a
        # positive list naming `table` would empty the position there.
        # `DROP TABLE` takes all three of these and refuses a view:
        # `"reports_active" is not a table`.
        Clause(
            name='DROP TABLE',
            suggests=RELATION_REFERENCE,
            followed_by=('CASCADE', 'RESTRICT'),
            relation_kinds=('table', 'partitioned table', 'foreign table'),
        ),
        Clause(
            name='DROP MATERIALIZED VIEW',
            suggests=RELATION_REFERENCE,
            followed_by=('CASCADE', 'RESTRICT'),
            relation_kinds=('materialized view',),
        ),
        Clause(
            name='DROP INDEX',
            suggests=RELATION_REFERENCE,
            followed_by=('CASCADE', 'RESTRICT'),
            relation_kinds=('index',),
        ),
        # Data-modifying CTEs, which are Postgres's own: all three forms plan
        # inside a body and after the list, and ClickHouse refuses the first
        # with a syntax error. `extend` replaces a clause of the same name
        # rather than merging into it, so ANSI's declarations are restated.
        Clause(
            name='WITH',
            suggests=(),
            opens_a_group=('SELECT', 'VALUES', 'WITH', 'INSERT INTO', 'UPDATE', 'DELETE FROM'),
            followed_by=('AS', 'SELECT', 'INSERT INTO', 'UPDATE', 'DELETE FROM'),
            aliases_with='AS',
            before_the_item=('RECURSIVE',),
        ),
    ),
    keywords=frozenset(word.upper() for word in RESERVED),
    reserved=RESERVED,
    types=TYPES,
    # The three calls that name a sequence in a string. Their argument is a
    # `regclass`, which the server will accept for any relation — so the fact
    # that only a sequence is *valid* here is knowledge about these functions
    # rather than about their signature, which is why it is written down.
    literal_arguments=(
        LiteralArgument(function='nextval', suggests=(Kind.SEQUENCE,)),
        LiteralArgument(function='currval', suggests=(Kind.SEQUENCE,)),
        LiteralArgument(function='setval', suggests=(Kind.SEQUENCE,)),
    ),
    catalog_queries=QUERIES,
)
