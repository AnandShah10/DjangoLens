import * as vscode from "vscode";
import { runSignalChains, SignalChain } from "./pythonBridge";

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

const EFFECT_LABEL: Record<string, string> = {
  recursive_save: "recursive save",
  network_call: "network call",
  email: "sends email",
};

function renderChain(chain: SignalChain): string {
  const hasEffects = chain.effects.length > 0;
  const effectBadges = chain.effects
    .map((e) => `<span class="badge risky">${escapeHtml(EFFECT_LABEL[e] ?? e)}</span>`)
    .join(" ");

  return `<div class="chain ${hasEffects ? "risky" : ""}">
    <div class="row">
      <span class="node sender">${escapeHtml(chain.sender ?? "?")}</span>
      <span class="arrow">&rarr;[${escapeHtml(chain.signal)}]&rarr;</span>
      <span class="node receiver">${escapeHtml(chain.receiver)}</span>
      ${hasEffects ? `<span class="arrow">&rarr;</span>${effectBadges}` : '<span class="badge ok">no effects detected</span>'}
    </div>
    <div class="location">${escapeHtml(chain.file)}:${chain.line}</div>
  </div>`;
}

function renderHtml(chains: SignalChain[]): string {
  if (chains.length === 0) {
    return `<!DOCTYPE html>
<html><head><meta charset="UTF-8"><style>
  body { font-family: var(--vscode-font-family); color: var(--vscode-foreground); padding: 16px; }
</style></head>
<body>
  <h2>DjangoLens — Signal Chains</h2>
  <p>No Django signal receivers found (looked for @receiver decorators and .connect() registrations).</p>
</body></html>`;
  }

  const rows = chains.map(renderChain).join("\n");

  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
  body { font-family: var(--vscode-font-family); color: var(--vscode-foreground); padding: 16px; }
  h2 { font-weight: 600; }
  p.note { opacity: 0.75; font-size: 0.9em; max-width: 640px; }
  .chain { border: 1px solid var(--vscode-panel-border, #4443); border-radius: 6px; padding: 10px 14px; margin-bottom: 10px; }
  .chain.risky { border-color: rgba(220, 80, 80, 0.5); }
  .row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .node { padding: 3px 10px; border-radius: 6px; background: rgba(120, 120, 120, 0.15); font-weight: 600; }
  .arrow { opacity: 0.7; font-size: 0.9em; }
  .badge { padding: 2px 8px; border-radius: 10px; font-size: 0.85em; }
  .badge.risky { background: rgba(220, 80, 80, 0.3); font-weight: 600; }
  .badge.ok { background: rgba(90, 200, 120, 0.2); opacity: 0.8; }
  .location { font-family: var(--vscode-editor-font-family, monospace); font-size: 0.8em; opacity: 0.65; margin-top: 6px; }
</style>
</head>
<body>
  <h2>DjangoLens — Signal Chains</h2>
  <p class="note">
    ${chains.length} receiver(s), covering both @receiver-decorated and .connect()-registered
    handlers. Red-bordered chains have a detected effect (recursive save risk, or a synchronous
    network/email call) — see the Signal Detector diagnostics in the Problems panel for the
    full explanation and suggested fix on each.
  </p>
  <div>${rows}</div>
</body>
</html>`;
}

export async function showSignalChains(context: vscode.ExtensionContext) {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || folders.length === 0) {
    vscode.window.showWarningMessage("DjangoLens: open a folder to view its signal chains.");
    return;
  }

  const workspaceRoot = folders[0].uri.fsPath;
  const pythonPath = vscode.workspace.getConfiguration("djangolens").get<string>("pythonPath", "python3");

  const panel = vscode.window.createWebviewPanel(
    "djangolensSignalChains",
    "DjangoLens: Signal Chains",
    vscode.ViewColumn.Active,
    { enableScripts: false }
  );
  panel.webview.html = `<html><body style="font-family: sans-serif; padding: 16px;">Loading signal chains…</body></html>`;

  try {
    const result = await runSignalChains(pythonPath, context.extensionPath, workspaceRoot);
    panel.webview.html = renderHtml(result.chains);
  } catch (err: any) {
    panel.webview.html = `<html><body style="font-family: sans-serif; padding: 16px;">
      <p>Failed to load signal chains: ${escapeHtml(err.message)}</p>
    </body></html>`;
  }
}
