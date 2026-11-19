import os
import tempfile
import unittest

from django_doctor.discovery import discover_apps
from django_doctor.analyzers.test_impact import find_affected_tests

FIXTURE_ROOT = os.path.join(os.path.dirname(__file__), "fixtures", "sample_project")


class TestImpactAnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.apps = discover_apps(FIXTURE_ROOT)

    def test_direct_importer_is_high_confidence(self):
        results = find_affected_tests(self.apps, "orders/models.py", FIXTURE_ROOT)
        direct = next(r for r in results if r["test_module"] == "orders.tests.test_models")
        self.assertEqual(direct["confidence"], "high")
        self.assertEqual(direct["hops"], 1)

    def test_transitive_same_app_importer_is_likely_confidence(self):
        # orders/tests/test_views.py imports orders/views.py, which imports
        # orders/models.py — two hops away, not a direct import.
        results = find_affected_tests(self.apps, "orders/models.py", FIXTURE_ROOT)
        transitive = next(r for r in results if r["test_module"] == "orders.tests.test_views")
        self.assertEqual(transitive["confidence"], "likely")
        self.assertEqual(transitive["hops"], 2)

    def test_transitive_cross_app_importer_is_found(self):
        # payments/tests/test_payment.py -> payments/models.py -> orders/models.py
        # (payments.models has `from orders.models import Order`).
        results = find_affected_tests(self.apps, "orders/models.py", FIXTURE_ROOT)
        cross_app = next(r for r in results if r["test_module"] == "payments.tests.test_payment")
        self.assertEqual(cross_app["confidence"], "likely")

    def test_isolated_file_has_no_affected_tests(self):
        # Nothing in the fixture imports payments/apps.py.
        results = find_affected_tests(self.apps, "payments/apps.py", FIXTURE_ROOT)
        self.assertEqual(results, [])

    def test_no_duplicate_entries_for_a_test_module(self):
        results = find_affected_tests(self.apps, "orders/models.py", FIXTURE_ROOT)
        modules = [r["test_module"] for r in results]
        self.assertEqual(len(modules), len(set(modules)))

    def test_accepts_absolute_path_too(self):
        abs_path = os.path.join(FIXTURE_ROOT, "orders", "models.py")
        results = find_affected_tests(self.apps, abs_path, FIXTURE_ROOT)
        self.assertTrue(any(r["test_module"] == "orders.tests.test_models" for r in results))

    def test_changed_file_itself_is_never_in_the_results(self):
        results = find_affected_tests(self.apps, "orders/models.py", FIXTURE_ROOT)
        self.assertFalse(any(r["test_module"] == "orders.models" for r in results))

    def test_broken_file_in_the_graph_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = os.path.join(tmp, "broken_app")
            os.makedirs(app_dir)
            with open(os.path.join(app_dir, "models.py"), "w") as f:
                f.write("class Widget:\n    pass\n")
            with open(os.path.join(app_dir, "views.py"), "w") as f:
                f.write("def f(:\n    not valid python")

            apps = discover_apps(tmp)
            results = find_affected_tests(apps, "broken_app/models.py", tmp)
            self.assertEqual(results, [])

    def test_nonexistent_changed_file_returns_empty_without_crashing(self):
        results = find_affected_tests(self.apps, "orders/does_not_exist.py", FIXTURE_ROOT)
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
