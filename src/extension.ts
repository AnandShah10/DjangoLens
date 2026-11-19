import * as vscode from "vscode";
import * as path from "path";
import * as fs from "fs";
import { runScan, DjangoLensDiagnostic, HealthScore, runTestImpactScan } from "./pythonBridge";
import { DjangoUrlsProvider } from "./urlsTreeProvider";
import { DjangoLensCodeActionProvider } from "./codeActionProvider";
import { DjangoLensHoverProvider } from "./hoverProvider";
import { DjangoLensCodeLensProvider } from "./codeLensProvider";
import { DjangoIssuesProvider } from "./issuesTreeProvider";
import { showSettingsOverview } from "./settingsWebview";
import { showRuntimeReport } from "./runtimeReportWebview";
import { showApiContractDrift } from "./apiDiffWebview";
import { showSignalChains } from "./signalChainsWebview";

let diagnosticCollection: vscode.DiagnosticCollection;
// Raw analyzer output, kept alongside diagnosticCollection so providers
// that need structured fields vscode.Diagnostic doesn't preserve
// (relation/insert_line/insert_col, explanation, suggested_fix, ...) can
// recover them — CodeActionProvider, HoverProvider, CodeLensProvider, and
// the Issues tree all read from these.
const rawDiagnosticsByFile = new Map<string, DjangoLensDiagnostic[]>();
let lastScanDiagnostics: DjangoLensDiagnostic[] = [];
let lastHealthScore: HealthScore | undefined;
let codeLensProvider: DjangoLensCodeLensProvider | undefined;
let issuesProvider: DjangoIssuesProvider | undefined;

function severityToVsCode(sev: string): vscode.DiagnosticSeverity {
  switch (sev) {
    case "error":
      return vscode.DiagnosticSeverity.Error;
    case "warning":
      return vscode.DiagnosticSeverity.Warning;
    default:
      return vscode.DiagnosticSeverity.Information;
  }
}

async function scanWorkspace(context: vscode.ExtensionContext) {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || folders.length === 0) {
    vscode.window.showWarningMessage("DjangoLens: open a folder to scan.");
    return;
  }

  const config = vscode.workspace.getConfiguration("djangolens");
  const pythonPath = config.get<string>("pythonPath", "python3");
  const configRelPath = config.get<string>("configPath", "djangolens.config.json");
  const workspaceRoot = folders[0].uri.fsPath;
  const absoluteConfigPath = path.join(workspaceRoot, configRelPath);
  const configPath = fs.existsSync(absoluteConfigPath) ? absoluteConfigPath : undefined;

  diagnosticCollection.clear();

  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "DjangoLens: scanning project…" },
    async () => {
      try {
        const result = await runScan(pythonPath, context.extensionPath, workspaceRoot, configPath);
        lastHealthScore = result.health;
        applyDiagnostics(result.diagnostics);
        const healthSuffix = result.health ? ` — health ${result.health.score}/100` : "";
        vscode.window.showInformationMessage(
          `DjangoLens: found ${result.diagnostic_count} issue(s) across ${result.apps.length} app(s)${healthSuffix}.`
        );
      } catch (err: any) {
        vscode.window.showErrorMessage(
          `DjangoLens scan failed: ${err.message}. Make sure Python 3 is installed and on PATH, ` +
            `or set "djangolens.pythonPath" in your settings.`
        );
      }
    }
  );
}

function applyDiagnostics(diagnostics: DjangoLensDiagnostic[]) {
  const byFile = new Map<string, vscode.Diagnostic[]>();
  rawDiagnosticsByFile.clear();
  lastScanDiagnostics = diagnostics;

  for (const d of diagnostics) {
    if (!d.file) {
      // Most diagnostics carry a file (circular-dependency reports now
      // attach a representative import location too — see
      // architecture.py). This branch is a safety net for any future
      // analyzer that reports something genuinely project-wide.
      continue;
    }

    const rawArr = rawDiagnosticsByFile.get(d.file) ?? [];
    rawArr.push(d);
    rawDiagnosticsByFile.set(d.file, rawArr);

    const line = Math.max((d.line ?? 1) - 1, 0);
    const col = Math.max(d.col ?? 0, 0);
    const range = new vscode.Range(line, col, line, col + 1);

    const messageParts = [d.message];
    if (d.explanation) {
      messageParts.push(d.explanation);
    }
    if (d.suggested_fix) {
      messageParts.push(`Suggested fix: ${d.suggested_fix}`);
    }

    const diagnostic = new vscode.Diagnostic(
      range,
      messageParts.join(" — "),
      severityToVsCode(d.severity)
    );
    diagnostic.source = `DjangoLens (${d.confidence} confidence)`;
    diagnostic.code = d.rule_id;

    const arr = byFile.get(d.file) ?? [];
    arr.push(diagnostic);
    byFile.set(d.file, arr);
  }

  for (const [file, diags] of byFile) {
    diagnosticCollection.set(vscode.Uri.file(file), diags);
  }

  codeLensProvider?.refresh();
  issuesProvider?.refresh();
}

async function analyzeTestImpact(context: vscode.ExtensionContext) {
  const editor = vscode.window.activeTextEditor;
  const folders = vscode.workspace.workspaceFolders;
  if (!editor || !folders || folders.length === 0) {
    vscode.window.showWarningMessage("DjangoLens: open a Python file to analyze its test impact.");
    return;
  }

  const workspaceRoot = folders[0].uri.fsPath;
  const relativePath = path.relative(workspaceRoot, editor.document.uri.fsPath);
  const pythonPath = vscode.workspace.getConfiguration("djangolens").get<string>("pythonPath", "python3");

  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "DjangoLens: analyzing test impact…" },
    async () => {
      try {
        const result = await runTestImpactScan(pythonPath, context.extensionPath, workspaceRoot, relativePath);

        if (result.affected_tests.length === 0) {
          vscode.window.showInformationMessage(
            `DjangoLens: no statically-traceable test modules import ${relativePath}, directly or transitively.`
          );
          return;
        }

        const picked = await vscode.window.showQuickPick(
          result.affected_tests.map((t) => ({
            label: `${t.confidence === "high" ? "$(check)" : "$(question)"} ${t.test_module}`,
            description: `${t.confidence}, ${t.hops} hop(s)`,
            detail: t.test_file,
            testFile: t.test_file,
          })),
          { placeHolder: `${result.affected_test_count} potentially affected test module(s) — select one to open` }
        );

        if (picked?.testFile) {
          const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(picked.testFile));
          await vscode.window.showTextDocument(doc);
        }
      } catch (err: any) {
        vscode.window.showErrorMessage(`DjangoLens test-impact analysis failed: ${err.message}`);
      }
    }
  );
}

export function activate(context: vscode.ExtensionContext) {
  diagnosticCollection = vscode.languages.createDiagnosticCollection("djangolens");
  context.subscriptions.push(diagnosticCollection);

  context.subscriptions.push(
    vscode.commands.registerCommand("djangolens.scan", () => scanWorkspace(context))
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("djangolens.refresh", () => scanWorkspace(context))
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("djangolens.analyzeTestImpact", () => analyzeTestImpact(context))
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("djangolens.showSettingsOverview", () => showSettingsOverview(context))
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("djangolens.showRuntimeReport", () => showRuntimeReport(context))
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("djangolens.showApiContractDrift", () => showApiContractDrift(context))
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("djangolens.showSignalChains", () => showSignalChains(context))
  );

  // Runtime performance workflow (explicit, never auto-runs Django)
  context.subscriptions.push(
    vscode.commands.registerCommand("djangolens.setupRuntime", async () => {
      const choice = await vscode.window.showInformationMessage(
        "DjangoLens runtime capture is opt-in and local-only. Enable djangolens_runtime middleware, browse your app, then open Runtime Report.",
        "Open setup docs",
        "Show Runtime Report"
      );
      if (choice === "Open setup docs") {
        const doc = vscode.Uri.file(path.join(context.extensionPath, "python", "djangolens_runtime", "README.md"));
        await vscode.commands.executeCommand("markdown.showPreview", doc);
      } else if (choice === "Show Runtime Report") {
        await vscode.commands.executeCommand("djangolens.showRuntimeReport");
      }
    })
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("djangolens.analyzePerformance", async () => {
      const pick = await vscode.window.showQuickPick([
        { label: "Open captured Runtime Report", description: "Read JSON already written by middleware" },
        { label: "Show setup instructions", description: "Enable local SQL capture without running Django from the extension" },
      ], { placeHolder: "DjangoLens Performance (local-only, explicit)" });
      if (!pick) return;
      if (pick.label.startsWith("Open")) {
        await vscode.commands.executeCommand("djangolens.showRuntimeReport");
      } else {
        await vscode.commands.executeCommand("djangolens.setupRuntime");
      }
    })
  );

  context.subscriptions.push(
    vscode.languages.registerCodeActionsProvider(
      { language: "python" },
      new DjangoLensCodeActionProvider((file) => rawDiagnosticsByFile.get(file) ?? []),
      { providedCodeActionKinds: DjangoLensCodeActionProvider.providedCodeActionKinds }
    )
  );

  context.subscriptions.push(
    vscode.languages.registerHoverProvider(
      { language: "python" },
      new DjangoLensHoverProvider((file) => rawDiagnosticsByFile.get(file) ?? [])
    )
  );

  codeLensProvider = new DjangoLensCodeLensProvider((file) => rawDiagnosticsByFile.get(file) ?? []);
  context.subscriptions.push(
    vscode.languages.registerCodeLensProvider({ language: "python" }, codeLensProvider)
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("djangolens.showDiagnosticDetail", (d: DjangoLensDiagnostic) => {
      const parts = [d.message];
      if (d.explanation) {
        parts.push(d.explanation);
      }
      if (d.suggested_fix) {
        parts.push(`Suggested fix: ${d.suggested_fix}`);
      }
      vscode.window.showInformationMessage(parts.join(" — "));
    })
  );

  issuesProvider = new DjangoIssuesProvider(() => lastScanDiagnostics, () => lastHealthScore);
  context.subscriptions.push(vscode.window.registerTreeDataProvider("djangolensIssues", issuesProvider));
  context.subscriptions.push(
    vscode.commands.registerCommand("djangolensIssues.refresh", () => scanWorkspace(context))
  );

  const getPythonPath = () =>
    vscode.workspace.getConfiguration("djangolens").get<string>("pythonPath", "python3");

  const urlsProvider = new DjangoUrlsProvider(context.extensionPath, getPythonPath);
  context.subscriptions.push(vscode.window.registerTreeDataProvider("djangolensUrls", urlsProvider));
  context.subscriptions.push(
    vscode.commands.registerCommand("djangolensUrls.refresh", () => urlsProvider.refresh())
  );
  // Re-resolve URLs whenever any urls.py in the workspace changes, so the
  // tree doesn't go stale as routes are added/removed during a session.
  const urlsWatcher = vscode.workspace.createFileSystemWatcher("**/urls.py");
  urlsWatcher.onDidChange(() => urlsProvider.refresh());
  urlsWatcher.onDidCreate(() => urlsProvider.refresh());
  urlsWatcher.onDidDelete(() => urlsProvider.refresh());
  context.subscriptions.push(urlsWatcher);
}

export function deactivate() {
  diagnosticCollection?.dispose();
}
