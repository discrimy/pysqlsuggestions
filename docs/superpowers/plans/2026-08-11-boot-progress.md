# Boot progress implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the forty seconds of unmoving `loading Python…` with a bar showing how much of the runtime has arrived.

**Architecture:** `browser.js` prefetches the five runtime files through a streaming reader, counting decoded bytes against a total the build injects, then calls `loadPyodide`, which finds them in the browser cache. The bar lives in the existing `#boot` element and is replaced by text once the download finishes.

**Tech Stack:** Vanilla ES modules, `fetch` + `ReadableStream`. Python 3.10+ standard library for the build. uv, ruff, mypy strict, pytest.

**Spec:** `docs/superpowers/specs/2026-08-11-boot-progress-design.md`

## Global Constraints

- **The demo must still boot if progress fails.** Every failure path in Task 2 falls back to calling `loadPyodide` directly. Progress is a nicety and must never become a way for the demo to fail.
- **No new files fetched at runtime.** The total is injected at build time; a `sizes.json` is explicitly rejected in the spec.
- **The external-reference gate still applies:** no absolute URL may appear in anything the build assembles. `scripts/build_pages.py` fails the build otherwise.
- **ruff:** line length 120, single quotes, `D` docstring rules. `scripts/*` already ignores `T201`, so `print()` needs no `# noqa` in new code there.
- **mypy strict**, and it follows imports into `scripts/` from tests.
- **Build invocation is `uv run python -m scripts.build_pages`** — not by path, since it imports `scripts.vendor_pyodide`.
- **Verification:** `./scripts/check.sh`. Offline subset: `uv run pytest -m 'not integration'`.
- **The bar itself is not covered by any test.** This suite runs no JavaScript. Task 4 is a manual verification step and the plan says so rather than implying CI covers it.

---

## File Structure

**Modified**
- `demo/static/browser.js` — the `RUNTIME_BYTES` placeholder, the prefetch, the new `starting Python…` phase, and `say()` gaining a percentage.
- `demo/static/index.html` — the bar's markup inside `#boot`, and three CSS rules.
- `scripts/build_pages.py` — `RUNTIME_BYTES` substitution, failing when the placeholder is absent.
- `tests/test_demo_browser.py` — the source-level assertion that the placeholder exists.
- `tests/test_build_pages.py` — the substitution test.

**Created** — none. This feature adds no file.

---

## Task 1: The build injects the total

**Files:**
- Modify: `scripts/build_pages.py` (constants near `WHEEL_NAME`, and `main()` beside the existing `browser.js` rewrite)
- Modify: `demo/static/browser.js:13` (the placeholder only)
- Test: `tests/test_build_pages.py`

**Interfaces:**
- Consumes: `vendor(SITE / 'pyodide')`, already called in `main()`.
- Produces: `RUNTIME_BYTES = re.compile(r'const RUNTIME_BYTES = (\d+);')` in `scripts/build_pages.py`; an emitted `site/browser.js` whose `RUNTIME_BYTES` equals the summed size of `site/pyodide/*`.

Doing the build half first means Task 2's JavaScript has a real number to read the moment it is written, rather than a zero that makes the bar look broken while it is being developed.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_build_pages.py`:

```python
def test_the_runtime_total_is_injected_into_the_built_transport() -> None:
    """
    The page cannot measure the download without knowing what it is measuring against.

    Injected rather than fetched: a `sizes.json` would cost a round trip before
    the bar could appear. Injected rather than hand-written: a constant nobody
    updates produces a bar that stops at 84% after a Pyodide upgrade, which is
    worse than no bar at all.
    """
    site = Path(__file__).resolve().parents[1] / 'site'
    if not (site / 'browser.js').exists():
        pytest.skip('run `uv run python -m scripts.build_pages` first')

    written = re.search(r'const RUNTIME_BYTES = (\d+);', (site / 'browser.js').read_text())
    assert written is not None, 'the build left no RUNTIME_BYTES in site/browser.js'
    assert int(written.group(1)) == sum(f.stat().st_size for f in (site / 'pyodide').iterdir())


def test_a_transport_without_the_placeholder_fails_the_build() -> None:
    """
    Renaming the constant must break the build, not the bar.

    The failure it guards is silent: the page would ship with whatever number was
    last hard-coded, and nothing downstream would notice.
    """
    assert RUNTIME_BYTES.search('const RUNTIME_BYTES = 0;') is not None
    assert RUNTIME_BYTES.search('const RUNTIME_TOTAL = 0;') is None
```

Add to that file's imports: `import re`, `import pytest`, and `RUNTIME_BYTES` to the existing `from scripts.build_pages import ...` line.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_build_pages.py -k runtime_total -v`
Expected: FAIL — `ImportError: cannot import name 'RUNTIME_BYTES' from 'scripts.build_pages'`

- [ ] **Step 3: Add the placeholder to the transport**

In `demo/static/browser.js`, immediately after the `PYODIDE` constant (line 13), add:

```js
// The decoded size of everything in ./pyodide/, written here by
// scripts/build_pages.py so the boot can report progress against it. Zero in the
// source on purpose: the number belongs to a build, not to a commit, and a
// hand-maintained one goes stale at the next Pyodide upgrade without anyone
// noticing — the bar would simply stop short.
const RUNTIME_BYTES = 0;
```

- [ ] **Step 4: Add the pattern and the substitution**

In `scripts/build_pages.py`, beside `WHEEL_NAME`:

```python
RUNTIME_BYTES = re.compile(r'const RUNTIME_BYTES = (\d+);')
"""
The decoded size of the vendored runtime, which the boot reports progress against.

Matched as a shape rather than a literal for the same reason as `WHEEL_NAME`: the
number moves with every Pyodide upgrade, and a build that quietly failed to
replace it would ship a bar that stops short of the end.
"""
```

In `main()`, replace the `browser.js` rewrite block:

```python
    # browser.js names the wheel too, and the version moves.
    driver = (SITE / 'browser.js').read_text()
    named = WHEEL_NAME.subn(wheels[-1].name, driver)
    if not named[1]:
        print('browser.js names no wheel to install', file=sys.stderr)  # noqa: T201
        return 1
    (SITE / 'browser.js').write_text(named[0])
```

with:

```python
    # browser.js names the wheel too, and the version moves.
    driver = (SITE / 'browser.js').read_text()
    named = WHEEL_NAME.subn(wheels[-1].name, driver)
    if not named[1]:
        print('browser.js names no wheel to install', file=sys.stderr)  # noqa: T201
        return 1

    runtime_bytes = sum(f.stat().st_size for f in (SITE / 'pyodide').iterdir())
    sized = RUNTIME_BYTES.subn(f'const RUNTIME_BYTES = {runtime_bytes};', named[0])
    if not sized[1]:
        print('browser.js declares no RUNTIME_BYTES to fill in', file=sys.stderr)  # noqa: T201
        return 1
    (SITE / 'browser.js').write_text(sized[0])
```

- [ ] **Step 5: Build and run the tests**

```bash
uv build --wheel
uv run python -m scripts.build_pages
uv run pytest tests/test_build_pages.py -v
```

Expected: build exits zero, tests PASS. Confirm the number by eye:

```bash
grep -o 'const RUNTIME_BYTES = [0-9]*;' site/browser.js
du -sb site/pyodide | cut -f1
```

Expected: the two figures agree, around 12,261,945.

- [ ] **Step 6: Prove the guard bites**

```bash
cp demo/static/browser.js /tmp/browser.js.bak
sed -i 's/const RUNTIME_BYTES = 0;/const RUNTIME_TOTAL = 0;/' demo/static/browser.js
uv run python -m scripts.build_pages; echo "exit=$?"
cp /tmp/browser.js.bak demo/static/browser.js
uv run python -m scripts.build_pages; echo "restored exit=$?"
git diff --stat demo/static/browser.js
```

Expected: `exit=1` with `browser.js declares no RUNTIME_BYTES to fill in`, then `restored exit=0`, then an empty diff.

- [ ] **Step 7: Full checks and commit**

Run: `uv run pytest -m 'not integration' -q && uv run mypy && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

```bash
git add scripts/build_pages.py demo/static/browser.js tests/test_build_pages.py
git commit -m "feat: the build tells the page how large the runtime is"
```

---

## Task 2: The prefetch that counts bytes

**Files:**
- Modify: `demo/static/browser.js` (`say()`, a new `prefetch()`, and `boot()`)
- Test: `tests/test_demo_browser.py`

**Interfaces:**
- Consumes: `RUNTIME_BYTES` from Task 1; `PYODIDE`, `say()`, `boot()` already in the file.
- Produces: `say(text, done, percent)` — `percent` is a number 0–100 or `undefined`; `prefetch()` returning a promise that resolves when every runtime file has been read, and never rejects.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_demo_browser.py`:

```python
def test_the_transport_declares_a_runtime_total_for_the_build_to_fill() -> None:
    """
    Asserted against the source, as the request body is, and for the same reason.

    `scripts/build_pages.py` substitutes this constant and fails when it is
    absent, so the two have to be kept in step. A rename here with no matching
    change there is caught by the build — but only at publish time, which is a
    long way from the edit.
    """
    source = DRIVER.read_text()
    assert 'const RUNTIME_BYTES = 0;' in source, 'the build has no placeholder to fill in'
    assert 'response.body.getReader()' in source, 'progress needs a streaming read'
```

`DRIVER` is already defined at the top of that file as the path to `demo/static/browser.js`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_demo_browser.py -k runtime_total -v`
Expected: FAIL on the second assertion — the placeholder exists after Task 1, the streaming read does not.

- [ ] **Step 3: Teach `say()` about a percentage**

In `demo/static/browser.js`, replace `say()`:

```js
function say(text, done, percent) {
  const el = status();
  if (!el) return;
  el.dataset.done = done ? 'yes' : 'no';
  if (percent === undefined) {
    el.textContent = text;
    return;
  }
  // Rebuilt rather than mutated: the bar exists only while a percentage does,
  // and every other caller sets plain text.
  el.textContent = '';
  const label = document.createElement('span');
  label.textContent = `${text} ${percent}%`;
  const track = document.createElement('div');
  track.className = 'track';
  track.setAttribute('role', 'progressbar');
  track.setAttribute('aria-valuenow', String(percent));
  track.setAttribute('aria-valuemin', '0');
  track.setAttribute('aria-valuemax', '100');
  const fill = document.createElement('i');
  fill.style.width = `${percent}%`;
  track.append(fill);
  el.append(label, track);
}
```

- [ ] **Step 4: Add the prefetch**

Insert above `boot()`:

```js
// Fetch the runtime ourselves so the download can be counted, then let
// loadPyodide ask for the same files and find them in the browser cache — Pages
// sends `cache-control: max-age=600`, so a second request inside one page load
// is a hit. With devtools' "disable cache" ticked it is not, and the runtime
// downloads twice; that is a developer's situation, not a visitor's.
//
// The bar covers this phase alone because it is the only one with a
// denominator. Compiling the wasm and starting the interpreter follow as text.
const RUNTIME_FILES = [
  'pyodide.asm.wasm',
  'python_stdlib.zip',
  'pyodide.asm.js',
  'pyodide-lock.json',
  'pyodide.mjs',
];

async function prefetch() {
  if (!RUNTIME_BYTES || typeof ReadableStream === 'undefined') return;
  let read = 0;
  let shown = -1;
  const show = () => {
    // Clamped: a total that disagrees with what arrives must not print 103%.
    const percent = Math.min(100, Math.round((read / RUNTIME_BYTES) * 100));
    // Only when the number changes. A chunk arrives every few kilobytes, so
    // repainting per chunk would rebuild these nodes some hundreds of times to
    // draw the same figure — and the repaint would compete with the download it
    // is reporting on.
    if (percent === shown) return;
    shown = percent;
    say('loading Python', false, percent);
  };
  show();
  await Promise.all(
    RUNTIME_FILES.map(async (name) => {
      const response = await fetch(`${PYODIDE}${name}`);
      const reader = response.body.getReader();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        read += value.length;
        show();
      }
    }),
  );
}
```

- [ ] **Step 5: Call it, and name the phase it uncovered**

Replace the first three lines of `boot()`:

```js
async function boot() {
  say('loading Python…');
  const { loadPyodide } = await import(`${PYODIDE}pyodide.mjs`);
  const py = await loadPyodide({ indexURL: PYODIDE });
```

with:

```js
async function boot() {
  say('loading Python…');
  // Never lets the demo fail: an unsupported stream, a rejected fetch, anything
  // — the page falls through to the load below and boots as it always did,
  // without a bar.
  await prefetch().catch(() => {});

  // Its own phase because it is its own two seconds. Folded into the message
  // above, the text sat unchanged past the point where the download had plainly
  // finished, which is most of why the boot read as a hang.
  say('starting Python…');
  const { loadPyodide } = await import(`${PYODIDE}pyodide.mjs`);
  const py = await loadPyodide({ indexURL: PYODIDE });
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_demo_browser.py -v && uv run pytest -m 'not integration' -q`
Expected: PASS. No Python behaviour changes here — `demo/browser.py` is what those tests drive, and it is untouched.

- [ ] **Step 7: Commit**

```bash
git add demo/static/browser.js tests/test_demo_browser.py
git commit -m "feat: the boot counts the bytes it is waiting for"
```

---

## Task 3: The bar

**Files:**
- Modify: `demo/static/index.html:92-93` (the `.boot` rules)

**Interfaces:**
- Consumes: the `.track` element and its `<i>` fill, created by `say()` in Task 2.
- Produces: no API. Three CSS rules.

- [ ] **Step 1: Add the styling**

In `demo/static/index.html`, replace:

```css
  .boot { color: var(--muted); font-size: 12px; margin: 6px 0; }
  .boot[data-done="yes"] { display: none; }
```

with:

```css
  .boot { color: var(--muted); font-size: 12px; margin: 6px 0; }
  .boot[data-done="yes"] { display: none; }
  /* Only present while a percentage is: `say` builds the bar and drops it again
     when the phase goes back to plain text, so a finished download never leaves
     a full bar sitting there looking stalled. */
  .boot .track { height: 4px; margin-top: 5px; border-radius: 2px; background: var(--line);
                 overflow: hidden; max-width: 320px; }
  .boot .track i { display: block; height: 100%; background: var(--accent);
                   transition: width 120ms linear; }
```

`--line` and `--accent` are both defined for light and dark in the same file, so the bar follows the theme without a second rule.

- [ ] **Step 2: Build and look at it**

```bash
uv build --wheel
uv run python -m scripts.build_pages
pkill -f 'http.server -d site'; nohup python3 -m http.server -d site 8001 >/dev/null 2>&1 &
sleep 2 && curl -sf -o /dev/null -w 'site: %{http_code}\n' http://localhost:8001/index.html
```

Expected: `site: 200`. Open `http://localhost:8001` and watch the boot. Locally the runtime is served uncompressed and from disk, so the bar will move fast — the point of looking is that it appears, advances, and is replaced by `starting Python…` rather than sitting full.

- [ ] **Step 3: Full checks and commit**

Run: `uv run python -m scripts.build_pages && uv run pytest -m 'not integration' -q`
Expected: build exits zero — this is also the external-reference gate confirming the new CSS introduced no URL — and tests PASS.

```bash
git add demo/static/index.html
git commit -m "feat: a bar under the editor while Python arrives"
```

---

## Task 4: Verify it against a slow, cold, real visit

**Files:** none modified. This is the acceptance step, and it is manual.

**Interfaces:**
- Consumes: everything above.

Nothing in the test suite exercises the bar, because this suite runs no JavaScript. This task is how the feature is actually confirmed, and skipping it means shipping a bar nobody has watched.

- [ ] **Step 1: Serve the built site**

```bash
uv build --wheel && uv run python -m scripts.build_pages
pkill -f 'http.server -d site'; nohup python3 -m http.server -d site 8001 >/dev/null 2>&1 &
sleep 2 && curl -sf -o /dev/null -w 'site: %{http_code}\n' http://localhost:8001/index.html
```

- [ ] **Step 2: Drive a cold visit and record the phases**

Chrome with the cache disabled, so the runtime is fetched rather than replayed. Node 24's `WebSocket` is global, so this needs no packages.

```bash
nohup google-chrome --headless=new --no-sandbox --disable-gpu \
  --user-data-dir=/tmp/boot-progress --remote-debugging-port=9225 about:blank >/dev/null 2>&1 &
for i in $(seq 1 25); do curl -sf http://127.0.0.1:9225/json/version >/dev/null && break; sleep 1; done
```

```javascript
// /tmp/watch-boot.mjs
const list = await (await fetch('http://127.0.0.1:9225/json/list')).json();
const page = list.find(t => t.type === 'page');
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise(r => (ws.onopen = r));
let id = 0; const pending = new Map();
ws.onmessage = e => { const d = JSON.parse(e.data); if (pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } };
const cmd = (method, params = {}) => new Promise(res => { const n = ++id; pending.set(n, res);
  ws.send(JSON.stringify({ id: n, method, params })); });
const evaluate = async expr => (await cmd('Runtime.evaluate',
  { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value;

await cmd('Network.enable');
await cmd('Network.setCacheDisabled', { cacheDisabled: true });
await cmd('Page.enable');
await cmd('Page.navigate', { url: 'http://localhost:8001/index.html' });

console.log(await evaluate(`(async () => {
  const seen = [];
  let last = null;
  const end = Date.now() + 180000;
  while (Date.now() < end) {
    const el = document.getElementById('boot');
    const now = el ? el.textContent : null;
    if (now !== last) { seen.push(now); last = now; }
    if (el && el.dataset.done === 'yes') break;
    await new Promise(r => setTimeout(r, 30));
  }
  const percents = seen.map(s => Number((s.match(/(\\d+)%/) || [])[1])).filter(n => !isNaN(n));
  return JSON.stringify({
    distinct: seen.length,
    percents: percents.length ? [percents[0], percents[percents.length - 1]] : [],
    monotonic: percents.every((p, i) => i === 0 || p >= percents[i - 1]),
    within: percents.every(p => p >= 0 && p <= 100),
    phases: seen.filter(s => !/\\d+%/.test(s)),
  }, null, 1);
})()`));
ws.close();
```

```bash
node /tmp/watch-boot.mjs
```

Expected, and each is a thing that could be wrong:
- `percents` shows several distinct values, not `[100, 100]` — the bar moves rather than jumping.
- `monotonic` is `true` — it never runs backwards.
- `within` is `true` — nothing over 100, which is the clamp doing its job.
- `phases` contains `starting Python…`, `installing pysqlsuggestions…`, `loading the demo schema…` — the download phase gave way to text rather than leaving a full bar.

- [ ] **Step 3: Confirm the fallback still boots**

The failure path matters more than the bar. Force it by making the total zero, which is what an unsubstituted build would leave:

```bash
sed -i 's/const RUNTIME_BYTES = [0-9]*;/const RUNTIME_BYTES = 0;/' site/browser.js
node /tmp/watch-boot.mjs
```

Expected: `phases` still ends with the three text phases and the page reaches `data-done="yes"`. `percents` is empty — no bar, because `prefetch()` returns immediately. Then rebuild to undo it:

```bash
uv run python -m scripts.build_pages
```

- [ ] **Step 4: Stop the servers**

```bash
pkill -f 'remote-debugging-port=9225'; pkill -f 'http.server -d site 8001'
```

- [ ] **Step 5: Full check**

Run: `./scripts/check.sh`
Expected: green.

- [ ] **Step 6: Commit anything the verification exposed**

If steps 2 or 3 found a defect, fix it and commit. If they passed clean there is nothing to commit, which is the expected outcome.

---

## Verification

```bash
./scripts/check.sh
uv build --wheel && uv run python -m scripts.build_pages
grep -o 'const RUNTIME_BYTES = [0-9]*;' site/browser.js
```

Done when: the build injects a total matching `site/pyodide/`; a cold visit shows a bar that advances monotonically, stays within 0–100, and is replaced by `starting Python…`; and a build with the total zeroed still boots the page to a working editor.
