import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { type EnsureOptions, ensureVenv, needsInstall, stampPath, venvPython } from '../../runtime';

/** Everything that touches the outside world, stubbed. Override per test. */
function options(overrides: Partial<EnsureOptions> = {}): EnsureOptions {
  return {
    root: '/storage',
    version: '0.2.1',
    wheelDir: '/ext/bundled/wheels',
    platform: 'linux',
    findPython: async () => 'python3',
    run: async () => {},
    readStamp: async () => undefined,
    writeStamp: async () => {},
    ...overrides,
  };
}

test('the venv interpreter is where the platform puts it', () => {
  assert.equal(venvPython('/storage', 'linux'), '/storage/venv/bin/python');
  assert.equal(venvPython('C:\\storage', 'win32'), 'C:\\storage/venv/Scripts/python.exe');
});

test('the stamp lives beside the venv, not inside it', () => {
  // Inside, deleting a broken venv would delete the evidence it was ever built,
  // and a half-deleted one would keep a stamp claiming it was fine.
  assert.equal(stampPath('/storage').includes('/venv/'), false);
});

test('a missing stamp means install', () => {
  assert.equal(needsInstall(undefined, '0.2.1'), true);
});

test('a matching stamp means skip', () => {
  assert.equal(needsInstall('0.2.1', '0.2.1'), false);
});

test('an older stamp means install', () => {
  // The VSIX carries wheels for its own version, so an upgraded extension has
  // wheels its existing venv has never seen.
  assert.equal(needsInstall('0.2.0', '0.2.1'), true);
});

test('a venv is built and stamped when there is no stamp', async () => {
  const commands: string[][] = [];
  let stamped: string | undefined;
  const runtime = await ensureVenv(
    options({
      run: async (command, args) => {
        commands.push([command, ...args]);
      },
      writeStamp: async (value) => {
        stamped = value;
      },
    }),
  );
  assert.equal(runtime.ready, true);
  assert.equal(stamped, '0.2.1');
  assert.equal(commands[0]?.includes('venv'), true);
});

test('the install never reaches the network', async () => {
  // A first run on a train must work. A wheel that is not in the VSIX is a
  // build bug, not something to quietly download.
  const commands: string[][] = [];
  await ensureVenv(
    options({
      run: async (command, args) => {
        commands.push([command, ...args]);
      },
    }),
  );
  const install = commands.find((command) => command.includes('install'));
  assert.ok(install, 'nothing was installed');
  assert.equal(install.includes('--no-index'), true);
  assert.equal(install.includes('--find-links'), true);
  assert.equal(install.includes('/ext/bundled/wheels'), true);
});

test('the extra that carries the driver is installed', async () => {
  // Without [pg8000] the server installs and can read no catalog at all.
  const commands: string[][] = [];
  await ensureVenv(
    options({
      run: async (command, args) => {
        commands.push([command, ...args]);
      },
    }),
  );
  const install = commands.find((command) => command.includes('install'));
  assert.ok(install?.some((argument) => argument.includes('pg8000')));
});

test('a stamped venv is not rebuilt', async () => {
  const commands: string[][] = [];
  const runtime = await ensureVenv(
    options({
      readStamp: async () => '0.2.1',
      run: async (command, args) => {
        commands.push([command, ...args]);
      },
    }),
  );
  assert.equal(runtime.ready, true);
  assert.deepEqual(commands, []);
});

test('no interpreter means not ready, and nothing is run', async () => {
  // The one failure with no graceful answer. It must not half-build anything.
  const commands: string[][] = [];
  const runtime = await ensureVenv(
    options({
      findPython: async () => undefined,
      run: async (command, args) => {
        commands.push([command, ...args]);
      },
    }),
  );
  assert.equal(runtime.ready, false);
  assert.deepEqual(commands, []);
});

test('a failed install leaves no stamp', async () => {
  // A stamp written after a failure is a broken environment that never
  // rebuilds itself, and the user has no way to learn that is why.
  let stamped: string | undefined;
  const runtime = await ensureVenv(
    options({
      run: async () => {
        throw new Error('pip exploded');
      },
      writeStamp: async (value) => {
        stamped = value;
      },
    }),
  );
  assert.equal(runtime.ready, false);
  assert.equal(stamped, undefined);
});

test('the reported interpreter is the venv one, even when not ready', async () => {
  // The caller reports and stays dormant; it must never be handed a path that
  // would start the wrong Python.
  const runtime = await ensureVenv(options({ findPython: async () => undefined }));
  assert.equal(runtime.python, '/storage/venv/bin/python');
});
