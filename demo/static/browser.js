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

const status = () => document.getElementById('boot');

function say(text, done) {
  const el = status();
  if (!el) return;
  el.textContent = text;
  el.dataset.done = done ? 'yes' : 'no';
}

async function boot() {
  say('loading Python…');
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
  const wheel = await fetch(new URL('./pysqlsuggestions-0.1.1-py3-none-any.whl', import.meta.url));
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
