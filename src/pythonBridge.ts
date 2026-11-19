import * as cp from "child_process";
import * as path from "path";

export interface DjangoLensDiagnostic {
  rule_id: string;
  analyzer: string;
  severity: "error" | "warning" | "information";
  confidence: string;
  message: string;
  explanation?: string;
  suggested_fix?: string;
  file?: string;
  line?: number;
  col?: number;
  module?: string;
  apps?: string[];
  // DJANGO003 (possible N+1) only — see n1_detector.py for why insert_line/
  // insert_col is always safe to append `.select_related(...)`/
  // `.prefetch_related(...)` at.
  relation?: string;
  insert_line?: number;
  insert_col?: number;
}

export interface HealthScore {
  score: number;
  total_diagnostics: number;
  deduction_by_rule: Record<string, number>;
  max_deduction_per_rule: number;
}

export interface DjangoLensScanResult {
  project_root: string;
  apps: string[];
  diagnostic_count: number;
  diagnostics: DjangoLensDiagnostic[];
  health?: HealthScore;
}

export interface UrlEndpoint {
  path: string;
  view?: string;
  name?: string;
  file?: string;
  line?: number;
  col?: number;
}

export interface AffectedTest {
  test_module: string;
  test_file?: string;
  confidence: "high" | "likely";
  hops: number;
}

export interface TestImpactResult {
  project_root: string;
  changed_file: string;
  affected_test_count: number;
  affected_tests: AffectedTest[];
}

export interface UrlsScanResult {
  project_root: string;
  endpoint_count: number;
  endpoints: UrlEndpoint[];
  notes: string[];
}

/**
 * Runs the Python analysis engine (python/django_doctor/cli.py) as a subprocess
 * and parses its JSON output. This is the same script that powers the standalone
 * `django-doctor` CLI, so VS Code and CI consume identical analysis logic.
 */
export function runScan(
  pythonPath: string,
  extensionRoot: string,
  workspaceRoot: string,
  configPath: string | undefined
): Promise<DjangoLensScanResult> {
  const args = ["scan", workspaceRoot, "--format", "json"];
  if (configPath) {
    args.push("--config", configPath);
  }
  return runCli<DjangoLensScanResult>(pythonPath, extensionRoot, args);
}

/**
 * Runs `django-doctor urls` — the URL Explorer. Separate return shape from
 * runScan: endpoints + notes, not severity-graded diagnostics.
 */
export interface SettingsFileSummary {
  env_label: string;
  file: string;
  prod_like: boolean;
  debug: boolean | null;
  secret_key_hardcoded: boolean | null;
  allowed_hosts_wildcard: boolean | null;
  // Added with settings-inheritance support. Optional so a response from
  // an older CLI (which only has the three own-file fields above) still
  // type-checks; the webview falls back to the own-file values then.
  extends?: string[];
  effective?: {
    debug: boolean | null;
    secret_key_hardcoded: boolean | null;
    allowed_hosts_wildcard: boolean | null;
  };
  inherited_from?: {
    debug: string | null;
    secret_key_hardcoded: string | null;
    allowed_hosts_wildcard: string | null;
  };
  parse_error: boolean;
}

export interface SettingsOverviewResult {
  project_root: string;
  file_count: number;
  files: SettingsFileSummary[];
}

export function runUrlsScan(
  pythonPath: string,
  extensionRoot: string,
  workspaceRoot: string
): Promise<UrlsScanResult> {
  return runCli<UrlsScanResult>(pythonPath, extensionRoot, ["urls", workspaceRoot, "--format", "json"]);
}

/**
 * Runs `django-doctor test-impact` for a single changed file (given
 * relative to workspaceRoot).
 */
export function runTestImpactScan(
  pythonPath: string,
  extensionRoot: string,
  workspaceRoot: string,
  changedFileRelativePath: string
): Promise<TestImpactResult> {
  return runCli<TestImpactResult>(pythonPath, extensionRoot, [
    "test-impact",
    workspaceRoot,
    changedFileRelativePath,
    "--format",
    "json",
  ]);
}

export function runSettingsOverview(
  pythonPath: string,
  extensionRoot: string,
  workspaceRoot: string
): Promise<SettingsOverviewResult> {
  return runCli<SettingsOverviewResult>(pythonPath, extensionRoot, [
    "settings-overview",
    workspaceRoot,
    "--format",
    "json",
  ]);
}

export interface RuntimeReportTopIssue {
  count: number;
  file?: string;
  line?: number;
  message: string;
}

export interface RuntimeReportEndpoint {
  path?: string;
  method?: string;
  wall_time: number;
  total_queries: number;
  duplicate_group_count: number;
  top_issue: RuntimeReportTopIssue | null;
}

export interface RuntimeReportResult {
  reports_dir: string;
  request_count: number;
  endpoints: RuntimeReportEndpoint[];
}

/**
 * Reads and summarizes JSON reports already written by the (separate,
 * Django-dependent) djangolens_runtime middleware — this call itself
 * never runs Django, it only reads files from `reportsDir`.
 */
export function runRuntimeReport(
  pythonPath: string,
  extensionRoot: string,
  reportsDir: string
): Promise<RuntimeReportResult> {
  return runCli<RuntimeReportResult>(pythonPath, extensionRoot, ["runtime-report", reportsDir, "--format", "json"]);
}

export interface ApiDiffResult {
  project_root: string;
  base_ref: string;
  head_ref: string;
  error: string | null;
  diagnostic_count: number;
  diagnostics: DjangoLensDiagnostic[];
}

export function runApiDiff(
  pythonPath: string,
  extensionRoot: string,
  workspaceRoot: string,
  baseRef: string,
  headRef?: string
): Promise<ApiDiffResult> {
  const args = ["api-diff", workspaceRoot, "--base", baseRef, "--format", "json"];
  if (headRef) {
    args.push("--head", headRef);
  }
  return runCli<ApiDiffResult>(pythonPath, extensionRoot, args);
}

export interface SignalChain {
  signal: string;
  sender: string | null;
  receiver: string;
  file: string;
  line: number;
  effects: string[];
}

export interface SignalChainsResult {
  project_root: string;
  apps: string[];
  chain_count: number;
  chains: SignalChain[];
}

export function runSignalChains(
  pythonPath: string,
  extensionRoot: string,
  workspaceRoot: string
): Promise<SignalChainsResult> {
  return runCli<SignalChainsResult>(pythonPath, extensionRoot, ["signal-chains", workspaceRoot, "--format", "json"]);
}

function runCli<T>(pythonPath: string, extensionRoot: string, args: string[]): Promise<T> {
  return new Promise((resolve, reject) => {
    const pythonRoot = path.join(extensionRoot, "python");
    const child = cp.spawn(pythonPath, ["-m", "django_doctor.cli", ...args], {
      cwd: pythonRoot,
      env: { ...process.env, PYTHONPATH: pythonRoot }
    });

    let stdout = "";
    let stderr = "";

    child.stdout.on("data", (data) => (stdout += data.toString()));
    child.stderr.on("data", (data) => (stderr += data.toString()));

    child.on("error", (err) => {
      reject(new Error(`Could not launch "${pythonPath}": ${err.message}`));
    });

    child.on("close", (code) => {
      if (code !== 0 && !stdout.trim()) {
        reject(new Error(`DjangoLens analyzer exited with code ${code}: ${stderr}`));
        return;
      }
      try {
        resolve(JSON.parse(stdout) as T);
      } catch (e) {
        reject(new Error(`Failed to parse DjangoLens analyzer output: ${stderr || stdout}`));
      }
    });
  });
}
