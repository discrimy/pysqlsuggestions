import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import {
  type EnsureOptions,
  ensureVenv,
  findInterpreter,
  meetsMinimum,
  needsInstall,
  stampPath,
  venvPython,
} from '../../runtime';

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

test('an interpreter at or above the floor is accepted', () => {
  assert.equal(meetsMinimum('3.12.11'), true);
  assert.equal(meetsMinimum('3.10.0'), true);
  assert.equal(meetsMinimum('4.0.0'), true);
});

test('an interpreter below the floor is rejected', () => {
  // 3.9 builds a venv happily and then refuses every wheel in the bundle,
  // leaving a working-looking extension with no completion in it.
  assert.equal(meetsMinimum('3.9.13'), false);
  assert.equal(meetsMinimum('2.7.18'), false);
});

test('a stub that reports no version is rejected', () => {
  // On Windows `python3` is often a Store stub: it prints `Python`, exits
  // zero, and installs nothing. Exit status alone cannot tell it apart.
  assert.equal(meetsMinimum('Python'), false);
  assert.equal(meetsMinimum(''), false);
});

test('the first adequate interpreter is chosen', async () => {
  const probed: string[] = [];
  const found = await findInterpreter(['python3', 'python'], async (command) => {
    probed.push(command);
    return command === 'python3' ? 'Python' : '3.12.1';
  });
  assert.equal(found, 'python');
  assert.deepEqual(probed, ['python3', 'python']);
});

test('a candidate that cannot run is skipped', async () => {
  const found = await findInterpreter(['missing', 'python'], async (command) => {
    if (command === 'missing') {
      throw new Error('ENOENT');
    }
    return '3.11.0';
  });
  assert.equal(found, 'python');
});

test('no adequate interpreter is undefined, not a bad one', async () => {
  // Returning the too-old one would build a venv that installs nothing.
  assert.equal(await findInterpreter(['python'], async () => '3.9.13'), undefined);
});

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
