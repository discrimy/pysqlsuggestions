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
import { initializationOptions, needsPassword, readProfiles, resolveProfile } from './profiles';
import { MINIMUM_PYTHON, ensureVenv, findInterpreter, stampPath } from './runtime';
import { promptForPassword, readPassword } from './secrets';
import { Status } from './status';

/** The server's own notification, sent once when the catalog stops being usable. */
const DEGRADED = 'pysqlsuggestions/degraded';

let client: LanguageClient | undefined;
let status: Status | undefined;
let output: vscode.OutputChannel | undefined;

/**
 * Profiles the user declined to give a password for, this window.
 *
 * In memory rather than stored: dismissing once should not be remembered
 * forever, and a reload is how someone says "ask me again".
 */
const declined = new Set<string>();

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

  context.subscriptions.push(
    vscode.commands.registerCommand('pysqlsuggestions.showLogs', () => output?.show()),
    vscode.commands.registerCommand('pysqlsuggestions.restartServer', () => restart(context)),
    vscode.commands.registerCommand('pysqlsuggestions.selectConnection', () => selectConnection(context)),
    vscode.commands.registerCommand('pysqlsuggestions.addConnection', () => addConnection()),
  );

  await start(context);
}

async function start(context: vscode.ExtensionContext): Promise<void> {
  const settings = vscode.workspace.getConfiguration('pysqlsuggestions');
  const root = context.globalStorageUri.fsPath;
  await fs.mkdir(root, { recursive: true });

  const version = (context.extension.packageJSON as { version: string }).version;
  const runtime = await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Window, title: 'pysqlsuggestions: preparing Python…' },
    () =>
      ensureVenv({
        root,
        version,
        wheelDir: vscode.Uri.joinPath(context.extensionUri, 'bundled', 'wheels').fsPath,
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
    status?.set('degraded', profile?.name, params.reason);
    output?.appendLine(`catalog unusable: ${params.reason ?? 'no reason given'}`);
  });

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
        void addConnection();
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

/**
 * Open the settings UI rather than reimplementing a form.
 *
 * The schema in package.json already describes every field and its allowed
 * values, so a hand-written wizard would be a second description of the same
 * thing, kept in step by hand.
 */
async function addConnection(): Promise<void> {
  await vscode.commands.executeCommand('workbench.action.openSettings', 'pysqlsuggestions.connections');
}

export async function deactivate(): Promise<void> {
  await stop();
}
