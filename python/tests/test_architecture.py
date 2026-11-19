import os
import tempfile
import unittest

from django_doctor.discovery import discover_apps
from django_doctor.indexer import build_cross_app_imports
from django_doctor.analyzers.architecture import (
    detect_circular_dependencies,
    check_architecture_rules,
)

FIXTURE_ROOT = os.path.join(os.path.dirname(__file__), "fixtures", "sample_project")


class DiscoveryTests(unittest.TestCase):
    def test_discover_apps_finds_expected_apps(self):
        apps = discover_apps(FIXTURE_ROOT)
        names = {name for name, _ in apps}
        self.assertEqual(names, {"orders", "payments", "users"})

    def test_discover_apps_ignores_migrations_and_hidden_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "migrations"))
            with open(os.path.join(tmp, "migrations", "models.py"), "w") as f:
                f.write("# not a real app\n")
            os.makedirs(os.path.join(tmp, ".git"))
            apps = discover_apps(tmp)
            self.assertEqual(apps, [])

    def test_discover_apps_finds_apps_nested_under_src(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = os.path.join(tmp, "src", "widgets")
            os.makedirs(app_dir)
            open(os.path.join(app_dir, "models.py"), "w").close()

            apps = discover_apps(tmp)

            names = {name for name, _ in apps}
            self.assertIn("widgets", names)

    def test_discover_apps_finds_apps_nested_under_apps_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = os.path.join(tmp, "apps", "gadgets")
            os.makedirs(app_dir)
            open(os.path.join(app_dir, "apps.py"), "w").close()

            apps = discover_apps(tmp)

            names = {name for name, _ in apps}
            self.assertIn("gadgets", names)

    def test_flat_layout_app_is_not_duplicated_by_src_scan(self):
        # A same-named app shouldn't appear twice even if it happens to
        # also exist (differently) under src/ or apps/ — first match wins.
        with tempfile.TemporaryDirectory() as tmp:
            flat_app = os.path.join(tmp, "widgets")
            os.makedirs(flat_app)
            open(os.path.join(flat_app, "models.py"), "w").close()

            nested_app = os.path.join(tmp, "src", "widgets")
            os.makedirs(nested_app)
            open(os.path.join(nested_app, "models.py"), "w").close()

            apps = discover_apps(tmp)
            names = [name for name, _ in apps]
            self.assertEqual(names.count("widgets"), 1)

    def test_discover_apps_finds_apps_nested_two_levels_under_src(self):
        # src/myproject/orders/ — an extra package level beyond the
        # previous one-level-only support.
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = os.path.join(tmp, "src", "myproject", "orders")
            os.makedirs(app_dir)
            open(os.path.join(app_dir, "models.py"), "w").close()

            apps = discover_apps(tmp)

            names = {name for name, _ in apps}
            self.assertIn("orders", names)

    def test_non_app_intermediate_directories_are_not_treated_as_apps(self):
        # src/myproject/common/utils/ has no models.py/apps.py anywhere
        # along the way — none of these intermediate directories should
        # show up as a (false-positive) app.
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "src", "myproject", "common", "utils"))
            os.makedirs(os.path.join(tmp, "src", "myproject", "orders"))
            open(os.path.join(tmp, "src", "myproject", "orders", "models.py"), "w").close()

            apps = discover_apps(tmp)

            names = {name for name, _ in apps}
            self.assertEqual(names, {"orders"})

    def test_does_not_descend_into_an_already_identified_app(self):
        # An app directory containing its own nested models.py-bearing
        # subdirectory (unusual, but possible) must not spawn a second,
        # phantom "app" for that subdirectory.
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = os.path.join(tmp, "src", "orders")
            nested = os.path.join(app_dir, "weird_subdir")
            os.makedirs(nested)
            open(os.path.join(app_dir, "models.py"), "w").close()
            open(os.path.join(nested, "models.py"), "w").close()

            apps = discover_apps(tmp)

            names = {name for name, _ in apps}
            self.assertEqual(names, {"orders"})


class CircularDependencyTests(unittest.TestCase):
    def setUp(self):
        self.apps = discover_apps(FIXTURE_ROOT)
        self.records = build_cross_app_imports(FIXTURE_ROOT, self.apps)

    def test_detects_circular_dependency_between_orders_and_payments(self):
        diagnostics = detect_circular_dependencies(self.records)
        self.assertEqual(len(diagnostics), 1)
        diag = diagnostics[0]
        self.assertEqual(diag["rule_id"], "DJANGO001")
        self.assertEqual(diag["confidence"], "high")
        self.assertTrue({"orders", "payments"}.issubset(set(diag["apps"])))

    def test_no_circular_dependency_reported_for_users(self):
        # users is a leaf (imported by orders, imports nothing itself) so it
        # can never be part of a cycle in this fixture.
        diagnostics = detect_circular_dependencies(self.records)
        for diag in diagnostics:
            self.assertNotIn("users", diag["apps"])

    def test_circular_dependency_diagnostic_has_a_file_location(self):
        # Without a file/line, this diagnostic can never render in an
        # editor's Problems panel — only in the CLI/JSON output.
        diagnostics = detect_circular_dependencies(self.records)
        diag = diagnostics[0]
        self.assertIn("file", diag)
        self.assertTrue(diag["file"].endswith(".py"))
        self.assertGreater(diag["line"], 0)


class ArchitectureRuleTests(unittest.TestCase):
    def setUp(self):
        self.apps = discover_apps(FIXTURE_ROOT)
        self.records = build_cross_app_imports(FIXTURE_ROOT, self.apps)

    def test_deny_list_violation_detected(self):
        config = {"architecture": {"payments": {"cannotImport": ["orders"]}}}
        diagnostics = check_architecture_rules(self.records, config)

        self.assertEqual(len(diagnostics), 1)
        diag = diagnostics[0]
        self.assertEqual(diag["rule_id"], "DJANGOARCH")
        self.assertTrue(diag["module"].startswith("orders"))
        self.assertTrue(diag["file"].endswith(os.path.join("payments", "models.py")))

    def test_allow_list_no_false_positive_for_legal_dependencies(self):
        # orders legitimately imports both users and payments in this fixture.
        config = {"architecture": {"orders": {"canImport": ["users", "payments"]}}}
        diagnostics = check_architecture_rules(self.records, config)
        self.assertEqual(diagnostics, [])

    def test_allow_list_flags_undeclared_dependency(self):
        # orders also imports payments, which is not declared here.
        config = {"architecture": {"orders": {"canImport": ["users"]}}}
        diagnostics = check_architecture_rules(self.records, config)
        self.assertTrue(any(d["module"].startswith("payments") for d in diagnostics))

    def test_app_with_no_configured_rule_is_never_flagged(self):
        config = {"architecture": {"payments": {"cannotImport": ["orders"]}}}
        diagnostics = check_architecture_rules(self.records, config)
        self.assertTrue(all(f"{os.sep}users{os.sep}" not in d["file"] for d in diagnostics))


if __name__ == "__main__":
    unittest.main()
