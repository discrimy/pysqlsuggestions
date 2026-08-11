/**
 * Passwords, kept where a password belongs.
 *
 * Never in settings: a settings field is a field somebody commits, and the
 * schema in package.json deliberately has nowhere to put one.
 *
 * The key is per profile name, so two connections to the same host as
 * different users do not share one secret — which would silently authenticate
 * one of them as the other.
 */

import * as vscode from 'vscode';

function key(profileName: string): string {
  return `pysqlsuggestions.password.${profileName}`;
}

/** The stored password for `profileName`, or undefined. */
export async function readPassword(
  secrets: vscode.SecretStorage,
  profileName: string,
): Promise<string | undefined> {
  return secrets.get(key(profileName));
}

/**
 * Ask for a password and store it. Undefined when the user dismisses the prompt.
 *
 * Dismissing is a legitimate answer, not an error: trust authentication and
 * `.pgpass` both mean the server needs no password from us, and a profile with
 * no password is still a usable profile.
 */
export async function promptForPassword(
  secrets: vscode.SecretStorage,
  profileName: string,
): Promise<string | undefined> {
  const entered = await vscode.window.showInputBox({
    title: `Password for ${profileName}`,
    prompt: "Stored in the editor's secret storage, never in settings.",
    password: true,
    ignoreFocusOut: true,
  });
  if (entered === undefined || entered.length === 0) {
    return undefined;
  }
  await secrets.store(key(profileName), entered);
  return entered;
}

/** Forget the stored password, so the next connect prompts again. */
export async function forgetPassword(secrets: vscode.SecretStorage, profileName: string): Promise<void> {
  await secrets.delete(key(profileName));
}
