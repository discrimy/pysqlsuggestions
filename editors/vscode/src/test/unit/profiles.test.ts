import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { initializationOptions, needsPassword, readProfiles, resolveProfile } from '../../profiles';

const PG = { name: 'local', dialect: 'postgres', host: 'localhost', port: 5432, user: 'ana' };

test('a well formed entry is read', () => {
  const profiles = readProfiles([PG]);
  assert.equal(profiles.length, 1);
  assert.equal(profiles[0]?.host, 'localhost');
});

test('an entry missing a dialect is dropped rather than throwing', () => {
  // Settings are user-edited JSON; half a profile must cost that profile, not
  // the extension. Throwing on activation would take completion out entirely.
  assert.deepEqual(readProfiles([{ name: 'x', host: 'h' }]), []);
});

test('an entry missing a host is dropped', () => {
  assert.deepEqual(readProfiles([{ name: 'x', dialect: 'postgres' }]), []);
});

test('an entry missing a name is dropped', () => {
  // The name keys the stored password, so an unnamed profile has nowhere to
  // keep one.
  assert.deepEqual(readProfiles([{ dialect: 'postgres', host: 'h' }]), []);
});

test('a non-array is no profiles', () => {
  assert.deepEqual(readProfiles('nonsense'), []);
});

test('a null entry does not throw', () => {
  assert.deepEqual(readProfiles([null, PG]).length, 1);
});

test('a port of the wrong type is dropped but the profile survives', () => {
  const profile = readProfiles([{ ...PG, port: '5432' }])[0];
  assert.equal(profile?.port, undefined);
  assert.equal(profile?.host, 'localhost');
});

test('the preferred connection wins', () => {
  const profiles = readProfiles([PG, { ...PG, name: 'staging', host: 'far' }]);
  assert.equal(resolveProfile(profiles, 'staging')?.host, 'far');
});

test('with no preference and one connection, that one is used', () => {
  assert.equal(resolveProfile(readProfiles([PG]), null)?.name, 'local');
});

test('with no preference and several connections, none is guessed', () => {
  // Guessing means completing against the wrong database, and a wrong schema
  // looks exactly like a working one until it matters.
  const profiles = readProfiles([PG, { ...PG, name: 'prod', host: 'far' }]);
  assert.equal(resolveProfile(profiles, null), undefined);
});

test('a named connection that does not exist resolves to nothing', () => {
  assert.equal(resolveProfile(readProfiles([PG]), 'typo'), undefined);
});

test('initialization options carry the password and drop the name', () => {
  // `name` is ours; the server's Profile.from_options has no field for it.
  const options = initializationOptions(readProfiles([PG])[0], 'hunter2');
  assert.equal(options?.password, 'hunter2');
  assert.equal(options?.dialect, 'postgres');
  assert.equal('name' in (options ?? {}), false);
});

test('no profile means no options, which is the servers degraded mode', () => {
  assert.equal(initializationOptions(undefined, undefined), undefined);
});

test('a profile with no password still produces options', () => {
  // Trust authentication and .pgpass both exist; a missing password is not a
  // missing profile.
  const options = initializationOptions(readProfiles([PG])[0], undefined);
  assert.equal(options?.host, 'localhost');
  assert.equal('password' in (options ?? {}), false);
});

test('absent optional fields are omitted rather than sent as undefined', () => {
  // The server type-checks each field; a key present with a null value is not
  // the same as an absent key.
  const bare = readProfiles([{ name: 'b', dialect: 'ansi', host: 'h' }])[0];
  const options = initializationOptions(bare, undefined) ?? {};
  assert.deepEqual(Object.keys(options).sort(), ['dialect', 'host']);
});

test('a profile with a user and no stored password wants prompting', () => {
  // Configuring a connection by editing settings.json never goes near the
  // prompt, so without this the server connects unauthenticated and silently
  // degrades — which looks like an extension that does not work.
  assert.equal(needsPassword(readProfiles([PG])[0], undefined, new Set()), true);
});

test('a profile with a stored password does not', () => {
  assert.equal(needsPassword(readProfiles([PG])[0], 'hunter2', new Set()), false);
});

test('a profile with no user does not', () => {
  // No user means the driver picks one up from the environment, and asking
  // for a password to go with a username we do not have helps nobody.
  const bare = readProfiles([{ name: 'b', dialect: 'postgres', host: 'h' }])[0];
  assert.equal(needsPassword(bare, undefined, new Set()), false);
});

test('a profile the user already declined is not asked again', () => {
  // Dismissing is a legitimate answer: trust authentication and .pgpass both
  // mean no password is needed. Asking every restart would be nagging.
  assert.equal(needsPassword(readProfiles([PG])[0], undefined, new Set(['local'])), false);
});

test('no profile wants nothing', () => {
  assert.equal(needsPassword(undefined, undefined, new Set()), false);
});

test('secure is read when it is a boolean and dropped when it is not', () => {
  const profiles = readProfiles([
    { name: 'a', dialect: 'trino', host: 'h', secure: true },
    { name: 'b', dialect: 'trino', host: 'h', secure: 'yes' },
    { name: 'c', dialect: 'trino', host: 'h' },
  ]);
  assert.deepStrictEqual(
    profiles.map((profile) => profile.secure),
    [true, undefined, undefined],
  );
});

test('verify is carried only when it is a boolean', () => {
  const profiles = readProfiles([
    { name: 'a', dialect: 'trino', host: 'h', secure: true, verify: false },
    { name: 'b', dialect: 'trino', host: 'h', secure: true, verify: 'no' },
  ]);
  assert.deepStrictEqual(
    profiles.map((profile) => profile.verify),
    [false, undefined],
  );
});

test('an omitted verify sends no key, so the server keeps its safe default', () => {
  // Sending `verify: undefined` would serialise as a present key on some paths.
  // The server reads `is not False`, so only a real false may ever reach it.
  const options = initializationOptions({ name: 'a', dialect: 'trino', host: 'h', secure: true }, undefined);
  assert.equal('verify' in (options ?? {}), false);
});

test('an explicit verify false does reach the server', () => {
  const options = initializationOptions(
    { name: 'a', dialect: 'trino', host: 'h', secure: true, verify: false },
    undefined,
  );
  assert.equal(options?.verify, false);
});
