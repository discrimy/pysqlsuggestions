import { strict as assert } from 'node:assert';
import * as cp from 'node:child_process';
import * as vscode from 'vscode';
import { type Spawn, testConnection } from '../../check';

/**
 * The extension loaded by a real editor, against the docker Postgres.
 *
 * Everything else is unit-tested logic with the outside world injected. This
 * is the only place the whole chain runs — venv built from bundled wheels,
 * client started, server spawned, database read — and so the only place a
 * broken link in it can be seen.
 */

const PROFILE = {
  name: 'docker',
  dialect: 'postgres',
  host: 'localhost',
  port: 57432,
  database: 'report_service',
  user: 'report',
};

/** The checker, spawned exactly as the extension spawns it. */
function spawnFor(python: string): Spawn {
  return (input, timeoutMs) =>
    new Promise((resolve, reject) => {
      const child = cp.spawn(python, ['-m', 'pysqlsuggestions_lsp.check'], { windowsHide: true });
      let stdout = '';
      let stderr = '';
      const timer = setTimeout(() => {
        child.kill();
        reject(new Error('no answer — killed'));
      }, timeoutMs);
      child.stdout.on('data', (chunk: Buffer) => (stdout += chunk.toString()));
      child.stderr.on('data', (chunk: Buffer) => (stderr += chunk.toString()));
      child.on('error', reject);
      child.on('close', (code) => {
        clearTimeout(timer);
        resolve({ stdout, stderr, code });
      });
      child.stdin.write(input);
      child.stdin.end();
    });
}

/** `vscode.window` with its prompts made assignable, for the stubs below. */
type Writable = { showQuickPick: unknown; showInputBox: unknown };

async function completionsFor(sql: string): Promise<string[]> {
  const document = await vscode.workspace.openTextDocument({ language: 'sql', content: sql });
  await vscode.window.showTextDocument(document);
  const list = await vscode.commands.executeCommand<vscode.CompletionList>(
    'vscode.executeCompletionItemProvider',
    document.uri,
    document.positionAt(sql.length),
  );
  return list.items.map((item) => (typeof item.label === 'string' ? item.label : item.label.label));
}

suite('pysqlsuggestions', () => {
  suiteSetup(async function () {
    this.timeout(180000);
    // No interpreter to point at any more: the extension unpacks the one it
    // ships with, and PYSQLSUGGESTIONS_TEST_PYTHON had nothing left to select.
    const settings = vscode.workspace.getConfiguration('pysqlsuggestions');
    await settings.update('connections', [PROFILE], vscode.ConfigurationTarget.Global);

    // The password has to reach SecretStorage, and there is deliberately no
    // setting that can carry one. So drive the real path: this suite runs in
    // the extension host and shares its `vscode` module, so stubbing the two
    // prompts makes `selectConnection` store the secret exactly as a user
    // would — and covers prompt, store and restart while it is at it.
    const quickPick = vscode.window.showQuickPick;
    const inputBox = vscode.window.showInputBox;
    (vscode.window as Writable).showQuickPick = async () => 'docker';
    (vscode.window as Writable).showInputBox = async () => 'report';
    try {
      await vscode.commands.executeCommand('pysqlsuggestions.selectConnection');
    } finally {
      (vscode.window as Writable).showQuickPick = quickPick;
      (vscode.window as Writable).showInputBox = inputBox;
    }
  });

  test('the extension activated', async () => {
    const extension = vscode.extensions.getExtension('pysqlsuggestions.pysqlsuggestions');
    assert.ok(extension, 'the extension is not installed in the test instance');
    assert.equal(extension.isActive, true);
  });

  test('a CTE defined in the statement is offered', async () => {
    // Needs no catalog, so this passing and the next failing would say the
    // server is fine and the database is not.
    const labels = await completionsFor('WITH recent AS (SELECT 1) SELECT * FROM rec');
    assert.ok(labels.includes('recent'), `no CTE among ${labels.slice(0, 10).join(', ')}`);
  });

  test('columns come from the database', async () => {
    const labels = await completionsFor('SELECT * FROM auth_user u WHERE u.');
    assert.ok(labels.includes('username'), `no column among ${labels.slice(0, 10).join(', ')}`);
  });

  test('a join proposal arrives with its condition', async () => {
    const labels = await completionsFor('SELECT * FROM reports_report r JOIN ');
    assert.ok(
      labels.some((label) => label.includes(' ON ')),
      `no join among ${labels.slice(0, 10).join(', ')}`,
    );
  });

  test('values come from the statistics', async () => {
    const labels = await completionsFor('SELECT * FROM reports_runlog r WHERE r.status = ');
    assert.ok(
      labels.some((label) => label.startsWith("'")),
      `no literal among ${labels.slice(0, 10).join(', ')}`,
    );
  });

  test('the engines order survives the trip', async () => {
    // `executeCompletionItemProvider` merges every provider's items, and the
    // editor's own word-based one contributes words from the open documents.
    // Those are not ours and carry no four-digit sortText, so the claim under
    // test — that our ranking reaches the client intact — is about ours alone.
    const content = 'SELECT * FROM reports_report r JOIN ';
    const document = await vscode.workspace.openTextDocument({ language: 'sql', content });
    await vscode.window.showTextDocument(document);
    const list = await vscode.commands.executeCommand<vscode.CompletionList>(
      'vscode.executeCompletionItemProvider',
      document.uri,
      document.positionAt(content.length),
    );

    // The command hands back raw provider output; the editor sorts by sortText
    // at display time. So the checkable claim is not the array's order but that
    // every item carries a distinct rank, numbered from the top with no gaps —
    // which is what the editor then sorts by instead of its own fuzzy score.
    const ours = list.items.map((item) => item.sortText ?? '').filter((value) => /^\d{4}$/.test(value));
    assert.ok(ours.length > 5, `too few ranked items to be meaningful: ${String(ours.length)}`);
    const expected = ours.map((_, index) => String(index).padStart(4, '0'));
    assert.deepEqual([...ours].sort(), expected, 'ranks are duplicated, gapped, or do not start at the top');
  });
});

suite('a connection configured in settings alone', () => {
  test('is asked for its password rather than left to degrade', async function () {
    // The bug this covers: a profile written straight into settings.json never
    // passes through `selectConnection`, so nothing ever asked for a password.
    // The server then connected unauthenticated and completion degraded to
    // nothing at all — indistinguishable, to a user, from a dead extension.
    this.timeout(180000);
    const settings = vscode.workspace.getConfiguration('pysqlsuggestions');
    await settings.update(
      'connections',
      [{ ...PROFILE, name: 'never-selected' }],
      vscode.ConfigurationTarget.Global,
    );
    await settings.update('defaultConnection', 'never-selected', vscode.ConfigurationTarget.Global);

    let asked = 0;
    const inputBox = vscode.window.showInputBox;
    (vscode.window as Writable).showInputBox = async () => {
      asked += 1;
      return 'report';
    };
    try {
      await vscode.commands.executeCommand('pysqlsuggestions.restartServer');
    } finally {
      (vscode.window as Writable).showInputBox = inputBox;
    }
    assert.equal(asked, 1, 'starting with an unauthenticated profile asked nobody for a password');

    const labels = await completionsFor('SELECT * FROM auth_user u WHERE u.');
    assert.ok(labels.includes('username'), `still degraded: ${labels.slice(0, 8).join(', ')}`);
  });
});

suite('managing connections', () => {
  test('every command the view needs is registered', async () => {
    // A menu entry pointing at an unregistered command fails silently when
    // clicked, which is the one failure a unit test of `rowFor` cannot see.
    const commands = await vscode.commands.getCommands(true);
    for (const name of [
      'addConnection',
      'editConnection',
      'removeConnection',
      'testConnection',
      'setPassword',
      'clearPassword',
      'useConnection',
      'refreshConnections',
    ]) {
      assert.ok(commands.includes(`pysqlsuggestions.${name}`), `${name} is not registered`);
    }
  });

  test('testing a good connection reports a real verdict', async function () {
    this.timeout(60000);
    // Asserted through the checker the extension actually spawns, in the venv
    // it actually built. An earlier version of this test ended in
    // `assert.ok(true)` and so passed while the bundled wheel was missing
    // check.py entirely — a test that cannot fail is worse than no test,
    // because it is counted.
    const python = process.env.PYSQLSUGGESTIONS_TEST_PYTHON;
    assert.ok(python, 'no interpreter for the harness to check with');
    const verdict = await testConnection(PROFILE, 'report', spawnFor(python));
    assert.equal(verdict.ok, true, `expected a working connection, got: ${verdict.detail}`);
    assert.match(verdict.detail, /relations visible/);
  });

  test('a connection with no password says so rather than shrugging', async function () {
    this.timeout(60000);
    // The message this whole feature exists for. Untranslated, pg8000 says
    // "'NoneType' object has no attribute 'decode'".
    const python = process.env.PYSQLSUGGESTIONS_TEST_PYTHON;
    assert.ok(python);
    const verdict = await testConnection(PROFILE, undefined, spawnFor(python));
    assert.equal(verdict.ok, false);
    assert.match(verdict.detail, /password/);
    assert.equal(verdict.detail.includes('decode'), false);
  });

  test('a checker that cannot run says so, not "no verdict"', async function () {
    this.timeout(60000);
    // Exactly the failure that reached a user: a venv whose installed package
    // had no check.py. stdout was empty, the exit code was 1, and the message
    // was a generic shrug.
    const verdict = await testConnection(PROFILE, 'report', spawnFor(process.execPath));
    assert.equal(verdict.ok, false);
    assert.match(verdict.detail, /could not run|no answer/);
  });

  test('testing a connection on a dead port gives up rather than hanging', async function () {
    this.timeout(60000);
    const started = Date.now();
    await vscode.commands.executeCommand('pysqlsuggestions.testConnection', {
      profile: { ...PROFILE, name: 'dead', port: 59999 },
      scope: 'user',
    });
    assert.ok(Date.now() - started < 30000, 'the check did not give up');
  });

  test('a connection added through the flow reaches settings', async function () {
    this.timeout(60000);
    const inputBox = vscode.window.showInputBox;
    const quickPick = vscode.window.showQuickPick;
    const answers = ['added-by-test', 'localhost', '57432', 'report_service', 'report', 'report'];
    let asked = 0;
    (vscode.window as Writable).showInputBox = async () => answers[asked++];
    (vscode.window as Writable).showQuickPick = async () => ({ label: 'postgres' });
    try {
      await vscode.commands.executeCommand('pysqlsuggestions.addConnection');
    } finally {
      (vscode.window as Writable).showInputBox = inputBox;
      (vscode.window as Writable).showQuickPick = quickPick;
    }

    const stored = vscode.workspace
      .getConfiguration('pysqlsuggestions')
      .get<{ name: string; password?: string }[]>('connections', []);
    const added = stored.find((entry) => entry.name === 'added-by-test');
    assert.ok(added, `not saved: ${stored.map((entry) => entry.name).join(', ')}`);
    // Settings are the one place a password must never reach.
    assert.equal('password' in added, false);
  });
});
