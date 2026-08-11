/**
 * What one connection looks like in the view, as data.
 *
 * Separate from `tree.ts` because that imports `vscode`, which does not exist
 * outside an editor — and everything here is a decision worth testing without
 * standing one up.
 *
 * Two facts are rendered and deliberately not merged: the **icon** is health,
 * and the `· in use` suffix is which profile the running server holds. The
 * connection in use may be the broken one, which is exactly the case worth
 * seeing; conflating them is how a status display starts lying.
 */

import { type Stored } from './store';

export type Health = 'untested' | 'testing' | 'ok' | 'failed';

export interface Row {
  label: string;
  description: string;
  icon: string;
  tooltip: string;
  contextValue: string;
}

const ICONS: Record<Health, string> = {
  untested: 'circle-outline',
  testing: 'sync~spin',
  ok: 'pass-filled',
  failed: 'warning',
};

/**
 * Everything shown for one connection.
 *
 * Health is never persisted, so `untested` is the honest state for a
 * connection nobody has asked about this session.
 */
export function rowFor(entry: Stored, health: Health, inUse: boolean, detail?: string): Row {
  const { profile } = entry;
  const target = profile.port === undefined ? profile.host : `${profile.host}:${String(profile.port)}`;
  const lines = [
    `**${profile.name}** — ${profile.dialect}`,
    `${target}${profile.database === undefined ? '' : ` · ${profile.database}`}`,
    profile.user === undefined ? 'no user set' : `as ${profile.user}`,
    `defined in ${entry.scope} settings`,
  ];
  if (detail !== undefined && detail.length > 0) {
    lines.push('', detail);
  }
  return {
    label: profile.name,
    description: `${profile.dialect} · ${target}${inUse ? ' · in use' : ''}`,
    icon: ICONS[health],
    tooltip: lines.join('\n\n'),
    contextValue: 'connection',
  };
}
