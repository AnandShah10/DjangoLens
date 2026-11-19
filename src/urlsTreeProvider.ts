import * as vscode from "vscode";
import { runUrlsScan, UrlEndpoint } from "./pythonBridge";

export class UrlTreeItem extends vscode.TreeItem {
  constructor(
    label: string,
    public readonly endpoint?: UrlEndpoint,
    collapsibleState: vscode.TreeItemCollapsibleState = vscode.TreeItemCollapsibleState.None
  ) {
    super(label, collapsibleState);

    if (endpoint) {
      this.description = endpoint.view ?? undefined;
      this.tooltip = endpoint.name ? `${endpoint.path}  (name=${endpoint.name})` : endpoint.path;
      this.iconPath = new vscode.ThemeIcon("link");
      this.contextValue = "djangolensUrlEndpoint";
      if (endpoint.file && endpoint.line !== undefined) {
        const line = Math.max(endpoint.line - 1, 0);
        const col = Math.max(endpoint.col ?? 0, 0);
        this.command = {
          command: "vscode.open",
          title: "Open",
          arguments: [
            vscode.Uri.file(endpoint.file),
            { selection: new vscode.Range(line, col, line, col) }
          ]
        };
      }
    } else {
      this.iconPath = new vscode.ThemeIcon("folder");
    }
  }
}

/**
 * Groups endpoints by their first path segment (e.g. "orders/",
 * "payments/") so the tree isn't one flat list. This loosely mirrors the
 * hierarchical URL Explorer mockup from the original spec without needing
 * full route-trie construction (deferred — see README).
 */
function groupByFirstSegment(endpoints: UrlEndpoint[]): Map<string, UrlEndpoint[]> {
  const groups = new Map<string, UrlEndpoint[]>();
  for (const ep of endpoints) {
    const segment = ep.path.split("/").filter(Boolean)[0] ?? "(root)";
    const arr = groups.get(segment) ?? [];
    arr.push(ep);
    groups.set(segment, arr);
  }
  return groups;
}

export class DjangoUrlsProvider implements vscode.TreeDataProvider<UrlTreeItem> {
  private readonly _onDidChangeTreeData = new vscode.EventEmitter<UrlTreeItem | undefined | void>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  private groups: Map<string, UrlEndpoint[]> = new Map();
  private notes: string[] = [];
  private loaded = false;
  private loadError: string | undefined;

  constructor(
    private readonly extensionPath: string,
    private readonly getPythonPath: () => string
  ) {}

  refresh(): void {
    this.loaded = false;
    this.loadError = undefined;
    this._onDidChangeTreeData.fire();
  }

  getTreeItem(element: UrlTreeItem): vscode.TreeItem {
    return element;
  }

  async getChildren(element?: UrlTreeItem): Promise<UrlTreeItem[]> {
    const folders = vscode.workspace.workspaceFolders;
    if (!folders || folders.length === 0) {
      return [new UrlTreeItem("Open a folder to see Django URLs")];
    }

    if (!this.loaded) {
      try {
        const result = await runUrlsScan(this.getPythonPath(), this.extensionPath, folders[0].uri.fsPath);
        this.groups = groupByFirstSegment(result.endpoints);
        this.notes = result.notes;
        this.loaded = true;
      } catch (err: any) {
        this.loadError = err.message;
        this.loaded = true;
      }
    }

    if (this.loadError) {
      return [new UrlTreeItem(`Failed to load URLs: ${this.loadError}`)];
    }

    if (!element) {
      if (this.groups.size === 0) {
        const items = [new UrlTreeItem("No urlpatterns found (or ROOT_URLCONF could not be resolved)")];
        for (const note of this.notes) {
          items.push(new UrlTreeItem(note));
        }
        return items;
      }
      const segmentItems = Array.from(this.groups.keys())
        .sort()
        .map((segment) => new UrlTreeItem(`/${segment}`, undefined, vscode.TreeItemCollapsibleState.Collapsed));
      return segmentItems;
    }

    const segment = String(element.label ?? "").replace(/^\//, "");
    const endpoints = (this.groups.get(segment) ?? []).slice().sort((a, b) => a.path.localeCompare(b.path));
    return endpoints.map((ep) => new UrlTreeItem(`/${ep.path}`, ep));
  }
}
