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
import { initializationOptions, readProfiles, resolveProfile } from './profiles';
import { ensureVenv, stampPath } from './runtime';
import { promptForPassword, readPassword } from './secrets';
import { Status } from './status';

let client: LanguageClient | undefined;
let status: Status | undefined;
let output: vscode.OutputChannel | undefined;

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

/**
 * The first interpreter that answers, or undefined.
 *
 * Failures are silent here because only the caller knows whether running out
 * of candidates matters — and it reports once, not per candidate.
 */
async function findPython(configured: string | null): Promise<string | undefined> {
  const candidates = [configured, 'python3', 'python'].filter((c): c is string => c !== null && c.length > 0);
  for (const candidate of candidates) {
    try {
      await run(candidate, ['--version']);
      return candidate;
    } catch {
      continue;
    }
  }
  return undefined;
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
        'pysqlsuggestions needs a Python 3.10+ interpreter and could not find one on PATH.',
        'Show logs',
      )
      .then((choice) => {
        if (choice === 'Show logs') {
          output?.show();
        }
      });
    return;
  }

  const profiles = readProfiles(settings.get('connections', []));
  const profile = resolveProfile(profiles, settings.get<string | null>('defaultConnection', null));
  const password = profile === undefined ? undefined : await readPassword(context.secrets, profile.name);

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
  status?.set(profile === undefined ? 'no-profile' : 'connected', profile?.name);
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
  await settings.update('defaultConnection', chosen, vscode.ConfigurationTarget.Workspace);

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
