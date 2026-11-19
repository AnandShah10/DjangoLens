import os
import tempfile
import unittest

from django_doctor.analyzers.urls import discover_urls, _resolve_urls_module

FIXTURE_ROOT = os.path.join(os.path.dirname(__file__), "fixtures", "sample_project")


class UrlExplorerTests(unittest.TestCase):
    def test_resolves_full_tree_from_root_urlconf(self):
        endpoints, notes = discover_urls(FIXTURE_ROOT)
        paths = {e["path"] for e in endpoints}
        self.assertEqual(paths, {"orders/", "orders/count/", "payments/webhook/"})
        self.assertEqual(notes, [])

    def test_endpoint_carries_view_name_and_location(self):
        endpoints, _ = discover_urls(FIXTURE_ROOT)
        order_list = next(e for e in endpoints if e["path"] == "orders/")
        self.assertEqual(order_list["view"], "order_list_view")
        self.assertEqual(order_list["name"], "order-list")
        self.assertTrue(order_list["file"].endswith(os.path.join("orders", "urls.py")))
        self.assertGreater(order_list["line"], 0)

    def test_prefix_is_concatenated_across_include_boundaries(self):
        endpoints, _ = discover_urls(FIXTURE_ROOT)
        payment_webhook = next(e for e in endpoints if "webhook" in e["path"])
        # "payments/" (from config/urls.py) + "webhook/" (from payments/urls.py)
        self.assertEqual(payment_webhook["path"], "payments/webhook/")

    def test_no_crash_and_empty_result_without_root_urlconf(self):
        with tempfile.TemporaryDirectory() as tmp:
            endpoints, notes = discover_urls(tmp)
            self.assertEqual(endpoints, [])

    def test_broken_urls_file_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "urls.py")
            with open(path, "w") as f:
                f.write("urlpatterns = [\n  this is not valid python")
            endpoints, notes = [], []
            _resolve_urls_module(tmp, path, "", set(), endpoints, notes)
            self.assertEqual(endpoints, [])

    def test_dynamic_include_is_skipped_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "urls.py")
            with open(path, "w") as f:
                f.write(
                    "from django.urls import path, include\n"
                    "from myapp.views import router\n\n"
                    "urlpatterns = [\n"
                    "    path('api/', include(router.urls)),\n"
                    "]\n"
                )
            endpoints, notes = [], []
            _resolve_urls_module(tmp, path, "", set(), endpoints, notes)
            self.assertEqual(endpoints, [])
            self.assertEqual(notes, [])

    def test_unresolvable_string_include_produces_a_note_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "urls.py")
            with open(path, "w") as f:
                f.write(
                    "from django.urls import path, include\n\n"
                    "urlpatterns = [\n"
                    "    path('missing/', include('nonexistent_app.urls')),\n"
                    "]\n"
                )
            endpoints, notes = [], []
            _resolve_urls_module(tmp, path, "", set(), endpoints, notes)
            self.assertEqual(endpoints, [])
            self.assertEqual(len(notes), 1)
            self.assertIn("nonexistent_app.urls", notes[0])

    def test_include_cycle_does_not_infinite_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            a_path = os.path.join(tmp, "a.py")
            b_path = os.path.join(tmp, "b.py")
            with open(a_path, "w") as f:
                f.write(
                    "from django.urls import path, include\n\n"
                    "urlpatterns = [path('b/', include('b'))]\n"
                )
            with open(b_path, "w") as f:
                f.write(
                    "from django.urls import path, include\n\n"
                    "urlpatterns = [path('a/', include('a'))]\n"
                )
            endpoints, notes = [], []
            # If this doesn't hang, the visited-set cycle guard works.
            _resolve_urls_module(tmp, a_path, "", set(), endpoints, notes)
            self.assertEqual(endpoints, [])


if __name__ == "__main__":
    unittest.main()
