# Progress while the demo boots — design

Date: 2026-08-11
Status: **proposed**. Nothing built yet.

The published page shows one unmoving line for the forty seconds it spends
downloading Python. This replaces it with a measured bar.

---

## 1. Context

### What a cold visit looks like

Measured against the published page with the cache disabled, on a link that
moved the runtime at about 70 kB/s:

| phase | from | to |
| --- | --- | --- |
| page loads | 0.0 s | 0.9 s |
| **`loading Python…`** | **0.9 s** | **42.9 s** |
| `installing pysqlsuggestions…` | 42.9 s | 43.7 s |
| `loading the demo schema…` | 43.7 s | 44.0 s |

Inside that 42-second gap, `pyodide.asm.wasm` took 40.0 s to transfer 2.73 MiB;
compiling it and starting the interpreter accounted for roughly 2 s afterwards.

So the wait is dominated by *download*, it varies with the visitor's connection
rather than their machine, and it is precisely the stretch during which the page
says one thing and never changes it. A visitor cannot distinguish that from a
page that has hung — and the first thing this project shows anyone is a page
that appears to hang for the better part of a minute.

### Why the byte count is not the obvious one

`fetch` hands back a *decoded* stream. `Content-Length` describes the compressed
response. For the wasm those are 8.25 MiB and 2.73 MiB, so a reader counting
bytes cannot count toward the figure the network is actually moving. Any
"X MB of Y MB" display is therefore either measuring one thing and labelling it
another, or quoting a total the visitor's connection will never transfer.

### Prior art in this codebase to follow

- **The build rewrites the page and fails when it cannot.** `build_pages.py`
  substitutes the wheel filename into `index.html` and `browser.js`, and returns
  non-zero if `browser.js` names no wheel. A value the page needs and the build
  knows travels this way already.
- **A source string the build depends on is asserted in tests.**
  `test_the_page_hands_the_body_over_intact` asserts on `browser.js` source
  because the failure it guards is silent. The same tool fits here.
- **Comments carry the argument.** Where a decision is not obvious from the
  code, the file says why.

### Decisions taken during brainstorming

1. **Real byte progress**, not an indeterminate animation and not numbered
   steps. Download is both the dominant phase and the variable one, so it is the
   only part whose remaining time a visitor can judge.
2. **A percentage, no megabytes.** The percentage is exact — decoded bytes over
   a decoded total the build knows precisely. Every megabyte figure available to
   the page is either the decoded total (11.7 MiB, roughly twice what the wire
   moves, and the number this demo would least like to be judged on) or an
   estimate scaled by a compression ratio that holds on GitHub Pages and fails
   on any server that does not gzip — including the `python3 -m http.server` the
   demo is checked with locally.
3. **The total is injected by the build**, not fetched at runtime. A
   `sizes.json` would add a round trip before the bar could appear and another
   file for the external-reference gate to reason about.

---

## 2. Scope

### In

A prefetch with progress in `demo/static/browser.js`; a `RUNTIME_BYTES`
placeholder and its substitution in `scripts/build_pages.py`; bar markup and
styling in `demo/static/index.html`; a new `starting Python…` phase; tests for
the injection and the placeholder; a manual verification step.

### Out, deliberately

Shrinking the download. Measured separately: the wire moves 5.38 MiB, of which
2.73 MiB is the compressed wasm — fixed for a given Pyodide — and 2.30 MiB is
`python_stdlib.zip`, already deflated and so served uncompressed. Pruning the
stdlib could save perhaps 1 MiB, at the cost of rewriting a vendored artifact
that the lock exists to pin unmodified, and of rediscovering the safe prune list
at every Pyodide upgrade. Not worth it for 17%.

Also out: a service worker; caching the runtime in the Cache API across visits;
disabling the editor during boot.

---

## 3. How progress is measured

`browser.js` prefetches the five runtime files before calling `loadPyodide`,
reading each through `response.body.getReader()` and accumulating decoded bytes:

```js
const RUNTIME_BYTES = 0;  // rewritten by scripts/build_pages.py
```

`loadPyodide` then runs unchanged and finds all five in the browser cache. That
is not an assumption: GitHub Pages sends `cache-control: max-age=600` on these
files, so a re-request inside the same page load is a hit.

The bar covers the prefetch alone, because it is the only phase with a
denominator. Compiling the wasm and starting the interpreter follow it as text.

### Failure modes, each with a defined behaviour

- **No `ReadableStream`, or the prefetch throws.** Skip it and call
  `loadPyodide` directly. The page boots exactly as it does today, phase text
  and no bar. Progress is a nicety and must never become a way for the demo to
  fail.
- **A cache miss on the second request.** The runtime downloads twice. This is
  what happens with devtools' *disable cache* ticked — a developer's situation,
  not a visitor's — and the source says so, because anyone who spots it in a
  network tab deserves the explanation rather than a mystery.
- **Decoded size disagreeing with the recorded total.** The percentage is
  clamped to 100 and never runs backwards.

---

## 4. What a visitor sees

`#boot` keeps its place directly above the editor and gains a bar: a `div` with
an inner fill whose width is the percentage, carrying `role="progressbar"` and
`aria-valuenow` so it is not merely decorative.

Not a native `<progress>`. This page draws everything from its own CSS variables
in both light and dark, and `<progress>` resists that across browsers for no
gain at this size.

```
loading Python  ████████░░░░░░  61%      the prefetch — the 40-second phase
starting Python…                         loadPyodide: compile and init, ~2 s
installing pysqlsuggestions…             ~0.8 s
loading the demo schema…                 ~0.2 s
```

The bar is **replaced** by text at 100% rather than left sitting full: a stalled
full bar reads as a hang, which is the impression this change exists to remove.

`starting Python…` is new. Today those two seconds are silently part of
`loading Python…`, which is why the existing message appears to hang past the
point where the download has plainly finished.

`.boot[data-done="yes"] { display: none }` already exists, so the whole
indicator disappears on completion without a new rule.

---

## 5. Testing

The browser half cannot be unit-tested here — this suite runs no JavaScript — so
the work splits along that line rather than pretending otherwise.

**In Python:**

- `build_pages.py` rewrites `RUNTIME_BYTES` and returns non-zero when it finds
  no placeholder to rewrite, exactly as it already does for the wheel name. A
  test asserts the emitted `site/browser.js` carries a total equal to the sum of
  `site/pyodide/*`. A stale or missing number then fails the build rather than
  producing a bar that runs to 300%.
- The placeholder's presence is asserted against `demo/static/browser.js`
  itself, following `test_the_page_hands_the_body_over_intact`. Renaming the
  constant without updating the build would otherwise be silent until a
  publish.

**By hand:** the behaviour is verified by driving the published page headlessly
with the cache disabled, asserting the percentage advances and `data-done`
reaches `yes`. This is a verification step in the plan, not a test in the suite,
and nothing in CI covers it. Said plainly here so nobody later reads the green
build as proof the bar works.

---

## 6. Open questions carried forward

1. **Repeat visits.** `max-age=600` means a visitor returning after ten minutes
   re-downloads the runtime. The Cache API would fix that, and is a larger
   decision about storing 11.7 MiB in someone's browser without asking.
2. **The editor during boot.** It is visible and accepts typing while nothing
   can answer it. The bar makes the reason legible, which may be enough; if not,
   that is its own change.
