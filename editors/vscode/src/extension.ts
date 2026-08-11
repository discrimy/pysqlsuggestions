/**
 * Activation: provision, resolve, start.
 *
 * Order matters. The client starts even with no profile, because completing
 * from the statement alone is a useful mode rather than a failure — and it is
 * exactly what a user gets before they have configured anything at all. The
 * one thing that stops the extension is having no Python to run the server
 * with, and that is reported once rather than on every `.sql` file opened.
 */

import * as cp from 'node:child_process';
import * as fs from 'node:fs/promises';
import * as vscode from 'vscode';
import { LanguageClient, type LanguageClientOptions, type ServerOptions } from 'vscode-languageclient/node';
import { type Spawn, testConnection } from './check';
import { type Profile, initializationOptions, needsPassword, readProfiles, resolveProfile } from './profiles';
import { MINIMUM_PYTHON, ensureVenv, findInterpreter, stampFor, stampPath } from './runtime';
import { forgetPassword, promptForPassword, readPassword } from './secrets';
import { Status } from './status';
import {
  type Scope,
  type SettingsAccess,
  type Stored,
  addConnection,
  removeConnection,
  updateConnection,
} from './store';
import { ConnectionTree } from './tree';

/** The server's own notification, sent once when the catalog stops being usable. */
const DEGRADED = 'pysqlsuggestions/degraded';

let client: LanguageClient | undefined;
let status: Status | undefined;
let output: vscode.OutputChannel | undefined;
let tree: ConnectionTree | undefined;

/**
 * The managed venv's interpreter, once there is one.
 *
 * Undefined means nothing can be tested, which the test flow reports rather
 * than spawning something that cannot exist.
 */
let venvPython: string | undefined;

/**
 * Profiles the user declined to give a password for, this window.
 *
 * In memory rather than stored: dismissing once should not be remembered
 * forever, and a reload is how someone says "ask me again".
 */
const declined = new Set<string>();

/**
 * The wheels the VSIX carries, by name and size.
 *
 * Empty when the directory is unreadable, which makes the stamp depend on the
 * version alone — the behaviour before this existed, and the right fallback:
 * an unreadable bundle is a broken install that the install step will report
 * far more clearly than a stamp mismatch would.
 */
async function bundledWheels(directory: string): Promise<{ name: string; size: number }[]> {
  try {
    const names = await fs.readdir(directory);
    return await Promise.all(
      names
        .filter((name) => name.endsWith('.whl'))
        .map(async (name) => ({ name, size: (await fs.stat(`${directory}/${name}`)).size })),
    );
  } catch {
    return [];
  }
}

/** Settings, as `store.ts` wants them. */
function settingsAccess(): SettingsAccess {
  const inspect = () =>
    vscode.workspace.getConfiguration('pysqlsuggestions').inspect<unknown[]>('connections');
  return {
    user: () => inspect()?.globalValue,
    workspace: () => inspect()?.workspaceValue,
    write: async (scope: Scope, value: unknown[]) => {
      await vscode.workspace
        .getConfiguration('pysqlsuggestions')
        .update(
          'connections',
          value,
          scope === 'user' ? vscode.ConfigurationTarget.Global : vscode.ConfigurationTarget.Workspace,
        );
    },
  };
}

/**
 * Run the checker in the managed venv, killed if it does not answer.
 *
 * The kill is a backstop: `check.py` gives the driver its own shorter deadline
 * so it can usually say *why* it failed. A killed process only ever reports
 * that it was killed.
 */
function checkSpawn(python: string): Spawn {
  return (input, timeoutMs) =>
    new Promise((resolve, reject) => {
      const child = cp.spawn(python, ['-m', 'pysqlsuggestions_lsp.check'], { windowsHide: true });
      let stdout = '';
      let stderr = '';
      const timer = setTimeout(() => {
        child.kill();
        reject(new Error(`no answer in ${String(timeoutMs / 1000)}s — killed`));
      }, timeoutMs);
      child.stdout.on('data', (chunk: Buffer) => (stdout += chunk.toString()));
      // Kept as well as logged: when the checker itself cannot run, this holds
      // the only sentence that says why, and the user needs it in the tooltip
      // rather than only in a channel they have no reason to open.
      child.stderr.on('data', (chunk: Buffer) => {
        stderr += chunk.toString();
        output?.append(chunk.toString());
      });
      child.on('error', (error) => {
        clearTimeout(timer);
        reject(error);
      });
      child.on('close', (code) => {
        clearTimeout(timer);
        resolve({ stdout, stderr, code });
      });
      child.stdin.write(input);
      child.stdin.end();
    });
}

/** Run a command, streaming everything it says into the output channel. */
function run(command: string, args: string[]): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = cp.spawn(command, args, { windowsHide: true });
    child.stdout.on('data', (chunk: Buffer) => output?.append(chunk.toString()));
    child.stderr.on('data', (chunk: Buffer) => output?.append(chunk.toString()));
    child.on('error', reject);
    child.on('close', (code) => {
      if (code === 0) {
        resolve();
      } else {
        reject(new Error(`${command} exited ${String(code)}`));
      }
    });
  });
}

/** Run a command and return what it printed. */
function capture(command: string, args: string[]): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = cp.spawn(command, args, { windowsHide: true });
    let collected = '';
    child.stdout.on('data', (chunk: Buffer) => (collected += chunk.toString()));
    child.on('error', reject);
    child.on('close', (code) => {
      if (code === 0) {
        resolve(collected);
      } else {
        reject(new Error(`${command} exited ${String(code)}`));
      }
    });
  });
}

/**
 * Ask an interpreter what version it is.
 *
 * `sys.version_info` rather than `--version`, because the Windows Store stub
 * answers `--version` with the word `Python` and exit code zero. Asking it to
 * execute something makes the difference visible.
 */
function probePython(command: string): Promise<string> {
  return capture(command, ['-c', "import sys; print('%d.%d.%d' % sys.version_info[:3])"]);
}

/** The first interpreter that runs and is new enough, or undefined. */
function findPython(configured: string | null): Promise<string | undefined> {
  const candidates = [configured, 'python3', 'python', 'py'].filter(
    (candidate): candidate is string => candidate !== null && candidate.length > 0,
  );
  return findInterpreter(candidates, probePython);
}

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  output = vscode.window.createOutputChannel('pysqlsuggestions');
  status = new Status();
  status.set('starting');
  context.subscriptions.push(output, status);

  tree = new ConnectionTree(settingsAccess());
  context.subscriptions.push(
    tree,
    vscode.window.createTreeView('pysqlsuggestions.connections', { treeDataProvider: tree }),
    vscode.commands.registerCommand('pysqlsuggestions.showLogs', () => output?.show()),
    vscode.commands.registerCommand('pysqlsuggestions.restartServer', () => restart(context)),
    vscode.commands.registerCommand('pysqlsuggestions.selectConnection', () => selectConnection(context)),
    vscode.commands.registerCommand('pysqlsuggestions.addConnection', () => addConnectionFlow(context)),
    vscode.commands.registerCommand('pysqlsuggestions.refreshConnections', () => tree?.refresh()),
    vscode.commands.registerCommand('pysqlsuggestions.editConnection', (entry: Stored) =>
      editConnectionFlow(context, entry),
    ),
    vscode.commands.registerCommand('pysqlsuggestions.removeConnection', (entry: Stored) =>
      removeConnectionFlow(context, entry),
    ),
    vscode.commands.registerCommand('pysqlsuggestions.testConnection', (entry: Stored) => runTest(context, entry)),
    vscode.commands.registerCommand('pysqlsuggestions.setPassword', async (entry: Stored) => {
      await promptForPassword(context.secrets, entry.profile.name);
      if (isInUse(entry.profile.name)) {
        await restart(context);
      }
    }),
    vscode.commands.registerCommand('pysqlsuggestions.clearPassword', async (entry: Stored) => {
      await forgetPassword(context.secrets, entry.profile.name);
      // Whatever was verified was verified with that password.
      tree?.setHealth(entry.profile.name, 'untested');
    }),
    vscode.commands.registerCommand('pysqlsuggestions.useConnection', async (entry: Stored) => {
      const scope =
        vscode.workspace.workspaceFolders === undefined
          ? vscode.ConfigurationTarget.Global
          : vscode.ConfigurationTarget.Workspace;
      await vscode.workspace
        .getConfiguration('pysqlsuggestions')
        .update('defaultConnection', entry.profile.name, scope);
      await restart(context);
    }),
  );

  await start(context);
}

async function start(context: vscode.ExtensionContext): Promise<void> {
  const settings = vscode.workspace.getConfiguration('pysqlsuggestions');
  const root = context.globalStorageUri.fsPath;
  await fs.mkdir(root, { recursive: true });

  const version = (context.extension.packageJSON as { version: string }).version;
  const wheelDir = vscode.Uri.joinPath(context.extensionUri, 'bundled', 'wheels').fsPath;
  const runtime = await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Window, title: 'pysqlsuggestions: preparing Python…' },
    async () =>
      ensureVenv({
        root,
        // The bundle, not just the version: a server rebuilt under an unchanged
        // version would otherwise leave a venv holding code the VSIX no longer
        // carries, and nothing would notice.
        version: stampFor(version, await bundledWheels(wheelDir)),
        wheelDir,
        platform: process.platform,
        findPython: () => findPython(settings.get<string | null>('pythonPath', null)),
        run,
        readStamp: () =>
          fs
            .readFile(stampPath(root), 'utf8')
            .then((value) => value.trim())
            .catch(() => undefined),
        writeStamp: (value) => fs.writeFile(stampPath(root), value, 'utf8'),
      }),
  );

  if (!runtime.ready) {
    status?.set('dormant');
    void vscode.window
      .showErrorMessage(
        `pysqlsuggestions needs Python ${MINIMUM_PYTHON} or newer and found none on PATH. ` +
          'Set "pysqlsuggestions.pythonPath" if you have one elsewhere.',
        'Show logs',
        'Open settings',
      )
      .then((choice) => {
        if (choice === 'Show logs') {
          output?.show();
        } else if (choice === 'Open settings') {
          void vscode.commands.executeCommand('workbench.action.openSettings', 'pysqlsuggestions.pythonPath');
        }
      });
    return;
  }

  venvPython = runtime.python;

  const profiles = readProfiles(settings.get('connections', []));
  const profile = resolveProfile(profiles, settings.get<string | null>('defaultConnection', null));
  let password = profile === undefined ? undefined : await readPassword(context.secrets, profile.name);

  // Ask before starting rather than after failing. A profile written straight
  // into settings.json never passes through `selectConnection`, so this is the
  // only point at which anyone asks — and without it the server connects
  // unauthenticated and quietly stops being schema-aware.
  if (profile !== undefined && needsPassword(profile, password, declined)) {
    password = await promptForPassword(context.secrets, profile.name);
    if (password === undefined) {
      declined.add(profile.name);
    }
  }

  const serverOptions: ServerOptions = { command: runtime.python, args: ['-m', 'pysqlsuggestions_lsp'] };
  const clientOptions: LanguageClientOptions = {
    documentSelector: [
      { scheme: 'file', language: 'sql' },
      { scheme: 'untitled', language: 'sql' },
    ],
    initializationOptions: initializationOptions(profile, password),
    outputChannel: output,
  };

  client = new LanguageClient('pysqlsuggestions', 'pysqlsuggestions', serverOptions, clientOptions);
  await client.start();

  // The server tells us when the catalog stops being usable. Nothing else can:
  // a degraded list still holds keywords and aliases and looks entirely
  // healthy, so without this the status bar would keep claiming schema
  // awareness the user is no longer getting.
  client.onNotification(DEGRADED, (params: { reason?: string }) => {
    if (profile !== undefined) {
      tree?.setHealth(profile.name, 'failed', params.reason);
    }
    status?.set('degraded', profile?.name, params.reason);
    output?.appendLine(`catalog unusable: ${params.reason ?? 'no reason given'}`);
  });

  tree?.setInUse(profile?.name);
  status?.set(profile === undefined ? 'no-profile' : 'bound', profile?.name);
}

async function stop(): Promise<void> {
  await client?.stop();
  client = undefined;
}

/** Restarting is how a profile change takes effect: one connection per process. */
async function restart(context: vscode.ExtensionContext): Promise<void> {
  status?.set('starting');
  await stop();
  await start(context);
}

async function selectConnection(context: vscode.ExtensionContext): Promise<void> {
  const settings = vscode.workspace.getConfiguration('pysqlsuggestions');
  const profiles = readProfiles(settings.get('connections', []));
  if (profiles.length === 0) {
    void vscode.window.showInformationMessage('No connections configured yet.', 'Add one').then((choice) => {
      if (choice === 'Add one') {
        void addConnectionFlow(context);
      }
    });
    return;
  }

  const chosen = await vscode.window.showQuickPick(
    profiles.map((profile) => profile.name),
    { title: 'Use which connection?' },
  );
  if (chosen === undefined) {
    return;
  }
  // Workspace when there is one, so different projects can face different
  // databases. Global otherwise: a single `.sql` file open with no folder is
  // an ordinary way to work, and updating workspace settings there throws.
  const scope =
    vscode.workspace.workspaceFolders === undefined
      ? vscode.ConfigurationTarget.Global
      : vscode.ConfigurationTarget.Workspace;
  await settings.update('defaultConnection', chosen, scope);

  if ((await readPassword(context.secrets, chosen)) === undefined) {
    await promptForPassword(context.secrets, chosen);
  }
  await restart(context);
}

const DIALECTS = [
  { label: 'postgres', detail: 'Columns, joins from foreign keys, values from statistics' },
  { label: 'clickhouse', detail: 'No driver bundled — keywords and quoting only' },
  { label: 'trino', detail: 'No driver bundled — keywords and quoting only' },
  { label: 'ansi', detail: 'No connection — keywords only' },
];

/** Ask for one field. Undefined when the user backs out, which cancels the flow. */
function ask(prompt: string, value?: string, required = false): Thenable<string | undefined> {
  return vscode.window.showInputBox({
    title: prompt,
    value,
    ignoreFocusOut: true,
    validateInput: (entered) => (required && entered.trim().length === 0 ? 'Required' : undefined),
  });
}

/** A trimmed value, or undefined when it was blank. */
function optional(entered: string): string | undefined {
  return entered.trim().length > 0 ? entered.trim() : undefined;
}

/** A port number, or undefined when blank or not a number. */
function port(entered: string): number | undefined {
  const parsed = Number(entered.trim());
  return entered.trim().length > 0 && !Number.isNaN(parsed) ? parsed : undefined;
}

/** Whether `name` is the profile the running server holds. */
function isInUse(name: string): boolean {
  return vscode.workspace.getConfiguration('pysqlsuggestions').get<string | null>('defaultConnection', null) === name;
}

/** Test one connection and record the verdict on its row. Never throws. */
async function runTest(context: vscode.ExtensionContext, entry: Stored): Promise<void> {
  const name = entry.profile.name;
  if (venvPython === undefined) {
    tree?.setHealth(name, 'failed', 'the Python environment is not ready — run "Show logs"');
    return;
  }
  tree?.setHealth(name, 'testing');
  const password = await readPassword(context.secrets, name);
  const verdict = await testConnection(entry.profile, password, checkSpawn(venvPython));
  tree?.setHealth(name, verdict.ok ? 'ok' : 'failed', verdict.detail);
  output?.appendLine(`${name}: ${verdict.ok ? 'ok' : 'failed'} — ${verdict.detail}`);
}

async function addConnectionFlow(context: vscode.ExtensionContext): Promise<void> {
  const name = await ask('Connection name', undefined, true);
  if (name === undefined) return;
  const dialect = await vscode.window.showQuickPick(DIALECTS, { title: 'Which backend?' });
  if (dialect === undefined) return;
  const host = await ask('Host', 'localhost', true);
  if (host === undefined) return;
  const enteredPort = await ask('Port (blank for the driver default)', '5432');
  if (enteredPort === undefined) return;
  const database = await ask('Database (blank for the default)');
  if (database === undefined) return;
  const user = await ask('User (blank to let the driver decide)');
  if (user === undefined) return;

  const profile: Profile = {
    name: name.trim(),
    dialect: dialect.label,
    host: host.trim(),
    port: port(enteredPort),
    database: optional(database),
    user: optional(user),
  };
  await addConnection(settingsAccess(), profile);
  tree?.refresh();

  // A connection with a user almost certainly wants a password, and being asked
  // now beats discovering later that completion quietly stopped being
  // schema-aware.
  if (profile.user !== undefined) {
    await promptForPassword(context.secrets, profile.name);
  }
  await runTest(context, { profile, scope: 'user' });
}

async function editConnectionFlow(context: vscode.ExtensionContext, entry: Stored): Promise<void> {
  const { profile } = entry;
  const fields = [
    { label: 'name', description: profile.name },
    { label: 'dialect', description: profile.dialect },
    { label: 'host', description: profile.host },
    { label: 'port', description: profile.port === undefined ? '(default)' : String(profile.port) },
    { label: 'database', description: profile.database ?? '(default)' },
    { label: 'user', description: profile.user ?? '(driver decides)' },
  ];
  const chosen = await vscode.window.showQuickPick(fields, { title: `Edit ${profile.name}` });
  if (chosen === undefined) return;

  const updated: Profile = { ...profile };
  if (chosen.label === 'dialect') {
    const dialect = await vscode.window.showQuickPick(DIALECTS, { title: 'Which backend?' });
    if (dialect === undefined) return;
    updated.dialect = dialect.label;
  } else {
    const current =
      chosen.label === 'port' ? (profile.port === undefined ? '' : String(profile.port)) : chosen.description;
    const entered = await ask(chosen.label, current, chosen.label === 'name' || chosen.label === 'host');
    if (entered === undefined) return;
    if (chosen.label === 'name') updated.name = entered.trim();
    if (chosen.label === 'host') updated.host = entered.trim();
    if (chosen.label === 'port') updated.port = port(entered);
    if (chosen.label === 'database') updated.database = optional(entered);
    if (chosen.label === 'user') updated.user = optional(entered);
  }

  await updateConnection(settingsAccess(), profile.name, updated);
  tree?.refresh();
  // One connection per process, so a change to the one in use means a restart.
  if (isInUse(profile.name)) {
    await restart(context);
  }
}

async function removeConnectionFlow(context: vscode.ExtensionContext, entry: Stored): Promise<void> {
  const confirmed = await vscode.window.showWarningMessage(
    `Remove the connection "${entry.profile.name}"?`,
    { modal: true },
    'Remove',
  );
  if (confirmed !== 'Remove') return;
  await removeConnection(settingsAccess(), entry.profile.name);
  // The stored password goes with it: an orphaned secret means a later
  // connection reusing that name silently inherits somebody else's password.
  await forgetPassword(context.secrets, entry.profile.name);
  tree?.refresh();
  if (isInUse(entry.profile.name)) {
    await restart(context);
  }
}

export async function deactivate(): Promise<void> {
  await stop();
}
