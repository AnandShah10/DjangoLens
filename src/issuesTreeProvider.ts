import * as vscode from "vscode";
import { DjangoLensDiagnostic, HealthScore } from "./pythonBridge";

const SEVERITY_ORDER = ["error", "warning", "information"];
const SEVERITY_ICON: Record<string, string> = {
  error: "error",
  warning: "warning",
  information: "info",
};

class IssueTreeItem extends vscode.TreeItem {
  constructor(
    label: string,
    public readonly diagnostic?: DjangoLensDiagnostic,
    collapsibleState: vscode.TreeItemCollapsibleState = vscode.TreeItemCollapsibleState.None
  ) {
    super(label, collapsibleState);

    if (diagnostic) {
      this.description = diagnostic.rule_id;
      this.tooltip = diagnostic.explanation ?? diagnostic.message;
      this.iconPath = new vscode.ThemeIcon(SEVERITY_ICON[diagnostic.severity] ?? "info");
      if (diagnostic.file && diagnostic.line !== undefined) {
        const line = Math.max(diagnostic.line - 1, 0);
        const col = Math.max(diagnostic.col ?? 0, 0);
        this.command = {
          command: "vscode.open",
          title: "Open",
          arguments: [
            vscode.Uri.file(diagnostic.file),
            { selection: new vscode.Range(line, col, line, col) },
          ],
        };
      }
    } else {
      this.iconPath = new vscode.ThemeIcon("list-tree");
    }
  }
}

/**
 * Flat "all issues from the last scan, grouped by severity" view, with a
 * transparent health-score header — see health_score.py for the exact,
 * documented formula (this UI never invents its own).
 */
export class DjangoIssuesProvider implements vscode.TreeDataProvider<IssueTreeItem> {
  private readonly _onDidChangeTreeData = new vscode.EventEmitter<IssueTreeItem | undefined | void>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  constructor(
    private readonly getDiagnostics: () => DjangoLensDiagnostic[],
    private readonly getHealth: () => HealthScore | undefined
  ) {}

  refresh(): void {
    this._onDidChangeTreeData.fire();
  }

  getTreeItem(element: IssueTreeItem): vscode.TreeItem {
    return element;
  }

  getChildren(element?: IssueTreeItem): IssueTreeItem[] {
    const all = this.getDiagnostics();

    if (!element) {
      if (all.length === 0) {
        return [new IssueTreeItem('No issues yet — run "DjangoLens: Scan Project"')];
      }

      const items: IssueTreeItem[] = [];
      const health = this.getHealth();
      if (health) {
        const topRules = Object.entries(health.deduction_by_rule)
          .sort((a, b) => b[1] - a[1])
          .slice(0, 3)
          .map(([rule, points]) => `${rule} (-${points})`)
          .join(", ");
        const healthItem = new IssueTreeItem(`Health: ${health.score}/100`);
        healthItem.tooltip = topRules
          ? `Top deductions: ${topRules}\n\nFormula: health_score.py (see ARCHITECTURE.md) — deducted per rule, capped at ${health.max_deduction_per_rule} points per rule so no single noisy rule dominates.`
          : "No diagnostics — nothing deducted.";
        healthItem.iconPath = new vscode.ThemeIcon("pulse");
        items.push(healthItem);
      }

      const countBySeverity: Record<string, number> = {};
      for (const d of all) {
        countBySeverity[d.severity] = (countBySeverity[d.severity] ?? 0) + 1;
      }
      items.push(
        ...SEVERITY_ORDER.filter((sev) => countBySeverity[sev]).map(
          (sev) =>
            new IssueTreeItem(
              `${sev} (${countBySeverity[sev]})`,
              undefined,
              vscode.TreeItemCollapsibleState.Expanded
            )
        )
      );
      return items;
    }

    const severity = String(element.label ?? "").split(" ")[0];
    return all.filter((d) => d.severity === severity).map((d) => new IssueTreeItem(d.message, d));
  }
}
