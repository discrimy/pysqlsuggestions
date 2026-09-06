# Changelog

Grouped by what changes for someone using the library rather than by commit.
The engine's whole job is what it offers at a caret, so that is what this
records: the positions where it now answers differently.

## 0.11.0

### `resolve` no longer takes a `limit`

Nothing in `complete`'s signature changes, and no caret answers differently. This
is for anyone using the lower-level entry point directly.

The parameter bounded the prefix searches, which now bound themselves at
`SEARCH_ROWS`, so it had become an argument that was accepted and ignored. That
is worse than removing it: a caller asking for five suggestions used to search a
smaller part of the database than one asking for forty, and would have gone on
believing they still could.

### A truncated completion list now says it is truncated

The language server reported `isIncomplete: false` on every answer. The LSP
specification says a list is recomputed on further typing only when that flag is
true, so a client told `false` caches the items and filters them itself — and on
a large schema every answer is cut to `limit`. One truncated list at `SELECT u`
therefore stayed wrong through `user`, `user_r` and `user_ref`: the server was
never asked again, and the column being reached for could not appear however much
more of it you typed.

A full list is now reported as incomplete. The signal is right-biased on purpose:
a list that happens to be exactly `limit` long and is genuinely complete gets
re-queried once for nothing, at a few milliseconds. The opposite mistake is
silent and lasts for the rest of the word.

### A cross-relation search ranks a thousand rows instead of two hundred

The search that answers `SELECT user⌶` before any FROM truncates on the server,
and the server cannot rank: it orders by match position, name length and then the
alphabet, while the engine also knows declaration order and — most decisively —
whether a relation can be referenced without qualifying it.

Those two numbers used to disagree. The queries stopped at 500 rows while
`resolve` asked for `limit × 5`, which is 200 by default, so three hundred rows
were ordered, returned and discarded unranked. They are one number now,
`SEARCH_ROWS`, interpolated into every shipped search query so a dialect and the
engine cannot drift apart.

Raised to 1000 with it. Measured on a 5000-table schema at the worst prefix there
is: 200 rows cost 65 ms, 1000 cost 72 ms, 2000 cost 84 ms — five times the
headroom for about a tenth of the time.

**This narrows the failure rather than removing it, and is not offered as a
fix.** 700 relations in an off-search-path schema named `aaa_*` will still hide
the one `public` column a bare reference could have used; the wanted row appears
at 701 and not at 700. What closes it properly is putting search-path visibility
into the server's ordering, which costs about 30 ms on the worst prefix and is
not currently thought worth it — `docs/gaps.md` records that with the numbers.

### Searching for a column by name stops checking privileges it will discard

`SELECT user⌶`, before any FROM clause, searches every relation in the database
for a matching column. On a 5000-table schema that matched 55 000 columns at a
one-character prefix, and the query computed `has_column_privilege` and
`format_type` for every one of them *before* truncating to 500.

| `search_columns` | before | after |
| --- | --- | --- |
| `'u'` | 238 ms | **40.9 ms** |
| `'user'` | 41.7 ms | 33.6 ms |

The narrowing now happens first and those two functions are computed on the 500
rows that survive it. Identical rows in identical order — this is a change of
query shape, not of answer.

Worth knowing if you maintain a dialect: the sort was not the problem, though it
is the obvious suspect. Postgres uses a top-N heapsort for `ORDER BY … LIMIT` and
it costs nothing worth naming; removing only the privilege column took the same
query to 36 ms, which is how the cause was identified rather than guessed. A
per-row function in the select list of a query with a LIMIT is evaluated before
the limit applies.

### Ranking renders the suggestions it shows, not the ones it discards

A `FROM ⌶` on a 5000-relation schema built five thousand suggestions — each with
its quoting decided and its text rendered — in order to return forty. That was
about half of what ranking cost, and none of it was needed.

The sort key's first three elements are availability, score and name length, all
of which come from the candidate itself; only the fourth is the rendered text. So
the shortlist is chosen on the first three and only what survives is rendered.

| warm caret, 5000 tables | before | after |
| --- | --- | --- |
| `SELECT * FROM ⌶` | 38.0 ms | 12.9 ms |
| `SELECT ⌶` | 18.6 ms | 7.4 ms |
| `... JOIN ⌶` | 42.5 ms | 14.9 ms |
| `WHERE ⌶`, 20 relations | 24.3 ms | 8.7 ms |

Ranking itself went from 21.3 ms to 2.5 ms at that first caret, and no longer
grows with the catalog.

**The output is identical**, which is the only thing that matters here and took
two things to be true rather than nearly true. Every candidate tying with the
last of the shortlist is rendered too, because the rendered text is what decides
between them and a large catalog produces long runs of ties. And the shortlist
grows if deduplication leaves fewer suggestions than were asked for, since
duplicates can only be found after rendering.

### A relation caret stops fetching every index in the database

`Catalog.tables` returns everything in the catalog, because `DROP INDEX ⌶` reads
that same list and wants precisely what every other position exists to hide. On
a 5000-table schema that is 20 000 rows fetched to serve 5000 — a table carries a
primary key index and usually more — so `FROM ⌶` was moving fifteen thousand
indexes across the wire in order to discard them.

`SupportsQueryableRelations` is a new capability for the narrower read, and the
broad one is untouched, so `DROP INDEX` and the sequence positions go on working
and no existing adapter changes. Cold, on a 5000-table Postgres:

| caret | before | after |
| --- | --- | --- |
| `SELECT * FROM ⌶` | 84.8 ms | 45.0 ms |
| `SELECT ⌶` | 82.5 ms | 36.2 ms |
| `... JOIN ⌶` | 185.9 ms | 146.9 ms |

The cached payload for that read drops from 1991 KiB to 498, which is what a
`ByteCache` across a socket decodes on **every** keystroke — 24 ms of it before.

The two reads are cached under separate keys, which matters more than it looks:
one key would let a `FROM` caret write its index-free list where the `DROP INDEX`
caret looks, emptying that position for as long as the entry lived and saying
nothing about why.

### A caret in a joined statement reads every relation at once

`SELECT * FROM a JOIN b JOIN c … WHERE ⌶` asked the catalog for one relation's
columns at a time, so a twenty-way join issued twenty-one queries for a single
keystroke. Now it issues two.

This is worth stating in latency rather than in queries, because a server on the
same machine hides it completely:

| `WHERE ⌶`, 20 relations in scope | before | after |
| --- | --- | --- |
| local server | 52.6 ms | 31.2 ms |
| 20 ms round trip | 494.9 ms | 73.4 ms |

It is also **flat in the size of the catalog** — 490 ms against a 100-table
schema, 495 ms against 5000 — because the cost was the join count and nothing
else. So this is not a large-schema fix: every wide query paid it, on every
database, and a small one never grew out of it.

`SupportsBulkColumns` is a new capability, so nothing an existing adapter does
changes. Absent, the reads happen one at a time exactly as before and the
suggestions are identical; `DbapiCatalog` implements it for all three shipped
dialects, and a dialect that ships no `columns_in` query falls back the same way.

A batch is a transport detail and deliberately not a cache key: each relation is
stored under the key a single read would have used, so a later statement sharing
two of its three relations still pays for one. Adding it to a `MemoryCache` keyed
per batch would have made the two optimisations compete instead of compound.

Dialect authors get one new piece of the marker language: `$2...` is a spread,
expanding to as many placeholders as there are values. It must be the last marker
in a query, since it claims every remaining value.

### A keystroke on a large schema costs a quarter less, and offers the same thing

Two pure functions in the hot path were recomputed on every keystroke over names
that had not changed. `lex.reads_as_one_identifier` walks a name character by
character to decide whether it survives unquoted, and `rank._words` splits one
into its components for matching; ranking asks each of them once per candidate,
so a 5000-relation schema paid 5000 walks and 5000 splits per completion. They
measured as 45% and 59% of ranking respectively — the first dominating at an
empty prefix, where no matching runs, and the second once something is typed.

Both are now memoised on the string, which is all either depends on. Measured on
a 5000-table Postgres with a warm cache and no I/O at all:

| caret | before | after |
| --- | --- | --- |
| `SELECT * FROM ⌶` | 38.0 ms | 28.6 ms |
| `SELECT * FROM ord⌶` | 36.7 ms | 26.5 ms |
| `... JOIN ⌶` | 42.5 ms | 31.6 ms |
| `WHERE ⌶`, 20 relations in scope | 24.3 ms | 19.2 ms |

Output is unchanged everywhere — this is the same ranking, arrived at with less
work. The memos are bounded rather than unbounded, for the reason 0.10.0 bounded
`MemoryCache`: what reaches them is not only catalog names but whatever has been
typed, and a language server stays up for a working day.

What this does *not* fix is the shape underneath it: a caret still builds a
candidate for all 5000 relations in order to show 40, so the cost still grows
with the catalog. That is recorded in `docs/gaps.md` rather than fixed here.

### A Trino caret no longer offers another catalog's columns

`SELECT * FROM orders o WHERE o.⌶` asked `system.jdbc.columns` for a relation by
name with nothing constraining the catalog, so it answered from every connector
the coordinator federates. A catalog bound to `postgresql` returned ClickHouse's
columns for a table Postgres does not have — and where two backends hold a
same-named table, the caret offered a mixture of both relations' columns as
though they were one.

The unqualified position is now bound to `current_catalog`, which is what an
unqualified name means. Naming a schema still reaches across catalogs, because
that is what Trino is for: `FROM postgresql.public.reports_report p JOIN
clickhouse.analytics.report_executions c ON c.⌶` is unchanged.

It was also the slowest read in the library. Bounding the scan takes that
position from **9.8 s to 0.05 s** — the query had been reaching every
connector's metadata in turn — and the integration suite from 84 s to 60 s.

Worth knowing if you maintain a dialect: the filter has to be spelled as a
top-level conjunct. The disjunction that reads more naturally is equally correct
and entirely useless, because Trino pushes conjuncts into a connector and cannot
push a disjunction. Both the comment in `dialects/trino.py` and a timing test say
so, since nothing else can tell the two apart.

## 0.10.0

### A caret sees a CREATE TABLE without a restart

`MemoryCache` entries now expire after five minutes by default, where 0.9.0 kept
them for the life of the process. Nothing in this library hears about DDL, so an
editor session that had already read `public` would go on offering the same
relation list until somebody restarted the server — a table created ten minutes
ago was simply not there.

Five minutes is invisible to a person: `_Reader` collapses repeated reads inside
one request, so an expiry costs at most one query per read kind per completion.
`MemoryCache(default_ttl=None)` restores the old behaviour.

### Nothing else changes at a caret

Every position answers what it answered in 0.9.0, with the same ranking and the
same degradations. The rest of this release is about what the cache does while
nobody is looking at it.

### The in-memory cache is bounded

`MemoryCache` holds 1024 entries and evicts least-recently-used. 0.9.0 was
unbounded, on the argument that entries are bounded by the size of the catalog
times the number of roles a process serves — which is true, and is not a bound:
it says the ceiling exists, not that it is low enough for an editor plugin to be
free to reach.

`maxsize` counts entries rather than bytes, and one entry can be a
fifty-thousand-row column list. What it bounds is the number of distinct
namespace paths a session accumulates, which is the thing that grows while
somebody types. `MemoryCache(maxsize=None)` is the old behaviour.

### The in-memory cache is safe to share between threads

`MemoryCache.get` checked an entry's expiry and then deleted it, and two threads
through that window raced into `KeyError`. Every operation now takes a lock.

The failure was real but quiet in two ways. It needs a narrow window — at the
default 5 ms switch interval it did not appear in 40 000 operations, and at 10 us
it appeared on every run of 4 000 — and `resolve.py` catches everything a cache
raises, so the symptom was not an error but caching that silently stopped for
the rest of a request. The bound had the same problem in a different shape:
eight concurrent writers pushed a cache of four entries to a peak of ten before
the evictions caught up.

`lsp/` was never exposed to either, because every cache access there already
happens inside the session lock.

### The in-memory cache can be invalidated and measured

`clear()` drops everything, for the caller who has just changed the schema and
would rather not name what went stale. `delete(key)` drops one entry, addressed
by a key from `cache_key` — deliberately not a prefix, because matching on part
of the key would make its grammar a format, and it is not one.

`stats()` returns hits, misses, expiries, evictions, entries and maxsize.
Expiries are counted as misses as well as separately: the caller re-read either
way, so the hit rate has to include them, but a cold miss and a five-minute
expiry say opposite things about whether the TTL is right.

## 0.9.0

### Nothing changes at a caret

Which is worth stating, because everything else here does. Every position
answers exactly what it answered in 0.8.0, with the same ranking and the same
degradations. What changes is who is allowed to hold the answers between
keystrokes.

### Breaking: a plain dict is no longer a cache

`Cache` was one protocol whose docstring opened by saying a dict satisfied it.
It is now two — `ObjectCache`, with `get(key)` and `set(key, value, ttl=None)`,
and `ByteCache`, with `get_bytes` and `set_bytes` — and a dict satisfies
neither, because it has `get` and no `set`.

Passing one raises `TypeError` naming the replacement. That is deliberate:
treating it as "no cache" would have left every existing caller correct, silent
and uncached, with nothing to notice but completions that had quietly got
slower.

```python
complete(sql, caret, POSTGRES, catalog, cache={})              # 0.8.0
complete(sql, caret, POSTGRES, catalog, cache=MemoryCache())   # 0.9.0
```

`MemoryCache` is in `pysqlsuggestions.caches` and is the dict it used to be,
with an optional expiry.

### Any store can now be a cache

`ByteCache` exists so that redis, memcached, diskcache or anything else can hold
catalog reads for a fleet of processes rather than one. The library owns the
encoding on both ends: the key is an opaque string from `cache_key`, and values
are encoded by the library, so an implementation never sees a `Table` and cannot
forget to serialise one. An `ObjectCache` is stored as-is, so the in-process
path pays nothing for the existence of the other one.

### A redis cache, in an extra

```bash
pip install 'pysqlsuggestions[cache-redis]'
```

```python
from pysqlsuggestions.caches.redis import RedisCache

cache = RedisCache.from_url('redis://localhost:6379/0', namespace='prod-pg')
```

`namespace` is required and has no default. The key leads with the role and
carries the dialect but names no server, so two databases sharing a namespace
would serve each other's privilege-filtered reads — silently, and looking like a
database permission bug. One namespace per database, and per identity you cannot
name.

The module never imports redis outside `from_url`; it duck-types a client with
`get` and `set`, which is what makes it work with redis-py 3 through 6, valkey,
cluster clients and pooling wrappers alike.

### A cache that fails costs suggestions, never the completion

The rule the rest of the library follows now covers this port too. A store that
is down, full, or handing back something foreign is caught, and the completion
answers as it would have with no cache at all. A transport failure disables the
cache for the remainder of that one request — otherwise a two-second socket
timeout is paid once per read rather than once per request — while an
undecodable value is treated as a miss and nothing more.

### For anyone writing an adapter

`pysqlsuggestions.testing` ships `CacheConformance`, which checks the parts of
the `ByteCache` contract that two method names do not state: that a miss is
`None` and not `b''`, that arbitrary binary round-trips, that a write replaces,
and that keys are opaque. `InMemoryByteCache` is there too, for exercising the
encoded path without a socket.

## 0.8.0

### Accepting a suggestion no longer breaks the statement

A column offered before any `FROM` exists writes the clause with it, and that
clause now lands in the query that needs it. `SELECT na⌶ -- note` used to splice
it inside the comment, leaving a statement with no `FROM` at all; `SELECT na⌶
UNION SELECT 1` produced `FROM public.auth_userUNION`, one identifier with the
set operation gone; and a caret in a CTE body or a derived table put the clause
in the *enclosing* statement, which then had two while the subquery that asked
for one still had none. All three were one offset, computed without regard to
the caret's parentheses or to what trailing trivia the statement ended with.

At `SELECT ⌶` — the commonest trigger there is — the language server was handing
the editor a completion whose main edit was the `FROM` clause, with the column
demoted beside it. Both edits legitimately start at the caret there, and the one
at the caret is the later of the two.

### Positions that answer where they used to go quiet, or wrong

`GROUP BY ROLLUP ⌶` on Postgres offers the grouping items. The dialect offered
`ROLLUP` and then could not read it back, so the word registered as the clause's
own item and the caret after it proposed `HAVING` — which the server refuses.
`CUBE` and `GROUPING SETS` were the same. No other dialect was affected.

Typing `SELECT "".⌶` no longer empties the relation list for the rest of the
session. A quoted empty identifier reaches the catalog as both "every relation"
and "the relations in a schema named nothing", and the two shared a cache key.

`INSERT INTO users (id) (SELECT ⌶ FROM orders)` offers the source's columns and
not the target's. A source query written in parentheses is the same statement
with brackets round it, and all three backends refuse the target inside them —
but the target was found one level up and stayed in scope, so the subquery read
it the way a correlated subquery reads an enclosing one. The unparenthesised
form was already right; this was the half of it that was missed.

`WHERE CAST(x AS boolean) = ⌶` narrows to what a boolean can face, as
`WHERE x::boolean = ⌶` already did. Two spellings of one operation gave two
answers, and `::` is an extension — on a dialect that declares no cast operator,
the functional form is the only spelling there is and narrowing never happened
at all.

`JOIN ⌶` proposes every declared constraint rather than losing some of them
silently. A column that is the referencing end of two constraints — what a
polymorphic reference looks like — offered only the last of them; and qualifying
a relation returned *fewer* proposals than writing its bare name, because the
constraints were fetched for the schema the statement named and a join reaches
in both directions.

### Statements that used to crash or hang

A document of deeply nested `WITH` bodies, or a long chain of CTEs each selecting
from the last, raised `RecursionError` out of `complete` and out of the language
server's completion handler. Three walks descended without a bound; all three
have one now, and a query too deep to follow loses the tail of its answer rather
than the whole request.

A half-typed query of nested subqueries — parentheses opened and not yet closed,
which is what an editor holds on most keystrokes — took thirty-five seconds at
2.4 KB and now takes under one. A run of *bare* unclosed parentheses was a
second, separate mechanism and is now linear too: eight thousand of them took
about four seconds and take 0.04.

### For anyone running the language server

A slow database no longer costs the session its schema completion. The bound on
reaching a host was left on the socket afterwards, so it governed every later
read — and a catalog query on a database with enough tables to be slow raised,
which the server treats as the catalog having failed. Reaching a host is still
bounded at five seconds; reading one is now bounded separately, and the two
HTTP-backed dialects keep their own per-request bound rather than the connect's.

The server still re-lexes the whole document on each keystroke: about 28 ms on a
14 KB statement, and linear in the size of the document. That is now recorded as
gap 2 in `docs/gaps.md` rather than left implicit — closing it means lexing
incrementally against `didChange`, which is a feature rather than a repair.

## 0.7.0

### Positions that had no answer

`CREATE TABLE t (id ⌶` offers types, and the caret past one offers the
constraints that may follow it — `NOT NULL`, `DEFAULT`, `PRIMARY KEY` and, on
Postgres, `UNIQUE`, `REFERENCES` and `CHECK`. A constraint already written in
that column is not offered again. A column name being invented still answers
nothing, as does every nested paren: a type's parameters, a `CHECK`, a foreign
key's column list.

`CREATE ⌶` answers `TABLE`, and `CREATE TABLE if⌶` answers `IF NOT EXISTS`.

`TABLE users ⌶` is a statement form at last: the relation, then the query tail
it shares with a `SELECT`, then that relation's own columns after `ORDER BY`.
`TABLE ONLY ⌶` on Postgres, the only backend that takes the word. ClickHouse has
no such form and is not offered one.

`CREATE TABLE t AS SELECT …` is still not offered. The clause carries no
continuations at all, because a clause's own reach into its parentheses put `AS`
where `CREATE TABLE t (id AS` parses as nothing.

### Nothing changes at a caret

`TRUNCATE TABLE` is a clause in its own right. It reads the same as it always
did — the two-word entry exists so that it goes on doing so now that `TABLE` is
modelled, since one-word `TRUNCATE` ends before the `TABLE` after it and lost
the match outright.

## 0.6.0

### Positions that now say what the role may not read

`WHERE d.⌶` still offers every column, but one the connected role cannot select
arrives last rather than among its readable neighbours, carrying `no SELECT
privilege`. It is offered at all because a name that vanishes reads as the
engine not knowing about it, where one that arrives annotated says what is true.
The same holds for a relation with no grant at all in a `FROM` list, and for a
join proposal to one — which keeps its `fk:` annotation, the constraint being
real whether or not the role may use it.

Postgres only. ClickHouse has no `has_column_privilege` equivalent and Trino
keeps access control outside SQL, so both go on answering exactly as before.

### Answers that were wrong and are now right

`SELECT *⌶` over a relation with one column withheld expanded to every column,
and accepting it wrote a statement the server refuses — table-level `SELECT`
implies every column, so withholding one means there is no table-level grant.
The expansion now names the columns that work and says how many it left out.

`WHERE d.password = ⌶` offered value literals for a column the role cannot read.
It offers nothing there now, from either source: the planner statistics that
would have leaked actual data, and the self-enumerating types — a boolean, an
enum — that leak none but whose comparison is refused all the same.

### For callers

`Suggestion` gains `availability` and `reason`. `MemoryCatalog` gains
`restricted=`, taking a list of columns per relation or `None` for a relation
with no grant at all. Over LSP a restricted item carries the `Deprecated` tag
and its reason in `detail`; the protocol has no disabled state and this does not
fake one, so a client will still insert what it is given.

`identity=` now does something. It has led the documented cache key since 0.1,
and this is the feature that gives it meaning: a cache shared between roles
without it serves one user's readable set to another.

## 0.5.0

### Wrong answers that are now right

`SELECT * FROM users FOR ⌶` offered `users`, and accepting wrote
`SELECT * FROM users FOR users`. `FOR` was not a clause, so the caret after it
was still read as inside `FROM`. The four locking forms are clauses now, and
that caret offers `UPDATE`, `NO KEY UPDATE`, `SHARE` and `KEY SHARE`.

`WINDOW ⌶` offered a column where a window name is being defined. It suggests
nothing now, and `WINDOW w AS (⌶` offers `PARTITION BY` and `ORDER BY`.

`FROM t TABLESAMPLE ⌶` offered `JOIN` and `WHERE`, `TABLESAMPLE … REPEATABLE (⌶`
offered relations, `WITH … CYCLE ⌶` offered `SELECT` and `INSERT INTO`, and
`FROM LATERAL (⌶` offered relations where a subquery belongs. All four are quiet
or correct now, three of them because the word became a clause at all.

A parenthesis that opens a list of names being defined offered relations or the
CTE body words. `WITH x (⌶` proposed `SELECT` and `VALUES` inside a column list,
`FROM t AS u (⌶` and `FROM f(1) AS t (⌶` proposed table names where a column is
being named, and `FROM ROWS FROM(⌶` read the construct as an ordinary `FROM`.

The first four are quiet now. They are told apart from the bodies and calls that
must go on answering — a CTE body, a function's arguments, `INSERT`'s column
list, `IN`'s values — by the word that introduced the paren, read from the
dialect's own `aliases_with` rather than matched against `AS`. `ROWS FROM(⌶`
offers a function, which is what the grammar puts there.

### Positions that had no answer

The `FETCH { FIRST | NEXT } … { ONLY | WITH TIES }` tail, at all four of its
carets. `OFFSET n ⌶` takes `ROW` and `ROWS`. `ORDER BY id ⌶` offers `USING`.
`RIGHT JOIN` and `FULL JOIN` join the join list, everywhere a join is offered.
`FROM t ⌶` offers `TABLESAMPLE`, and `WITH … SEARCH ⌶` offers `BREADTH` and
`DEPTH`.

Behind a prefix, where the engine puts words that would otherwise crowd out a
column: `SELECT al⌶` → `ALL`; `GROUP BY rol⌶` → `ROLLUP`, with `CUBE`,
`GROUPING SETS`, `ALL` and `DISTINCT`; `LIMIT al⌶` → `ALL`.

### A bug that had hidden all of those

`before_the_item` — the mechanism that puts a word behind a prefix — did nothing
for any clause that was not the first in its statement. `at_the_clause_start`
compared the whole run of words before the caret to the clause name, and that
run does not stop at a clause boundary, so `GROUP BY rol` compared
`('USERS', 'GROUP', 'BY')` and failed. `DISTINCT` worked, and only because
`SELECT` comes first, which is why nothing had noticed.

A dialect that declared `before_the_item` on any other clause was silently
getting nothing.

### Four things tried and withdrawn

Each is a position the grammar names, reachable, and refused because reaching it
cost more elsewhere. All four keep a case in `tests/grammar/` recording the
reason.

- **`UNION DISTINCT`.** `_half_written_clauses` treats every `followed_by` entry
  as a phrase and skips a head that is already one, so naming `DISTINCT` there
  made `SELECT DISTINCT ⌶` stop offering `ON`. `DISTINCT ON` is a feature people
  write; `UNION DISTINCT` is a default spelled out.
- **`TABLE t`.** A statement form is found by the first word that starts one,
  and `TABLE` is a word inside `CREATE TABLE` — so modelling it made
  `CREATE TABLE t (id ⌶` offer relations in a column definition list. It waits
  on `CREATE TABLE`.
- **`USING (…) AS join_using_alias`.** Both spellings are dropped by the
  alias-spending machinery before the caret renders.
- **`WITH ORDINALITY`.** It applies to a function item, and `followed_by` is per
  clause rather than per item kind, so offering it after `generate_series(…) ⌶`
  would also offer it after `FROM users ⌶`, where the server refuses it.

### Nothing changes at a caret

A conformance suite for the official PostgreSQL `SELECT` grammar, in
`tests/grammar/`. The synopsis is stored verbatim and every case cites the line
it comes from, so the suite can be checked against the document rather than
against memory of it; a test asserts that no line goes uncited.

Fifty-seven of sixty-nine positions are answered, and none of the twelve it still
records is a wrong answer — every one of them is a caret that stays silent. They
are listed in that file with a reason each: four withdrawn deliberately and
described above, three waiting on `CREATE TABLE` so the longer form wins the
match, and five needing a capability that does not exist — a `Kind` meaning "a
relation this statement already has", an operator outside a predicate clause,
`MATERIALIZED`, and the item-openers `resolve.py` filters out on purpose.

The conformance suite runs on more than Postgres. Thirty-eight of its cases
describe behaviour ClickHouse and Trino share, and a `dialects` field on each
case says so — the `FETCH` tail, `OFFSET`'s noise words, `RIGHT` and `FULL
JOIN`, `WINDOW`'s definition body, and the rule that a parenthesis naming
columns answers nothing.

All five of those were added to the shared baseline in this release and reached
those two backends with nothing asserting them there. The marking is measured
rather than assumed: every case naming a dialect was run against it first.

Three cases name Trino and not ClickHouse, which declares no `TABLESAMPLE`, and
eighteen name Postgres alone because the productions are Postgres's — `LATERAL`,
`ROWS FROM`, the `FOR UPDATE` family, the grouping words, `DISTINCT ON`,
`LIMIT ALL`, `SEARCH` and `CYCLE`, `ORDER BY … USING`.

The test summary also stopped lying about the ported report_service suite. That
line counted passes under `tests/reference/`, which is not a path here, against
*every* xfail in the run — so it read `0/37 passing` the moment another suite
had pending cases. Both halves are scoped to `tests/queries/` now, and it reads
`158/158 passing, 0 known gaps`.

### The extension carries its own Python

There is no interpreter to install and no setting pointing at one. Each build
ships a stripped CPython 3.13 with the language server already inside it,
unpacked once into the extension's storage the first time a `.sql` file is
opened.

This closes a failure that had no graceful answer before: an interpreter that is
present, new enough, and still unable to build a virtual environment. Debian
unbundles `ensurepip` from `python3.13`, so `python3 -m venv` fails there with a
message about `apt install python3.13-venv` — and PEP 668, the Windows Store
stub and a shadowing conda environment each fail differently in the same place.
The extension no longer asks.

`pysqlsuggestions.pythonPath` is **removed**. Keeping it would mean keeping
interpreter discovery, virtual-environment creation and the whole matrix of
environment failures alive on the machines that set it — and almost nobody
would, so that code would go untested until it broke for someone.

The download is now per platform: nine builds rather than one, between 17 MB
(win32-arm64) and 34 MB (linux-x64). The marketplace picks the right one;
installing a `.vsix` by hand means picking the one matching your OS and
architecture.

### ClickHouse and Trino answer from a catalog in the editor

Both read one now, so `FROM ⌶`, `db.⌶` and `alias.⌶` offer real relations and
columns against either backend instead of keywords alone. Neither declares
foreign keys, so join proposals stay Postgres-only; Trino ships no
relation-search query, so a bare prefix still finds nothing there. Both were
already true for library users and are now reachable from the extension.

The readers are the library's own, over each backend's HTTP interface —
`catalogs/clickhouse_http.py` and `catalogs/trino_http.py`, stdlib only. Their
official clients hard-require lz4, orjson, zstandard or a C extension, every one
of them to compress a wire carrying seven introspection queries against a cache
that is warm for the rest of a session. The clients remain supported: a caller
holding a `trino` connection still passes its cursor to `DbapiCatalog`.

A connection can now say `secure` to speak TLS. Trino refuses password
authentication without it, and the reader says so at connect time rather than
sending the password to find out.

`verify` turns certificate checking off for one connection, for the case it
exists to serve: a self-signed certificate on an internal server. It is off only
when set to exactly `false` — a missing or malformed value leaves checking on,
which is the opposite of how every other field in a profile behaves and is
deliberate. Turning it off stops the hostname being checked too, because a
self-signed certificate rarely names the host it is reached by and half-checking
would fail on exactly those endpoints while reading as though something were
still being verified.

A ClickHouse query that fails part-way through a large result is no longer
silently truncated. ClickHouse flushes headers before it knows a query will
succeed, so such a failure arrives as HTTP 200 carrying the rows already sent
plus an `exception`; the reader returned those rows as a complete answer. It now
checks for the exception on every response, before the status, and reports the
server's sentence rather than the JSON around it.

## 0.4.1

A packaging fix. Nothing about what the engine offers at a caret changed.

`pysqlsuggestions-lsp` 0.4.0 declared `pysqlsuggestions==0.2.1`, so installing
the server from PyPI pulled a library two releases behind the one it was built
against. It also reported `0.2.1` as its own version over LSP, which is the
number a bug report quotes.

Both now have tests holding them to the library's, beside the three version
guards that were already there. Neither had one, for the same reason in two
shapes: a number whose only reader is remote is a number nothing local notices
going wrong. The pin is read by pip and never by a checkout, which resolves the
library through the uv workspace; `__version__` is read by a client over the
wire, and the guard that looked like it covered it compares two manifests.

## 0.4.0

Three carets that answered wrongly or not at all, and a server that stopped
answering slowly.

The language server ran its completion handler on the event loop, so a slow
introspection query stopped it serving anything — including the client's own
cancellation of the request that was stuck. `WITH` answered nothing at any of
its five positions. And `DROP TABLE` offered views, which the server refuses.

`Clause` gains `opens_a_group` and `relation_kinds`, both with defaults: a
dialect declaring neither behaves exactly as it did.

### A clause says which relations it means

`DROP TABLE ⌶` used to offer views. `DROP TABLE public.reports_active` is
refused — `"reports_active" is not a table` — so that was a wrong answer, and it
is the reason this landed rather than staying a nicety.

`DROP VIEW ⌶`, `DROP MATERIALIZED VIEW ⌶` and `DROP INDEX ⌶` now offer what they
mean. Indexes reach the catalog for the first time, and reach no other position:
`SELECT * FROM an_index` is `cannot open relation`, and there are more indexes
than tables in an ordinary schema — 31 against 19 in the fixture this library
develops against.

`Clause` gains `relation_kinds`. It is a positive list, so it is only true where
the vocabulary is known — `DROP TABLE`'s narrowing is declared for Postgres,
which wrote its own `relkind` mapping, while ClickHouse reports storage engine
names and keeps the unnarrowed clause. `DROP VIEW` is the one that reaches the
baseline: all three backends have the statement and all three spell that kind
`view`.

**`FROM ⌶` is unchanged**, which is most of the work: a view is queryable and
still belongs there, a sequence and an index are not and still do not.

### `WITH` answers where it never did

`WITH a AS (⌶` offers the statements a CTE body may contain — `SELECT`,
`VALUES`, a nested `WITH`, and on Postgres the data-modifying forms, all
verified against the server. `WITH a AS (…) ⌶` offers the statement the CTE
feeds. `WITH a ⌶` offers `AS`, and `WITH rec⌶` offers `RECURSIVE`.

Every one of those positions answered nothing before: the clause declared no
`suggests`, no `followed_by`, and nothing declared it `follows`, so its
continuations were empty and each caret fell through.

`WITH ⌶` and `WITH a AS (…), ⌶` still answer nothing, which is right — a CTE
name is the author's to invent.

`Clause` gains `opens_a_group`, the words that may begin a clause's
parenthesised body. A dialect needs it to describe a CTE: what belongs inside
the group and what belongs after it are different lists, and a nested `WITH` is
the case that proves it. A clause that declares it also has a mandatory alias
word — `AS` is what introduces the group — so nothing else is offered until it
is written.

ClickHouse keeps the conservative body list, because it refuses a data-modifying
CTE outright.

`WITH a AS (…) VALUES (1)` plans and is still not offered: `VALUES` declares
itself part of `INSERT INTO`, and the statement form at that caret is `WITH`, so
the clause model filters it out. Reaching it would mean widening `INSERT INTO`'s
model for a caret almost nobody types.

### A slow database no longer freezes the editor

The language server ran its completion handler on the event loop, so a slow
introspection query — a database behind a VPN, a cold connection — stopped the
server answering anything at all until it returned. Including the client's own
cancellation of the request that was stuck.

The handler now runs in pygls's thread pool. Nothing became asynchronous: the
`Catalog` port is synchronous by design, and pre-fetching into a
`MemoryCatalog` is still the bridge for callers who need otherwise.

Concurrency that the server never had before is now possible, so the state
behind it is locked: two carets arriving together used to be able to open two
connections and leak one, and to announce a degraded catalog twice. One caret at
a time reaches the database — which costs nothing, since completions are
latest-wins and the cache makes the second read instant.

## 0.3.0

Two new surfaces, and six changes to what the engine answers.

The surfaces are a language server and a VS Code extension, so the library is
usable now without writing an editor integration first.

The rest is one theme seen from six positions: a caret that used to answer with
something plausible and wrong now answers correctly, or not at all. A caret
inside `:name` no longer offers column names. A statement form the engine does
not model no longer proposes `SELECT`. A procedure is not offered where the
server refuses one, a sequence is not offered a `FROM` list, and a column
reference is no longer written in a form the server calls ambiguous.

**Breaking, for callers constructing these types by hand.**
`Candidate.qualifier` is `tuple[str, ...]` rather than `str | None`;
`Function.result` may be `None`; `Function` carries a `kind`. `Suggestion`, the
`Catalog` protocol and every capability protocol are unchanged.

### A column reference that resolves

Two relations with the same name in different schemas can both be in scope —
`FROM public.invoices, billing.invoices` is legal, and Postgres aliases the
second internally. Every column reference the engine wrote for them was
`invoices.amount`, which the server refuses: `table reference "invoices" is
ambiguous`. Each now carries its relation's whole path.

`SELECT *` over the two used to expand to `invoices.amount, invoices.id,
invoices.amount, invoices.period` — ambiguous, and naming `amount` twice.

Before any `FROM` exists, a column that several schemas have is now offered once
per schema instead of once in total. Previously the others were unreachable at
that caret however much you typed, because ranking dedupes on the text to be
inserted and all of them rendered alike. In a database with a schema per tenant
this makes that list longer; the schema on the search path sorts first.

**Nothing changes without a collision.** A single-schema database gets exactly
what it got before, and that is asserted rather than assumed.

`Candidate.qualifier` is now `tuple[str, ...]` rather than `str | None` — a path
is not a name, and a dotted string in the old field would have been quoted as
one name containing a dot. Only callers constructing a `Candidate` by hand are
affected; `Suggestion` is unchanged, since the qualifier is already part of its
`text`.

### Procedures and sequences

`CALL ⌶` offers procedures. `SELECT ⌶` does not — a procedure in an expression
is refused by the server, so this is a wrong answer kept out rather than a
missing one added. `CALL billing.⌶` still means a procedure, where the namespace
rule would have answered with columns and tables.

`nextval('⌶`, `currval('⌶` and `setval('⌶` offer sequences, written into the
literal with their identifier quotes intact —
`nextval('billing."MonthlyTotals_id_seq"')`, because the server parses that
string as a `regclass` and refuses the bare spelling. Which functions name a
sequence is dialect data, so a dialect can declare its own.

`DROP SEQUENCE ⌶` and `ALTER SEQUENCE ⌶` offer sequences, and `DROP ⌶` now
answers `TABLE` and `SEQUENCE`.

**`SELECT ⌶` and `FROM ⌶` are unchanged**, which is the point of most of the
work: sequences reach the catalog now, and a schema has one per serial column.

`Function` carries a `kind` — function, aggregate, window or procedure — and a
`result` that may be `None`. ClickHouse used to report `count() -> aggregate`,
a kind in the return-type field for want of anywhere else; it now reports
`count()  aggregate` and no return type, which is the truth about what
`system.functions` knows. Postgres marks its aggregates and window functions
for the first time.

ClickHouse no longer offers `CALL`, which its parser rejects.

### Statements that are not queries

`DROP TABLE ⌶` used to offer `SELECT`, `WITH` and `INSERT INTO` — the words a
statement may *begin* with, inside a statement that had already begun. Accepting
one wrote `DROP TABLE SELECT`.

`DROP TABLE`, `TRUNCATE` and `ALTER TABLE` now offer relations, and are offered
themselves where a statement may begin. `DROP ⌶` offers `TABLE`. `EXPLAIN` takes
the statements a planner accepts — not `DROP`, which is a syntax error.

**Every other unrecognised form now answers with nothing.** `GRANT`, `VACUUM`,
`COMMENT`, `SET`, `BEGIN` and anything a third-party dialect has not modelled
are silent where they used to propose `SELECT`. (`CALL` was on this list and is
modelled now — see *Procedures and sequences* above.) A half-typed keyword is
not an unrecognised form: `SELEC⌶` still completes to `SELECT`, and so do an
empty editor, the position after a `;`, and the position after a comment.

`DROP VIEW` and `DROP INDEX` were among the silent ones when this shipped, and
are not any longer — see *A clause says which relations it means* above. The
choice that was waiting for a second consumer got one, and ClickHouse settled
it: reporting storage engines rather than relational categories is what makes a
positive kind list dialect-local rather than universal.

`ALTER TABLE` offers `ADD COLUMN` and `RENAME TO` and stops there. A bare `DROP`
among its continuations would make `DROP ⌶` stop answering `TABLE`, for the same
reason `ON ⌶` does not answer `CONFLICT` alone.

### A name is found wherever it lives, not only where the search path looks

`FROM invo⌶` found nothing when `invoices` lived in a schema the connection does
not default to. It now finds it and writes `billing.invoices`. Matching still
runs against the bare name, so typing `invo` — or `voic` — reaches it; the schema
is about what gets inserted, not what you have to type.

A relation you can write bare ranks above one that needs a schema prefix, by a
margin small enough that a better name match still wins.

The same gap had a second half nobody had noticed. `SELECT amou⌶` was equally
blind, because the column-search query filtered on visibility too — and the
`FROM` clause a searched column wrote dropped its schema, so lifting that filter
alone would have produced `FROM invoices`, which the server refuses. Both are
fixed: `SELECT amou⌶` now writes `SELECT invoices.amount FROM billing.invoices`.

Optional, and per backend, because the cost is what decides it:

| backend | ships it | measured against the docker fixture |
| --- | --- | --- |
| PostgreSQL | yes | 0.4–2.3 ms over 228 relations |
| ClickHouse | yes | 1.8–4.2 ms, and it reaches another database |
| Trino | no | 179 ms for *one* catalog's `information_schema` |

An empty prefix searches nothing: `FROM ⌶` is not a request for every relation
in the database.

**A limitation this entry recorded is now fixed.** It said two columns with the
same name, in same-named tables, in different schemas "still collapse to a
single suggestion", and that telling them apart "needs a qualifier that can hold
a path rather than a name". That qualifier exists — see *A column reference that
resolves* above.

It also understated the fault. The collapse was the visible half; the invisible
half was that the surviving suggestion is itself refused once both relations are
in scope, so the position was writing SQL that does not run rather than merely
offering one answer where two were due.

### `SELECT *` expands to the columns it stands for

Put the caret directly on a star and the top suggestion is the column list,
accepted in one go. One space further along is still the position that wants
`FROM`, and it still answers with `FROM`.

A bare star expands **qualified** as soon as more than one relation is in scope.
Two relations in a join very often share `id`, and the unqualified list is a
statement Postgres refuses with `column reference "id" is ambiguous`. One
relation expands bare. A star the author qualified — `u.*` — stays qualified
however few relations it covers, because the edit replaces the `u.` too.

Nothing is capped. A forty-column relation expands to forty columns, which is
what somebody who asked to expand a star asked for.

`Kind.EXPANSION` is new, so a front end colouring by kind should give it a
colour; `lsp/` reports it as a snippet. Reserved and mixed-case names are quoted
inside the list, so a column called `user` arrives as `d."user"`.

### Bound parameters are no longer read as column names

`WHERE id = :us⌶` used to propose `users` — or any column starting `us` — and
accepting one wrote valid SQL that ran a different query. The lexer now has a
token for a parameter. A caret inside one suggests nothing, and a caret past one
reads it as a finished operand, so `WHERE id = ? ⌶` offers `AND` rather than a
second column.

Spelled per dialect on `Syntax.placeholders`:

| dialect | spellings |
| --- | --- |
| ANSI | `?`, `:name` |
| PostgreSQL | `$1`, `:name` |
| Trino | `?` |
| ClickHouse | `{name:Type}` |

**PostgreSQL deliberately does not treat `?` as a parameter.** It is the JSONB
existence operator, and `data ? 'key'` is a predicate people write.

`${var}` is a templating convention rather than any backend's syntax, so it
ships as `TEMPLATE_PLACEHOLDER` wired into no dialect. A caller whose SQL is
templated composes it in:

```python
from dataclasses import replace
from pysqlsuggestions.dialects.base import TEMPLATE_PLACEHOLDER
from pysqlsuggestions.dialects.postgres import POSTGRES

syntax = replace(POSTGRES.syntax, placeholders=(*POSTGRES.syntax.placeholders, TEMPLATE_PLACEHOLDER))
DIALECT = replace(POSTGRES, syntax=syntax)
```

Bound parameter *names* are still not offered inside a placeholder. That needs
the caller to supply the binding, and it is a feature of its own.

### A VS Code extension

`editors/vscode/` drives the language server from an editor. It builds its own
Python environment from wheels shipped inside the VSIX — no network, and the
project's own environment is never touched — and needs Python 3.10+ on PATH.

PostgreSQL only, for anything that reads a schema. The other backends' drivers
are not pure Python, so bundling them would mean one build per operating system;
their dialects still select, and still bring the right keywords and quoting.

- **Connections are managed from a view**, not by editing JSON: add, edit,
  remove, set and clear a password, choose which one is in use.

- **A connection can be asked whether it works, and answers in words.** Every
  kind of failure looks identical from an editor — completion simply stops
  being schema-aware — so the message is the feature. A missing password says
  so; pg8000's own answer is `'NoneType' object has no attribute 'decode'`,
  which sent this project's author debugging in the wrong direction. A rejected
  password, a database that is not there and a port with nothing behind it are
  each named distinctly.

- **Health and use are shown separately.** The icon is the last test result;
  the label says which connection the server holds. The one in use may be the
  broken one, and that is the case most worth seeing.

- **Verdicts are never remembered across sessions.** A tick from last week is a
  claim nobody checked today.

- **Passwords have nowhere to live but secret storage.** The settings schema has
  no field for one, a test asserts it stays that way, and removing a connection
  removes its password — an orphan would be inherited by the next connection
  reusing that name.

### A language server

The engine now speaks LSP, so an editor can drive it. `pysqlsuggestions-lsp` is
a second distribution in `lsp/` rather than a module in `src/`: a server needs
pygls and a driver, and the library's promise is that importing it pulls in
neither. Two tests hold that line — the versions must agree, and `src/` may not
name the server package.

The library itself is unchanged. Nothing was added to it, renamed in it, or
removed from it.

- **Completion at a caret, over stdio.** The connection profile arrives in
  `initializationOptions`; without one the server completes from the statement
  alone, which is the library's documented degraded mode rather than an error.

- **A completion request never fails.** An unreachable database, a rejected
  password or a dialect with no driver all fall back to that same mode. The
  failure is recorded rather than retried, because retrying means a blocking
  connection attempt for every character typed.

- **The database is not contacted until the first completion.** Opening a
  document opens no socket, so a backend that is down costs a completion rather
  than a hung editor.

- **The engine's ranking survives the trip.** Items carry `sortText`, since a
  client re-sorts by its own fuzzy score otherwise, and `filterText` set to the
  term the engine matched — the column name, so `usern` still finds
  `u.username`. Items carry a `textEdit` with an explicit range and never an
  `insertText`: re-deriving a word boundary is what drops a qualifier.

- **`plan_insertion`'s second edit reaches the editor.** A column accepted
  before any FROM exists writes the clause it needs as an `additionalTextEdit`,
  and a suggestion carrying template blanks — a statement shape, `Kind.SNIPPET`
  — becomes a snippet placeholder. A join proposal carries none: it inserts a
  finished clause, alias and condition included.

- **Statements are cut at semicolon tokens, not characters.** Scope comes from
  the whole statement, and a semicolon inside a literal, a comment or a quoted
  identifier is not a boundary. The dialect's own lexer decides.

- **A `pg8000` extra.** Pure Python, so the wheels an editor extension bundles
  are platform-independent. psycopg2 remains the documented choice for library
  users; this only governs what a bundle carries. ClickHouse is consequently a
  dialect the library serves and the server does not, its driver not being pure
  Python — the dialect still resolves, so keywords and quoting are right, and
  only the catalog is absent.

## 0.2.1

The library is unchanged — `src/` is byte-identical to 0.2.0. This release exists
to publish the demo, which is what a `v*` tag does.

### Demo

- **The boot shows how far along it is.** A cold visit spent 42 of its 44
  seconds on one unmoving `loading Python…`, 40 of them the wasm transferring.
  That is indistinguishable from a hang, and it is the first thing this project
  shows anyone. The runtime is now read through a streaming counter before
  `loadPyodide`, which then finds it in cache rather than fetching it twice, and
  the page draws a bar against a total the build injects.

  A percentage rather than megabytes: a `fetch` stream yields decoded bytes
  while the wire moves compressed ones — 8.25 MiB against 2.73 MiB for the wasm
  — so no byte counter on that page can honestly report how much has arrived.

- **`starting Python…` is a new phase.** Compiling the wasm and starting the
  interpreter take about two seconds during which the old message sat unchanged,
  past the point where the download had plainly finished. That was most of why
  the boot read as stuck.

- The bar disappears at 100% rather than sitting full, since a stalled full bar
  reads as the very hang this removes. If the stream is unsupported or a fetch
  refuses, the page boots exactly as it did before, without a bar.

## 0.2.0

Joins. `JOIN ⌶` answers with the whole clause and `ON ⌶` with the whole
condition, both read from the foreign keys the database already declares.

Nothing was removed or renamed. The additions are new fields with defaults and a
new `Kind` member — worth knowing if you exhaustively match on `Kind`, since
`kind` is what consumers serialise into an editor payload.

### Positions that now answer differently

- **`JOIN ⌶` offers whole clauses.** `FROM booking b JOIN ⌶` proposes
  `flight f ON b.flight_id = f.id` — relation, alias and condition in one
  accept — ahead of the relation names it used to list alone. Each proposal is
  annotated with the constraint it came from.
- **`ON ⌶` offers the whole condition.** `JOIN auth_user u ON ⌶` proposes
  `r.author_id = u.id` rather than leaving the comparison to be typed. The
  columns stay underneath, for a condition the constraints do not describe.
- **`ON r.⌶` ranks that relation's foreign key columns up**, annotated. A
  qualifier has committed the left side, so a whole condition is no longer
  expressible there.

Proposals fire from **both ends** of a constraint, because a constraint is
directed and a join is not: a query starting at `auth_user` — which holds no
foreign key columns and is referenced by seven tables in the test fixture — is
offered the relations that reference *it*. Many-to-one ranks above one-to-many,
being both more often wanted and unable to multiply the result set. Two
constraints to the same relation stay two proposals; choosing between them is
the caller's.

**Postgres only, and deliberately.** ClickHouse and Trino declare no
constraints, so both positions there behave exactly as before. The obvious
fallback — matching `<singular>_id` against `<table>.id` — is rejected rather
than unbuilt: it is right often enough to be inviting and wrong often enough to
matter, and a wrong join condition is valid SQL that silently returns the wrong
rows. No parser catches that, and neither does the person reading the result.

### Added

- `ForeignKey` — one declared relationship, with column tuples on both sides, so
  a composite key needs no special case and renders as an `AND` chain.
- `SupportsForeignKeys` — the capability behind the two positions above. Absent,
  they answer as they did before. A backend that keeps no constraints should not
  implement it rather than guess.
- `Kind.JOIN` — a candidate that is a whole clause or condition rather than a
  name, so a front end can render it distinctly.
- `Suggestion.note` — why a suggestion outranks its neighbours, as
  `fk: auth_user.id`. Distinct from `detail`, which says what the thing is.
- `Candidate.match_text` — what matching runs against when that is neither the
  text nor the label. A join proposal is hunted for by the relation name and
  inserts a whole clause; without a field of its own the two collided and the
  list showed a bare name.
- `MemoryCatalog(foreign_keys=...)` — declare relationships in a snapshot, which
  is what makes the two positions testable without a database.
- `pysqlsuggestions.testing.DialectConformance` — the shared corpus every
  dialect must pass, specified for 0.1 and not built until now. It reads a
  dialect's declarations for mistakes that can only ever do nothing (a
  lowercase clause name, a `follows` naming a clause that is absent), then puts
  it the propositions every caller assumes: an alias reaches its columns, a
  dotted path narrows one level per segment, a quoted name is the same name,
  both sides of a join are in scope. The SQL is spelled from what each dialect
  says about its own namespace and quoting, so a three-level dialect is asked
  about three levels.

  Shipped rather than kept in `tests/`, so anyone publishing a dialect can hold
  it to the same standard.

- `pysqlsuggestions.dialects.registry` — `available()` and `named()`, which
  read the `pysqlsuggestions.dialects` entry-point group. The group has been
  advertised in `pyproject.toml` since 0.1.0 and nothing read it, so a
  third-party dialect could register correctly and never be found.

### Demo

- **The published page reaches nothing.** Pyodide is carried in the site rather
  than fetched from a CDN, pinned by digest, and the build refuses to assemble a
  page whose files name any absolute URL. That costs 11.7 MiB against a demo
  payload of 135 kB and buys a page that works on an air-gapped laptop and
  cannot be broken by somebody else's outage — which is the claim the demo
  exists to make. It had already failed the other way: a load where `micropip`
  could not be fetched left the page booted with a dead editor and nothing a
  visitor could act on.
- `micropip` is gone with it. It was loaded only to install one pure-Python
  wheel with no dependencies, which `unpackArchive` does in three lines.
- The demo schema declares its foreign keys, including two from one relation to
  the same target and two that cross a schema boundary.

## 0.1.1

Every change is a fix. Nothing was removed and nothing renamed; the additions
are new fields with defaults.

### Suggestions that were wrong

Each of these produced SQL the server rejects, and each is now checked against a
real Postgres at every caret in a corpus of statements — see *Testing* below.

- **A relation that is already written no longer offers another.** The blank
  line under a finished query answered with every relation in the schema, and
  with the catalog list on a three-level dialect. A comma or a JOIN has to come
  between two relations.
- **`AS` is spent once it has been used.** `FROM flight_raw AS fr ` offered `AS`
  again, at the top of the list. The same in a select list: `SELECT id AS x `.
- **An alias is offered for the relation it would attach to**, rather than for
  the last one still lacking a name — `FROM a JOIN b AS y ` proposed a name for
  `a`, which would have landed after `y`.
- **A clause name stopped between its two words takes only the rest of itself.**
  `GROUP ` offered every relation in the schema; `ORDER `, `INSERT `, `DELETE `
  and `LEFT ` were the same. Derived from the clause model, so a dialect adding
  `ARRAY JOIN` or `DISTINCT ON` is covered without further declaration.
- **Clauses that shape a result set are offered only where there is one.**
  A finished `UPDATE ... WHERE id = 2` offered `GROUP BY`, `HAVING`, `WINDOW`,
  `ORDER BY`, `LIMIT`, `OFFSET`, `FETCH` and the three set operators. A query
  nested inside an INSERT or a CTE still gets them.
- **`LATERAL` and Trino's `UNNEST` are offered where a reference begins**, not
  after one: `JOIN t AS u LATERAL` parses as nothing.
- **`LIMIT ` takes a number** and no longer fills that position with `OFFSET`.
  `LIMIT` and `FETCH` are two spellings of one limit, so writing either settles
  both.
- **`DISTINCT` is offered before the select list rather than after an item**,
  and behind a typed prefix. It was offered at `SELECT * ` and `SELECT x AS n `,
  where it is a syntax error, and `SELECT dis` found nothing at all.
- **`UPDATE` and `INSERT INTO` offer what actually follows them** — `SET` and
  the rows respectively. `FROM`, `WHERE`, `RETURNING` and `ON CONFLICT` come
  after those, not after the table being named.
- **An operator position offers operators.** `UPDATE t SET total ` answered with
  the reserved word list — `AS`, `BY`, `DO`, `IN`, `IS`, `ON` — where only `=`
  belongs.
- **A star takes no alias** (`SELECT * AS x` is a syntax error), while
  `count(*) ` may still be aliased.
- **A cast offers its own keyword.** `SELECT cast(total ` offered `FROM` and
  `GROUP BY`; only `AS` can follow the value.
- **An enum column that also has statistics listed every value twice**, once
  named by the type and once measured by the planner.
- **A word the clause model can suggest is a word the analyser recognises.**
  Twelve were missing, including `UPDATE`, `INSERT`, `DELETE` and `SET`, so
  writing one looked like finishing an operand: `UPDATE ` offered the clauses
  that follow a relation instead of the relation.

### Insertion

- **Accepting a namespace keeps the completion list open.** Choosing a schema,
  database or catalog left the caret past the dot with nothing offered, so the
  next level had to be triggered by hand.
- **Finishing a statement template leaves the caret past the end of it.** The
  blank filled last is the select list, in the middle of the statement, so a
  completed template stranded the caret inside a finished clause.

### Added

- `Insertion.expects_more` — whether the caret was left where completion should
  carry straight on. Not inferable from the caret and the edits, which is how a
  front end got it backwards for every namespace whose dot it had to write.
- `Clause.before_the_item`, `Clause.opens_an_item`, `Clause.aliases_with` — what
  stands between a clause and its first item, what may only begin an item, and
  the word that gives a relation its alias.
- A dialect folds its own clause vocabulary into `keywords` at construction, so
  the set the analyser consults cannot fall behind the words the model offers.

### Demo

- The browser build dropped the outstanding template blanks crossing into
  Pyodide, so choosing a table in the template left the caret where it was and
  closed the list. The request now crosses whole, as the server receives it.
- A Trino catalog is labelled `catalog`, a ClickHouse database `database`. The
  engine has one kind for every level of a dotted path because they behave
  identically; the word for it belongs to the dialect.

### Testing

Two harnesses, both of which found defects listed above and neither of which
existed before:

- `tests/integration/test_acceptance.py` accepts every suggestion at every caret
  across thirteen statements and asks Postgres to parse the result, telling a
  misplaced token from an unfinished statement by the error position. 1173 of
  1173 now land valid SQL, from 1144 of 1286.
- `tests/test_writable.py` walks realistic statements offline and asks whether
  anything offered continues them. 55 of 55 carets, from 49.

## 0.1.0

First release. Context-aware, schema-aware SQL completion as a library: lex,
analyse, derive a request, resolve it against a catalog, rank. Postgres deep;
ClickHouse and Trino ship as dialect data. No runtime dependencies.
