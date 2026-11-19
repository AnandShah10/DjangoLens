import json
import os
import subprocess
import sys
import unittest

from django_doctor.cli import run_scan, format_sarif

FIXTURE_ROOT = os.path.join(os.path.dirname(__file__), "fixtures", "sample_project")
PYTHON_PKG_ROOT = os.path.join(os.path.dirname(__file__), "..")


class SarifOutputTests(unittest.TestCase):
    def test_sarif_has_valid_top_level_shape(self):
        result = run_scan(FIXTURE_ROOT, {})
        sarif = format_sarif(result)

        self.assertEqual(sarif["version"], "2.1.0")
        self.assertIn("runs", sarif)
        run = sarif["runs"][0]
        self.assertEqual(run["tool"]["driver"]["name"], "DjangoLens")
        self.assertIn("results", run)

    def test_every_diagnostic_becomes_a_sarif_result(self):
        # All fixture diagnostics carry a file (including circular
        # dependencies, since that gap was fixed), so nothing should be
        # silently dropped for lacking a location.
        result = run_scan(FIXTURE_ROOT, {})
        sarif = format_sarif(result)
        self.assertEqual(len(sarif["runs"][0]["results"]), result["diagnostic_count"])

    def test_rules_dictionary_covers_every_result_rule_id(self):
        result = run_scan(FIXTURE_ROOT, {})
        sarif = format_sarif(result)
        run = sarif["runs"][0]

        rule_ids_declared = {r["id"] for r in run["tool"]["driver"]["rules"]}
        rule_ids_used = {r["ruleId"] for r in run["results"]}
        self.assertTrue(rule_ids_used.issubset(rule_ids_declared))

    def test_locations_are_relative_forward_slash_paths(self):
        result = run_scan(FIXTURE_ROOT, {})
        sarif = format_sarif(result)
        for r in sarif["runs"][0]["results"]:
            uri = r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
            self.assertFalse(os.path.isabs(uri))
            self.assertNotIn("\\", uri)

    def test_severity_maps_to_valid_sarif_levels(self):
        result = run_scan(FIXTURE_ROOT, {})
        sarif = format_sarif(result)
        valid_levels = {"error", "warning", "note", "none"}
        for r in sarif["runs"][0]["results"]:
            self.assertIn(r["level"], valid_levels)

    def test_diagnostics_missing_a_file_are_skipped_not_crashed_on(self):
        fake_result = {
            "project_root": FIXTURE_ROOT,
            "diagnostics": [
                {"rule_id": "DJANGO001", "severity": "error", "message": "no file here"},
            ],
        }
        sarif = format_sarif(fake_result)
        self.assertEqual(sarif["runs"][0]["results"], [])
        # Still declared as a known rule even though it produced no located result.
        self.assertEqual(len(sarif["runs"][0]["tool"]["driver"]["rules"]), 1)

    def test_sarif_output_is_valid_json_end_to_end_via_cli(self):
        proc = subprocess.run(
            [sys.executable, "-m", "django_doctor.cli", "scan", FIXTURE_ROOT, "--format", "sarif"],
            cwd=PYTHON_PKG_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        parsed = json.loads(proc.stdout)
        self.assertEqual(parsed["version"], "2.1.0")


if __name__ == "__main__":
    unittest.main()
