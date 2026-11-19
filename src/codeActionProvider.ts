import * as vscode from "vscode";
import { DjangoLensDiagnostic } from "./pythonBridge";

/**
 * Quick fixes for:
 * - DJANGO003 (possible N+1 query) — select_related / prefetch_related
 * - DJANGO009 (on_delete issues) — safe insertions where unambiguous
 *
 * Deliberately offers BOTH `.select_related(...)` and `.prefetch_related
 * (...)` rather than picking one: static analysis can't know whether a
 * relation is a forward FK/OneToOne (select_related) or a reverse FK/
 * ManyToMany (prefetch_related) without executing the Django app registry,
 * which this extension never does (see README "Security/Privacy"). Both
 * insertions are safe and additive — they're appended at the exact end of
 * the queryset expression (`insert_line`/`insert_col`, computed in
 * n1_detector.py), which is always valid regardless of the chain's shape.
 * The developer picks the one that actually matches their model.
 *
 * For DJANGO009 SET_NULL-without-null=True, offers a single safe edit:
 * insert `, null=True` before the closing paren of the field call when
 * the diagnostic message indicates that specific pattern.
 */
export class DjangoLensCodeActionProvider implements vscode.CodeActionProvider {
  static readonly providedCodeActionKinds = [vscode.CodeActionKind.QuickFix];

  constructor(private readonly getDiagnosticsForFile: (file: string) => DjangoLensDiagnostic[]) {}

  provideCodeActions(
    document: vscode.TextDocument,
    _range: vscode.Range | vscode.Selection,
    context: vscode.CodeActionContext
  ): vscode.CodeAction[] {
    const actions: vscode.CodeAction[] = [];
    const rawDiagnostics = this.getDiagnosticsForFile(document.uri.fsPath);

    for (const vsDiagnostic of context.diagnostics) {
      const code = vsDiagnostic.code;

      if (code === "DJANGO003") {
        this._n1Actions(document, vsDiagnostic, rawDiagnostics, actions);
      } else if (code === "DJANGO009") {
        this._onDeleteActions(document, vsDiagnostic, rawDiagnostics, actions);
      } else if (code === "DJANGO010") {
        this._nullBlankActions(document, vsDiagnostic, rawDiagnostics, actions);
      } else if (code === "DJANGO011") {
        this._indexActions(document, vsDiagnostic, rawDiagnostics, actions);
      }
    }

    return actions;
  }

  private _n1Actions(
    document: vscode.TextDocument,
    vsDiagnostic: vscode.Diagnostic,
    rawDiagnostics: DjangoLensDiagnostic[],
    actions: vscode.CodeAction[]
  ) {
    const match = rawDiagnostics.find(
      (d) =>
        d.rule_id === "DJANGO003" &&
        d.line !== undefined &&
        d.col !== undefined &&
        d.line - 1 === vsDiagnostic.range.start.line &&
        d.col === vsDiagnostic.range.start.character &&
        d.relation !== undefined &&
        d.insert_line !== undefined &&
        d.insert_col !== undefined
    );
    if (!match) {
      return;
    }

    const insertPosition = new vscode.Position(match.insert_line! - 1, match.insert_col!);

    for (const methodName of ["select_related", "prefetch_related"] as const) {
      const action = new vscode.CodeAction(
        `DjangoLens: Add .${methodName}("${match.relation}")`,
        vscode.CodeActionKind.QuickFix
      );
      const edit = new vscode.WorkspaceEdit();
      edit.insert(document.uri, insertPosition, `.${methodName}("${match.relation}")`);
      action.edit = edit;
      action.diagnostics = [vsDiagnostic];
      const preferred = (match as any).preferred_optimizer === "prefetch_related" ? "prefetch_related" : "select_related";
        action.isPreferred = methodName === preferred;
      actions.push(action);
    }
  }

  private _onDeleteActions(
    document: vscode.TextDocument,
    vsDiagnostic: vscode.Diagnostic,
    rawDiagnostics: DjangoLensDiagnostic[],
    actions: vscode.CodeAction[]
  ) {
    const match = rawDiagnostics.find(
      (d) =>
        d.rule_id === "DJANGO009" &&
        d.line !== undefined &&
        d.line - 1 === vsDiagnostic.range.start.line
    );
    if (!match) {
      return;
    }

    const lineIdx = (match.line ?? 1) - 1;
    const lineText = document.lineAt(lineIdx).text;

    // SET_NULL without null=True → insert null=True before closing paren
    if (match.message.includes("SET_NULL requires null=True")) {
      const closeParen = lineText.lastIndexOf(")");
      if (closeParen > 0) {
        const action = new vscode.CodeAction(
          "DjangoLens: Add null=True",
          vscode.CodeActionKind.QuickFix
        );
        const edit = new vscode.WorkspaceEdit();
        // Insert before the final ')'
        const needsComma = !lineText.slice(0, closeParen).trimEnd().endsWith(",");
        const insertion = needsComma ? ", null=True" : " null=True";
        edit.insert(document.uri, new vscode.Position(lineIdx, closeParen), insertion);
        action.edit = edit;
        action.diagnostics = [vsDiagnostic];
        action.isPreferred = true;
        actions.push(action);
      }
    }

    // Missing on_delete entirely → offer CASCADE as the most common default
    if (match.message.includes("missing on_delete")) {
      const closeParen = lineText.lastIndexOf(")");
      if (closeParen > 0) {
        for (const choice of ["models.CASCADE", "models.PROTECT", "models.SET_NULL"] as const) {
          const action = new vscode.CodeAction(
            `DjangoLens: Add on_delete=${choice}`,
            vscode.CodeActionKind.QuickFix
          );
          const edit = new vscode.WorkspaceEdit();
          const needsComma = !lineText.slice(0, closeParen).trimEnd().endsWith(",");
          let insertion = needsComma ? `, on_delete=${choice}` : ` on_delete=${choice}`;
          if (choice === "models.SET_NULL") {
            insertion += ", null=True";
          }
          edit.insert(document.uri, new vscode.Position(lineIdx, closeParen), insertion);
          action.edit = edit;
          action.diagnostics = [vsDiagnostic];
          action.isPreferred = choice === "models.CASCADE";
          actions.push(action);
        }
      }
    }
  }

  private _nullBlankActions(
    document: vscode.TextDocument,
    vsDiagnostic: vscode.Diagnostic,
    rawDiagnostics: DjangoLensDiagnostic[],
    actions: vscode.CodeAction[]
  ) {
    const match = rawDiagnostics.find(
      (d) =>
        d.rule_id === "DJANGO010" &&
        d.line !== undefined &&
        d.line - 1 === vsDiagnostic.range.start.line
    );
    if (!match) {
      return;
    }

    const lineIdx = (match.line ?? 1) - 1;
    const lineText = document.lineAt(lineIdx).text;

    // null=True on CharField without blank=True
    if (match.message.includes("without blank=True")) {
      const closeParen = lineText.lastIndexOf(")");
      if (closeParen > 0) {
        const action = new vscode.CodeAction(
          "DjangoLens: Add blank=True",
          vscode.CodeActionKind.QuickFix
        );
        const edit = new vscode.WorkspaceEdit();
        const needsComma = !lineText.slice(0, closeParen).trimEnd().endsWith(",");
        const insertion = needsComma ? ", blank=True" : " blank=True";
        edit.insert(document.uri, new vscode.Position(lineIdx, closeParen), insertion);
        action.edit = edit;
        action.diagnostics = [vsDiagnostic];
        action.isPreferred = true;
        actions.push(action);
      }
    }
  }

  private _indexActions(
    document: vscode.TextDocument,
    vsDiagnostic: vscode.Diagnostic,
    rawDiagnostics: DjangoLensDiagnostic[],
    actions: vscode.CodeAction[]
  ) {
    const match = rawDiagnostics.find(
      (d) =>
        d.rule_id === "DJANGO011" &&
        d.line !== undefined &&
        d.line - 1 === vsDiagnostic.range.start.line
    );
    if (!match) return;
    const lineIdx = (match.line ?? 1) - 1;
    const lineText = document.lineAt(lineIdx).text;
    if (lineText.includes("db_index=False")) {
      const action = new vscode.CodeAction(
        "DjangoLens: Remove db_index=False",
        vscode.CodeActionKind.QuickFix
      );
      const edit = new vscode.WorkspaceEdit();
      const updated = lineText.replace(/,?\s*db_index\s*=\s*False/, "");
      edit.replace(
        document.uri,
        new vscode.Range(lineIdx, 0, lineIdx, lineText.length),
        updated
      );
      action.edit = edit;
      action.diagnostics = [vsDiagnostic];
      action.isPreferred = true;
      actions.push(action);
    } else if (!lineText.includes("db_index=")) {
      const closeParen = lineText.lastIndexOf(")");
      if (closeParen > 0) {
        const action = new vscode.CodeAction(
          "DjangoLens: Add db_index=True",
          vscode.CodeActionKind.QuickFix
        );
        const edit = new vscode.WorkspaceEdit();
        const needsComma = !lineText.slice(0, closeParen).trimEnd().endsWith(",");
        const insertion = needsComma ? ", db_index=True" : " db_index=True";
        edit.insert(document.uri, new vscode.Position(lineIdx, closeParen), insertion);
        action.edit = edit;
        action.diagnostics = [vsDiagnostic];
        action.isPreferred = true;
        actions.push(action);
      }
    }
  }

}
