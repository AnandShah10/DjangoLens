import * as vscode from "vscode";
import { runRuntimeReport, RuntimeReportEndpoint } from "./pythonBridge";

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderHtml(reportsDir: string, endpoints: RuntimeReportEndpoint[]): string {
  if (endpoints.length === 0) {
    return `<!DOCTYPE html>
<html><head><meta charset="UTF-8"><style>
  body { font-family: var(--vscode-font-family); color: var(--vscode-foreground); padding: 16px; }
  code { font-family: var(--vscode-editor-font-family, monospace); }
</style></head>
<body>
  <h2>DjangoLens — Runtime Report</h2>
  <p>No reports found in <code>${escapeHtml(reportsDir)}</code>.</p>
  <p>Enable <code>djangolens_runtime.middleware.DjangoLensQueryMiddleware</code> in your Django
     project's settings and browse a few pages first — see
     <code>python/djangolens_runtime/README.md</code> for setup. This reads its output; it does
     not run Django itself.</p>
</body></html>`;
  }

  const rows = endpoints
    .map((e) => {
      const ms = (e.wall_time * 1000).toFixed(1);
      const queryLabel = e.total_queries === 1 ? "query" : "queries";
      const issueCell = e.top_issue
        ? `<span class="badge risky">${e.top_issue.count}x duplicate</span> ${escapeHtml(
            e.top_issue.file ? `${e.top_issue.file}:${e.top_issue.line ?? "?"}` : ""
          )}`
        : `<span class="badge ok">none</span>`;
      return `<tr>
        <td>${escapeHtml(e.method ?? "?")}</td>
        <td class="path">${escapeHtml(e.path ?? "?")}</td>
        <td>${ms} ms</td>
        <td>${e.total_queries} ${queryLabel}</td>
        <td>${issueCell}</td>
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
  tr:first-child td { font-weight: 600; } /* slowest request, sorted first by the CLI */
  .path { font-family: var(--vscode-editor-font-family, monospace); }
  .badge { padding: 2px 8px; border-radius: 10px; font-size: 0.85em; }
  .badge.ok { background: rgba(90, 200, 120, 0.25); }
  .badge.risky { background: rgba(220, 80, 80, 0.3); font-weight: 600; }
</style>
</head>
<body>
  <h2>DjangoLens — Runtime Report</h2>
  <p class="note">
    ${endpoints.length} captured request(s), sorted slowest first. "Duplicate" queries are the
    runtime signature of an N+1: the same query shape from the same source location, issued more
    than once in one request.
  </p>
  <table>
    <thead><tr><th>Method</th><th>Path</th><th>Time</th><th>Queries</th><th>Top issue</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>
</body>
</html>`;
}

export async function showRuntimeReport(context: vscode.ExtensionContext) {
  const folders = vscode.workspace.workspaceFolders;
  const defaultDir = folders && folders.length > 0 ? `${folders[0].uri.fsPath}/.djangolens/runtime` : "";

  const reportsDir = await vscode.window.showInputBox({
    prompt: "Directory containing djangolens_runtime's JSON reports",
    value: defaultDir,
    placeHolder: "/path/to/.djangolens/runtime",
  });
  if (!reportsDir) {
    return; // user cancelled
  }

  const pythonPath = vscode.workspace.getConfiguration("djangolens").get<string>("pythonPath", "python3");

  const panel = vscode.window.createWebviewPanel(
    "djangolensRuntimeReport",
    "DjangoLens: Runtime Report",
    vscode.ViewColumn.Active,
    { enableScripts: false }
  );
  panel.webview.html = `<html><body style="font-family: sans-serif; padding: 16px;">Loading runtime report…</body></html>`;

  try {
    const result = await runRuntimeReport(pythonPath, context.extensionPath, reportsDir);
    panel.webview.html = renderHtml(result.reports_dir, result.endpoints);
  } catch (err: any) {
    panel.webview.html = `<html><body style="font-family: sans-serif; padding: 16px;">
      <p>Failed to load runtime report: ${escapeHtml(err.message)}</p>
    </body></html>`;
  }
}
