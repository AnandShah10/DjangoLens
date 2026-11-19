import * as vscode from "vscode";
import { DjangoLensDiagnostic } from "./pythonBridge";

/**
 * Adds structured, rule-labeled hover content for DjangoLens diagnostics —
 * on top of (not replacing) VS Code's own default diagnostic hover, which
 * just shows the flattened message string. This gives the rule ID,
 * confidence level, explanation, and suggested fix each their own
 * clearly-labeled section.
 */
export class DjangoLensHoverProvider implements vscode.HoverProvider {
  constructor(private readonly getDiagnosticsForFile: (file: string) => DjangoLensDiagnostic[]) {}

  provideHover(document: vscode.TextDocument, position: vscode.Position): vscode.Hover | undefined {
    const raw = this.getDiagnosticsForFile(document.uri.fsPath);
    const match = raw.find(
      (d) =>
        d.line !== undefined &&
        d.line - 1 === position.line &&
        d.col !== undefined &&
        position.character >= d.col &&
        position.character <= d.col + 1
    );
    if (!match) {
      return undefined;
    }

    const md = new vscode.MarkdownString(undefined, true);
    md.isTrusted = false;
    md.appendMarkdown(`**DjangoLens — ${match.rule_id}** _(${match.confidence} confidence)_\n\n`);
    md.appendMarkdown(`${match.message}\n\n`);
    if (match.explanation) {
      md.appendMarkdown(`${match.explanation}\n\n`);
    }
    if (match.suggested_fix) {
      md.appendMarkdown(`**Suggested fix:** ${match.suggested_fix}\n\n`);
    }
    if (match.rule_id === "DJANGO003") {
      md.appendMarkdown(`_A quick fix is available — see the 💡 lightbulb._`);
    }

    const col = match.col ?? 0;
    const range = new vscode.Range(position.line, col, position.line, col + 1);
    return new vscode.Hover(md, range);
  }
}
