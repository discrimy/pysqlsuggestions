// The static build's transport: Pyodide in a worker-free page, no server.
//
// GitHub Pages serves files and nothing else, so there is no FastAPI process
// and no database to reach. The library has no runtime dependencies and its
// core is pure, so the whole pipeline loads into the page and answers from a
// snapshot exported from the docker fixtures.
//
// Defined before index.html's own script runs, which is what makes it pick this
// up as `window.DRIVER` instead of talking to an API that is not there.

// Vendored beside this file by scripts/vendor_pyodide.py. The page reaches no
// host but the one serving it, which is the claim the demo exists to make.
const PYODIDE = new URL('./pyodide/', import.meta.url).href;

// The decoded size of everything in ./pyodide/, written here by
// scripts/build_pages.py so the boot can report progress against it. Zero in the
// source on purpose: the number belongs to a build, not to a commit, and a
// hand-maintained one goes stale at the next Pyodide upgrade without anyone
// noticing — the bar would simply stop short.
const RUNTIME_BYTES = 0;

const status = () => document.getElementById('boot');

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

  say('installing pysqlsuggestions…');
  // A wheel is a zip and this one is pure Python with no dependencies, so
  // unpacking it onto sys.path is the whole install. micropip would add a
  // package download and a resolver to reach the same place — and did, until it
  // failed to fetch one morning and left the page booted with a dead editor.
  //
  // Unpacked into a directory we name rather than site-packages, whose real path
  // carries the interpreter version and would break silently on an upgrade.
  // Wheel and demo sources sit beside this file; the build step puts them there.
  const wheel = await fetch(new URL('./pysqlsuggestions-0.2.0-py3-none-any.whl', import.meta.url));
  py.unpackArchive(await wheel.arrayBuffer(), 'zip', { extractDir: '/wheel' });
  py.runPython('import sys; sys.path.insert(0, "/wheel")');

  say('loading the demo schema…');
  const modules = ['payload.py', 'schema.py', 'browser.py'];
  const sources = await Promise.all(
    modules.map((name) => fetch(new URL(`./${name}`, import.meta.url)).then((r) => r.text())),
  );

  py.FS.mkdirTree('/demo');
  py.FS.writeFile('/demo/__init__.py', '');
  modules.forEach((name, i) => py.FS.writeFile(`/demo/${name}`, sources[i]));
  py.runPython('import sys; sys.path.insert(0, "/")');

  const demo = py.runPython('from demo.browser import Demo\nDemo()');

  // The body goes across whole, exactly as the server route receives it. Listing
  // the fields here instead meant this call had to be updated whenever the page
  // sent something new, and when it was not — `pending`, which has a default on
  // the other side — nothing failed: template blanks simply stopped advancing.
  window.DRIVER = {
    backends: async () => JSON.parse(demo.backends()),
    suggest: async (body) => JSON.parse(demo.suggest(JSON.stringify(body))),
  };
  say('', true);
  return window.DRIVER;
}

// index.html reads window.DRIVER at parse time, so the boot has to finish first.
// The page is loaded as a module after this one resolves.
window.DRIVER_READY = boot().catch((error) => {
  say(`failed to start: ${error}`, true);
  throw error;
});
