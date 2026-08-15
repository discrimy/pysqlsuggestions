"""Plain relation references, qualifiers, and the clauses that introduce them."""

from __future__ import annotations

from pysqlsuggestions.api import complete
from pysqlsuggestions.catalogs.memory import MemoryCatalog
from pysqlsuggestions.dialects.postgres import POSTGRES
from tests.queries.harness import ALL_ORDER_COLUMNS, USER_COLUMNS, kinds, texts


def test_plain_alias_columns(cur: MemoryCatalog) -> None:
    """
    The simplest thing the engine does, and the one everything else is measured
    against: an alias resolves to its relation and to nothing else.
    """
    assert sorted(texts(cur, 'select * from auth_user u where u.')) == sorted(USER_COLUMNS)


def test_plain_table_name_qualifier(cur: MemoryCatalog) -> None:
    """
    A relation with no alias answers to its own name. Requiring an alias would
    leave the commonest short query with no qualified completion at all.
    """
    sql = 'select * from auth_user where auth_user.'
    assert sorted(texts(cur, sql)) == sorted(USER_COLUMNS)


def test_plain_unqualified_columns(cur: MemoryCatalog) -> None:
    """
    With no dot typed, the relation's columns are still what belongs — qualified,
    because a bare name stops being unambiguous the moment a join is added.
    """
    assert sorted(texts(cur, 'select * from auth_group where ')) == ['auth_group.id', 'auth_group.name']


def test_join_brings_both_relations(cur: MemoryCatalog) -> None:
    """
    Both sides of a join are in scope at once, which is what makes the qualifier
    necessary: two relations here both have `id`.
    """
    sql = 'select * from auth_user u join orders o on o.user_id = u.id where '
    got = texts(cur, sql, limit=50)
    assert 'u.username' in got
    assert 'o.total' in got


def test_schema_qualified_table_columns(cur: MemoryCatalog) -> None:
    """
    `billing.invoices` is one reference of two segments, and `invoices.` resolves
    against it. Matching a qualifier against only the last path segment is easy
    to get wrong in the other direction — by not matching at all.
    """
    sql = 'select * from billing.invoices where invoices.'
    assert sorted(texts(cur, sql)) == ['amount', 'id', 'order_id']


def test_from_offers_tables(cur: MemoryCatalog) -> None:
    """
    A relation position offers relations. Obvious, and the thing most likely to
    be broken silently by a change to clause detection.
    """
    assert 'auth_user' in texts(cur, 'select * from ')


def test_insert_column_list_uses_the_target_table(cur: MemoryCatalog) -> None:
    """
    The parenthesis after `INSERT INTO orders` opens a column list, not an
    argument list and not another relation. A leading WITH makes it harder: the
    CTE body's own parenthesis is at the same depth.
    """
    sql = 'WITH a AS (SELECT id FROM auth_user)\nINSERT INTO orders ('
    assert sorted(texts(cur, sql, limit=50)) == ['orders.created', 'orders.id', 'orders.total', 'orders.user_id']


def test_delete_using_relation_is_in_scope(cur: MemoryCatalog) -> None:
    """
    `DELETE ... USING` introduces a relation exactly as a join does. It shares a
    keyword with the join's column list, and only the parenthesis after it tells
    the two apart.
    """
    sql = 'DELETE FROM orders o USING auth_user u WHERE u.'
    assert sorted(texts(cur, sql)) == sorted(USER_COLUMNS)


def test_join_using_column_list_is_not_a_relation(cur: MemoryCatalog) -> None:
    """
    The other reading of the same word. Treating `(id)` as a relation list loses
    `orders` and answers `o.` with nothing.
    """
    sql = 'SELECT * FROM auth_user u JOIN orders o USING (id) WHERE o.'
    assert sorted(texts(cur, sql)) == ALL_ORDER_COLUMNS


def test_natural_join_keeps_both_relations(cur: MemoryCatalog) -> None:
    """
    A NATURAL JOIN has no ON and no USING, so nothing follows the relation to
    anchor the parse. The join qualifier has to be skipped rather than read as a
    relation of its own.
    """
    sql = 'SELECT * FROM auth_user u NATURAL JOIN orders o WHERE o.'
    assert sorted(texts(cur, sql)) == ALL_ORDER_COLUMNS


def test_a_finished_relation_takes_a_connective_not_another_relation(cur: MemoryCatalog) -> None:
    """
    The blank line under a written-out query is where a caret sits most often,
    and every relation in the catalog was the answer there.

    A relation clause offers relations where one may be named, and past a
    complete reference no second one can follow without a comma or a JOIN
    between them. What belongs is a word — and an alias, while the relation
    still lacks one, because that is the other thing that may be written there.
    """
    after = kinds(cur, 'select u.id from auth_user as u\n')
    assert 'auth_user' not in after, 'a second relation cannot simply follow the first'
    assert set(after.values()) == {'keyword'}

    unaliased = kinds(cur, 'select * from auth_user ')
    assert 'auth_user' not in unaliased
    assert 'alias' in unaliased.values(), 'the relation has no alias yet, so names for it belong'

    naming = kinds(cur, 'select * from ')
    assert naming['auth_user'] == 'table', 'and where a relation may be named, relations are offered'


def test_a_relation_that_has_an_alias_is_not_offered_another(cur: MemoryCatalog) -> None:
    """
    `AS` is spent once it has been used, and a second one parses as nothing.

    A clause's continuation list says what may follow it, not what is still
    unused, and the words already written cannot settle this the way they settle
    ASC against DESC: an item runs to the last comma, and joins are not
    comma-separated, so `FROM a AS x JOIN b` is one item holding an `AS` that
    belongs to a different relation. The relation has to answer instead — the
    most recent one, since that is what an alias would attach to.
    """
    assert 'as' in texts(cur, 'select * from auth_user ')
    assert 'as' not in texts(cur, 'select * from auth_user as u ')
    assert 'as' not in texts(cur, 'select * from auth_user u '), 'an alias needs no AS to be an alias'

    assert 'as' in texts(cur, 'select * from auth_user as u join orders '), 'the new relation has none'
    assert 'as' not in texts(cur, 'select * from auth_user join orders as o ')


def test_an_alias_is_offered_for_the_relation_it_would_attach_to(cur: MemoryCatalog) -> None:
    """
    The same rule, for the names rather than the keyword.

    Offering an alias for the last relation *lacking* one proposes it after
    whatever was actually written last: `FROM auth_user JOIN orders AS o` would
    take `au`, giving `orders AS o au`. Only the most recent relation can be
    named, and only while it is unnamed.
    """
    assert 'au' in texts(cur, 'select * from auth_user ')
    assert 'au' not in texts(cur, 'select * from auth_user join orders as o ')
    assert 'o' in texts(cur, 'select * from auth_user as u join orders ')


def test_a_select_item_that_has_an_alias_is_not_offered_another(cur: MemoryCatalog) -> None:
    """
    The same spending, settled the other way.

    Select items are comma-separated, so the words of the item answer it and no
    relation needs consulting — and the `AS` inside `CAST(x AS text)` sits at a
    deeper level than the item, which is what keeps it from counting.
    """
    assert 'as' in texts(cur, 'select u.id ')
    assert 'as' not in texts(cur, 'select u.id as x ')
    assert 'as' in texts(cur, 'select u.id as x, u.email '), 'the comma starts a fresh item'
    assert 'as' in texts(cur, 'select cast(u.id as text) '), "the cast's AS belongs to the cast"


def test_the_insert_target_is_not_in_scope_for_the_source_select() -> None:
    """
    Measured on all three backends, which agree for once.

    `INSERT INTO auth_group (id, name) SELECT id, name FROM auth_user` is
    `column "name" does not exist` on Postgres, `Column 'name' cannot be
    resolved` on Trino and `Missing columns: 'name'` on ClickHouse — and the
    qualified form is refused by all three too. The target is the thing being
    written to, not a relation the source query may read from.
    """
    catalog = MemoryCatalog(
        {
            ('public', 'users'): [('id', 'bigint'), ('name', 'text'), ('email', 'text')],
            ('public', 'orders'): [('id', 'bigint'), ('user_id', 'bigint'), ('total', 'numeric')],
        }
    )
    for tail in ('SELECT  FROM orders', 'SELECT id FROM orders WHERE '):
        sql = f'INSERT INTO users (id) {tail}'
        caret = sql.index('SELECT ') + 7 if tail.startswith('SELECT ') and 'WHERE' not in tail else len(sql)
        offered = [s.text for s in complete(sql, caret, POSTGRES, catalog, limit=20)]
        assert offered, tail
        assert not [text for text in offered if text.startswith('users.')], (tail, offered)


def test_returning_sees_the_target_and_not_the_source() -> None:
    """
    The mirror of the same rule, and the reason the target stays in scope at all.

    Postgres is the only one of the three with RETURNING — Trino and ClickHouse
    fail to parse the word — and it resolves only the row that was written:
    `RETURNING username` against a source called `auth_user` is `column
    "username" does not exist`, qualified or not.
    """
    catalog = MemoryCatalog(
        {
            ('public', 'users'): [('id', 'bigint'), ('name', 'text')],
            ('public', 'orders'): [('id', 'bigint'), ('total', 'numeric')],
        }
    )
    sql = 'INSERT INTO users (id) SELECT id FROM orders RETURNING '
    offered = [s.text for s in complete(sql, len(sql), POSTGRES, catalog, limit=20)]
    assert [text for text in offered if text.startswith('users.')]
    assert not [text for text in offered if text.startswith('orders.')], offered


def test_the_insert_column_list_still_names_the_target() -> None:
    """The position the target is in scope for, and the reason it stays there."""
    catalog = MemoryCatalog({('public', 'users'): [('id', 'bigint'), ('name', 'text')]})
    for sql in ('INSERT INTO users (', 'INSERT INTO users (id) VALUES (1) RETURNING '):
        offered = [s.text for s in complete(sql, len(sql), POSTGRES, catalog, limit=10)]
        assert [text for text in offered if 'id' in text], sql


def test_the_insert_column_list_names_only_the_target() -> None:
    """
    The commit that split an INSERT's three positions did not actually change
    this one — `[*written_to, *relations]` is target *and* source.

    `INSERT INTO auth_group (username)` is `column "username" of relation
    "auth_group" does not exist`; the list names columns of the table being
    written to, and nothing else. The earlier test used a statement with no
    source `SELECT`, which is the one shape where the two happen to agree.
    """
    catalog = MemoryCatalog(
        {
            ('public', 'groups'): [('id', 'bigint'), ('name', 'text')],
            ('public', 'users'): [('id', 'bigint'), ('username', 'text')],
        }
    )
    sql = 'INSERT INTO groups () SELECT id, username FROM users'
    offered = [s.text for s in complete(sql, sql.index('()') + 1, POSTGRES, catalog, limit=20)]
    assert offered, 'the target still answers'
    assert not [text for text in offered if 'username' in text], offered
