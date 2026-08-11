import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import {
  type Scope,
  type SettingsAccess,
  addConnection,
  effectiveScope,
  listConnections,
  removeConnection,
  updateConnection,
} from '../../store';

const PG = { name: 'local', dialect: 'postgres', host: 'localhost', port: 5432, user: 'ana' };
const FAR = { name: 'staging', dialect: 'postgres', host: 'far', user: 'ana' };

/** An accessor over two plain arrays, recording what was written where. */
function access(user: unknown[] = [], workspace: unknown[] | undefined = undefined) {
  const writes: { scope: Scope; value: unknown[] }[] = [];
  const settings: SettingsAccess = {
    user: () => user,
    workspace: () => workspace,
    write: async (scope, value) => {
      writes.push({ scope, value });
    },
  };
  return { settings, writes };
}

test('user connections are listed when no workspace list exists', () => {
  const { settings } = access([PG]);
  assert.deepEqual(
    listConnections(settings).map((entry) => [entry.profile.name, entry.scope]),
    [['local', 'user']],
  );
});

test('a workspace list replaces the user list rather than joining it', () => {
  // VS Code resolves array settings by override, not element-wise merge. A
  // union here would list connections the extension can never use.
  const { settings } = access([PG], [FAR]);
  assert.deepEqual(
    listConnections(settings).map((entry) => [entry.profile.name, entry.scope]),
    [['staging', 'workspace']],
  );
});

test('an empty workspace array still overrides', () => {
  // `[]` is a value and VS Code treats it as one. Falling back to user here
  // would resurrect connections somebody deliberately switched off.
  const { settings } = access([PG], []);
  assert.deepEqual(listConnections(settings), []);
});

test('malformed entries are dropped, not thrown over', () => {
  const { settings } = access([PG, { name: 'broken' }]);
  assert.equal(listConnections(settings).length, 1);
});

test('the effective scope is workspace when a workspace list exists', () => {
  assert.equal(effectiveScope(access([PG], [FAR]).settings), 'workspace');
  assert.equal(effectiveScope(access([PG]).settings), 'user');
});

test('adding writes to user settings in the ordinary case', async () => {
  const { settings, writes } = access([PG]);
  await addConnection(settings, { ...FAR, name: 'new' });
  assert.equal(writes[0]?.scope, 'user');
  assert.equal(writes[0]?.value.length, 2);
});

test('adding while a workspace list overrides writes there instead', async () => {
  // Writing to user here would store a connection the extension can never
  // read, because the workspace array is what it resolves.
  const { settings, writes } = access([PG], [FAR]);
  await addConnection(settings, { ...FAR, name: 'new' });
  assert.equal(writes[0]?.scope, 'workspace');
  assert.equal(writes[0]?.value.length, 2);
});

test('editing writes back to the scope the connection came from', async () => {
  const { settings, writes } = access([PG], [FAR]);
  await updateConnection(settings, 'staging', { ...FAR, host: 'moved' });
  assert.equal(writes[0]?.scope, 'workspace');
  assert.equal((writes[0]?.value[0] as { host: string }).host, 'moved');
});

test('editing keeps the other connections untouched', async () => {
  const { settings, writes } = access([PG, FAR]);
  await updateConnection(settings, 'local', { ...PG, port: 6000 });
  assert.equal(writes[0]?.value.length, 2);
  assert.equal((writes[0]?.value[1] as { name: string }).name, 'staging');
});

test('renaming is an edit like any other', async () => {
  const { settings, writes } = access([PG]);
  await updateConnection(settings, 'local', { ...PG, name: 'renamed' });
  assert.equal((writes[0]?.value[0] as { name: string }).name, 'renamed');
});

test('clearing an optional field removes the key rather than storing undefined', async () => {
  const { settings, writes } = access([PG]);
  await updateConnection(settings, 'local', { ...PG, port: undefined });
  assert.equal('port' in (writes[0]?.value[0] as object), false);
});

test('removing takes only the named one', async () => {
  const { settings, writes } = access([PG, FAR]);
  await removeConnection(settings, 'local');
  assert.deepEqual(
    (writes[0]?.value as { name: string }[]).map((entry) => entry.name),
    ['staging'],
  );
});

test('a stored connection never carries a password field', async () => {
  // Settings are the one place a password must never reach, and a Profile in
  // memory may well have one.
  const { settings, writes } = access([]);
  await addConnection(settings, { ...PG, ...({ password: 'hunter2' } as object) });
  assert.equal('password' in (writes[0]?.value[0] as object), false);
});

test('editing something that is not there writes nothing', async () => {
  const { settings, writes } = access([PG]);
  await updateConnection(settings, 'ghost', { ...PG, name: 'ghost' });
  assert.deepEqual(writes, []);
});

test('removing something that is not there writes nothing', async () => {
  const { settings, writes } = access([PG]);
  await removeConnection(settings, 'ghost');
  assert.deepEqual(writes, []);
});
