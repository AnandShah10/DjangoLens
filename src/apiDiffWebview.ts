import * as vscode from "vscode";
import { runApiDiff, DjangoLensDiagnostic } from "./pythonBridge";

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

const SEVERITY_CLASS: Record<string, string> = {
  error: "risky",
  warning: "warn",
  information: "ok",
};

function renderHtml(baseRef: string, headRef: string, error: string | null, diagnostics: DjangoLensDiagnostic[]): string {
  const head = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
  body { font-family: var(--vscode-font-family); color: var(--vscode-foreground); padding: 16px; }
  h2 { font-weight: 600; }
  p.note { opacity: 0.75; font-size: 0.9em; max-width: 640px; }
  code { font-family: var(--vscode-editor-font-family, monospace); }
  .item { border-bottom: 1px solid var(--vscode-panel-border, #4443); padding: 10px 0; }
  .badge { padding: 2px 8px; border-radius: 10px; font-size: 0.85em; margin-right: 6px; }
  .badge.risky { background: rgba(220, 80, 80, 0.3); font-weight: 600; }
  .badge.warn { background: rgba(230, 180, 60, 0.3); }
  .badge.ok { background: rgba(90, 200, 120, 0.25); }
  .message { font-weight: 600; }
  .explanation, .fix, .location { font-size: 0.9em; opacity: 0.85; margin-top: 2px; }
  .fix { font-family: var(--vscode-editor-font-family, monospace); }
</style>
</head>
<body>
  <h2>DjangoLens — API Contract Drift</h2>
  <p class="note">Comparing <code>${escapeHtml(baseRef)}</code> &rarr; <code>${escapeHtml(headRef)}</code>.
     Only DRF serializer field declarations are compared — no consumer usage is inspected or claimed.</p>`;

  if (error) {
    return `${head}<p><strong>Could not run:</strong> ${escapeHtml(error)}</p></body></html>`;
  }

  if (diagnostics.length === 0) {
    return `${head}<p>No API contract drift found.</p></body></html>`;
  }

  const items = diagnostics
    .map((d) => {
      const cls = SEVERITY_CLASS[d.severity] ?? "warn";
      return `<div class="item">
        <span class="badge ${cls}">${escapeHtml(d.severity.toUpperCase())}</span>
        <span class="message">${escapeHtml(d.message)}</span>
        ${d.explanation ? `<div class="explanation">${escapeHtml(d.explanation)}</div>` : ""}
        ${d.suggested_fix ? `<div class="fix">Suggested fix: ${escapeHtml(d.suggested_fix)}</div>` : ""}
        <div class="location">${escapeHtml(d.file ?? "")}</div>
      </div>`;
    })
    .join("\n");

  return `${head}<div>${items}</div></body></html>`;
}

export async function showApiContractDrift(context: vscode.ExtensionContext) {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || folders.length === 0) {
    vscode.window.showWarningMessage("DjangoLens: open a folder to compare its API contract.");
    return;
  }
  const workspaceRoot = folders[0].uri.fsPath;

  const baseRef = await vscode.window.showInputBox({
    prompt: "Git ref to compare FROM (e.g. main, a commit SHA, a tag)",
    placeHolder: "main",
  });
  if (!baseRef) {
    return; // user cancelled
  }

  const headRef = await vscode.window.showInputBox({
    prompt: "Git ref to compare TO — leave empty to use the current working tree",
    placeHolder: "(current working tree)",
  });
  // headRef === undefined means the input box was cancelled (Esc) — treat
  // that as "use the default" (empty string) rather than aborting the
  // whole command, since the person already committed to a base ref.
  const resolvedHeadRef = headRef || undefined;

  const pythonPath = vscode.workspace.getConfiguration("djangolens").get<string>("pythonPath", "python3");

  const panel = vscode.window.createWebviewPanel(
    "djangolensApiDiff",
    "DjangoLens: API Contract Drift",
    vscode.ViewColumn.Active,
    { enableScripts: false }
  );
  panel.webview.html = `<html><body style="font-family: sans-serif; padding: 16px;">Comparing API contracts…</body></html>`;

  try {
    const result = await runApiDiff(pythonPath, context.extensionPath, workspaceRoot, baseRef, resolvedHeadRef);
    panel.webview.html = renderHtml(result.base_ref, result.head_ref, result.error, result.diagnostics);
  } catch (err: any) {
    panel.webview.html = `<html><body style="font-family: sans-serif; padding: 16px;">
      <p>Failed to run API Contract Drift: ${escapeHtml(err.message)}</p>
    </body></html>`;
  }
}
