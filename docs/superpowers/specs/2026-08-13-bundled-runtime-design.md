# A bundled runtime, and stdlib catalog readers — design

Date: 2026-08-13
Status: **proposed**. Nothing built yet.

The extension currently asks the machine for a Python and builds a venv in it.
This replaces that with an interpreter shipped inside the VSIX, and — because the
same change would otherwise cost the extension two of its three backends —
replaces the Trino and ClickHouse clients with catalog readers written against
the stdlib.

---

## 1. Context

### What actually broke

The 2026-08-11 design's open question #4 named `python3` being **absent** as "the
one failure mode with no graceful answer". The reported failure was narrower and
worse than that: `python3` was present, was 3.13.5, and could not create a venv.

```
The virtual environment was not created successfully because ensurepip is not
available. On Debian/Ubuntu systems, you need to install the python3-venv package
```

Debian unbundles `ensurepip` from `python3.13`. Reproduced against the
extension's own commands: `runtime.ts` picks the first of `[configured, 'python3',
'python', 'py']` that meets `MINIMUM_PYTHON`, `/usr/bin/python3` satisfies that
test, and `python3 -m venv` then fails on a machine that also had a perfectly
good `~/.local/bin/python3.11` two entries further down a list that never got
there.

So the discovery predicate was never the right shape. "Is there an interpreter,
and is it new enough" does not answer "can this interpreter build the environment
we need", and the set of ways the second question fails — unbundled `ensurepip`,
PEP 668 `externally-managed`, a Homebrew Python whose `venv` inherits a broken
`pip`, a Windows Store stub on PATH, an interpreter inside a conda environment
that shadows the wheels we install — is open-ended. Each one is a separate
detection rule, none of which can be written against a machine we do not have.

The graceful answer to an open-ended set of environment failures is to stop
depending on the environment.

### Decisions taken during brainstorming

1. **Ship an interpreter, always** — never the system one, even when a good one
   exists. The universal VSIX is abandoned deliberately, not regretfully; §11
   records what was weighed.
2. **Nine platform-targeted VSIXes**, one per VS Code target.
3. **Trino and ClickHouse get stdlib HTTP catalog readers.** The three compiled
   dependencies each client drags in are wire compression codecs, and a catalog
   reader has nothing to compress.
4. **Per-target capability differences are therefore out.** They were forced by
   the musllinux and armv7l wheel gaps; removing the compiled wheels removes the
   gaps, and all nine targets serve all three backends identically.
5. **`pysqlsuggestions.pythonPath` is removed**, not repurposed.
6. **uv is a build-time tool only.** With every wheel `none-any` there is nothing
   left for a resolver to do at runtime.

---

## 2. Scope

### In

A per-target `bundled/runtime.tar.gz` replacing `bundled/wheels/`; an activation
path in `runtime.ts` that extracts and spawns rather than discovers and installs;
a nine-target loop in `scripts/build_vsix.py`; two stdlib catalog readers in
`src/pysqlsuggestions/catalogs/`; the tests and structural guards for all of it.

### Out, deliberately

**Backends beyond Postgres, Trino and ClickHouse.** The wider question — what
happens when a fourth and fifth backend arrive, each with its own driver — was
asked and set aside for these three. Nothing here forecloses it: §11 records the
on-demand-install shape that was designed and then not needed, so it can be
recovered rather than re-derived.

**Kerberos, OAuth and JWT authentication for Trino and ClickHouse.** The readers
do username, and password over TLS. Anything richer is exactly what the real
clients are for, and they remain installable — see §5.

**Query execution, results grids, schema trees, notebook cells.** Unchanged from
the 2026-08-11 scope.

---

## 3. What ships

Nine VSIXes, built with `vsce package --target`:

| target | interpreter triple | stripped tarball |
| --- | --- | --- |
| `win32-x64` | `x86_64-pc-windows-msvc` | 22.0 MB |
| `win32-arm64` | `aarch64-pc-windows-msvc` | 20.7 MB |
| `linux-x64` | `x86_64-unknown-linux-gnu` | 34.2 MB |
| `linux-arm64` | `aarch64-unknown-linux-gnu` | 29.2 MB |
| `linux-armhf` | `armv7-unknown-linux-gnueabihf` | 25.6 MB |
| `alpine-x64` | `x86_64-unknown-linux-musl` | 28.2 MB |
| `alpine-arm64` | `aarch64-unknown-linux-musl` | 28.6 MB |
| `darwin-x64` | `x86_64-apple-darwin` | 24.7 MB |
| `darwin-arm64` | `aarch64-apple-darwin` | 25.0 MB |

Sizes are measured against python-build-standalone release `20260807`,
`install_only_stripped`. Repacking with the server and its wheels already
installed adds roughly 4.5 MB — measured at 38.7 MB for `linux-x64` against a
34.2 MB bare tarball. Every target has a build; none of the nine is a gap.

**CPython 3.13.** Availability was checked across the nine targets rather than
assumed: 3.11 through 3.14 are complete, 3.10 is missing `win32-arm64`. 3.13 is
the newest that is both complete and comfortably inside the library's supported
range, and the pin is a constant in the build script rather than a resolved
range, so moving it is a deliberate commit with a per-target smoke test behind it.

The tarball contains the interpreter with `pysqlsuggestions`,
`pysqlsuggestions-lsp`, `pygls` and `pg8000` **already installed into its own
`site-packages` at build time**. There is no venv, and no `pip` invocation at
runtime.

### Why a tarball and not a pre-extracted tree

A VSIX is a zip, which compresses per file. The extracted tree — several
thousand small `.py`, `.pyc` and `.so` files — zipped comes to **74.9 MB** for
`linux-x64`; the same content as a solid `tar.gz` inside the zip is **38.7 MB**. The cost is one
extraction on first activation, which §4 handles.

python-build-standalone ships `pip` and a working `ensurepip`, which is a fact
worth recording even though nothing at runtime uses either: the failure that
started this design is not merely avoided by not creating a venv — it could not
occur against this interpreter even if we did.

---

## 4. What happens at activation

```
stamp matches → spawn
otherwise     → extract → chmod +x → write stamp → spawn
```

The stamp is the existing mechanism, keyed on the extension version and the
runtime's digest instead of on an interpreter path. As today, **no stamp is
written on failure** — a stamp written before the environment is known good is a
broken install that never rebuilds itself.

`runtime.ts` loses `findInterpreter`, `meetsMinimum`, `MINIMUM_PYTHON`,
`venvPython` and both `run()` calls, and keeps `stampPath`, `needsInstall` and
`stampFor` with new inputs. What remains is extract-and-stamp.

### Extraction uses the system `tar`

Every target platform ships one, including Windows 10 1803 and later, which is
below VS Code's own floor. It handles what a hand-rolled extractor gets
wrong: symlinks (`bin/python3` → `python3.13`, and the whole `lib/` layout),
execute bits, and — on macOS — extracting without stamping every file with
`com.apple.quarantine`, which is what causes a "cannot be opened because the
developer cannot be verified" dialog on a binary the user never chose to run.

This is the weakest dependency in the design and it is named as such. The
fallback, if a target turns out to lack a usable `tar`, is vendoring a
`tar.gz` reader in TypeScript — a known quantity, roughly 200 lines, but 200
lines of file-format code we would own and that has to be right about symlinks
and permissions on three operating systems. Not paid up front.

### `pysqlsuggestions.pythonPath` is removed

Keeping it as an escape hatch was considered and rejected. Its only remaining
purpose would be "I have a working interpreter and object to the download size",
and honouring it means keeping every code path this design exists to delete —
discovery, version comparison, venv creation, `pip install`, and the entire
matrix of environment failures above — alive and, because almost nobody would
set it, untested. A setting that resurrects the bug on the machines that set it
is worse than no setting.

Removal is a breaking change to a published setting and gets a `CHANGELOG` entry
and a deprecation note in `README.md`, which currently states "Python 3.10 or
newer on your PATH" as a requirement and will state the opposite.

---

## 5. Stdlib catalog readers

### What the surface actually is

`catalogs/dbapi.py` defines the whole contract in two methods:

```python
class Cursor(Protocol):
    def execute(self, operation: str, parameters: Any = ...) -> Any: ...
    def fetchall(self) -> Sequence[Any]: ...
```

and its module docstring already named "an HTTP proxy" as a legitimate source.
Above that line sit the seven `CatalogQueries`, the row mappers, `render()`'s
paramstyle rewriting, capability detection and ranking — none of which change.
Below it, for these two backends, is a request and a JSON parse.

The `Catalog` port is four prefix-independent methods and the project **never
reads table data** by rule, so there is no result set of consequence, no
transaction, no prepared-statement lifecycle, no type adapter registry, no
pooling and no bulk path. This is what makes writing a reader reasonable where
writing a driver would not be.

### What each client costs today

| client | compiled dependencies | targets covered |
| --- | --- | --- |
| `pg8000` | none — `python-dateutil`, `scramp`, both pure | 9/9 |
| `trino>=0.328` | `lz4`, `orjson`, `zstandard` | 6/9 |
| `clickhouse-connect` | own C extension, `lz4`, `backports.zstd` | 8/9 |

All three compiled dependencies of the Trino client are `Requires-Dist`, not
extras. Every one of them exists to compress a wire that carries, in our case,
seven small introspection results per session against a cache that is warm for
the rest of it.

### `catalogs/trino_http.py`

`POST /v1/statement` with the SQL as the body, then follow `nextUri` until the
response stops carrying one, accumulating `data`. Headers: `X-Trino-User`,
`X-Trino-Catalog`, `X-Trino-Schema`. The spooled protocol — the only thing lz4
and zstandard are for — is opt-in via `X-Trino-Query-Data-Encoding`; not sending
it yields inline JSON.

Parameters go through Trino's prepared-statement headers, which is what the
official client does too: `render()` with `qmark` produces the `?`-marked SQL for
`X-Trino-Prepared-Statement`, and the values render into `EXECUTE <name> USING
<literals>`. Client-side literal rendering is unavoidable here and is not a
weakening — the official client renders them the same way. Trino has no
backslash escapes in string literals by default, so doubling `'` is the complete
escape, and every value we pass is a schema, relation or prefix string.

The `nextUri` loop is the one piece with real behaviour in it: a query returns
`[]` before it returns rows, `error` can arrive in any response, and the loop
needs a **total deadline** rather than only a per-request timeout, or a queued
query stalls a completion. The server already runs off the event loop
(`2026-08-12-lsp-off-the-event-loop`), so a slow read cannot freeze the editor —
but an unbounded one still holds a worker.

### `catalogs/clickhouse_http.py`

`POST /` on the HTTP interface with `FORMAT JSONCompact`, `X-ClickHouse-User` and
`X-ClickHouse-Key` headers rather than credentials in the URL, and
`enable_http_compression` left off.

Parameters are server-side: ClickHouse binds `param_p1=` query arguments to
`{p1:String}` markers, so nothing is escaped or interpolated at all. That style
is not one of PEP 249's five, and the reader — not `render()` — owns the
translation. `render()` is called with `named`, producing `:p1`, and the reader
rewrites those to `{p1:String}` when it builds the request. Adding a sixth
paramstyle to a shared function documented as rewriting for "whatever paramstyle
the driver reports" would put a non-standard style into code every adapter
shares, to serve one adapter.

`render()` needs no change for either reader. Both `named` and `qmark` skip the
`%`-doubling branch, so `nspname NOT LIKE 'pg\_%'` and ClickHouse's own `LIKE`
patterns survive intact, and repeated markers — the `$1 = $1` in ClickHouse's
`schemas` query — already work.

### They are additive, not replacements

`lsp/pyproject.toml` keeps its `trino` extra and the library keeps documenting
the real clients. A user who needs Kerberos, or who is already holding a `trino`
connection, passes its cursor to `DbapiCatalog` exactly as today. The readers are
what the *bundle* uses, and what a user with no wheels for their platform can
fall back to.

### The claim this changes, stated plainly

`catalogs/__init__.py` says: *"Catalog implementations. Nothing here imports a
database driver."* That stays true — the readers import `urllib`, `json` and
`ssl`, and `test_import_pulls_in_no_drivers` is unaffected. But the honest
description of the change is that for two backends the library *becomes* the
driver: it owns a socket and a wire format, where before it only ever called
`execute` on a cursor somebody handed it.

That is a real widening of what this library is, and it is accepted for a
specific reason: the alternative is compiled wheels the extension cannot ship to
three of nine platforms, and the protocols in question are documented, versioned,
and read-only in our use. It is not a precedent for a hand-rolled Postgres
driver — pg8000 is free, and SCRAM authentication is the one part of this surface
where a mistake is a security bug rather than a missing suggestion.

The readers must not be reachable from `pysqlsuggestions/__init__.py`, which
imports `api`, `ports` and `types` and no adapter at all. A new structural guard
asserts neither reader module reaches `sys.modules` on a bare
`import pysqlsuggestions` — the property `test_import_pulls_in_no_drivers`
protects, applied to the two adapters that are now their own transport.

**Considered and rejected: putting the readers in `lsp/`.** It would keep the
library's shape untouched, but it hides two generally useful adapters inside a
server distribution, and puts non-server code in a package whose modules are
otherwise LSP handlers. `pysqlsuggestions.testing.DialectConformance` is already
the precedent for the library shipping something that exists for adapters.

---

## 6. The build

`scripts/build_vsix.py` grows a target loop around what it already does:

1. Build the two wheels from the tree (`uv build --wheel`, root and `lsp/`) and
   fetch `pygls` and `pg8000` — unchanged, and still verified to be `none-any`.
   `verify()`'s existing rejection of any non-`-none-any.whl` becomes the
   guarantee that the cross-target install below is meaningful.
2. Per target: fetch the python-build-standalone tarball, digest-pinned in a lock
   file beside `scripts/pyodide.lock`, extract to a cache.
3. `uv pip install --python <extracted> --no-index --find-links <wheels>` — the
   whole set, into the interpreter's own `site-packages`.
4. Repack as `bundled/runtime.tar.gz`, then `vsce package --target <target>`.

Cross-target installation is trivially correct because every wheel is `none-any`:
there is no platform to resolve for, no `--python-platform` to get right, and no
per-target wheel set to verify. That property is the whole return on §5, and it
is worth more than the three targets of restored coverage.

uv earns its place here for speed, for `--no-index` determinism, and for
installing into an interpreter that cannot run on the build machine. Not at
runtime: with nothing left to resolve, a resolver is 24.3 MB of dead weight per
VSIX.

Nine tarball downloads at 238 MB total make the cache load-bearing rather than a
convenience; a cold build fetches once and a release build should not fetch at
all.

---

## 7. Capability parity

All nine targets serve Postgres, Trino and ClickHouse identically. There is no
capability matrix, no per-target dialect list, and no UI that has to explain why
this machine completes fewer things than that one.

What still differs per backend is what the *dialect* can answer, which is
unchanged and already handled: Trino and ClickHouse keep no declared
constraints, so `foreign_keys` is absent and join proposals stay Postgres-only,
and Trino ships no `relation_search`. That degradation is defined once at the
port and implemented in `resolve.py`, and neither reader knows it is happening.

The 2026-08-11 spec's §2 excluded ClickHouse and Trino from the bundle and its
open question #2 blocked ClickHouse on "a pure-Python driver or on accepting
platform-targeted VSIXs". This design takes both halves of that sentence: the
VSIXes become platform-targeted, and the pure-Python path is the reader.

---

## 8. Error handling

The governing rule is unchanged: **a completion request never fails.** Two rows
of the 2026-08-11 table go away and one arrives.

| failure | response |
| --- | --- |
| ~~no `python3` on PATH~~ | cannot occur |
| ~~venv creation or install fails~~ | cannot occur |
| runtime extraction fails | tar output to a channel, no stamp written, then dormant |
| database unreachable | unchanged — catalog-free mode, status bar, one notification |
| authentication rejected | unchanged |
| server process crashes | unchanged |

A reader raising on a malformed response, a TLS failure or a deadline is a
catalog failure like any other and degrades to catalog-free completion. It is not
a new error class and gets no new UI.

---

## 9. Testing

**The readers, without a network:** request construction and response parsing
against recorded payloads — `nextUri` paging across two pages, an `error` body, a
first response with `data` absent, `param_` rewriting, the `EXECUTE … USING`
rendering including an embedded quote, and the deadline.

**The readers, against docker:** the existing integration suite already covers
all three backends through the `Catalog` port and skips when a backend is
unreachable. The readers slot in behind that same port, so it becomes their
conformance suite at no cost — which is the point of the port having been four
methods.

**The build:** a per-target smoke test that extracts the packed runtime and runs
`python -c "import pysqlsuggestions_lsp"`. It can only *execute* for the host
target; for the other eight it asserts structure — the interpreter binary exists,
is executable, and `site-packages` holds the expected distributions. A test that
pretended to do more would be lying about eight of nine cases.

**Structural guards**, in the spirit of `test_purity.py`:

- Neither reader module is in `sys.modules` after `import pysqlsuggestions`.
- The runtime lock file's digests match what the VSIX contains.
- The nine-target build produces nine VSIXes, and each contains exactly one
  `runtime.tar.gz`.

**TypeScript:** `runtime.ts`'s tests lose interpreter discovery and gain
stamp-vs-extract decisions. The `@vscode/test-electron` test is unchanged.

---

## 10. Open questions carried forward

1. **Cache invalidation.** Unchanged from 2026-08-11 §9.1 and untouched here.
2. **Marketplace release mechanics.** Nine VSIXes per release is a publishing
   pipeline, not a `vsce publish`. Out of scope for this design; blocking for the
   first release that uses it.
3. **A fourth backend.** §11's on-demand-install shape is the answer if one
   arrives with a compiled driver and no HTTP interface. Not built.
4. **Trino and ClickHouse authentication beyond password.** Deferred until
   someone asks, because the real clients already answer it.

---

## 11. Rejected alternatives

**Keep the universal VSIX and fix the discovery predicate.** Probe the candidate
interpreter by actually creating a throwaway venv rather than by comparing
versions, and walk the full candidate list on failure. This is the smallest
possible change and it would have fixed the reported bug. It was rejected because
the set of ways a system interpreter can be unsuitable is open-ended and machine-
specific; each new one is a bug report from someone whose editor silently has no
completions, and the predicate grows a rule per report forever.

**Download the interpreter on first activation** — one universal VSIX, ~1 MB,
fetching python-build-standalone from GitHub on demand. Rejected on the repo's
own stated position: the browser demo vendors Pyodide by digest rather than
fetching it, and the 2026-08-11 design says an extension that installs from the
network on first run "would be the same project taking the opposite position on
the same question". It also fails on exactly the machines — air-gapped, proxied,
corporate-TLS — most likely to have an unusual Python in the first place.

**Ship uv at runtime and let it bootstrap the interpreter and packages.** This
was the shape first chosen during brainstorming and then abandoned once the
consequences were followed through. uv is 24.3 MB zipped per VSIX; it can install
offline from a `file://` mirror, so it *works* — but with the interpreter already
shipped and every wheel `none-any` and pre-installed, it resolves nothing,
downloads nothing and installs nothing. It is a resolver in a design with no
resolution left in it.

**Per-target capabilities: ship the real clients where wheels exist.** Trino on
6/9, ClickHouse on 8/9, `resolve.py`'s existing degradation covering the rest.
Costs no new code and was the chosen shape before the reader question was asked.
Rejected on the build rather than on coverage: it means per-target wheel sets,
platform tags, and a verification step that can only really run on the target
itself, permanently. The coverage it loses — Trino on `linux-armhf` and both
Alpines, ClickHouse on `linux-armhf` — matters less than that, though Alpine
matters more than it sounds, being where container and remote development land.

**On-demand driver install, with a runtime resolver.** Designed in full when the
scope was an open-ended driver catalog, and dropped when the scope became three
backends. Recorded here so it can be recovered: ship uv, keep a per-backend
requirement set, install into the bundled interpreter on first use of a dialect,
and surface the download in the status bar. Everything about it is justified by
backends this design does not have.
