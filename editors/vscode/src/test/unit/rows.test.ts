import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { rowFor } from '../../rows';

const ENTRY = {
  profile: { name: 'docker', dialect: 'postgres', host: 'localhost', port: 57432, user: 'report' },
  scope: 'user' as const,
};

test('the row names the connection and describes where it points', () => {
  const row = rowFor(ENTRY, 'untested', false);
  assert.equal(row.label, 'docker');
  assert.equal(row.description, 'postgres · localhost:57432');
});

test('a connection with no port says so rather than showing undefined', () => {
  const bare = { ...ENTRY, profile: { name: 'x', dialect: 'postgres', host: 'db' } };
  assert.equal(rowFor(bare, 'untested', false).description, 'postgres · db');
});

test('the one in use is marked in the description', () => {
  assert.match(rowFor(ENTRY, 'ok', true).description, /in use$/);
});

test('health and being in use are shown separately', () => {
  // The connection in use may be the broken one, and that is exactly the case
  // worth seeing. Conflating them is how a status display starts lying.
  const broken = rowFor(ENTRY, 'failed', true);
  assert.equal(broken.icon, 'warning');
  assert.match(broken.description, /in use$/);
});

test('each health has its own icon', () => {
  assert.equal(rowFor(ENTRY, 'untested', false).icon, 'circle-outline');
  assert.equal(rowFor(ENTRY, 'testing', false).icon, 'sync~spin');
  assert.equal(rowFor(ENTRY, 'ok', false).icon, 'pass-filled');
  assert.equal(rowFor(ENTRY, 'failed', false).icon, 'warning');
});

test('the tooltip carries what the row cannot', () => {
  const row = rowFor(ENTRY, 'failed', false, 'password authentication failed');
  assert.match(row.tooltip, /report/);
  assert.match(row.tooltip, /password authentication failed/);
});

test('the tooltip says which settings the connection lives in', () => {
  // Editing writes back to that scope, so a user should be able to see which.
  assert.match(rowFor({ ...ENTRY, scope: 'workspace' }, 'ok', false).tooltip, /workspace/);
});

test('a connection with no user says so rather than leaving a gap', () => {
  const bare = { ...ENTRY, profile: { name: 'x', dialect: 'postgres', host: 'db' } };
  assert.match(rowFor(bare, 'untested', false).tooltip, /no user set/);
});

test('every row is a connection for the menus to attach to', () => {
  assert.equal(rowFor(ENTRY, 'ok', false).contextValue, 'connection');
});
