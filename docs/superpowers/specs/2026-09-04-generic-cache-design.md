# A cache port any store can implement — design

Date: 2026-09-04
Status: **specified**. Nothing built.

`Cache` is the one port in this library that only ever had a local
implementation. This makes it something an external store can satisfy, and
ships one for redis.

---

## 1. Context

`ports.Cache` today is two methods, `get` and `__setitem__`, and its docstring
opens by saying a plain dict satisfies it. That has been true of every
implementation there has ever been: `lsp/server.py:117` holds one dict per
session, `demo/app.py:128` holds one per backend, `demo/browser.py:64` one per
catalog. Each of those is private to a process, and most of the design rests on
that privacy without saying so.

Three things follow from it, and all three are what an external store breaks.

### 1.1 The key is a Python object

`resolve.py:_key` returns `(identity, dialect.name, *parts)`, where `None` and
`''` mean different things and the discriminator is a sentinel folded into the
data — `'\x00tables'`, `'\x00fk'`, `f'\x00values:{column}'`. A tuple is a
perfectly good dict key and is not a redis key.

The sentinel exists because of a bug the `tables` docstring still records:
`SELECT "".⌶` is a quoted empty identifier, so `columns(None, '')` computed the
same key as `tables(None)`, and depending on which arrived first the engine
either handed a `Table` to the column renderer or cached an empty column list as
the answer to "what relations are there". The second is silent, and one such
caret emptied the relation list for the rest of the session.

That is the hazard every external adapter would be asked to re-solve, by
inventing its own flattening of a tuple into a string. `demo/app.py:217` already
writes those tuples by hand, so the key shape is a public API that was never
published.

### 1.2 The values are Python objects

Six reads are cached, and their value types are a closed set: `Sequence[str]`
for `schemas`, and sequences of `Table`, `Column`, `Function`, `ColumnValue` and
`ForeignKey`. All five records are frozen slotted dataclasses whose fields are
`str`, `int`, `int | None`, `float | None`, `str | None`, `tuple[str, ...]` and
one enum. Redis stores bytes.

### 1.3 A dict cannot fail, and cannot expire

`resolve.py` states, for every capability, what happens when it is absent, and
CLAUDE.md makes that a rule: missing capability, fewer suggestions, never an
error. `Cache` is the one port with no such paragraph, because there was no
failure to describe. A network store can be down, slow, or return something
foreign.

Nor can a dict go stale in a way that matters. It dies with the process; a
shared store outlives every session and every `CREATE TABLE`, and `__setitem__`
has nowhere to put a TTL.

### 1.4 Prior art in this codebase to follow

- **Capability protocols.** `Supports*` with distinct method names, detected by
  `isinstance` on a `runtime_checkable` Protocol, with the absent case handled
  once in `resolve.py`. The two cache disciplines use the same mechanism.
- **`catalogs/`.** `memory.py` is the dependency-free one; the named modules
  adapt one backend and take their imports lazily. `caches/` mirrors it.
- **`pysqlsuggestions.testing.DialectConformance`.** Shipped in the wheel so a
  third-party implementation can prove itself. `CacheConformance` joins it.
- **`tests/test_purity.py`.** Structural properties asserted by walking the tree
  rather than trusted to review. The codec guards are written the same way.
- **The role-first key.** `(role, ...)` has led since v0.1 so that a cache
  shared across roles cannot leak one user's readable set into another's
  session. Sharing a store makes that argument load-bearing rather than
  precautionary; see §9.

### 1.5 Decisions taken during brainstorming

1. **Two protocols, not one.** `ObjectCache` keeps Python objects, `ByteCache`
   keeps bytes. An implementer satisfies whichever they can. Object caches
   outside a dict are rare; byte stores are almost everything else.
2. **A clean break.** A plain dict stops satisfying `Cache`. The alternative —
   accepting a `MutableMapping` and wrapping it at the door — preserves a
   promise the docstring makes in its first line, at the cost of two accepted
   shapes forever. At 0.8.0, pre-1.0 and `Development Status :: 3 - Alpha`, the
   break is affordable and the result has one way to write an adapter.
3. **The library owns the key encoding.** The port takes an opaque `str`. This
   makes the dict path and the redis path byte-identical and removes the
   invent-your-own-flattening hazard entirely.
4. **The library owns the codec, and applies it only on the byte path.** An
   `ObjectCache` stores objects as they are, so the `lsp/` dict pays no
   serialisation per keystroke. A `ByteCache` receives encoded bytes, so an
   adapter that forgets to encode is unrepresentable.
5. **The codec is explicit, and the guards against it are reflective.** See §5
   and §11.3.
6. **One TTL, owned by the adapter.** The library always passes `ttl=None`.
7. **`identity` is untouched.** Whether it earns its place in the key is a real
   question and a separate one; it becomes a numbered gap. See §9.
8. **The redis adapter never imports redis.**

### 1.6 Rejected approaches

- **Keep `get`/`__setitem__`; add TTL, batching and explicit-miss as `Supports*`
  capabilities.** Most faithful to the architecture CLAUDE.md states for
  `Catalog`, and breaks nothing. Refused because TTL is not a richer answer a
  store can give, it is how the store is written — the capability mechanism
  would be describing the wrong axis — and `_read_through` grows a branch per
  capability to say so.
- **One protocol; the library serialises before every `set`.** Uniform, and it
  charges every in-process dict hit a JSON round trip to serve a use case it
  does not have. The `lsp/` session cache is the hot path and it is an
  `ObjectCache`.
- **Pickle instead of a codec.** A shared store is a trust boundary, and a
  pickle happily reconstructs a `Table` shape the running library no longer has.
- **The shape fingerprint in the value envelope rather than the key.** Keeps
  keys readable and overwrites stale entries in place rather than orphaning
  them. Refused on rolling deploys: two library versions against one store would
  each read, discard and overwrite the other's entry, so neither ever hits for
  the length of the rollout. In the key they get disjoint keyspaces — both
  correct, each cold, neither fighting.
- **A reflective codec.** Walking `dataclasses.fields()` would mean a new
  cacheable type needs no edit at all. Refused in favour of an explicit codec
  with reflective *tests*: reflection that fails loudly in CI is a different
  tool from reflection that runs in front of users, and an unhandled field type
  should be a red build at the moment someone adds it rather than a decision
  taken in advance on their behalf.
- **A `Codec` protocol so an implementer can substitute msgpack.** Nobody has
  asked, and JSON in redis is readable from `redis-cli`, which is worth more
  than the bytes.

---

## 2. Scope

### In

- `ObjectCache` and `ByteCache` in `ports.py`, with `Cache` as their union.
- `pysqlsuggestions.caches`: `cache_key`, `codec`, `MemoryCache`, `RedisCache`.
- `pysqlsuggestions.testing`: `InMemoryByteCache`, `CacheConformance`.
- The `cache-redis` extra.
- `resolve.py`: the encoding, the two-discipline dispatch, and the failure
  policy.
- Every caller in this tree, and a redis service for the integration suite.
- `0.9.0`.

### Out, deliberately

- **Batch reads.** A `SupportsBulkRead` looks like the obvious companion to a
  network cache and is not reachable from here: `_Reader` discovers its keys as
  the request resolves — nothing knows it needs `columns(schema, table)` until
  scope resolution has named the relation — so batching means restructuring
  `_Reader` into a plan-then-execute pass. At roughly six distinct reads per
  completion, with `_memo` already collapsing repeats within one request, that
  is a separate feature with its own correctness surface. `docs/gaps.md`.
- **Caching `all_columns`.** Prefix-independent and the single most valuable
  thing a shared cache could hold, and blocked by a rule this document
  introduces: it returns `Sequence[Column] | None`, where `None` is a real
  answer meaning "too many to enumerate", and `None` is also how a miss is
  spelled. Caching it needs a sentinel or a value envelope, which is a different
  design. `docs/gaps.md`.
- **Thundering herd.** Cold shared store, several processes, all miss the same
  key, all read the database at once. The cure is a lock or a single-flight
  token, which is a distributed-systems feature inside a library whose rule is
  that a shortfall produces fewer suggestions and never an error.
- **Async.** `complete` is synchronous and `lsp/` already moves catalog reads
  off the event loop. The port stays synchronous; an adapter over an asyncio
  redis client is the caller's bridge to build.
- **Making `identity` required.** §9.

### Non-goals

- **Type parameters.** "Generic" here means storage-generic: one string key, two
  value disciplines, any backend, and a protocol small enough that extending
  what the library caches never touches an implementation. It does not mean
  `Cache[K, V]` — the key is always `str` and the value is always one of six
  known types, so each parameter would have exactly one inhabitant.
- **Sharing a cache between library versions.** §4.2 makes that impossible on
  purpose.

---

## 3. The port

```python
@runtime_checkable
class ObjectCache(Protocol):
    """Keeps Python objects as they are. In practice, a dict."""

    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any, ttl: int | None = None) -> None: ...


@runtime_checkable
class ByteCache(Protocol):
    """Keeps bytes. Anything that crosses a process boundary."""

    def get_bytes(self, key: str) -> bytes | None: ...
    def set_bytes(self, key: str, value: bytes, ttl: int | None = None) -> None: ...


Cache: TypeAlias = ObjectCache | ByteCache
```

`Cache` stays the exported name so nothing looking it up in `__init__.py` has to
learn a new one. `ObjectCache` and `ByteCache` join `__all__`.

**The method names differ deliberately.** `isinstance` against a
`runtime_checkable` Protocol compares method names and nothing else, so two
protocols both spelling `get` and `set` would be indistinguishable at runtime
and the dispatch would need a marker attribute whose only job is to say which of
two identical shapes was meant. Distinct names make the detection structural,
which is the mechanism every `Supports*` protocol already uses. Two smaller
things fall out: a `ByteCache` is usually a thin wrapper over a client that
already has `get` and `set` with different semantics, and can now delegate
without shadowing; and a two-tier cache — objects in-process over bytes remotely
— can implement both. Where both are present the library prefers `ObjectCache`,
because it is the one that costs nothing.

**`None` means miss.** The cached value space excludes it: all six values are
sequences and none is ever `None`. Both protocol docstrings say so, because it
is the constraint that makes a one-channel miss signal safe, and §2 records the
one feature it costs.

**`ttl` is integer seconds.** Redis's `ex` is integer seconds and rejects `0`;
a `float` would mean the adapter carried a `ceil` and a floor-at-1, and a
sub-second TTL that silently became "never expire" is the wrong failure. Nothing
about schema metadata, which changes on DDL, wants sub-second expiry.

---

## 4. The key

### 4.1 Grammar

```
1:9f3c2a71:+analyst:+postgres:tables:+public
│ │        │        │         │      └─ parts, one per level
│ │        │        │         └─ kind
│ │        │        └─ dialect
│ │        └─ role
│ └─ shape fingerprint (§4.2)
└─ key format version
```

Components join on `:`. A component that is `None` is a bare `-`; a component
that is a string is `+` followed by a percent-escaping of everything outside
`[A-Za-z0-9_.]`. A literal `-` in data therefore encodes as `+%2D` and can never
be confused with the `None` marker, and `''` encodes as `+`. That is injective
in one sentence, which is the property the `\x00` sentinel was reaching for.

`kind` is drawn from a fixed set — `schemas`, `tables`, `columns`, `functions`,
`values`, `fk` — and needs no escaping. Making it a field of the grammar rather
than a sentinel folded into the data is what makes the `tables(None)` against
`columns(None, '')` collision structurally impossible rather than avoided.

| kind | parts |
| --- | --- |
| `schemas` | catalog |
| `tables` | schema |
| `columns` | schema, table |
| `functions` | schema |
| `values` | schema, table, column |
| `fk` | schema |

### 4.2 The fingerprint

Eight hex characters from `hashlib.blake2b(digest_size=4)` over a canonical
rendering of the cached types: for each of `Table`, `Column`, `Function`,
`ColumnValue` and `ForeignKey`, its name and its fields' names and type names;
and for every enum reachable from those fields — today only `Availability` — its
members' values.

`hashlib`, never the builtin `hash()`, which `PYTHONHASHSEED` randomises: keys
must agree between two processes and between two runs of one process.

The enum half is not decoration. `Availability` gaining a member changes no field
name and no field type name, so a fingerprint over field shapes alone would not
see it. The consequence would not be corruption — an old library decoding a new
member raises, and §6 makes that a miss — but the miss would be permanent and
silent for as long as both versions ran.

A test pins the fingerprint for the current shapes, so a change to what is
cached appears as a deliberate line in a diff rather than a silent cold start
nobody can account for.

### 4.3 `cache_key` is public; its output is not

`cache_key` is exported from `pysqlsuggestions.caches` and is the only supported
way to build one — `demo/app.py`'s prewarm needs it, and so does anyone else's.
The string it returns is explicitly not a stable format: the fingerprint changes
it on every shape change by design. Anyone constructing the string themselves is
broken by the next release and should be.

---

## 5. The codec

`caches/codec.py`, used by `_Reader` on the byte path only, never by an adapter.

JSON, in a tagged envelope: `{"t": <tag>, "v": [ ... ]}`, where `tag` is one of
`str`, `Table`, `Column`, `Function`, `ColumnValue`, `ForeignKey`. Enums encode
by `.value`; `tuple` fields encode as arrays and decode back to tuples, because
these are frozen hashable records and a `list` would be a silent shape change.
`decode` returns a `tuple`.

**The tag resolves against an allowlist derived from `pysqlsuggestions.types`.**
It must never import a name read out of cache bytes. That is exactly the pickle
hazard §1.6 refused, and a clever codec is how it would come back.

The table is written by hand, one entry per type. §11.3 is what keeps it honest.

---

## 6. Failure

In `_read_through`, per CLAUDE.md — once, not in each adapter.

- **A transport error from `get_bytes`, `set_bytes`, `get` or `set`** is caught
  as `Exception`. The library cannot name `redis.ConnectionError` without
  importing redis, and a caller's object may raise anything. On the first
  failure the reader **latches the cache off for the remainder of the request**.
  That is the part that matters: with a two-second socket timeout, an unlatched
  down store costs six timeouts per keystroke instead of one, which is slower
  than no cache at all and looks like the engine hanging.
- **A decode failure does not latch.** It is a miss and the read proceeds. One
  undecodable value under our namespace most likely means someone else's key, so
  disabling the cache for the other five reads would punish the wrong thing.

Neither ever reaches a completion. A cache is an optimisation, and the rule that
a missing capability costs suggestions rather than raising extends to it
unchanged.

**A `cache` that is neither protocol raises `TypeError` from `complete`**,
naming `MemoryCache`. This is the one place a raise is right. A `dict` has
`.get` and no `.set`, so under the new protocols it satisfies neither, and
treating "neither" as "no cache" would leave every existing caller correct,
silent and uncached — invisible, indefinitely. The raise fires the moment a
caller passes the wrong object, so it cannot reach a user's keystroke.

---

## 7. The adapters

### 7.1 `MemoryCache`

An `ObjectCache` over a dict. `MemoryCache(default_ttl=None)`; `None` is exactly
today's behaviour, and storing `(value, expires_at)` against `time.monotonic()`
is what stops `ttl` being a lie in a signature that has one.

No `maxsize`, and the docstring says why rather than leaving it to look like an
oversight: the key is role, dialect, kind and namespace path, so entries are
bounded by the size of the catalog times the number of roles a process serves —
not by keystrokes, not by documents, not by anything that grows while somebody
types. The unbounded dict this replaces was never the leak it resembles.

### 7.2 `RedisCache`

A `ByteCache`, constructed as
`RedisCache(client, *, namespace: str, default_ttl: int | None = 300)`.

**It never imports redis.** It takes a client the caller built and duck-types
it:

```python
class RedisClient(Protocol):
    """The contact surface. Two methods, because that is the whole adapter."""

    def get(self, name: str) -> bytes | str | None: ...
    def set(self, name: str, value: bytes, ex: int | None = None) -> object: ...
```

A version floor is a promise about a package; a two-method contact surface is a
guarantee about the code. This one holds across redis-py 3 through 6, and gets
valkey, `RedisCluster`, `fakeredis` and any pooling wrapper for free.

The extra earns its place through one constructor:

```python
@classmethod
def from_url(cls, url: str, *, namespace: str, default_ttl: int | None = 300) -> RedisCache:
    """`import redis` lives here and nowhere else."""
```

On `ImportError` it raises pointing at `pip install pysqlsuggestions[cache-redis]`.
That is the one place a missing extra can be diagnosed properly, and it leaves
the primary path client-agnostic.

Three details, all of which belong in docstrings:

- **`namespace` is required and has no default.** §9.
- **`default_ttl` is 300 seconds.** Not `None`. §4.2 gives each library version
  its own keyspace, so an upgrade orphans the previous one; the TTL is what
  bounds how long the orphans live. The docstring says that `default_ttl=None`
  turns a shared cache into one nothing invalidates and one nothing reclaims.
- **A client built with `decode_responses=True` returns `str` from `get`.** The
  codec accepts `bytes` and `str` on decode. It would otherwise break for a
  caller whose client is configured perfectly reasonably for their other uses.

---

## 8. `resolve.py`

`_Reader.__init__` decides the discipline once, not per read. `ObjectCache` if
the cache satisfies it; failing that `ByteCache` if it satisfies that; failing
both, the `TypeError` of §6. An object satisfying both is used as an
`ObjectCache`, because that path costs no encode.

`_key` returns `cache_key(...)`. `_memo` keeps the same string keys, so within
one request a repeat read still costs neither a round trip nor a decode.

`_read_through` gains the encode and decode on the byte path, and the failure
policy of §6.

Nothing else in `resolve.py` changes: the six cached reads, the capability
detection and the degradation paths are untouched.

---

## 9. `identity`, and the contract that replaces it

`identity` leads the key so a cache shared across roles cannot leak one user's
privilege-filtered view into another's session. That has held for free until
now, because a dict lives inside one session and the isolation was structural.

A shared store removes the structure, and `identity` is optional —
`server.py:241` passes `self.profile.user if self.profile else None`, and
`Profile.user` is itself `str | None` (`connections.py:83`), so a profile using
peer auth or a bare DSN has none to pass.

Two mechanisms were considered and both refused. Making `identity` required on
`complete` removes the hazard by construction, and is a second breaking API
change in one release for a question — whether `identity` belongs in the key at
all — that has not been settled. Resolving it through a `SupportsIdentity`
capability is the most correct source, since privilege filtering evaluates
against the session role rather than against what a settings file claimed, and
it reintroduces the same problem one level down: something still has to happen
when the capability is absent.

So the mitigation is a contract, stated in `RedisCache`'s `namespace` docstring
and in the `Cache` protocol docstring, which already carries a contract for
precisely this reason — because users supply their own cache:

> A cache must not be shared across databases. It must also not be shared across
> identities **unless** the caller passes `identity`, since that already leads
> the key. One namespace per database, per identity you cannot name.

No warning fires. A `ByteCache` with `identity=None` is *correct* whenever the
namespace already distinguishes identities, which is the deployment this
recommends, and a warning on the recommended configuration teaches people to
filter the channel.

Whether `identity` earns its place in the key becomes a numbered gap in
`docs/gaps.md`.

---

## 10. Packaging and callers

`pyproject.toml`:

```toml
cache-redis = ['redis>=3.0']
```

The comment above `[project.optional-dependencies]` covers catalog drivers only,
and `demo` is already an exception nobody wrote down. It gains the rule: a
catalog driver is named after the driver, anything else is named for its role
first, so `cache-redis` and a future `cache-valkey` are siblings the way
`psycopg2` and `pg8000` are. `redis>=3.0` is a floor and not a pin — 3.0 is
where `set(name, value, ex=...)` landed, and §7.2 is what actually delivers the
compatibility.

`dev` gains `redis>=3.0` and `fakeredis`, under the existing comment.

| file | change |
| --- | --- |
| `lsp/pysqlsuggestions_lsp/server.py:117` | `MemoryCache()` |
| `demo/app.py:128`, `:210` | `MemoryCache()` |
| `demo/app.py:217` | prewarm through `cache_key` |
| `demo/browser.py:64` | `MemoryCache()` |
| `demo/payload.py:54` | the annotation |
| `docker/docker-compose.yml` | a redis service |

Neither demo uses redis. The browser demo cannot — it is Pyodide — and a
`REDIS_URL` branch in the server demo would be a second thing to keep true for
no coverage the integration test does not already carry.

`0.9.0` in `pyproject.toml`, `src/pysqlsuggestions/__init__.py`,
`lsp/pyproject.toml`, `editors/vscode/package.json`, and the
`pysqlsuggestions==` pin in `lsp/pyproject.toml`.

---

## 11. Testing

### 11.1 The adapters

`fakeredis` in the fast suite, not a hand-written double. It implements
redis-py's actual semantics, so it exercises `ex`, the bytes-versus-`str` return
under `decode_responses`, and binary-safe keys — the three details §7.2 writes
docstrings about. A twenty-line fake would agree with the author's assumptions
about those, which is the wrong thing for a test to do.

One integration test keeps a real redis, `@pytest.mark.integration`, skipping
when unreachable like the other three backends. It covers what an in-process
fake structurally cannot: that a *server* enforces the TTL, and that a killed
connection latches the cache off rather than raising into a completion. That
second one is the failure this design is built around and it deserves to be
proven against something that can genuinely be unplugged.

### 11.2 The byte path, from the suite

`InMemoryByteCache` lives in `pysqlsuggestions.testing`, not `caches/`: in
memory it is strictly worse than `MemoryCache`, paying encode and decode to
reach a dict in the same process, so putting it beside `MemoryCache` is an
invitation somebody would accept. `testing` says what it is for, and it gives
`CacheConformance` a reference implementation to check itself against.

Two halves, doing different jobs:

- A fixture parametrised over both disciplines across the thirteen existing
  `cache=` sites in `test_availability.py`, `test_complete.py` and
  `integration/test_acceptance.py`. Nearly free, and it gets the byte path
  exercised by tests written for other reasons, which is where unplanned
  coverage comes from.
- A dedicated module driving all six `_Reader` reads through
  `InMemoryByteCache`. The thirteen sites reach neither `common_values` nor
  `foreign_keys` — the two capability-gated reads, whose value types are least
  like the others — so this is what makes "every cached type round-trips" a
  statement rather than a hope.

### 11.3 The codec guards

An explicit codec needs two assertions, and they catch different failures.

**Coverage.** `ast.parse` `resolve.py`, collect the methods of `_Reader` whose
body calls `self._read`, resolve each one's return annotation with
`get_type_hints` and unwrap `Sequence[X]`. Assert the codec's tag table covers
exactly that set — in both directions, so a type the codec cannot encode fails
and a codec entry nothing caches fails as dead. Which methods cache is then a
fact about the code rather than a list somebody maintains, and the same
mechanism `test_purity.py` already uses to assert structural properties.

**Completeness.** For each covered type, build an instance with *every* field
populated from a synthetic value derived by walking `dataclasses.fields()`,
round-trip it, and assert equality. These are frozen dataclasses with generated
`__eq__`, so a dropped field fails the comparison with no bespoke assertion.
Enum fields take a non-default member, so a codec hardcoding
`Availability.UNKNOWN` is caught too.

Completeness is the one that protects an explicit codec. Coverage catches the
louder failure: adding a cacheable type crashes on first encode, and §11.2 would
find it. A field added to an existing type is silent — the tag table still has
the entry, the encoder drops the field, the fingerprint changes so nothing stale
is decoded, and the field is correct uncached and empty cached. A bug that
depends on whether the cache was warm is the worst thing this design can
produce.

The synthetic builder is a small type resolver, which is the reflection §1.6
declined to put in the codec. The difference is what an unknown field type does:
in the codec it is a decision taken in advance on someone's behalf, and in the
guard it is a red build saying "teach the guard about `X`" at the moment someone
is adding `X`.

### 11.4 `CacheConformance`

`pysqlsuggestions.testing`, alongside `DialectConformance`, `ByteCache` contract
only — `MemoryCache`'s contract is "a dict". It asserts that a miss returns
`None` and not `b''`, that arbitrary binary round-trips, that an overwrite takes
effect, and that keys are opaque and survive unreinterpreted.

It does **not** assert expiry. A portable test would have to sleep, and the
integration test of §11.1 covers it for the adapter that ships.

### 11.5 `test_purity.py`

`redis` joins `DRIVERS`. `pysqlsuggestions.caches.redis` joins the reader-leak
guard: there is nothing in that module to leak, which is exactly the property
worth pinning.

---

## 12. Documentation

- `CHANGELOG.md`, grouped by what changes at a caret — which here is nothing,
  and saying so is the point. The breaking entry names `MemoryCache`.
- `README.md`: the cache section gains the two protocols and the extra.
- `docs/gaps.md`: three entries — caching `all_columns` and what blocks it,
  whether `identity` earns its place in the key, and batch reads with the
  plan-then-execute restructure they need.

---

## 13. Open questions carried forward

1. **Does `identity` belong in the key?** §9. Deferred deliberately; the answer
   changes `complete`'s signature and possibly adds a capability, and neither
   belongs in a release about storage.
2. **`InMemoryByteCache` against `MemoryCache` reads like an accident.**
   `MemoryByteCache` would parallel it. Left as written; renaming is free until
   it ships.
