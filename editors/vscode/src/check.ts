/**
 * Asking a connection whether it works.
 *
 * Runs `python -m pysqlsuggestions_lsp.check` in the managed venv, which reuses
 * the server's own `Profile.from_options` and `open_catalog` — so a test
 * exercises the path the server will take rather than an approximation of it.
 *
 * A one-shot process rather than a request to the running server: any profile
 * can be tested, including one not in use, without touching the running
 * server's warm cache — and a connection that hangs cannot block the process
 * serving keystrokes.
 *
 * The governing rule, as everywhere else here: **this always produces a
 * verdict.** A user pressed a button and must get an answer, even when the
 * answer is that we could not ask.
 */

import { type Profile } from './profiles';

export interface Verdict {
  ok: boolean;
  detail: string;
}

export type Spawn = (input: string, timeoutMs: number) => Promise<string>;

/** Long enough for a slow VPN, short enough that nobody wonders if it hung. */
export const CHECK_TIMEOUT_MS = 10000;

/**
 * The verdict in `output`, or a verdict saying there was not one.
 *
 * Scans backwards for the last JSON object rather than parsing the whole
 * stream: a driver or an import warning may print first, and losing the answer
 * to unrelated noise would be its own bug.
 */
export function parseVerdict(output: string): Verdict {
  for (const line of output.split('\n').reverse()) {
    const start = line.indexOf('{');
    if (start === -1) {
      continue;
    }
    try {
      const parsed = JSON.parse(line.slice(start)) as Partial<Verdict>;
      if (typeof parsed.ok === 'boolean') {
        return { ok: parsed.ok, detail: typeof parsed.detail === 'string' ? parsed.detail : '' };
      }
    } catch {
      continue;
    }
  }
  return { ok: false, detail: 'the check produced no verdict' };
}

/** Test `profile`, using `password` if there is one. Never throws. */
export async function testConnection(
  profile: Profile,
  password: string | undefined,
  spawn: Spawn,
): Promise<Verdict> {
  // Built field by field, matching the server's contract exactly. `name` is
  // ours and has no field there; an absent field is not an empty one.
  const options: Record<string, unknown> = { dialect: profile.dialect, host: profile.host };
  if (profile.port !== undefined) {
    options.port = profile.port;
  }
  if (profile.database !== undefined) {
    options.database = profile.database;
  }
  if (profile.user !== undefined) {
    options.user = profile.user;
  }
  if (password !== undefined) {
    options.password = password;
  }

  try {
    return parseVerdict(await spawn(JSON.stringify(options), CHECK_TIMEOUT_MS));
  } catch (error) {
    return { ok: false, detail: error instanceof Error ? error.message : String(error) };
  }
}
