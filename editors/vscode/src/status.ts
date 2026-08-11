/**
 * What the status bar is for.
 *
 * A completion list is schema-aware or it is not, and the difference is
 * invisible in the list itself — a degraded list still holds keywords, CTE
 * columns and aliases, and looks entirely healthy. This is the only place that
 * distinction can be seen, which is the whole reason it exists.
 */

import * as vscode from 'vscode';

export type State = 'dormant' | 'starting' | 'bound' | 'degraded' | 'no-profile';

const LABELS: Record<State, { icon: string; tooltip: string }> = {
  dormant: {
    icon: '$(circle-slash)',
    tooltip: 'pysqlsuggestions is not running. Run "pysqlsuggestions: Show logs" for why.',
  },
  starting: { icon: '$(sync~spin)', tooltip: 'pysqlsuggestions is starting…' },
  // Deliberately not "connected". The database is not contacted until the
  // first completion, so at this point nothing has verified that it answers,
  // and a status bar that says otherwise is the exact lie this exists to stop.
  bound: {
    icon: '$(database)',
    tooltip: 'Using this connection. The database is read on the first completion.',
  },
  degraded: {
    icon: '$(warning)',
    tooltip: 'The database could not be read. Completing from the statement alone.',
  },
  'no-profile': {
    icon: '$(database)',
    tooltip: 'No connection selected. Completing from the statement alone — click to choose one.',
  },
};

export class Status {
  private readonly item: vscode.StatusBarItem;

  constructor() {
    this.item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    this.item.command = 'pysqlsuggestions.selectConnection';
  }

  /** Show `state`, naming `profile` when there is one and `detail` when it explains something. */
  set(state: State, profile?: string, detail?: string): void {
    const label = LABELS[state];
    this.item.text = profile === undefined ? `${label.icon} SQL` : `${label.icon} ${profile}`;
    this.item.tooltip = detail === undefined ? label.tooltip : `${label.tooltip}\n\n${detail}`;
    this.item.show();
  }

  dispose(): void {
    this.item.dispose();
  }
}
