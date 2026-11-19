import * as vscode from "vscode";
import { DjangoLensDiagnostic } from "./pythonBridge";

const SEVERITY_ICON: Record<string, string> = {
  error: "$(error)",
  warning: "$(warning)",
  information: "$(info)",
};

/**
 * Shows a lens above every DjangoLens-flagged line summarizing the rule ID
 * and confidence, without requiring the person to open the Problems panel.
 * Clicking it shows the full message/explanation/suggested-fix — the same
 * content as the hover, but reachable with a click for anyone who prefers
 * not to hover.
 */
export class DjangoLensCodeLensProvider implements vscode.CodeLensProvider {
  private readonly _onDidChangeCodeLenses = new vscode.EventEmitter<void>();
  readonly onDidChangeCodeLenses = this._onDidChangeCodeLenses.event;

  constructor(private readonly getDiagnosticsForFile: (file: string) => DjangoLensDiagnostic[]) {}

  refresh(): void {
    this._onDidChangeCodeLenses.fire();
  }

  provideCodeLenses(document: vscode.TextDocument): vscode.CodeLens[] {
    const raw = this.getDiagnosticsForFile(document.uri.fsPath);
    const lenses: vscode.CodeLens[] = [];

    for (const d of raw) {
      if (d.line === undefined) {
        continue;
      }
      const line = Math.max(d.line - 1, 0);
      const range = new vscode.Range(line, 0, line, 0);
      const icon = SEVERITY_ICON[d.severity] ?? "$(info)";
      const command: vscode.Command = {
        title: `${icon} DjangoLens: ${d.rule_id} (${d.confidence})`,
        command: "djangolens.showDiagnosticDetail",
        arguments: [d],
      };
      lenses.push(new vscode.CodeLens(range, command));
    }

    return lenses;
  }
}
