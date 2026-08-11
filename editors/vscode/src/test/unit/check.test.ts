import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { type Verdict, parseVerdict, testConnection, verdictOf } from '../../check';

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
    return { stdout: '{"ok":true,"detail":"ok"}', stderr: '', code: 0 };
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
    return { stdout: '{"ok":true,"detail":"ok"}', stderr: '', code: 0 };
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
    return { stdout: '{"ok":true,"detail":"ok"}', stderr: '', code: 0 };
  });
  assert.ok(given > 0, 'no timeout was passed to the spawn');
});

test('a checker that could not run says so, not "no verdict"', () => {
  // The spec is explicit: a non-zero exit means the harness broke — a missing
  // module, a half-built venv — and must read differently from a database that
  // refused. The stderr holds the only useful sentence in that case.
  const verdict = verdictOf({
    stdout: '',
    stderr: "No module named pysqlsuggestions_lsp.check\n",
    code: 1,
  });
  assert.equal(verdict.ok, false);
  assert.match(verdict.detail, /could not run/);
  assert.match(verdict.detail, /No module named/);
});

test('a verdict on stdout wins even when the exit code is odd', () => {
  // The verdict is the product. An interpreter that answered and then died
  // still answered.
  const verdict = verdictOf({ stdout: '{"ok":false,"detail":"refused"}', stderr: 'noise', code: 1 });
  assert.equal(verdict.detail, 'refused');
});

test('a clean exit with no verdict is still no verdict', () => {
  const verdict = verdictOf({ stdout: 'nothing useful', stderr: '', code: 0 });
  assert.match(verdict.detail, /no verdict/);
});

test('only the last line of stderr is quoted', () => {
  // Python prints a traceback; the last line is the part that names the fault.
  const verdict = verdictOf({
    stdout: '',
    stderr: 'Traceback (most recent call last):\n  File "x"\nImportError: cannot import name\n',
    code: 1,
  });
  assert.match(verdict.detail, /ImportError/);
  assert.equal(verdict.detail.includes('Traceback'), false);
});

test('a checker that could not run and said nothing still reports the code', () => {
  const verdict = verdictOf({ stdout: '', stderr: '', code: 9 });
  assert.match(verdict.detail, /9/);
});
