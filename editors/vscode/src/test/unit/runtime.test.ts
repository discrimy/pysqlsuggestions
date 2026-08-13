import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import {
  type EnsureOptions,
  ensureRuntime,
  interpreterPath,
  needsInstall,
  runtimeRoot,
  stampFor,
  stampPath,
} from '../../runtime';

const ARCHIVE = { name: 'runtime.tar.gz', size: 40_594_112 };

/** Everything that touches the outside world, stubbed, with a log of what it was asked to do. */
function harness(overrides: Partial<EnsureOptions> = {}): { options: EnsureOptions; log: string[] } {
  const log: string[] = [];
  const options: EnsureOptions = {
    root: '/storage',
    version: '0.4.1+abc',
    archive: '/ext/bundled/runtime.tar.gz',
    platform: 'linux',
    extract: async (from, into) => {
      log.push(`extract ${from} -> ${into}`);
    },
    makeExecutable: async (path) => {
      log.push(`chmod ${path}`);
    },
    remove: async (path) => {
      log.push(`remove ${path}`);
    },
    readStamp: async () => undefined,
    writeStamp: async (value) => {
      log.push(`stamp ${value}`);
    },
    ...overrides,
  };
  return { options, log };
}

test('a matching stamp unpacks nothing', () => {
  const { options, log } = harness({ readStamp: async () => '0.4.1+abc' });
  return ensureRuntime(options).then((runtime) => {
    assert.equal(runtime.ready, true);
    assert.deepEqual(log, []);
  });
});

test('a missing stamp unpacks, marks executable and stamps, in that order', async () => {
  const { options, log } = harness();
  const runtime = await ensureRuntime(options);
  assert.equal(runtime.ready, true);
  assert.deepEqual(log, [
    'remove /storage/runtime',
    'extract /ext/bundled/runtime.tar.gz -> /storage/runtime',
    'chmod /storage/runtime/python/bin/python3',
    'stamp 0.4.1+abc',
  ]);
});

test('a stale stamp removes what is there before unpacking over it', async () => {
  // A half-extracted tree from an activation the user killed would otherwise be
  // merged with the new one, and the result looks like a complete interpreter.
  const { options, log } = harness({ readStamp: async () => '0.4.0+old' });
  await ensureRuntime(options);
  assert.equal(log[0], 'remove /storage/runtime');
});

test('a failed extraction writes no stamp', async () => {
  const { options, log } = harness({
    extract: async () => {
      throw new Error('tar: unexpected end of file');
    },
  });
  const runtime = await ensureRuntime(options);
  assert.equal(runtime.ready, false);
  assert.equal(
    log.some((line) => line.startsWith('stamp')),
    false,
  );
});

test('a failed chmod writes no stamp either', async () => {
  // An interpreter that is present and not executable is the failure mode that
  // looks most like success, and a stamp over it never rebuilds.
  const { options, log } = harness({
    makeExecutable: async () => {
      throw new Error('EPERM');
    },
  });
  const runtime = await ensureRuntime(options);
  assert.equal(runtime.ready, false);
  assert.equal(
    log.some((line) => line.startsWith('stamp')),
    false,
  );
});

test('the interpreter it reports is the one it made executable', async () => {
  // Reporting one path and preparing another is a runtime that is ready and
  // cannot be started, which no later step would explain.
  const { options, log } = harness();
  const runtime = await ensureRuntime(options);
  assert.equal(log.includes(`chmod ${runtime.python}`), true);
});

test('the interpreter sits where each platform puts it', () => {
  assert.equal(interpreterPath('/storage', 'linux'), '/storage/runtime/python/bin/python3');
  assert.equal(interpreterPath('/storage', 'darwin'), '/storage/runtime/python/bin/python3');
  assert.equal(interpreterPath('/storage', 'win32'), '/storage/runtime/python/python.exe');
});

test('the runtime is unpacked beside the stamp, not over the whole storage directory', () => {
  // Extracting into globalStorage itself would put `python/` beside the stamp
  // and make `remove` delete the stamp too.
  assert.equal(runtimeRoot('/storage'), '/storage/runtime');
  assert.equal(stampPath('/storage'), '/storage/installed.txt');
});

test('the stamp changes when the archive does', () => {
  assert.notEqual(stampFor('0.4.1', ARCHIVE), stampFor('0.4.1', { ...ARCHIVE, size: ARCHIVE.size + 1 }));
  assert.equal(stampFor('0.4.1', ARCHIVE), stampFor('0.4.1', ARCHIVE));
});

test('an unreadable archive still yields a stamp', () => {
  // The extraction step reports a missing archive far more clearly than a
  // stamp mismatch would, so this must not throw on the way there.
  assert.equal(stampFor('0.4.1', undefined).startsWith('0.4.1+'), true);
});

test('any difference means reinstall, including a downgrade', () => {
  assert.equal(needsInstall('0.4.1+abc', '0.4.1+abc'), false);
  assert.equal(needsInstall('0.5.0+xyz', '0.4.1+abc'), true);
  assert.equal(needsInstall(undefined, '0.4.1+abc'), true);
});
