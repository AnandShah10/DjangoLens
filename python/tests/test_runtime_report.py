import json
import os
import tempfile
import unittest

from django_doctor.runtime_report import load_runtime_reports, summarize_runtime_reports


def _write_report(directory, filename, report):
    with open(os.path.join(directory, filename), "w", encoding="utf-8") as f:
        json.dump(report, f)


class LoadRuntimeReportsTests(unittest.TestCase):
    def test_loads_every_json_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_report(tmp, "1.json", {"path": "/orders/", "method": "GET"})
            _write_report(tmp, "2.json", {"path": "/payments/", "method": "POST"})
            reports = load_runtime_reports(tmp)
            self.assertEqual(len(reports), 2)

    def test_ignores_non_json_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_report(tmp, "1.json", {"path": "/orders/"})
            with open(os.path.join(tmp, "readme.txt"), "w") as f:
                f.write("not a report")
            reports = load_runtime_reports(tmp)
            self.assertEqual(len(reports), 1)

    def test_skips_malformed_json_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_report(tmp, "good.json", {"path": "/orders/"})
            with open(os.path.join(tmp, "bad.json"), "w") as f:
                f.write("{not valid json")
            reports = load_runtime_reports(tmp)
            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0]["path"], "/orders/")

    def test_nonexistent_directory_returns_empty_list(self):
        self.assertEqual(load_runtime_reports("/does/not/exist"), [])


class SummarizeRuntimeReportsTests(unittest.TestCase):
    def test_sorted_slowest_first(self):
        reports = [
            {"path": "/fast/", "method": "GET", "wall_time": 0.01, "total_queries": 1, "duplicate_groups": []},
            {"path": "/slow/", "method": "GET", "wall_time": 1.5, "total_queries": 200, "duplicate_groups": []},
        ]
        endpoints = summarize_runtime_reports(reports)
        self.assertEqual(endpoints[0]["path"], "/slow/")
        self.assertEqual(endpoints[1]["path"], "/fast/")

    def test_top_issue_reflects_first_duplicate_group(self):
        reports = [{
            "path": "/orders/",
            "method": "GET",
            "wall_time": 0.5,
            "total_queries": 50,
            "duplicate_groups": [
                {"count": 40, "file": "orders/views.py", "line": 12, "sql_sample": "SELECT ...", "total_duration": 0.4},
                {"count": 3, "file": "orders/views.py", "line": 20, "sql_sample": "SELECT ...", "total_duration": 0.05},
            ],
        }]
        endpoints = summarize_runtime_reports(reports)
        top_issue = endpoints[0]["top_issue"]
        self.assertEqual(top_issue["count"], 40)
        self.assertEqual(top_issue["file"], "orders/views.py")
        self.assertIn("N+1", top_issue["message"])

    def test_no_duplicate_groups_means_no_top_issue(self):
        reports = [{"path": "/health/", "method": "GET", "wall_time": 0.001, "total_queries": 0, "duplicate_groups": []}]
        endpoints = summarize_runtime_reports(reports)
        self.assertIsNone(endpoints[0]["top_issue"])

    def test_empty_reports_list(self):
        self.assertEqual(summarize_runtime_reports([]), [])

    def test_missing_optional_fields_do_not_crash(self):
        # A minimal/partial report (e.g. from an older middleware version)
        # should still summarize without KeyError.
        endpoints = summarize_runtime_reports([{"path": "/x/"}])
        self.assertEqual(endpoints[0]["total_queries"], 0)
        self.assertEqual(endpoints[0]["duplicate_group_count"], 0)


if __name__ == "__main__":
    unittest.main()
