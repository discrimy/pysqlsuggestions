import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { type Verdict, parseVerdict, testConnection } from '../../check';

const PG = { name: 'local', dialect: 'postgres', host: 'localhost', port: 5432, user: 'ana' };

test('a verdict is read from the JSON the checker printed', () => {
  assert.deepEqual(parseVerdict('{"ok":true,"detail":"3 relations visible"}'), {
    ok: true,
    detail: '3 relations visible',
  });
});

test('surrounding noise does not stop a verdict being found', () => {
  // A driver warning on stdout must not cost the user their answer.
  const verdict = parseVerdict('some warning\n{"ok":false,"detail":"refused"}\n');
  assert.equal(verdict.ok, false);
  assert.equal(verdict.detail, 'refused');
});

test('unparseable output is a verdict, not an exception', () => {
  const verdict = parseVerdict('Traceback (most recent call last): ...');
  assert.equal(verdict.ok, false);
  assert.match(verdict.detail, /no verdict/);
});

test('empty output is a verdict too', () => {
  assert.equal(parseVerdict('').ok, false);
});

test('a verdict missing its detail still parses', () => {
  // `ok` is the part that decides an icon; a missing detail costs a tooltip.
  assert.deepEqual(parseVerdict('{"ok":true}'), { ok: true, detail: '' });
});

test('something that is JSON but not a verdict is rejected', () => {
  assert.match(parseVerdict('{"unrelated":1}').detail, /no verdict/);
});

test('the profile reaches the checker without its name', async () => {
  // `name` is the extension's own. The server contract has no field for it,
  // and sending it would be a second, silent definition of that contract.
  let sent = '';
  await testConnection(PG, 'hunter2', async (input) => {
    sent = input;
    return '{"ok":true,"detail":"ok"}';
  });
  const parsed = JSON.parse(sent) as Record<string, unknown>;
  assert.equal('name' in parsed, false);
  assert.equal(parsed.password, 'hunter2');
  assert.equal(parsed.dialect, 'postgres');
  assert.equal(parsed.port, 5432);
});

test('no password sends no password field', async () => {
  // The server distinguishes absent from empty, and so must this.
  let sent = '';
  await testConnection(PG, undefined, async (input) => {
    sent = input;
    return '{"ok":true,"detail":"ok"}';
  });
  assert.equal('password' in (JSON.parse(sent) as object), false);
});

test('a spawn that throws becomes a failed verdict', async () => {
  // The rule: testing always produces a verdict. A user pressed a button and
  // must get an answer, even when the answer is that we could not ask.
  const verdict: Verdict = await testConnection(PG, undefined, async () => {
    throw new Error('venv is not ready');
  });
  assert.equal(verdict.ok, false);
  assert.match(verdict.detail, /venv is not ready/);
});

test('the spawn is given a timeout to enforce', async () => {
  let given = 0;
  await testConnection(PG, undefined, async (_input, timeoutMs) => {
    given = timeoutMs;
    return '{"ok":true,"detail":"ok"}';
  });
  assert.ok(given > 0, 'no timeout was passed to the spawn');
});
