/**
 * The interpreter the VSIX carries.
 *
 * There is no interpreter discovery here any more, and that is the point. The
 * extension used to find a `python3` and build a venv in it, which fails in an
 * open-ended number of ways — an unbundled `ensurepip`, PEP 668, a Windows
 * Store stub, a conda environment shadowing what we install. Every one of them
 * is a separate detection rule written against a machine we do not have, and
 * the graceful answer to an open-ended set of environment failures is to stop
 * depending on the environment.
 *
 * What is left is: unpack an archive once per version, mark its interpreter
 * executable, remember that it worked. The workspace's own environment is
 * never touched, and neither is the machine's.
 *
 * Everything that touches the outside world is injected, so every decision here
 * is testable without spawning a process — which matters because the failures
 * that count are the ones where nothing should happen at all.
 */

export interface Runtime {
  python: string;
  ready: boolean;
}

/** Where the archive is unpacked. One directory, replaced wholesale. */
export function runtimeRoot(root: string): string {
  return `${root}/runtime`;
}

/**
 * The bundled interpreter's path.
 *
 * Every python-build-standalone archive unpacks to a single `python/`
 * directory. Windows puts the executable at its root; everyone else under
 * `bin/`, where `python3` is a symlink the archive carries.
 */
export function interpreterPath(root: string, platform: NodeJS.Platform): string {
  const base = `${runtimeRoot(root)}/python`;
  return platform === 'win32' ? `${base}/python.exe` : `${base}/bin/python3`;
}

/**
 * Where the installed-version marker lives — beside the runtime, never inside it.
 *
 * Inside, deleting a broken tree would delete the evidence that it was ever
 * unpacked, and a half-deleted one would keep a stamp claiming it was fine.
 */
export function stampPath(root: string): string {
  return `${root}/installed.txt`;
}

/**
 * Whether the runtime has to be unpacked for `stamp`.
 *
 * Any difference means install, not just an older one: a downgraded extension
 * carries an archive its existing tree has never seen either.
 */
export function needsInstall(existing: string | undefined, wanted: string): boolean {
  return existing !== wanted;
}

/**
 * What the installed runtime must match: the version, and the archive.
 *
 * The version alone is not enough, and that is not hypothetical — a server
 * rebuilt under an unchanged version once left an environment holding a package
 * with no `check.py` in it, and nothing noticed because the number had not
 * moved. The archive is the thing actually installed, so the archive is what is
 * fingerprinted.
 *
 * Name and size rather than a content hash: reading forty megabytes on every
 * activation to detect a change that only happens when the extension is rebuilt
 * would be paying constantly for a rare event.
 *
 * `undefined` when the archive cannot be read, which makes the stamp depend on
 * the version alone. The extraction step reports a missing archive far more
 * clearly than a stamp mismatch ever would.
 */
export function stampFor(version: string, archive: { name: string; size: number } | undefined): string {
  const listed = archive === undefined ? '' : `${archive.name}:${String(archive.size)}`;
  let hash = 0;
  for (let index = 0; index < listed.length; index += 1) {
    hash = (Math.imul(hash, 31) + listed.charCodeAt(index)) | 0;
  }
  // The version leads so that a human opening the file learns something.
  return `${version}+${(hash >>> 0).toString(16)}`;
}

export interface EnsureOptions {
  root: string;
  /** What the installed runtime must match — see `stampFor`. */
  version: string;
  /** The bundled archive, inside the VSIX. */
  archive: string;
  platform: NodeJS.Platform;
  extract: (archive: string, into: string) => Promise<void>;
  makeExecutable: (path: string) => Promise<void>;
  remove: (path: string) => Promise<void>;
  readStamp: () => Promise<string | undefined>;
  writeStamp: (value: string) => Promise<void>;
}

/**
 * The interpreter to run the server with.
 *
 * `ready` false means the caller should report and stay dormant — never
 * half-start. Nothing is written on failure, so the next activation tries again
 * rather than trusting a stamp over a tree that is not there.
 */
export async function ensureRuntime(options: EnsureOptions): Promise<Runtime> {
  const python = interpreterPath(options.root, options.platform);
  if (!needsInstall(await options.readStamp(), options.version)) {
    return { python, ready: true };
  }

  try {
    // Removed rather than extracted over: a half-unpacked tree from an
    // activation the user killed would merge with the new one, and the result
    // looks exactly like a complete interpreter.
    await options.remove(runtimeRoot(options.root));
    await options.extract(options.archive, runtimeRoot(options.root));
    // The archive carries the bit, but a zip in the middle of the delivery
    // chain does not, and an interpreter that is present and not executable is
    // the failure that looks most like success.
    await options.makeExecutable(python);
  } catch {
    // No stamp on failure: a stamp written here is a broken runtime that never
    // rebuilds itself, and the user has no way to learn that is why.
    return { python, ready: false };
  }

  await options.writeStamp(options.version);
  return { python, ready: true };
}
