import traceback
import unittest

from djangolens_runtime.frames import first_project_frame


def _fs(filename, lineno=1, name="f"):
    return traceback.FrameSummary(filename, lineno, name)


class FramesTests(unittest.TestCase):
    def test_skips_django_and_site_packages_frames(self):
        stack = [
            _fs("/app/orders/views.py", name="order_list_view"),
            _fs("/usr/lib/python3.11/site-packages/django/db/models/query.py", name="_fetch_all"),
            _fs("/usr/lib/python3.11/site-packages/django/db/backends/utils.py", name="execute"),
        ]
        frame = first_project_frame(stack, project_root="/app")
        self.assertEqual(frame.filename, "/app/orders/views.py")

    def test_returns_innermost_matching_frame_not_outermost(self):
        stack = [
            _fs("/app/orders/services.py", name="charge_order"),
            _fs("/app/orders/views.py", name="order_list_view"),
        ]
        frame = first_project_frame(stack, project_root="/app")
        # Innermost (closest to where the query was actually issued) wins —
        # that's the *last* entry in a traceback-style stack.
        self.assertEqual(frame.name, "order_list_view")

    def test_falls_back_to_innermost_frame_when_nothing_matches_project_root(self):
        stack = [_fs("/usr/lib/site-packages/django/db/backends/utils.py", name="execute")]
        frame = first_project_frame(stack, project_root="/app")
        self.assertIsNotNone(frame)
        self.assertEqual(frame.name, "execute")

    def test_no_project_root_still_skips_django_frames(self):
        stack = [
            _fs("/app/orders/views.py", name="order_list_view"),
            _fs("/usr/lib/site-packages/django/db/backends/utils.py", name="execute"),
        ]
        frame = first_project_frame(stack, project_root=None)
        self.assertEqual(frame.name, "order_list_view")

    def test_empty_stack_returns_none(self):
        self.assertIsNone(first_project_frame([], project_root="/app"))


if __name__ == "__main__":
    unittest.main()
