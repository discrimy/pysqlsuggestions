/**
 * The SQL Connections view.
 *
 * Holds the two things settings cannot: which connection the running server is
 * using, and what happened the last time each was tested. Both are per session
 * and neither is persisted — a stored "ok" from last week is a claim nobody
 * verified today, and a healthy-looking list that is not is the failure this
 * view exists to end.
 *
 * The rendering itself lives in `rows.ts`, which imports no `vscode` and can
 * therefore be tested as data.
 */

import * as vscode from 'vscode';
import { type Health, rowFor } from './rows';
import { type SettingsAccess, type Stored, listConnections } from './store';

export class ConnectionTree implements vscode.TreeDataProvider<Stored> {
  private readonly changed = new vscode.EventEmitter<undefined>();
  readonly onDidChangeTreeData = this.changed.event;

  private readonly health = new Map<string, { health: Health; detail?: string }>();
  private inUse: string | undefined;

  constructor(private readonly access: SettingsAccess) {}

  /** Read from settings, never from what we hoped we wrote. */
  getChildren(): Stored[] {
    return listConnections(this.access);
  }

  getTreeItem(entry: Stored): vscode.TreeItem {
    const state = this.health.get(entry.profile.name);
    const row = rowFor(entry, state?.health ?? 'untested', entry.profile.name === this.inUse, state?.detail);
    const item = new vscode.TreeItem(row.label, vscode.TreeItemCollapsibleState.None);
    item.description = row.description;
    item.iconPath = new vscode.ThemeIcon(row.icon);
    item.tooltip = new vscode.MarkdownString(row.tooltip);
    item.contextValue = row.contextValue;
    return item;
  }

  refresh(): void {
    this.changed.fire(undefined);
  }

  setHealth(name: string, health: Health, detail?: string): void {
    this.health.set(name, { health, detail });
    this.refresh();
  }

  /**
   * Record which connection the server now holds.
   *
   * Its health resets: what was verified is no longer what is running.
   */
  setInUse(name: string | undefined): void {
    this.inUse = name;
    if (name !== undefined) {
      this.health.delete(name);
    }
    this.refresh();
  }

  dispose(): void {
    this.changed.dispose();
  }
}
