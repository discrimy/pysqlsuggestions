// The static build's transport: Pyodide in a worker-free page, no server.
//
// GitHub Pages serves files and nothing else, so there is no FastAPI process
// and no database to reach. The library has no runtime dependencies and its
// core is pure, so the whole pipeline loads into the page and answers from a
// snapshot exported from the docker fixtures.
//
// Defined before index.html's own script runs, which is what makes it pick this
// up as `window.DRIVER` instead of talking to an API that is not there.

const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v0.28.3/full/';

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
  await py.loadPackage('micropip');
  const micropip = py.pyimport('micropip');
  // Wheel and demo sources sit beside this file; the build step puts them there.
  await micropip.install(new URL('./pysqlsuggestions-0.1.0.dev0-py3-none-any.whl', import.meta.url).href);

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

  window.DRIVER = {
    backends: async () => JSON.parse(demo.backends()),
    suggest: async (body) => JSON.parse(demo.suggest(body.sql, body.caret, body.backend, body.limit)),
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
