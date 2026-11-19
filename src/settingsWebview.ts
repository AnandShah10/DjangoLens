import * as vscode from "vscode";
import { runSettingsOverview, SettingsFileSummary } from "./pythonBridge";

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * `riskyWhenTrue` says whether `value === true` should render as the
 * "risky" badge — DEBUG and the wildcard checks are risky when true;
 * secret_key_hardcoded is also risky when true. All three of this
 * project's booleans happen to be "risky when true", but the parameter
 * keeps that an explicit choice per-column rather than assumed.
 */
function renderBadge(
  value: boolean | null,
  trueLabel: string,
  falseLabel: string,
  riskyWhenTrue: boolean,
  inheritedFrom?: string | null
): string {
  if (value === null) {
    return `<span class="badge unknown">?</span>`;
  }
  const label = value ? trueLabel : falseLabel;
  const isRisky = value === riskyWhenTrue;
  const inheritedTag = inheritedFrom ? ` <span class="tag">from ${escapeHtml(inheritedFrom)}</span>` : "";
  return `<span class="badge ${isRisky ? "risky" : "ok"}">${escapeHtml(label)}</span>${inheritedTag}`;
}

function renderHtml(files: SettingsFileSummary[]): string {
  const rows = files
    .map((f) => {
      if (f.parse_error) {
        return `<tr>
          <td>${escapeHtml(f.env_label)}</td>
          <td colspan="3"><span class="badge unknown">could not parse this file</span></td>
          <td class="file-path">${escapeHtml(f.file)}</td>
        </tr>`;
      }
      // Show the EFFECTIVE value (after following `from .base import *`),
      // falling back to the file's own value for output from an older CLI.
      const eff = f.effective ?? {
        debug: f.debug,
        secret_key_hardcoded: f.secret_key_hardcoded,
        allowed_hosts_wildcard: f.allowed_hosts_wildcard,
      };
      const from = f.inherited_from;
      const extendsTag = f.extends && f.extends.length > 0
        ? ` <span class="tag">extends ${escapeHtml(f.extends.join(", "))}</span>`
        : "";
      return `<tr class="${f.prod_like ? "prod-like" : ""}">
        <td>${escapeHtml(f.env_label)}${f.prod_like ? ' <span class="tag">prod-like</span>' : ""}${extendsTag}</td>
        <td>${renderBadge(eff.debug, "True", "False", true, from?.debug)}</td>
        <td>${renderBadge(eff.secret_key_hardcoded, "Hardcoded", "Not hardcoded", true, from?.secret_key_hardcoded)}</td>
        <td>${renderBadge(eff.allowed_hosts_wildcard, "Wildcard (*)", "OK", true, from?.allowed_hosts_wildcard)}</td>
        <td class="file-path">${escapeHtml(f.file)}</td>
      </tr>`;
    })
    .join("\n");

  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
  body { font-family: var(--vscode-font-family); color: var(--vscode-foreground); padding: 16px; }
  h2 { font-weight: 600; }
  p.note { opacity: 0.75; font-size: 0.9em; max-width: 640px; }
  table { border-collapse: collapse; width: 100%; margin-top: 12px; }
  th, td { text-align: left; padding: 6px 12px; border-bottom: 1px solid var(--vscode-panel-border, #4443); }
  tr.prod-like { background: rgba(255, 196, 0, 0.08); }
  .badge { padding: 2px 8px; border-radius: 10px; font-size: 0.85em; display: inline-block; }
  .badge.ok { background: rgba(90, 200, 120, 0.25); }
  .badge.risky { background: rgba(220, 80, 80, 0.3); font-weight: 600; }
  .badge.unknown { opacity: 0.6; background: rgba(128, 128, 128, 0.2); }
  .tag { font-size: 0.75em; opacity: 0.7; }
  .file-path { font-family: var(--vscode-editor-font-family, monospace); font-size: 0.85em; opacity: 0.8; }
</style>
</head>
<body>
  <h2>DjangoLens — Settings Overview</h2>
  <p class="note">
    Secret <strong>values</strong> are never shown here — only whether SECRET_KEY is hardcoded
    as a literal in this file. "?" means DjangoLens couldn't statically determine the value
    (most often because it's loaded from an environment variable, which is the recommended
    pattern — that's a good "?", not a bad one). Values shown are <em>effective</em>: when a
    file star-imports another (<code>from .base import *</code>) and doesn't assign a setting
    itself, the inherited value is shown with a "from &lt;file&gt;" tag. Rows highlighted are
    settings files whose name looks production-like.
  </p>
  <table>
    <thead><tr><th>Environment</th><th>DEBUG</th><th>SECRET_KEY</th><th>ALLOWED_HOSTS</th><th>File</th></tr></thead>
    <tbody>${rows || '<tr><td colspan="5">No settings files found.</td></tr>'}</tbody>
  </table>
</body>
</html>`;
}

export async function showSettingsOverview(context: vscode.ExtensionContext) {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || folders.length === 0) {
    vscode.window.showWarningMessage("DjangoLens: open a folder to view its settings overview.");
    return;
  }

  const workspaceRoot = folders[0].uri.fsPath;
  const pythonPath = vscode.workspace.getConfiguration("djangolens").get<string>("pythonPath", "python3");

  const panel = vscode.window.createWebviewPanel(
    "djangolensSettingsOverview",
    "DjangoLens: Settings Overview",
    vscode.ViewColumn.Active,
    { enableScripts: false }
  );
  panel.webview.html = `<html><body style="font-family: sans-serif; padding: 16px;">Loading settings overview…</body></html>`;

  try {
    const result = await runSettingsOverview(pythonPath, context.extensionPath, workspaceRoot);
    panel.webview.html = renderHtml(result.files);
  } catch (err: any) {
    panel.webview.html = `<html><body style="font-family: sans-serif; padding: 16px;">
      <p>Failed to load settings overview: ${escapeHtml(err.message)}</p>
    </body></html>`;
  }
}
