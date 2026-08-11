/**
 * Connections, as settings hold them.
 *
 * VS Code resolves array settings by **override**, not element-wise merge: a
 * workspace `pysqlsuggestions.connections` replaces the user one wholesale, and
 * an empty array overrides just as firmly as a full one. So exactly one scope
 * is in effect at a time, and this module says which.
 *
 * That matters twice. Listing a union would show connections the extension can
 * never use, and writing to the losing scope would create one silently — a
 * connection that appears in settings.json and never in the editor.
 *
 * Nothing here imports `vscode`. The accessor is passed in, which is what makes
 * all of this testable without an editor.
 */

import { type Profile, readProfiles } from './profiles';

export type Scope = 'user' | 'workspace';

export interface Stored {
  profile: Profile;
  scope: Scope;
}

export interface SettingsAccess {
  user(): unknown;
  workspace(): unknown;
  write(scope: Scope, value: unknown[]): Promise<void>;
}

/**
 * Which scope's list is actually in effect.
 *
 * A workspace array wins even when empty, because `[]` is a value: treating it
 * as absent would resurrect user connections somebody deliberately switched off.
 */
export function effectiveScope(access: SettingsAccess): Scope {
  return Array.isArray(access.workspace()) ? 'workspace' : 'user';
}

/** The connections the extension will actually use, with where they came from. */
export function listConnections(access: SettingsAccess): Stored[] {
  const scope = effectiveScope(access);
  const raw = scope === 'workspace' ? access.workspace() : access.user();
  return readProfiles(raw).map((profile) => ({ profile, scope }));
}

/**
 * The stored form of a profile: the settings schema's fields and nothing else.
 *
 * Built field by field rather than spread, so a password that reached a
 * `Profile` in memory cannot follow it into settings, and an optional field
 * that was cleared leaves no `undefined` behind.
 */
function stored(profile: Profile): Record<string, unknown> {
  const entry: Record<string, unknown> = {
    name: profile.name,
    dialect: profile.dialect,
    host: profile.host,
  };
  if (profile.port !== undefined) {
    entry.port = profile.port;
  }
  if (profile.database !== undefined) {
    entry.database = profile.database;
  }
  if (profile.user !== undefined) {
    entry.user = profile.user;
  }
  return entry;
}

/** Append `profile` to whichever list is in effect. */
export async function addConnection(access: SettingsAccess, profile: Profile): Promise<void> {
  const existing = listConnections(access).map((entry) => stored(entry.profile));
  await access.write(effectiveScope(access), [...existing, stored(profile)]);
}

/** Replace the connection called `name`. Writes nothing when there is none. */
export async function updateConnection(
  access: SettingsAccess,
  name: string,
  profile: Profile,
): Promise<void> {
  const entries = listConnections(access);
  if (!entries.some((entry) => entry.profile.name === name)) {
    return;
  }
  await access.write(
    effectiveScope(access),
    entries.map((entry) => stored(entry.profile.name === name ? profile : entry.profile)),
  );
}

/** Drop the connection called `name`. Writes nothing when there is none. */
export async function removeConnection(access: SettingsAccess, name: string): Promise<void> {
  const entries = listConnections(access);
  if (!entries.some((entry) => entry.profile.name === name)) {
    return;
  }
  await access.write(
    effectiveScope(access),
    entries.filter((entry) => entry.profile.name !== name).map((entry) => stored(entry.profile)),
  );
}
