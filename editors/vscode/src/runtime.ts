/**
 * The extension's private Python environment.
 *
 * Built once per version from wheels inside the VSIX, with `--no-index` so the
 * install cannot reach the network: a first run on a train must work, and a
 * wheel that is missing is a build bug rather than a slow download.
 *
 * The workspace's own environment is never touched. A user who curated a
 * project venv did not curate it for this.
 *
 * Everything that touches the outside world is injected, so every decision here
 * is testable without spawning a process — which matters because the failures
 * that count are the ones where nothing should happen at all.
 */

export interface Runtime {
  python: string;
  ready: boolean;
}

/** The library's own floor. A venv below it installs none of the wheels. */
export const MINIMUM_PYTHON = '3.10';

/**
 * Whether an interpreter reporting `version` can run the server.
 *
 * Checking that `--version` exits zero is not enough, and this is not a
 * hypothetical: on Windows `python3` is often a Store stub that prints
 * `Python`, exits zero and installs nothing, and a system `python` of 3.9 will
 * happily build a venv that then refuses every wheel in the bundle. Both
 * produce a working-looking extension with no completion in it.
 */
export function meetsMinimum(reported: string): boolean {
  const match = /^(\d+)\.(\d+)/.exec(reported.trim());
  if (match === null) {
    return false;
  }
  const major = Number(match[1]);
  const minor = Number(match[2]);
  return major > 3 || (major === 3 && minor >= 10);
}

/**
 * The first candidate that runs and is new enough, or undefined.
 *
 * `probe` reports an interpreter's version and throws when it cannot run at
 * all. Failures are swallowed per candidate because only the caller knows
 * whether running out of them matters.
 */
export async function findInterpreter(
  candidates: readonly string[],
  probe: (command: string) => Promise<string>,
): Promise<string | undefined> {
  for (const candidate of candidates) {
    try {
      if (meetsMinimum(await probe(candidate))) {
        return candidate;
      }
    } catch {
      continue;
    }
  }
  return undefined;
}

export interface EnsureOptions {
  root: string;
  /** What the installed environment must match — see `stampFor`. */
  version: string;
  wheelDir: string;
  platform: NodeJS.Platform;
  findPython: () => Promise<string | undefined>;
  run: (command: string, args: string[]) => Promise<void>;
  readStamp: () => Promise<string | undefined>;
  writeStamp: (value: string) => Promise<void>;
}

/** Where the venv's interpreter sits, which differs by platform. */
export function venvPython(root: string, platform: NodeJS.Platform): string {
  return platform === 'win32' ? `${root}/venv/Scripts/python.exe` : `${root}/venv/bin/python`;
}

/**
 * Where the installed-version marker lives — beside the venv, never inside it.
 *
 * Inside, deleting a broken venv would delete the evidence that it was ever
 * built, and a half-deleted one would keep a stamp claiming it was fine.
 */
export function stampPath(root: string): string {
  return `${root}/installed.txt`;
}

/**
 * Whether the environment has to be built for `stamp`.
 *
 * Any difference means install, not just an older one: a downgraded extension
 * carries wheels its existing venv has never seen either.
 */
export function needsInstall(existing: string | undefined, wanted: string): boolean {
  return existing !== wanted;
}

/**
 * What the installed environment must match: the version, and the bundle.
 *
 * The version alone is not enough, and that is not hypothetical — a server
 * rebuilt under an unchanged version once left a venv holding a package with no
 * `check.py` in it, and nothing noticed because the number had not moved. The
 * wheels are the thing actually installed, so the wheels are what is fingerprinted.
 *
 * Names and sizes rather than content hashes: reading twelve wheels on every
 * activation to detect a change that only happens when the extension is
 * rebuilt would be paying constantly for a rare event.
 *
 * Sorted, because directory order is not a fact about the bundle and
 * reinstalling at random would be worse than not reinstalling at all.
 */
export function stampFor(version: string, wheels: readonly { name: string; size: number }[]): string {
  const listed = [...wheels]
    .map((wheel) => `${wheel.name}:${String(wheel.size)}`)
    .sort()
    .join('|');
  let hash = 0;
  for (let index = 0; index < listed.length; index += 1) {
    hash = (Math.imul(hash, 31) + listed.charCodeAt(index)) | 0;
  }
  // The version leads so that a human opening the file learns something.
  return `${version}+${(hash >>> 0).toString(16)}`;
}

/**
 * The interpreter to run the server with.
 *
 * `ready` false means the caller should report and stay dormant — never
 * half-start. The only unrecoverable case is having no interpreter at all,
 * and in that case nothing is run and nothing is written.
 */
export async function ensureVenv(options: EnsureOptions): Promise<Runtime> {
  const python = venvPython(options.root, options.platform);
  if (!needsInstall(await options.readStamp(), options.version)) {
    return { python, ready: true };
  }

  const found = await options.findPython();
  if (found === undefined) {
    return { python, ready: false };
  }

  try {
    await options.run(found, ['-m', 'venv', `${options.root}/venv`]);
    await options.run(python, [
      '-m',
      'pip',
      'install',
      '--no-index',
      // pip skips a distribution whose version is already installed, whatever
      // the wheel now contains — "requirement already satisfied", and the old
      // files stay. Since we only reach here when the bundle's fingerprint
      // changed, replacing unconditionally is the point rather than a cost.
      '--force-reinstall',
      '--find-links',
      options.wheelDir,
      // The extra is what carries the driver. Without it the server installs
      // and can read no catalog at all, which looks like a working extension
      // that has stopped being schema-aware.
      'pysqlsuggestions-lsp[pg8000]',
    ]);
  } catch {
    // No stamp on failure: a stamp written here is a broken environment that
    // never rebuilds itself, and the user has no way to learn that is why.
    return { python, ready: false };
  }

  await options.writeStamp(options.version);
  return { python, ready: true };
}
