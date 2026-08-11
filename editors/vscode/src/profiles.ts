/**
 * Connection profiles, as the user wrote them and as the server wants them.
 *
 * Settings are user-edited JSON, so every field is checked rather than trusted.
 * A malformed entry costs that entry and nothing else: an extension that throws
 * on activation because one profile has a typo is worse than one that quietly
 * offers fewer connections.
 *
 * Nothing here imports `vscode`. The settings value is passed in, which is what
 * makes all of this testable without an editor.
 */

export interface Profile {
  name: string;
  dialect: string;
  host: string;
  port?: number;
  database?: string;
  user?: string;
}

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined;
}

/** The valid entries in `raw`, in order. Invalid ones are dropped. */
export function readProfiles(raw: unknown): Profile[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  const profiles: Profile[] = [];
  for (const entry of raw) {
    if (typeof entry !== 'object' || entry === null) {
      continue;
    }
    const record = entry as Record<string, unknown>;
    const name = text(record.name);
    const dialect = text(record.dialect);
    const host = text(record.host);
    // The name keys the stored password, so an unnamed profile has nowhere to
    // keep one. All three are required by the settings schema too; this is the
    // check that survives someone editing settings.json by hand.
    if (name === undefined || dialect === undefined || host === undefined) {
      continue;
    }
    profiles.push({
      name,
      dialect,
      host,
      port: typeof record.port === 'number' ? record.port : undefined,
      database: text(record.database),
      user: text(record.user),
    });
  }
  return profiles;
}

/**
 * The profile to use, or undefined.
 *
 * With several connections and no preference, none is chosen. Guessing would
 * mean completing against the wrong database, and a wrong schema looks exactly
 * like a working one until it matters.
 */
export function resolveProfile(profiles: Profile[], preferred: string | null): Profile | undefined {
  if (preferred !== null) {
    return profiles.find((profile) => profile.name === preferred);
  }
  return profiles.length === 1 ? profiles[0] : undefined;
}

/**
 * What goes in `initializationOptions`, matching the server's `Profile.from_options`.
 *
 * `name` is ours and has no field there. Optional fields are omitted rather
 * than set to undefined: the server type-checks each one, and a key present
 * with a null value is not the same as an absent key.
 *
 * Undefined when there is no profile, which is how the server is told to
 * complete from the statement alone.
 */
export function initializationOptions(
  profile: Profile | undefined,
  password: string | undefined,
): Record<string, unknown> | undefined {
  if (profile === undefined) {
    return undefined;
  }
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
  return options;
}
