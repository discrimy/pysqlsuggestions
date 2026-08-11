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

export interface Completed {
  stdout: string;
  stderr: string;
  code: number | null;
}

export type Spawn = (input: string, timeoutMs: number) => Promise<Completed>;

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

/**
 * The verdict a finished process amounts to.
 *
 * A verdict on stdout always wins: a checker that answered and then died still
 * answered. Failing that, a non-zero exit means *this harness* broke — a
 * missing module, a half-built venv — which has to read differently from a
 * database that refused, because the fix is completely different. The last line
 * of stderr is the part that names the fault; Python puts the traceback above
 * it.
 */
export function verdictOf(result: Completed): Verdict {
  const parsed = parseVerdict(result.stdout);
  if (parsed.detail !== 'the check produced no verdict') {
    return parsed;
  }
  if (result.code !== 0) {
    const lines = result.stderr.split('\n').filter((line) => line.trim().length > 0);
    const reason = lines[lines.length - 1] ?? `exit code ${String(result.code)}`;
    return { ok: false, detail: `the checker could not run: ${reason.trim()}` };
  }
  return parsed;
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
    return verdictOf(await spawn(JSON.stringify(options), CHECK_TIMEOUT_MS));
  } catch (error) {
    return { ok: false, detail: error instanceof Error ? error.message : String(error) };
  }
}
